import requests
from caches import simkl_cache
from caches.main_cache import cache_object, timedelta, MainCache
from modules import kodi_utils
from modules.cache import check_databases
from modules.utils import paginate_list, sort_for_article, title_key, jsondate_to_datetime, TaskPool
# logger = kodi_utils.logger

import time
from threading import Thread

get_setting, js2date = kodi_utils.get_setting, jsondate_to_datetime
EXPIRES_1_HOURS = 1
base_url = 'https://api.simkl.com/%s'
timeout = 10.05
session = requests.Session()
retry = requests.adapters.Retry(total=None, status=1, status_forcelist=(429, 502, 503, 504))
session.mount('https://api.simkl.com', requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=retry))

# Rate limit tracking
_rate_limit_remaining = 1000
_rate_limit_reset = 0

def _update_rate_limits(response):
	global _rate_limit_remaining, _rate_limit_reset
	try:
		if 'X-RateLimit-Remaining' in response.headers:
			_rate_limit_remaining = int(response.headers['X-RateLimit-Remaining'])
		if 'X-RateLimit-Reset' in response.headers:
			_rate_limit_reset = int(response.headers['X-RateLimit-Reset'])
	except (ValueError, KeyError):
		pass

def get_rate_limit_status():
	return {
		'remaining': _rate_limit_remaining,
		'reset': _rate_limit_reset
	}

def call_simkl(path, params=None, data=None, with_auth=True, method=None):
	headers = {'Content-Type': 'application/json', 'simkl-api-key': get_setting('simkl.client_id')}
	if with_auth:
		token = get_setting('simkl.token')
		if token: headers['Authorization'] = 'Bearer %s' % token
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
		result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
		if not response.ok: response.raise_for_status()
		return result
	except requests.exceptions.RequestException as e:
		kodi_utils.logger('simkl error', str(e))

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
		original_list.sort(key=lambda k: k.get('last_watched_at', ''), reverse=True)
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
	if not result: return []
	items = []
	key = 'movie' if media_type == 'movies' else 'show'
	for item in result:
		media = item.get(key, {})
		ids = media.get('ids', {})
		items.append({
			'title': media.get('title', ''),
			'release_year': media.get('year', ''),
			'id': ids.get('tmdb'),
			'imdb_id': ids.get('imdb', ''),
			'tmdb_id': ids.get('tmdb', ''),
			'simkl_id': ids.get('simkl', ''),
			'last_watched_at': item.get('last_watched_at', ''),
			'status': item.get('status', status),
			'user_rating': item.get('user_rating'),
			'mediatype': 'movie' if media_type == 'movies' else 'show'
		})
	return items

def simkl_sync_activities_thread(*args, **kwargs):
	Thread(target=simkl_sync_activities, args=args, kwargs=kwargs).start()

def simkl_sync_activities(force_update=False):
	def _get_timestamp(date_time):
		return int(time.mktime(date_time.timetuple()))
	def _compare(latest, cached, res_format='%Y-%m-%dT%H:%M:%SZ'):
		try: result = _get_timestamp(js2date(latest, res_format)) > _get_timestamp(js2date(cached, res_format))
		except Exception: result = True
		return result
	if not get_setting('simkl_user', ''): return 'no account'
	if force_update:
		check_databases()
		simkl_cache.clear_all_simkl_cache_data(refresh=False)
	latest = simkl_get_activity()
	if not latest:
		simkl_cache.clear_all_simkl_cache_data(refresh=False)
		return 'failed'
	success = 'not needed'
	cached = simkl_cache.reset_activity(latest)
	movies_changed = False
	shows_changed = False
	try:
		movies_activity = latest.get('movies', {})
		shows_activity = latest.get('tv_shows', {})
		cached_movies = cached.get('movies', {})
		cached_shows = cached.get('tv_shows', {})
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

def clear_simkl_cache(silent=False):
	from modules.kodi_utils import path_exists, clear_property, database_connect, maincache_db
	if not path_exists(maincache_db): return True
	dbcon = database_connect(maincache_db, isolation_level=None)
	try:
		dbcur = dbcon.cursor()
		dbcur.execute("""PRAGMA synchronous = OFF""")
		dbcur.execute("""PRAGMA journal_mode = OFF""")
		dbcur.execute("""SELECT id FROM maincache WHERE id LIKE ?""", ('simkl_%',))
		results = [str(i[0]) for i in dbcur.fetchall()]
		if not results: return True
		dbcur.execute("""DELETE FROM maincache WHERE id LIKE ?""", ('simkl_%',))
		for i in results: clear_property(i)
		return True
	except Exception: return False
	finally:
		dbcon.close()
