import re
import time
import requests
from threading import Thread, Lock
from caches import mdbl_cache
from caches.main_cache import cache_object, timedelta, MainCache
from caches.meta_cache import cache_function
from indexers.metadata import movie_external_id, tvshow_external_id
from modules import kodi_utils, settings
from modules.cache import check_databases
from modules.utils import make_thread_list, paginate_list, sort_for_article, title_key, jsondate_to_datetime, TaskPool

get_setting, js2date = kodi_utils.get_setting, jsondate_to_datetime
review_provider_id = {1: 'Trakt', 2: 'TMDb', 3: 'RT', 4: 'Metacritics'}
rank_map = {'0': 'mild', '1': 'mild', '2': 'moderate', '3': 'moderate', '4': 'severe', '5': 'severe'}
guide_map = {'Nudity': 'Sex & Nudity', 'Violence': 'Violence & Gore', 'Profanity': 'Profanity', 'Alcohol': 'Alcohol, Drugs & Smoking'}
EXPIRES_1_HOURS = 1
base_url = 'https://api.mdblist.com/%s'
timeout = 5.05
session = requests.Session()
retry = requests.adapters.Retry(total=None, status=1, status_forcelist=(429, 502, 503, 504))
session.mount('https://api.mdblist.com', requests.adapters.HTTPAdapter(pool_maxsize=100, max_retries=retry))

# Rate limit tracking
_rate_limit_remaining = 1000
_rate_limit_limit = 1000
_rate_limit_reset = 0
_rate_limit_lock = Lock()

def _update_rate_limits(response):
	"""Parse and store rate limit headers from MDBList API response."""
	global _rate_limit_remaining, _rate_limit_limit, _rate_limit_reset
	try:
		with _rate_limit_lock:
			if 'X-RateLimit-Remaining' in response.headers:
				_rate_limit_remaining = int(response.headers['X-RateLimit-Remaining'])
			if 'X-RateLimit-Limit' in response.headers:
				_rate_limit_limit = int(response.headers['X-RateLimit-Limit'])
			if 'X-RateLimit-Reset' in response.headers:
				_rate_limit_reset = int(response.headers['X-RateLimit-Reset'])
	except (ValueError, KeyError):
		pass

def get_rate_limit_status():
	"""Return current rate limit status for debugging/monitoring."""
	with _rate_limit_lock:
		return {
			'remaining': _rate_limit_remaining,
			'limit': _rate_limit_limit,
			'reset': _rate_limit_reset
		}

def call_mdblist(path, params=None, json=None, method=None):
	params = params or {}
	params['apikey'] = get_setting('mdblist.token')
	try:
		response = session.request(method or 'get', base_url % path, params=params, json=json, timeout=timeout)
		_update_rate_limits(response)
		result = response.json() if 'json' in response.headers.get('Content-Type', '') else response.text
		if not response.ok: response.raise_for_status()
		return result
	except requests.exceptions.RequestException as e:
		kodi_utils.logger('mdblist error', str(e))

def mdbl_searchlists(query):
	query = requests.utils.quote(query)
	string = 'mdbl_searchlists_%s' % query
	url = 'lists/search?query=%s' % query
	return cache_object(call_mdblist, string, url, json=False, expiration=EXPIRES_1_HOURS)

def mdbl_userlists():
	string = 'mdbl_userlists'
	url = 'lists/user'
	return cache_object(call_mdblist, string, url, json=False, expiration=EXPIRES_1_HOURS)

def mdbl_externallists():
	string = 'mdbl_externallists'
	url = 'external/lists/user'
	return cache_object(call_mdblist, string, url, json=False, expiration=EXPIRES_1_HOURS)

def mdbl_toplists():
	string = 'mdbl_toplists'
	url = 'lists/top'
	return cache_object(call_mdblist, string, url, json=False)

def mdbl_parentsguide(imdb_id, media_type):
	media_type = 'show' if media_type == 'tvshow' else 'movie'
	url = 'https://www.mdblist.com/%s/%s' % (media_type, imdb_id)
	string = 'mdbl_%s_parentsguide_%s' % (media_type, imdb_id)
	params = {'url': url, 'action': 'mdbl_parentsguide'}
	return cache_function(get_mdbl, string, params)

