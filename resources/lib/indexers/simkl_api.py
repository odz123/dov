import time
import requests
from caches import simkl_cache
from modules import kodi_utils
from modules.cache import check_databases
from modules.utils import paginate_list, sort_for_article
# logger = kodi_utils.logger

from threading import Thread, Lock

get_setting = kodi_utils.get_setting
base_url = 'https://api.simkl.com/%s'
timeout = (3.05, 10.05)
session = requests.Session()
_retry_kwargs = dict(
	total=5,
	connect=3,
	read=2,
	status=3,
	backoff_factor=1.0,
	status_forcelist=(502, 503, 504),
	respect_retry_after_header=True
)
_allowed = frozenset(['GET', 'POST', 'DELETE', 'HEAD', 'PUT', 'OPTIONS', 'TRACE'])
try:
	retry = requests.adapters.Retry(allowed_methods=_allowed, **_retry_kwargs)
except TypeError:
	retry = requests.adapters.Retry(method_whitelist=_allowed, **_retry_kwargs)
session.mount('https://api.simkl.com', requests.adapters.HTTPAdapter(pool_maxsize=100, pool_connections=4, max_retries=retry))

# Rate limit tracking
_rate_limit_remaining = 1000
_rate_limit_reset = 0
_rate_limit_lock = Lock()

def _update_rate_limits(response):
	global _rate_limit_remaining, _rate_limit_reset
	try:
		with _rate_limit_lock:
			if 'X-RateLimit-Remaining' in response.headers:
				_rate_limit_remaining = int(response.headers['X-RateLimit-Remaining'])
			if 'X-RateLimit-Reset' in response.headers:
				reset_val = int(response.headers['X-RateLimit-Reset'])
				if reset_val > 1000000000:
					_rate_limit_reset = reset_val
				else:
					_rate_limit_reset = int(time.time()) + reset_val
	except (ValueError, KeyError):
		pass

def _wait_for_rate_limit():
	"""Wait if rate limited before making a request."""
	global _rate_limit_remaining, _rate_limit_reset
	with _rate_limit_lock:
		if _rate_limit_remaining > 0:
			return
		wait_time = max(0, _rate_limit_reset - int(time.time()))
	if wait_time > 0:
		kodi_utils.logger('simkl', 'Rate limited, waiting %d seconds' % wait_time)
		time.sleep(min(wait_time + 1, 60))
	with _rate_limit_lock:
		_rate_limit_remaining = 1

def get_rate_limit_status():
	with _rate_limit_lock:
		return {
			'remaining': _rate_limit_remaining,
			'reset': _rate_limit_reset
		}

def _get_retry_wait(response):
	"""Get wait time in seconds from a 429 response."""
	try:
		if 'Retry-After' in response.headers:
			return int(response.headers['Retry-After'])
	except (ValueError, KeyError):
		pass
	try:
		if 'X-RateLimit-Reset' in response.headers:
			reset_val = int(response.headers['X-RateLimit-Reset'])
			if reset_val > 1000000000:
				return max(1, reset_val - int(time.time()))
			return max(1, reset_val)
	except (ValueError, KeyError):
		pass
	return 5

def _rebuild_session():
	"""Clear stale connections by closing and remounting the adapter."""
	try:
		session.close()
		session.mount('https://api.simkl.com', requests.adapters.HTTPAdapter(pool_maxsize=100, pool_connections=4, max_retries=retry))
	except Exception:
		pass

