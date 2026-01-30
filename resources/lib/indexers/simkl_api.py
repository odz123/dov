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
timeout = 10.05
session = requests.Session()
_retry_kwargs = dict(
	total=5,
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
session.mount('https://api.simkl.com', requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=retry))

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

def call_simkl(path, params=None, data=None, with_auth=True, method=None, expected_statuses=None):
	client_id = get_setting('simkl.client_id')
	if not client_id: return None
	headers = {'Content-Type': 'application/json', 'simkl-api-key': client_id}
	if with_auth:
		token = get_setting('simkl.token')
		if token: headers['Authorization'] = 'Bearer %s' % token
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
				continue
			try: result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
			except (ValueError, Exception): result = response.text
			if not response.ok:
				if expected_statuses and response.status_code in expected_statuses:
					return result
				response.raise_for_status()
			return result
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
	return call_simkl(url, params={'extended': 'full'})

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
	"""Check in to Simkl (real-time scrobbling). Tells Simkl the user is currently watching."""
	if not get_setting('simkl_user', ''): return
	try: tmdb_id = int(tmdb_id)
	except (ValueError, TypeError): return
	simkl_checkout()
	if media_type == 'movie':
		data = {'movie': {'ids': {'tmdb': tmdb_id}}}
	elif media_type == 'episode':
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		data = {'show': {'ids': {'tmdb': tmdb_id}}, 'episode': {'season': season, 'number': episode}}
	else: return
	return call_simkl('checkin', data=data, method='post')

def simkl_checkout():
	"""Cancel any active Simkl checkin to avoid 409 Conflict on next checkin."""
	if not get_setting('simkl_user', ''): return
	return call_simkl('checkin', method='delete', expected_statuses=(404,))

def simkl_watched_unwatched(action, media, media_id, season=None, episode=None):
	"""Push watched/unwatched status to Simkl. Called in background thread."""
	if not get_setting('simkl_user', ''): return
	try: media_id = int(media_id)
	except (ValueError, TypeError): return
	func = simkl_history_add if action == 'mark_as_watched' else simkl_history_remove
	if media == 'movies':
		data = {'movies': [{'ids': {'tmdb': media_id}}]}
	elif media == 'episode':
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [{'number': season, 'episodes': [{'number': episode}]}]}]}
	elif media == 'shows':
		data = {'shows': [{'ids': {'tmdb': media_id}}]}
	elif media == 'season':
		try: season = int(season)
		except (ValueError, TypeError): return
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [{'number': season}]}]}
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

def _fetch_all_items(args):
	"""Fetch all items from Simkl for a given media_type and status."""
	media_type, status = args
	result = simkl_all_items(media_type, status)
	if not result or not isinstance(result, list): return []
	items = []
	key = 'movie' if media_type == 'movies' else 'show'
	for item in result:
		media = item.get(key) or {}
		ids = media.get('ids') or {}
		tmdb_id = ids.get('tmdb') or ''
		imdb_id = ids.get('imdb') or ''
		if not tmdb_id and not imdb_id: continue
		year = media.get('year') or ''
		items.append({
			'title': media.get('title') or '',
			'release_year': str(year) if year else '',
			'id': tmdb_id,
			'imdb_id': imdb_id,
			'tmdb_id': tmdb_id,
			'simkl_id': ids.get('simkl') or '',
			'last_watched_at': item.get('last_watched_at') or '',
			'status': item.get('status', status),
			'user_rating': item.get('user_rating'),
			'mediatype': 'movie' if media_type == 'movies' else 'show'
		})
	return items

def simkl_sync_activities_thread(*args, **kwargs):
	Thread(target=simkl_sync_activities, args=args, kwargs=kwargs).start()

def simkl_sync_activities(force_update=False):
	if not get_setting('simkl_user', ''): return 'no account'
	if force_update:
		check_databases()
		simkl_cache.clear_all_simkl_cache_data(refresh=False)
	latest = simkl_get_activity()
	if not latest or not isinstance(latest, dict):
		return 'failed'
	success = 'not needed'
	cached = simkl_cache.reset_activity(latest)
	movies_changed = False
	shows_changed = False
	try:
		movies_activity = latest.get('movies') or {}
		shows_activity = latest.get('tv_shows') or {}
		cached_movies = cached.get('movies') or {}
		cached_shows = cached.get('tv_shows') or {}
		if movies_activity.get('all', '') != cached_movies.get('all', ''):
			movies_changed = True
		if shows_activity.get('all', '') != cached_shows.get('all', ''):
			shows_changed = True
	except Exception:
		movies_changed = True
		shows_changed = True
	if movies_changed or shows_changed:
		success = 'success'
		simkl_cache.clear_simkl_list_data()
	return success

def clear_simkl_cache():
	from modules.kodi_utils import path_exists, clear_property, database_connect, simkl_db
	if not path_exists(simkl_db): return True
	dbcon = database_connect(simkl_db, isolation_level=None)
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