def mdbl_media_info(imdb_id, media_type):
	if not get_setting('mdblist.token'): return
	media_type = 'show' if media_type == 'tvshow' else 'movie'
	string = 'mdbl_%s_mediainfo_%s' % (media_type, imdb_id)
	url = '%s/%s/%s?append_to_response=review' % ('imdb', media_type, imdb_id)
	return cache_function(call_mdblist, string, url)

def mdbl_media_info_batch(items, provider, media_type):
	url = '%s/%s' % (provider, media_type)
	return call_mdblist(url, json=items, method='post')

def mdblist_collection(media_type, page_no, letter):
	string = 'mdbl_collection'
	url = 'sync/collection'
	original_list = mdbl_cache.cache_mdbl_object(mdbl_collection_watchlist_items, string, url)
	if media_type == 'all':
		original_list = original_list['movies'] + original_list['shows']
		for i in original_list:
			key = 'movie' if 'movie' in i else 'show'
			i.update({'id': i.get(key, {}).get('ids', {}).get('tmdb'), 'imdb_id': i.get(key, {}).get('ids', {}).get('imdb')})
		return original_list
	original_list = original_list[media_type]
	key = 'movie' if media_type == 'movies' else 'show'
	for i in original_list: i.update({
		'id': i.get(key, {}).get('ids', {}).get('tmdb'), 'imdb_id': i.get(key, {}).get('ids', {}).get('imdb'),
		'title': i.get(key, {}).get('title', ''), 'release_year': i.get(key, {}).get('year', '')
	})
	sort_key = settings.lists_sort_order('collection')
	if   sort_key == 2: original_list.sort(key=lambda k: k['release_year'], reverse=True)
	elif sort_key == 1: original_list.sort(key=lambda k: k['collected_at'], reverse=True)
	else: original_list = sort_for_article(original_list, 'title', settings.ignore_articles())
	if settings.paginate():
		limit = settings.page_limit()
		final_list, total_pages = paginate_list(original_list, page_no, letter, limit)
	else: final_list, total_pages = original_list, 1
	return final_list, total_pages

def mdblist_watchlist(media_type, page_no, letter):
	string = 'mdbl_watchlist'
	url = 'watchlist/items'
	original_list = mdbl_cache.cache_mdbl_object(mdbl_collection_watchlist_items, string, url)
	if media_type == 'all':
		original_list = original_list['movies'] + original_list['shows']
		for i in original_list:
			key = 'movie' if 'movie' in i else 'show'
			i.update({'id': i.get(key, {}).get('ids', {}).get('tmdb'), 'imdb_id': i.get(key, {}).get('ids', {}).get('imdb')})
		return original_list
	original_list = original_list[media_type]
	key = 'movie' if media_type == 'movies' else 'show'
	for i in original_list: i.update({
		'id': i.get(key, {}).get('ids', {}).get('tmdb'), 'imdb_id': i.get(key, {}).get('ids', {}).get('imdb'),
		'title': i.get(key, {}).get('title', ''), 'release_year': i.get(key, {}).get('year', '')
	})
	sort_key = settings.lists_sort_order('watchlist')
	if   sort_key == 2: original_list.sort(key=lambda k: k['release_year'], reverse=True)
	elif sort_key == 1: original_list.sort(key=lambda k: k['watchlist_at'], reverse=True)
	else: original_list = sort_for_article(original_list, 'title', settings.ignore_articles())
	if settings.paginate():
		limit = settings.page_limit()
		final_list, total_pages = paginate_list(original_list, page_no, letter, limit)
	else: final_list, total_pages = original_list, 1
	return final_list, total_pages