def call_simkl(path, params=None, data=None, with_auth=True, method=None, expected_statuses=None):
	global _rate_limit_remaining
	client_id = get_setting('simkl.client_id')
	if not client_id:
		kodi_utils.logger('simkl error', 'no client_id configured')
		return None
	headers = {'simkl-api-key': client_id}
	if data is not None:
		headers['Content-Type'] = 'application/json'
	if with_auth:
		token = get_setting('simkl.token')
		if token: headers['Authorization'] = 'Bearer %s' % token
		else:
			kodi_utils.logger('simkl error', 'no auth token for %s (auth required)' % path)
			return None
	_wait_for_rate_limit()
	for attempt in range(4):
		try:
			response = session.request(
				method or ('post' if data else 'get'),
				base_url % path,
				params=params,
				json=data,
				headers=headers,
				timeout=timeout
			)
			_update_rate_limits(response)
			if response.status_code == 429:
				wait = min(_get_retry_wait(response), 60)
				kodi_utils.logger('simkl', '429 rate limited on %s, waiting %d seconds (attempt %d/4)' % (path, wait, attempt + 1))
				time.sleep(wait + 1)
				with _rate_limit_lock:
					_rate_limit_remaining = 1
				continue
			try: result = response.json()
			except (ValueError, Exception): result = response.text
			if not response.ok:
				if expected_statuses and response.status_code in expected_statuses:
					return result
				if response.status_code == 401:
					kodi_utils.logger('simkl error', 'HTTP 401 unauthorized for %s - token may be expired' % path)
					return None
				if response.status_code < 500:
					kodi_utils.logger('simkl error', 'HTTP %d for %s: %s' % (response.status_code, path, str(result)[:200]))
					return None
				response.raise_for_status()
			return result
		except requests.exceptions.ConnectionError as e:
			kodi_utils.logger('simkl error', 'connection error: %s (attempt %d/4)' % (str(e), attempt + 1))
			_rebuild_session()
			if attempt < 3:
				time.sleep(2 ** attempt)
				continue
			return None
		except requests.exceptions.RequestException as e:
			kodi_utils.logger('simkl error', '%s (attempt %d/4)' % (str(e), attempt + 1))
			if attempt < 3:
				time.sleep(2 ** attempt)
				continue
			return None
	kodi_utils.logger('simkl error', 'all retries exhausted for %s' % path)
	return None

def simkl_get_activity():
	return call_simkl('sync/activities')

def simkl_all_items(media_type, status):
	"""Get all items of a media type with a given status.
	media_type: movies, shows, anime
	status: watching, plantowatch, completed, hold, dropped
	"""
	url = 'sync/all-items/%s/%s' % (media_type, status)
	result = call_simkl(url, params={'extended': 'full'})
	if result is not None:
		rtype = type(result).__name__
		rlen = len(result) if isinstance(result, (list, dict, str)) else 'N/A'
		kodi_utils.logger('simkl', 'all_items %s/%s: type=%s len=%s' % (media_type, status, rtype, rlen))
	return result

def simkl_search_by_id(id_type, id_value):
	"""Search Simkl by external ID. id_type: imdb, tmdb, tvdb, mal, etc."""
	params = {id_type: id_value, 'client_id': get_setting('simkl.client_id')}
	return call_simkl('search/id', params=params, with_auth=False)

def simkl_user_settings():
	return call_simkl('users/settings')

def simkl_add_to_list(data):
	"""Add items to user's watchlist/list. data should contain movies/shows arrays."""
	return call_simkl('sync/add-to-list', data=data, method='post')

def simkl_history_add(data):
	"""Mark items as watched. data should contain movies/shows arrays."""
	return call_simkl('sync/history', data=data, method='post')

def simkl_history_remove(data):
	"""Remove items from watched history."""
	return call_simkl('sync/history/remove', data=data, method='post')

def simkl_checkin(media_type, tmdb_id, season=None, episode=None):
	"""Start a Simkl check-in (sets 'now watching' status).
	Uses POST /checkin per Simkl API. The item shows as watching on the site
	and auto-completes after the content's runtime elapses."""
	if not get_setting('simkl_user', ''): return
	try: tmdb_id = int(tmdb_id)
	except (ValueError, TypeError): return
	if media_type == 'movie':
		data = {'movie': {'ids': {'tmdb': tmdb_id}}}
	elif media_type == 'episode':
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		data = {'show': {'ids': {'tmdb': tmdb_id}}, 'episode': {'season': season, 'number': episode}}
	else: return
	return call_simkl('checkin', data=data, method='post', expected_statuses=(409,))

def simkl_checkout():
	"""Cancel a Simkl check-in (clears 'now watching' status).
	Uses DELETE /checkin to stop showing the user as currently watching."""
	if not get_setting('simkl_user', ''): return
	return call_simkl('checkin', method='delete', expected_statuses=(404,))

