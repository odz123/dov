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

def call_simkl(path, params=None, data=None, with_auth=True, method=None, expected_statuses=None, return_headers=False):
	global _rate_limit_remaining
	_none = (None, {}) if return_headers else None
	client_id = get_setting('simkl.client_id')
	if not client_id:
		kodi_utils.logger('simkl error', 'no client_id configured')
		return _none
	headers = {'simkl-api-key': client_id}
	if data is not None:
		headers['Content-Type'] = 'application/json'
	if with_auth:
		token = get_setting('simkl.token')
		if token: headers['Authorization'] = 'Bearer %s' % token
		else:
			kodi_utils.logger('simkl error', 'no auth token for %s (auth required)' % path)
			return _none
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
					return (result, dict(response.headers)) if return_headers else result
				if response.status_code == 401:
					kodi_utils.logger('simkl error', 'HTTP 401 unauthorized for %s - token may be expired' % path)
					return _none
				if response.status_code < 500:
					kodi_utils.logger('simkl error', 'HTTP %d for %s: %s' % (response.status_code, path, str(result)[:200]))
					return _none
				response.raise_for_status()
			return (result, dict(response.headers)) if return_headers else result
		except requests.exceptions.ConnectionError as e:
			kodi_utils.logger('simkl error', 'connection error: %s (attempt %d/4)' % (str(e), attempt + 1))
			_rebuild_session()
			if attempt < 3:
				time.sleep(2 ** attempt)
				continue
			return _none
		except requests.exceptions.RequestException as e:
			kodi_utils.logger('simkl error', '%s (attempt %d/4)' % (str(e), attempt + 1))
			if attempt < 3:
				time.sleep(2 ** attempt)
				continue
			return _none
	kodi_utils.logger('simkl error', 'all retries exhausted for %s' % path)
	return _none

def simkl_get_activity():
	return call_simkl('sync/activities')

def simkl_all_items(media_type, status):
	"""Get all items of a media type with a given status.
	media_type: movies, shows, anime
	status: watching, plantowatch, completed, hold, dropped
	Handles pagination to fetch all pages of results.
	"""
	url = 'sync/all-items/%s/%s' % (media_type, status)
	result, headers = call_simkl(url, params={'extended': 'full', 'limit': 100}, return_headers=True)
	if result is None: return None
	if not isinstance(result, list):
		kodi_utils.logger('simkl', 'all_items %s/%s: non-list response type=%s' % (media_type, status, type(result).__name__))
		return result
	try:
		page_count = int(headers.get('X-Pagination-Page-Count', '1'))
	except (ValueError, TypeError):
		page_count = 1
	if page_count > 1:
		for page in range(2, page_count + 1):
			page_result = call_simkl(url, params={'extended': 'full', 'limit': 100, 'page': page})
			if page_result is None: break
			if isinstance(page_result, list):
				result.extend(page_result)
	kodi_utils.logger('simkl', 'all_items %s/%s: total=%d pages=%d' % (media_type, status, len(result), page_count))
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

def _to_int(val):
	"""Safely convert a value to int, return None on failure."""
	if val is None or val == '': return None
	try: return int(val)
	except (ValueError, TypeError): return None

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
	key = 'movie' if media_type == 'movies' else 'show'
	for item in result:
		try:
			media = item.get(key) or {}
			ids = media.get('ids') or {}
			tmdb_id = _to_int(ids.get('tmdb'))
			imdb_id = ids.get('imdb') or ''
			if not tmdb_id and not imdb_id:
				simkl_id = ids.get('simkl')
				if simkl_id:
					try:
						lookup = simkl_search_by_id('simkl', simkl_id)
						if lookup and isinstance(lookup, list) and len(lookup) > 0:
							lookup_ids = lookup[0].get('ids') or {}
							tmdb_id = _to_int(lookup_ids.get('tmdb'))
							imdb_id = lookup_ids.get('imdb') or ''
					except Exception: pass
			if not tmdb_id and not imdb_id: continue
			year = media.get('year') or ''
			items.append({
				'title': media.get('title') or '',
				'release_year': str(year) if year else '',
				'id': tmdb_id or imdb_id,
				'imdb_id': imdb_id,
				'tmdb_id': tmdb_id,
				'simkl_id': ids.get('simkl') or '',
				'last_watched_at': item.get('last_watched_at') or '',
				'status': item.get('status', status),
				'user_rating': item.get('user_rating'),
				'mediatype': 'movie' if media_type == 'movies' else 'show'
			})
		except Exception: continue
	if not items:
		kodi_utils.logger('simkl', '_fetch_all_items: parsed 0 items from %d results for %s/%s' % (len(result), media_type, status))
	return items

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
		for key in ('movies', 'tv_shows', 'anime'):
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