def mdbl_collection_watchlist_items(url):
	params = {'limit': 5000}
	items = {'movies': [], 'shows': [], 'has_more': True}
	while items['has_more']:
		result = call_mdblist(url, params=params)
		if result is not None:
			pagination = result.get('pagination', {})
			items['has_more'] = pagination.get('has_more', False)
			params['offset'] = pagination.get('page', 0) * pagination.get('limit', 5000)
			if 'movies' in result: items['movies'] += result['movies']
			if 'shows' in result: items['shows'] += result['shows']
		else: items['has_more'] = False
	return items

def mdbl_list_items(list_id, list_type):
	sort_index = settings.lists_sort_order('mdblist')
	ignore_articles = settings.ignore_articles()
	if list_type: params = (
		{'sort': 'title', 'order': 'asc'}, {'sort': 'added', 'order': 'desc'},
		{'sort': 'released'}, {'sort': 'random'}, None # usort
	)[sort_index]
	else: params = None
	if list_type == 'external': url = 'external/lists/%s/items?unified=true' % list_id
	elif list_id == 'collection': return mdblist_collection('all', None, '')
	elif list_id == 'watchlist': return mdblist_watchlist('all', None, '')
	else: url = 'lists/%s/items?unified=true' % list_id
	cache = MainCache()
	try:
		cache_get, cache_set = cache.get, cache.set
		string = 'mdbl_userlists_%s' % list_id
		result = cache_get(string)
		if not result:
			result = call_mdblist(url, params=params)
			if result: cache_set(string, result, expiration=timedelta(hours=EXPIRES_1_HOURS))
		if result and list_type and ignore_articles and not sort_index:
			result.sort(key=lambda k: title_key(k.get('title', ''), ignore_articles), reverse=False)
		return result
	finally:
		cache.close()

def mdbl_modify_collection(data, action='add'):
	if action == 'add': url, key = 'sync/collection', 'updated'
	else: url, key = 'sync/collection/remove', 'removed'
	if 'movies' in data: data['movies'] = [{'ids': i} for i in data['movies']]
	if 'shows' in data: data['shows'] = [{'ids': i} for i in data['shows']]
	result = call_mdblist(url, json=data, method='post')
	if not result: return False
	success = key in result and any(result.get(key, {}).get(i, 0) for i in ('movies', 'shows'))
	return success

def mdbl_modify_list(list_id, data, action='add'):
	if list_id == 'collection': return mdbl_modify_collection(data, action)
	elif list_id == 'watchlist': url = 'watchlist/items/%s' % action
	else: url = 'lists/%s/items/%s' % (list_id, action)
	result = call_mdblist(url, json=data, method='post')
	if not result: return False
	key = 'added' if action == 'add' else 'removed'
	success = key in result and any(result.get(key, {}).get(i, 0) for i in ('movies', 'shows'))
	return success

def mdbl_watched_unwatched(action, media, media_id, tvdb_id=0, data=None, season=None, episode=None, key='tmdb'):
	if action == 'mark_as_watched': url, result_key = 'sync/watched', 'updated'
	else: url, result_key = 'sync/watched/remove', 'removed'
	try: media_id = {key: int(media_id)}
	except Exception: pass
	if media == 'movies':
		success_key = 'movies'
		data = {'movies': [{'ids': media_id}]}
	else:
		success_key = 'episodes'
		if media == 'episode': data = {'shows': [{'seasons': [{'episodes': [{'number': int(episode)}], 'number': int(season)}], 'ids': media_id}]}
		else: data = {'shows': [{'ids': media_id, 'seasons': data}]}
	result = call_mdblist(url, json=data, method='post')
	if not result: return False
	success = result.get(result_key, {}).get(success_key, 0) > 0
	if not success:
		if media != 'movies' and tvdb_id != 0:
			return mdbl_watched_unwatched(action, media, tvdb_id, 0, None, season, episode, 'tvdb')
	return success

def mdbl_indicators_movies(watched_info):
	def _process(item):
		tmdb_id = get_mdbl_movie_id(item.get('movie', {}).get('ids', {}))
		if not tmdb_id: return
		with _insert_lock:
			insert_append((
				'movie', str(tmdb_id), '', '', item.get('last_watched_at', ''), item.get('movie', {}).get('title', '')
			))
	insert_list = []
	insert_append = insert_list.append
	_insert_lock = Lock()
	watched_items = [(i,) for i in watched_info.get('movies', [])] # TaskPool requires tuple
	if not watched_items: return
	for i in TaskPool().tasks(_process, watched_items, Thread): i.join()
	with mdbl_cache.MDBLCache() as mc:
		mc.set_bulk_movie_watched(insert_list)