def simkl_watched_unwatched(action, media, media_id, season=None, episode=None):
	"""Push watched/unwatched status to Simkl. Called in background thread."""
	if not get_setting('simkl_user', ''): return
	try: media_id = int(media_id)
	except (ValueError, TypeError): return
	from datetime import datetime, timezone
	watched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
	func = simkl_history_add if action == 'mark_as_watched' else simkl_history_remove
	if media == 'movies':
		item = {'ids': {'tmdb': media_id}}
		if action == 'mark_as_watched': item['watched_at'] = watched_at
		data = {'movies': [item]}
	elif media == 'episode':
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		ep = {'number': episode}
		if action == 'mark_as_watched': ep['watched_at'] = watched_at
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [{'number': season, 'episodes': [ep]}]}]}
	elif media == 'shows':
		item = {'ids': {'tmdb': media_id}}
		if action == 'mark_as_watched': item['watched_at'] = watched_at
		data = {'shows': [item]}
	elif media == 'season':
		try: season = int(season)
		except (ValueError, TypeError): return
		season_data = {'number': season}
		if action == 'mark_as_watched': season_data['watched_at'] = watched_at
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [season_data]}]}
	else: return
	result = func(data)
	if result is not None:
		simkl_cache.clear_simkl_list_data()

def simkl_watchlist_items(media_type, status, page_no, letter):
	"""Get and paginate watchlist items by media_type and status."""
	from modules import settings
	cache_key = 'simkl_%s_%s' % (media_type, status)
	original_list = simkl_cache.cache_simkl_object(_fetch_all_items, cache_key, (media_type, status))
	if not original_list: return [], 1
	sort_key = settings.lists_sort_order('watchlist')
	if sort_key == 2:
		original_list.sort(key=lambda k: k.get('release_year', ''), reverse=True)
	elif sort_key == 1:
		original_list.sort(key=lambda k: k.get('last_watched_at') or '', reverse=True)
	else:
		original_list = sort_for_article(original_list, 'title', settings.ignore_articles())
	if settings.paginate():
		limit = settings.page_limit()
		final_list, total_pages = paginate_list(original_list, page_no, letter, limit)
	else:
		final_list, total_pages = original_list, 1
	return final_list, total_pages

def _extract_ids(ids_dict):
	"""Extract tmdb and imdb IDs from an ids dict, checking multiple key formats."""
	if not ids_dict or not isinstance(ids_dict, dict): return '', ''
	tmdb_id = ids_dict.get('tmdb') or ids_dict.get('tmdb_id') or ids_dict.get('tmdbid') or ''
	imdb_id = ids_dict.get('imdb') or ids_dict.get('imdb_id') or ids_dict.get('imdbid') or ''
	return tmdb_id, imdb_id