def mdbl_indicators_tv(watched_info):
	def _process(item):
		ep = item.get('episode', {})
		show = ep.get('show', {})
		tmdb_id = get_mdbl_tvshow_id(show.get('ids', {}))
		if not tmdb_id: return
		season, episode = ep.get('season', 0), ep.get('number', 0)
		with _insert_lock:
			insert_append((
				'episode', str(tmdb_id), season, episode, item.get('last_watched_at', ''), show.get('title', '')
			))
	insert_list = []
	insert_append = insert_list.append
	_insert_lock = Lock()
	watched_items = [(i,) for i in watched_info.get('episodes', [])] # TaskPool requires tuple
	if not watched_items: return
	for i in TaskPool().tasks(_process, watched_items, Thread): i.join()
	with mdbl_cache.MDBLCache() as mc:
		mc.set_bulk_tvshow_watched(insert_list)

def mdbl_progress(action, media, media_id, percent, season=None, episode=None, resume_id=None, refresh_mdb=False):
	url = 'scrobble/pause'
	try: media_id = int(media_id)
	except Exception: pass
	if media in ('movie', 'movies'): data = {'movie': {'ids': {'tmdb': media_id}}, 'progress': float(percent)}
	else:
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		data = {'show': {'ids': {'tmdb': media_id}, 'season': {'episode': {'number': episode}, 'number': season}}, 'progress': float(percent)}
	call_mdblist(url, json=data, method='post')
	if refresh_mdb: mdbl_sync_activities()

def mdbl_scrobble(action, media, media_id, percent, season=None, episode=None):
	"""Scrobble playback state to MDBList. Action can be 'start', 'pause', or 'stop'."""
	url = 'scrobble/%s' % action
	try: media_id = int(media_id)
	except Exception: pass
	if media in ('movie', 'movies'):
		data = {'movie': {'ids': {'tmdb': media_id}}, 'progress': float(percent)}
	else:
		try: season, episode = int(season), int(episode)
		except (ValueError, TypeError): return
		data = {'show': {'ids': {'tmdb': media_id}, 'season': {'episode': {'number': episode}, 'number': season}}, 'progress': float(percent)}
	return call_mdblist(url, json=data, method='post')

def mdbl_get_ratings(media_type='movies', offset=0, limit=1000, since=None):
	"""Get user ratings from MDBList. media_type can be 'movies' or 'shows'."""
	url = 'sync/ratings'
	params = {'offset': offset, 'limit': limit}
	if since: params['since'] = since
	return call_mdblist(url, params=params)

def mdbl_add_rating(media, media_id, rating, season=None, episode=None):
	"""Add a rating to MDBList. Rating should be 1-10."""
	url = 'sync/ratings'
	try: media_id = int(media_id)
	except Exception: pass
	if media in ('movie', 'movies'):
		data = {'movies': [{'ids': {'tmdb': media_id}, 'rating': int(rating)}]}
	elif media == 'episode':
		try: s, e = int(season), int(episode)
		except (ValueError, TypeError): return False
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [{'number': s, 'episodes': [{'number': e, 'rating': int(rating)}]}]}]}
	else:
		data = {'shows': [{'ids': {'tmdb': media_id}, 'rating': int(rating)}]}
	result = call_mdblist(url, json=data, method='post')
	return result.get('updated', {}).get('movies', 0) > 0 or result.get('updated', {}).get('episodes', 0) > 0 if result else False

def mdbl_remove_rating(media, media_id, season=None, episode=None):
	"""Remove a rating from MDBList."""
	url = 'sync/ratings/remove'
	try: media_id = int(media_id)
	except Exception: pass
	if media in ('movie', 'movies'):
		data = {'movies': [{'ids': {'tmdb': media_id}}]}
	elif media == 'episode':
		try: s, e = int(season), int(episode)
		except (ValueError, TypeError): return False
		data = {'shows': [{'ids': {'tmdb': media_id}, 'seasons': [{'number': s, 'episodes': [{'number': e}]}]}]}
	else:
		data = {'shows': [{'ids': {'tmdb': media_id}}]}
	result = call_mdblist(url, json=data, method='post')
	return result.get('removed', {}).get('movies', 0) > 0 or result.get('removed', {}).get('episodes', 0) > 0 if result else False

def mdbl_create_list(name, description='', privacy='private'):
	"""Create a new static list on MDBList. Returns list info or None on failure."""
	url = 'lists/user/add'
	data = {'name': name}
	if description: data['description'] = description
	if privacy: data['privacy'] = privacy
	return call_mdblist(url, json=data, method='post')

def mdbl_progress_movies(progress_info):
	def _process(item):
		movie = item.get('movie')
		if not movie: return
		tmdb_id = get_mdbl_movie_id(movie.get('ids', {}))
		if not tmdb_id: return
		season, episode = '', ''
		insert_append((
			'movie', str(tmdb_id), season, episode, str(round(float(item.get('progress', 0)), 1)),
			0, item.get('paused_at', ''), item.get('id', 0), movie.get('title', '')
		))
	insert_list = []
	insert_append = insert_list.append
	progress_items = [i for i in progress_info if i.get('type') == 'movie' and float(i.get('progress', 0)) > 1]
	if not progress_items: return
	threads = list(make_thread_list(_process, progress_items, Thread))
	for i in threads: i.join()
	with mdbl_cache.MDBLCache() as mc:
		mc.set_bulk_movie_progress(insert_list)

def mdbl_progress_tv(progress_info):
	def _process(item):
		show = item.get('show')
		ep = item.get('episode')
		if not show or not ep: return
		tmdb_id = get_mdbl_tvshow_id(show.get('ids', {}))
		if not tmdb_id: return
		season, episode = ep.get('season', 0), ep.get('number', 0)
		if season < 1: return
		insert_append((
			'episode', str(tmdb_id), season, episode, str(round(float(item.get('progress', 0)), 1)),
			0, item.get('paused_at', ''), item.get('id', 0), show.get('title', '')
		))
	insert_list = []
	insert_append = insert_list.append
	progress_items = [i for i in progress_info if i.get('type') == 'episode' and float(i.get('progress', 0)) > 1]
	if not progress_items: return
	threads = list(make_thread_list(_process, progress_items, Thread))
	for i in threads: i.join()
	with mdbl_cache.MDBLCache() as mc:
		mc.set_bulk_tvshow_progress(insert_list)

def get_mdbl_movie_id(item):
	if item.get('tmdb'): return item['tmdb']
	tmdb_id = None
	if item.get('imdb'):
		try:
			meta = movie_external_id('imdb_id', item['imdb'])
			tmdb_id = meta['id']
		except Exception: pass
	return tmdb_id

def get_mdbl_tvshow_id(item):
	if item.get('tmdb'): return item['tmdb']
	tmdb_id = None
	if item.get('imdb'):
		try:
			meta = tvshow_external_id('imdb_id', item['imdb'])
			tmdb_id = meta['id']
		except Exception: tmdb_id = None
	if not tmdb_id:
		if item.get('tvdb'):
			try:
				meta = tvshow_external_id('tvdb_id', item['tvdb'])
				tmdb_id = meta['id']
			except Exception: tmdb_id = None
	return tmdb_id

def mdbl_get_activity():
	url = 'sync/last_activities'
	return call_mdblist(url)

def mdbl_playback_progress():
	url = 'sync/playback'
	return call_mdblist(url)