def _fetch_all_items(args):
	"""Fetch all items from Simkl for a given media_type and status."""
	media_type, status = args
	result = simkl_all_items(media_type, status)
	if result is None: return None
	if isinstance(result, dict):
		kodi_utils.logger('simkl', '_fetch_all_items got dict response for %s/%s, keys: %s' % (media_type, status, list(result.keys())))
		original = result
		singular = {'movies': 'movie', 'shows': 'show', 'anime': 'anime'}.get(media_type, media_type)
		result = None
		for try_key in (media_type, singular, 'data'):
			v = original.get(try_key)
			if isinstance(v, list):
				result = v
				break
		if result is None:
			for v in original.values():
				if isinstance(v, list):
					result = v
					break
	if not isinstance(result, list):
		kodi_utils.logger('simkl', '_fetch_all_items: unexpected response type %s for %s/%s' % (type(result).__name__, media_type, status))
		return None
	items = []
	skipped = 0
	key = 'movie' if media_type == 'movies' else 'show'
	for item in result:
		try:
			media = item.get(key) or {}
			# Try IDs from the nested media object first (standard format: item.movie.ids)
			ids = media.get('ids') or {}
			tmdb_id, imdb_id = _extract_ids(ids)
			# Fallback: try IDs directly on the item level (alternate format: item.ids)
			if not tmdb_id and not imdb_id:
				item_ids = item.get('ids') or {}
				tmdb_id, imdb_id = _extract_ids(item_ids)
				if not ids and item_ids: ids = item_ids
			# Fallback: try IDs as direct fields on media or item objects
			if not tmdb_id:
				tmdb_id = media.get('tmdb') or media.get('tmdb_id') or item.get('tmdb') or item.get('tmdb_id') or ''
			if not imdb_id:
				imdb_id = media.get('imdb') or media.get('imdb_id') or item.get('imdb') or item.get('imdb_id') or ''
			# Fallback: search Simkl by simkl_id to resolve external IDs
			if not tmdb_id and not imdb_id:
				simkl_id = ids.get('simkl') or ids.get('simkl_id') or ''
				if not simkl_id:
					simkl_id = (item.get('ids') or {}).get('simkl') or ''
				if simkl_id:
					try:
						lookup = simkl_search_by_id('simkl', simkl_id)
						if lookup and isinstance(lookup, list) and len(lookup) > 0:
							lookup_ids = lookup[0].get('ids') or {}
							tmdb_id, imdb_id = _extract_ids(lookup_ids)
					except Exception: pass
			# Fallback: search TMDB by title and year
			if not tmdb_id and not imdb_id:
				title = media.get('title') or item.get('title') or ''
				year = media.get('year') or item.get('year') or ''
				if title:
					try:
						from indexers import tmdb_api
						if media_type == 'movies':
							tmdb_result = tmdb_api.movie_title_year(title, year)
						else:
							tmdb_result = tmdb_api.tvshow_title_year(title, year)
						if tmdb_result:
							tmdb_id = tmdb_result.get('id', '')
					except Exception: pass
			if not tmdb_id and not imdb_id:
				skipped += 1
				continue
			# Ensure IDs are strings for consistent cache handling
			if tmdb_id: tmdb_id = str(tmdb_id)
			if imdb_id: imdb_id = str(imdb_id)
			year = media.get('year') or item.get('year') or ''
			title = media.get('title') or item.get('title') or ''
			simkl_id = ids.get('simkl') or ids.get('simkl_id') or (item.get('ids') or {}).get('simkl') or ''
			items.append({
				'title': title,
				'release_year': str(year) if year else '',
				'id': tmdb_id,
				'imdb_id': imdb_id,
				'tmdb_id': tmdb_id,
				'simkl_id': str(simkl_id) if simkl_id else '',
				'last_watched_at': item.get('last_watched_at') or '',
				'status': item.get('status', status),
				'user_rating': item.get('user_rating'),
				'mediatype': 'movie' if media_type == 'movies' else 'show'
			})
		except Exception: continue
	if skipped:
		kodi_utils.logger('simkl', '_fetch_all_items: skipped %d/%d items without TMDB/IMDB IDs for %s/%s' % (skipped, len(result), media_type, status))
	if not items:
		kodi_utils.logger('simkl', '_fetch_all_items: parsed 0 items from %d results for %s/%s' % (len(result), media_type, status))
	return items or None

def simkl_sync_activities_thread(*args, **kwargs):
	Thread(target=simkl_sync_activities, args=args, kwargs=kwargs, daemon=True).start()

def simkl_sync_activities(force_update=False):
	if not get_setting('simkl_user', ''): return 'no account'
	if force_update:
		check_databases()
		simkl_cache.clear_all_simkl_cache_data(refresh=False)
	latest = simkl_get_activity()
	if not latest or not isinstance(latest, dict):
		return 'failed'
	success = 'not needed'
	cached = simkl_cache.get_cached_activity()
	changed = False
	try:
		for key in ('movies', 'tv_shows', 'shows', 'anime'):
			activity = latest.get(key) or {}
			cached_activity = cached.get(key) or {}
			if not isinstance(activity, dict) or not isinstance(cached_activity, dict):
				changed = True
				break
			if activity.get('all', '') != cached_activity.get('all', ''):
				changed = True
				break
	except Exception:
		changed = True
	if changed:
		success = 'success'
		simkl_cache.clear_simkl_list_data()
	simkl_cache.save_activity(latest)
	return success

def clear_simkl_cache():
	from modules.kodi_utils import path_exists, clear_property, database_connect, simkl_db
	if not path_exists(simkl_db): return True
	try:
		dbcon = database_connect(simkl_db, isolation_level=None)
	except Exception: return False
	try:
		dbcur = dbcon.cursor()
		dbcur.execute("""PRAGMA synchronous = OFF""")
		dbcur.execute("""PRAGMA journal_mode = OFF""")
		dbcur.execute("""SELECT id FROM simkl_data""")
		results = [str(i[0]) for i in dbcur.fetchall()]
		if not results: return True
		dbcur.execute("""DELETE FROM simkl_data""")
		for i in results: clear_property(i)
		return True
	except Exception: return False
	finally:
		dbcon.close()