def mdbl_watched_progress():
	url = 'sync/watched'
	params = {'limit': 5000}
	watched = {'movies': [], 'episodes': [], 'has_more': True}
	result = None
	while watched['has_more']:
		result = call_mdblist(url, params=params)
		if result is not None:
			watched['has_more'] = result.get('pagination', {}).get('has_more', False)
			pagination = result.get('pagination', {})
			params['offset'] = pagination.get('page', 0) * pagination.get('limit', 5000)
			if 'movies' in result: watched['movies'] += result['movies']
			if 'episodes' in result: watched['episodes'] += result['episodes']
		else: watched['has_more'] = False
	return watched if result is not None else result

def mdbl_sync_activities_thread(*args, **kwargs):
	Thread(target=mdbl_sync_activities, args=args, kwargs=kwargs).start()

def mdbl_sync_activities(force_update=False):
	def _get_timestamp(date_time):
		return int(time.mktime(date_time.timetuple()))
	def _compare(latest, cached, res_format='%Y-%m-%dT%H:%M:%SZ'):
		try: result = _get_timestamp(js2date(latest, res_format)) > _get_timestamp(js2date(cached, res_format))
		except Exception: result = True
		return result
	if not get_setting('mdblist_user', ''): return 'no account'
	if force_update:
		check_databases()
		mdbl_cache.clear_all_mdbl_cache_data(refresh=False)
	latest = mdbl_get_activity()
	if not latest:
		mdbl_cache.clear_all_mdbl_cache_data(refresh=False)
		return 'failed'
	success = 'not needed'
	progress_info = mdbl_playback_progress()
	if not progress_info: progress_info = []
	progress_info.sort(key=lambda k: k.get('paused_at', ''), reverse=True)
	latest['paused_at'] = progress_info[0]['paused_at'] if progress_info else None
	cached = mdbl_cache.reset_activity(latest)
	refresh_movies_watched = _compare(latest['watched_at'], cached['watched_at'])
	refresh_episodes_watched = _compare(latest['episode_watched_at'], cached['episode_watched_at'])
	if refresh_movies_watched or refresh_episodes_watched:
		success = 'success'
		watched_info = mdbl_watched_progress()
		if refresh_movies_watched: mdbl_indicators_movies(watched_info)
		if refresh_episodes_watched: mdbl_indicators_tv(watched_info)
	if latest.get('paused_at') and cached.get('paused_at') and _compare(latest['paused_at'], cached['paused_at']):
		success = 'success'
		mdbl_progress_movies(progress_info)
		mdbl_progress_tv(progress_info)
	if _compare(latest['watchlisted_at'], cached['watchlisted_at']):
		success = 'success'
		mdbl_cache.clear_mdbl_collection_watchlist_data('watchlist')
	if _compare(latest['collected_at'], cached['collected_at']):
		success = 'success'
		mdbl_cache.clear_mdbl_collection_watchlist_data('collection')
	return success

def clear_mdbl_cache(silent=False):
	from modules.kodi_utils import path_exists, clear_property, database_connect, maincache_db
	if not path_exists(maincache_db): return True
	dbcon = database_connect(maincache_db, isolation_level=None)
	try:
		dbcur = dbcon.cursor()
		dbcur.execute("""PRAGMA synchronous = OFF""")
		dbcur.execute("""PRAGMA journal_mode = OFF""")
		dbcur.execute("""SELECT id FROM maincache WHERE id LIKE ?""", ('mdbl_%',))
		mdb_results = [str(i[0]) for i in dbcur.fetchall()]
		if not mdb_results: return True
		dbcur.execute("""DELETE FROM maincache WHERE id LIKE ?""", ('mdbl_%',))
		for i in mdb_results: clear_property(i)
		return True
	except Exception: return False
	finally:
		dbcon.close()

def get_mdbl(params):
	results = []
	action = params['action']
	url = params['url']
	response = requests.get(url, timeout=timeout)
	if action == 'mdbl_parentsguide':
		def _process():
			for key, val in guide_map.items():
				try:
					if not (match := re.search(rf"{key}\:\ \d", html)): continue
					rank = rank_map[match.group().split(': ')[-1]]
					yield {'title': val, 'ranking': rank, 'listings': []}
				except Exception: pass
		html = response.text.replace('\n', ' ')
		results = list(_process())
	return results

