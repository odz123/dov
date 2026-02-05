import threading
from indexers import tmdb_api as tmdb, fanarttv_api as fanarttv
from caches.meta_cache import MetaCache
from modules.utils import jsondate_to_datetime, subtract_dates, TaskPool
from modules.kodi_utils import get_setting

movie_data, tvshow_data, tmdb_english_translation = tmdb.movie_details, tmdb.tvshow_details, tmdb.english_translation
movie_external_id, tvshow_external_id, season_episodes_details = tmdb.movie_external_id, tmdb.tvshow_external_id, tmdb.season_episodes_details
default_fanarttv_data, fanarttv_get, fanarttv_add = fanarttv.default_fanart_nometa, fanarttv.get, fanarttv.add
subtract_dates_function, jsondate_to_datetime_function = subtract_dates, jsondate_to_datetime
backup_resolutions, writer_credits = {'poster': 'w780', 'fanart': 'w1280', 'still': 'original', 'profile': 'h632'}, ('Author', 'Writer', 'Screenplay', 'Characters')
alt_titles_test, trailers_test, finished_show_check, empty_value_check = ('US', 'GB', 'UK', ''), ('Trailer', 'Teaser'), ('Ended', 'Canceled'), ('', 'None', None)
tmdb_image_base, youtube_url, date_format = tmdb.tmdb_image_base, 'plugin://plugin.video.youtube/play/?video_id=%s', '%Y-%m-%d'
EXPIRES_2_DAYS, EXPIRES_4_DAYS, EXPIRES_7_DAYS, EXPIRES_14_DAYS, EXPIRES_182_DAYS = 2, 4, 7, 14, 182
# Cache duration multipliers: Short=0, Standard=1, Long=2, Extended=3
_cache_duration_map = {
	0: {'base': EXPIRES_2_DAYS, 'mid': EXPIRES_4_DAYS, 'long': EXPIRES_7_DAYS, 'max': EXPIRES_14_DAYS},
	1: {'base': EXPIRES_4_DAYS, 'mid': EXPIRES_7_DAYS, 'long': EXPIRES_14_DAYS, 'max': EXPIRES_182_DAYS},
	2: {'base': EXPIRES_7_DAYS, 'mid': EXPIRES_14_DAYS, 'long': EXPIRES_14_DAYS, 'max': EXPIRES_182_DAYS},
	3: {'base': EXPIRES_14_DAYS, 'mid': EXPIRES_14_DAYS, 'long': EXPIRES_182_DAYS, 'max': EXPIRES_182_DAYS}
}

def _get_cache_expiry(user_info, level='mid'):
	duration = user_info.get('cache_duration', 1)
	return _cache_duration_map.get(duration, _cache_duration_map[1])[level]

# Thread-local MetaCache instances - each thread gets its own SQLite connection
# to avoid ProgrammingError from cross-thread cursor access
_metacache_local = threading.local()

def get_metacache():
	"""Get a thread-local MetaCache instance for thread-safe reuse."""
	if not hasattr(_metacache_local, 'instance') or _metacache_local.instance is None:
		_metacache_local.instance = MetaCache()
	return _metacache_local.instance

def movie_meta(id_type, media_id, user_info, current_date):
	if id_type == 'trakt_dict':
		if media_id.get('tmdb'): id_type, media_id = 'tmdb_id', media_id['tmdb']
		elif media_id.get('imdb'): id_type, media_id = 'imdb_id', media_id['imdb']
		else: id_type, media_id = None, None
	if media_id is None: return {}
	metacache = get_metacache()
	metacache_get, metacache_set = metacache.get, metacache.set
	fanarttv_data, language, extra_fanart_enabled, fanart_client_key = None, user_info['language'], user_info['extra_fanart_enabled'], user_info['fanart_client_key']
	meta = metacache_get('movie', id_type, media_id)
	if meta:
		if 'tmdb_id' in meta:
			if not meta.get('fanart_added', False) and extra_fanart_enabled:
				meta = fanarttv_add('movies', language, meta['tmdb_id'], fanart_client_key, meta)
				metacache_set('movie', id_type, meta, movie_expiry(current_date, meta, user_info))
			return meta
		else: fanarttv_data = dict(meta)
	try:
		tmdb_api = user_info['tmdb_api']
		if id_type == 'tmdb_id' or id_type == 'imdb_id': data = movie_data(media_id, language, tmdb_api)
		else:
			external_result = movie_external_id(id_type, media_id, tmdb_api)
			if not external_result: data = None
			else: data = movie_data(external_result['id'], language, tmdb_api)
		if not data or data.get('success', True) is False:
			if id_type == 'tmdb_id': meta = {'tmdb_id': media_id, 'imdb_id': 'tt0000000', 'tvdb_id': '0000000', 'fanart_added': True, 'blank_entry': True}
			else: meta = {'tmdb_id': '0000000', 'imdb_id': media_id, 'tvdb_id': '0000000', 'fanart_added': True, 'blank_entry': True}
			metacache_set('movie', id_type, meta, EXPIRES_2_DAYS)
			return meta
		if language != 'en':
			if data['overview'] in empty_value_check:
				media_id, id_type = data['id'], 'tmdb_id'
				eng_data = movie_data(media_id, 'en', tmdb_api)
				eng_overview = eng_data['overview']
				data['overview'] = eng_overview
				if 'videos' in data:
					all_trailers = data['videos']['results']
					if all_trailers:
						try: trailer_test = [i for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test]
						except Exception: trailer_test = False
					else: trailer_test = False
				else: trailer_test = False
				if not trailer_test:
					if 'videos' in eng_data:
						eng_all_trailers = eng_data['videos']['results']
						if eng_all_trailers:
							data['videos']['results'] = eng_all_trailers
		if not fanarttv_data and extra_fanart_enabled: fanarttv_data = fanarttv_get('movies', language, data['id'], fanart_client_key)
		meta = build_movie_meta(data, user_info, fanarttv_data)
		metacache_set('movie', id_type, meta, movie_expiry(current_date, meta))
	except Exception:
		if not meta or 'tmdb_id' not in meta: meta = {}
	return meta

def tvshow_meta(id_type, media_id, user_info, current_date):
	if id_type == 'trakt_dict':
		if media_id.get('tmdb'): id_type, media_id = 'tmdb_id', media_id['tmdb']
		elif media_id.get('imdb'): id_type, media_id = 'imdb_id', media_id['imdb']
		elif media_id.get('tvdb'): id_type, media_id = 'tvdb_id', media_id['tvdb']
		else: id_type, media_id = None, None
	if media_id is None: return {}
	metacache = get_metacache()
	metacache_get, metacache_set = metacache.get, metacache.set
	fanarttv_data, language, extra_fanart_enabled, fanart_client_key = None, user_info['language'], user_info['extra_fanart_enabled'], user_info['fanart_client_key']
	meta = metacache_get('tvshow', id_type, media_id)
	if meta:
		if 'tmdb_id' in meta:
			if not meta.get('fanart_added', False) and extra_fanart_enabled:
				meta = fanarttv_add('tv', language, meta['tvdb_id'], fanart_client_key, meta)
				metacache_set('tvshow', id_type, meta, tvshow_expiry(current_date, meta, user_info))
			return meta
		else: fanarttv_data = dict(meta)
	try:
		tmdb_api = user_info['tmdb_api']
		if id_type == 'tmdb_id':
			data = tvshow_data(media_id, language, tmdb_api)
		else:
			external_result = tvshow_external_id(id_type, media_id, tmdb_api)
			if not external_result: data = None
			else: data = tvshow_data(external_result['id'], language, tmdb_api)
		if not data or data.get('success', True) is False:
			if id_type == 'tmdb_id': meta = {'tmdb_id': media_id, 'imdb_id': 'tt0000000', 'tvdb_id': '0000000', 'fanart_added': True, 'blank_entry': True}
			elif id_type == 'imdb_id': meta = {'tmdb_id': '0000000', 'imdb_id': media_id, 'tvdb_id': '0000000', 'fanart_added': True, 'blank_entry': True}
			else: meta = {'tmdb_id': '0000000', 'imdb_id': 'tt0000000', 'tvdb_id': media_id, 'fanart_added': True, 'blank_entry': True}
			metacache_set('tvshow', id_type, meta, EXPIRES_2_DAYS)
			return meta
		if language != 'en':
			if data['overview'] in empty_value_check:
				media_id, id_type = data['id'], 'tmdb_id'
				eng_data = tvshow_data(media_id, 'en', tmdb_api)
				eng_overview = eng_data['overview']
				data['overview'] = eng_overview
				if 'videos' in data:
					all_trailers = data['videos']['results']
					if all_trailers:
						try: trailer_test = [i for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test]
						except Exception: trailer_test = False
					else: trailer_test = False
				else: trailer_test = False
				if not trailer_test:
					if 'videos' in eng_data:
						eng_all_trailers = eng_data['videos']['results']
						if eng_all_trailers:
							data['videos']['results'] = eng_all_trailers
		if not fanarttv_data and extra_fanart_enabled: fanarttv_data = fanarttv_get('tv', language, data['external_ids']['tvdb_id'], fanart_client_key)
		meta = build_tvshow_meta(data, user_info, fanarttv_data)
		metacache_set('tvshow', id_type, meta, tvshow_expiry(current_date, meta))
	except Exception:
		if not meta or 'tmdb_id' not in meta: meta = {}
	return meta

def season_episodes_meta(season, meta, user_info):
	def _process():
		for ep_data in data:
			writer, director, guest_stars = '', '', []
			ep_data_get = ep_data.get
			title, plot, premiered = ep_data_get('name'), ep_data_get('overview'), ep_data_get('air_date')
			season, episode, ep_type = ep_data_get('season_number'), ep_data_get('episode_number'), ep_data_get('episode_type')
			rating, votes, still_path = ep_data_get('vote_average'), ep_data_get('vote_count'), ep_data_get('still_path')
			ep_type = ep_details.get(ep_type) or ep_details.get(episode) or ep_type or ''
			if ep_type == 'mid_season_finale': ep_details[episode + 1] = 'mid_season_premiere'
			if still_path: thumb = tmdb_image_base % (still_resolution, still_path)
			else: thumb = None
			try: duration = ep_data_get('runtime') * 60
			except (TypeError, ValueError): duration = 60 * 60
			guest_stars_list = ep_data_get('guest_stars')
			if guest_stars_list:
				try: guest_stars = [
					{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (profile_resolution, i['profile_path']) if i['profile_path'] else ''}
					for i in guest_stars_list
				]
				except (KeyError, TypeError): pass
			crew = ep_data_get('crew')
			if crew:
				try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
				except (KeyError, TypeError): pass
				try: director = next((i['name'] for i in crew if i['job'] == 'Director'), '')
				except (KeyError, TypeError, StopIteration): pass
			yield {
				'thumb': thumb, 'title': title, 'guest_stars': guest_stars, 'plot': plot, 'premiered': premiered,
				'director': director, 'writer': writer, 'rating': rating, 'votes': votes, 'mediatype': 'episode',
				'episode_type': ep_type, 'season': season, 'episode': episode, 'duration': duration
			}
	metacache = get_metacache()
	metacache_get, metacache_set = metacache.get, metacache.set
	media_id, data = meta['tmdb_id'], None
	string = '%s_%s' % (media_id, season)
	data = metacache_get('season', 'tmdb_id', string)
	if data: return data
	try:
		show_ended, total_seasons = meta['status'] in finished_show_check, meta['total_seasons']
		expiration = EXPIRES_182_DAYS if show_ended or total_seasons > int(season) else EXPIRES_4_DAYS
		premiere = 'series_premiere' if int(season) == 1 else 'season_premiere'
		finale = 'series_finale' if show_ended and int(season) == total_seasons else 'season_finale'
		ep_details = {1: premiere, 'mid_season': 'mid_season_finale', 'finale': finale}
		image_resolution = user_info.get('image_resolution', backup_resolutions)
		still_resolution, profile_resolution = image_resolution['still'], image_resolution['profile']
		data = season_episodes_details(media_id, season, user_info['language'], user_info['tmdb_api'])['episodes']
		data = list(_process())
		metacache_set('season', 'tmdb_id', data, expiration, string)
	except Exception: pass
	return data

def all_episodes_meta(meta, user_info, Thread):
	def _get_tmdb_episodes(season):
		try: data.extend(season_episodes_meta(season, meta, user_info))
		except Exception: pass
	try:
		data = []
		seasons = [(i['season_number'],) for i in meta['season_data']] # TaskPool requires tuple
		for i in TaskPool().tasks(_get_tmdb_episodes, seasons, Thread): i.join()
	except Exception: pass
	return data

def english_translation(media_type, media_id, user_info):
	key = 'title' if media_type == 'movie' else 'name'
	translations = tmdb_english_translation(media_type, media_id, user_info['tmdb_api'])
	try: english = next((i['data'][key] for i in translations if i['iso_639_1'] == 'en'), '')
	except Exception: english = ''
	return english

def movie_expiry(current_date, meta, user_info=None):
	cache_mid = _get_cache_expiry(user_info, 'mid') if user_info else EXPIRES_7_DAYS
	try:
		cache_long = _get_cache_expiry(user_info, 'long') if user_info else EXPIRES_14_DAYS
		cache_max = _get_cache_expiry(user_info, 'max') if user_info else EXPIRES_182_DAYS
		difference = subtract_dates_function(current_date, jsondate_to_datetime_function(meta['premiered'], date_format, remove_time=True))
		if difference < 0: expiration = abs(difference) + 1
		elif difference <= 14: expiration = cache_mid
		elif difference <= 30: expiration = cache_long
		else: expiration = cache_max
	except Exception: return cache_mid
	return max(expiration, cache_mid)

def tvshow_expiry(current_date, meta, user_info=None):
	try:
		cache_max = _get_cache_expiry(user_info, 'max') if user_info else EXPIRES_182_DAYS
		cache_mid = _get_cache_expiry(user_info, 'mid') if user_info else EXPIRES_7_DAYS
		cache_base = _get_cache_expiry(user_info, 'base') if user_info else EXPIRES_4_DAYS
		if meta['status'] in finished_show_check: return cache_max
		next_episode_to_air = meta['extra_info'].get('next_episode_to_air')
		if not next_episode_to_air: return cache_mid
		expiration = subtract_dates_function(jsondate_to_datetime_function(next_episode_to_air['air_date'], date_format, remove_time=True), current_date)
	except Exception: return cache_base
	return max(expiration, cache_base)

def build_movie_meta(data, user_info, fanarttv_data=None):
	image_resolution = user_info.get('image_resolution', backup_resolutions)
	mpaa_country = user_info.get('mpaa_country', 'US')
	show_tmdblogo = user_info.get('show_tmdblogo', True)
	data_get = data.get
	cast, all_trailers, country, country_codes = [], [], [], []
	writer, mpaa, director, trailer, studio = '', '', '', '', ''
	tmdb_id, imdb_id = data_get('id', ''), data_get('imdb_id', '')
	rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
	plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('release_date', '')
	poster_path, backdrop_path = data_get('poster_path'), data_get('backdrop_path')
	if poster_path: poster = tmdb_image_base % (image_resolution['poster'], poster_path)
	else: poster = ''
	if backdrop_path: fanart = tmdb_image_base % (image_resolution['fanart'], backdrop_path)
	else: fanart = ''
	tmdblogo = ''
	if show_tmdblogo:
		try: tmdblogo_path = next((i['file_path'] for i in data_get('images')['logos'] if 'file_path' in i and i['file_path'].endswith('png')), None)
		except Exception: tmdblogo_path = None
		if tmdblogo_path: tmdblogo = tmdb_image_base % (image_resolution['fanart'], tmdblogo_path)
	title, original_title = data_get('title'), data_get('original_title')
	try: english_title = next((i['data']['title'] for i in data_get('translations')['translations'] if i['iso_639_1'] == 'en'), None)
	except Exception: english_title = None
	try: year = str(data_get('release_date').split('-')[0] or 0)
	except Exception: year = ''
	try: duration = int(data_get('runtime', '90') * 60)
	except Exception: duration = 0
	try: genre = ', '.join([i['name'] for i in data_get('genres')])
	except Exception: genre = []
	rootname = '%s (%s)' % (title, year)
	companies = data_get('production_companies')
	if companies:
		if len(companies) == 1: studio = companies[0]['name']
		else:
			try: studio = next((i['name'] for i in companies if i['logo_path'] not in empty_value_check), None) or companies[0]['name']
			except Exception: pass
	production_countries = data_get('production_countries')
	if production_countries:
		country = [i['name'] for i in production_countries]
		country_codes = [i['iso_3166_1'] for i in production_countries]
	release_dates = data_get('release_dates')
	if release_dates:
		try: mpaa = next(
			(x['certification']
			for i in release_dates['results']
			for x in i['release_dates']
			if i['iso_3166_1'] == mpaa_country and x['certification']),
			''
		)
		except Exception: pass
	credits = data_get('credits')
	if credits:
		all_cast = credits.get('cast')
		if all_cast:
			try: cast = [
				{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (image_resolution['profile'], i['profile_path']) if i['profile_path'] else ''}
				for i in all_cast
			]
			except Exception: pass
		crew = credits.get('crew')
		if crew:
			try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
			except Exception: pass
			try: director = next((i['name'] for i in crew if i['job'] == 'Director'), '')
			except Exception: pass
	alternative_titles = data_get('alternative_titles')
	if alternative_titles:
		alternatives = alternative_titles['titles']
		alternative_titles = [i['title'] for i in alternatives if i['iso_3166_1'] in alt_titles_test]
	videos = data_get('videos')
	if videos:
		all_trailers = videos['results']
		try: trailer = next((youtube_url % i['key'] for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test), '')
		except Exception: pass
	status, homepage = data_get('status', 'N/A'), data_get('homepage', 'N/A')
	belongs_to_collection = data_get('belongs_to_collection')
	if belongs_to_collection: ei_collection_name, ei_collection_id = belongs_to_collection['name'], belongs_to_collection['id']
	else: ei_collection_name, ei_collection_id = None, None
	try: ei_budget = '${:,}'.format(data_get('budget'))
	except Exception: ei_budget = '$0'
	try: ei_revenue = '${:,}'.format(data_get('revenue'))
	except Exception: ei_revenue = '$0'
	extra_info = {'status': status, 'collection_name': ei_collection_name, 'collection_id': ei_collection_id, 'budget': ei_budget, 'revenue': ei_revenue, 'homepage': homepage}
	meta_dict = {
		'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': 'None', 'imdbnumber': imdb_id, 'tmdblogo': tmdblogo,
		'poster': poster, 'fanart': fanart, 'year': year, 'title': title, 'rootname': rootname,
		'original_title': original_title, 'english_title': english_title, 'alternative_titles': alternative_titles,
		'tagline': tagline, 'plot': plot, 'mpaa': mpaa, 'studio': studio, 'director': director, 'writer': writer,
		'duration': duration, 'premiered': premiered, 'genre': genre, 'rating': rating, 'votes': votes,
		'country': country, 'country_codes': country_codes, 'trailer': trailer, 'all_trailers': all_trailers,
		'cast': cast, 'extra_info': extra_info, 'mediatype': 'movie', 'meta_language': user_info.get('language', '')
	}
	if fanarttv_data: meta_dict.update(fanarttv_data)
	else: meta_dict.update(default_fanarttv_data)
	return meta_dict

def build_tvshow_meta(data, user_info, fanarttv_data=None):
	image_resolution = user_info.get('image_resolution', backup_resolutions)
	mpaa_country = user_info.get('mpaa_country', 'US')
	show_tmdblogo = user_info.get('show_tmdblogo', True)
	data_get = data.get
	cast, all_trailers, country, country_codes = [], [], [], []
	writer, mpaa, director, trailer, studio = '', '', '', '', ''
	external_ids = data_get('external_ids')
	tmdb_id, imdb_id, tvdb_id = data_get('id', ''), external_ids.get('imdb_id', ''), external_ids.get('tvdb_id', 'None')
	rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
	plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('first_air_date', '')
	season_data, total_seasons, total_aired_eps = data_get('seasons'), data_get('number_of_seasons'), data_get('number_of_episodes')
	poster_path, backdrop_path = data_get('poster_path'), data_get('backdrop_path')
	if poster_path: poster = tmdb_image_base % (image_resolution['poster'], poster_path)
	else: poster = ''
	if backdrop_path: fanart = tmdb_image_base % (image_resolution['fanart'], backdrop_path)
	else: fanart = ''
	tmdblogo = ''
	if show_tmdblogo:
		try: tmdblogo_path = next((i['file_path'] for i in data_get('images')['logos'] if 'file_path' in i and i['file_path'].endswith('png')), None)
		except Exception: tmdblogo_path = None
		if tmdblogo_path: tmdblogo = tmdb_image_base % (image_resolution['fanart'], tmdblogo_path)
	title, original_title = data_get('name'), data_get('original_name')
	try: english_title = next((i['data']['name'] for i in data_get('translations')['translations'] if i['iso_639_1'] == 'en'), None)
	except Exception: english_title = None
	try: year = str(data_get('first_air_date').split('-')[0] or 0)
	except Exception: year = ''
	try: duration = min(data_get('episode_run_time')) * 60
	except Exception: duration = 0
	try: genre = ', '.join([i['name'] for i in data_get('genres')])
	except Exception: genre = []
	rootname = '%s (%s)' % (title, year)
	networks = data_get('networks')
	if networks:
		if len(networks) == 1: studio = networks[0]['name']
		else:
			try: studio = next((i['name'] for i in networks if i['logo_path'] not in empty_value_check), None) or networks[0]['name']
			except Exception: pass
	production_countries = data_get('production_countries')
	if production_countries:
		country = [i['name'] for i in production_countries]
		country_codes = [i['iso_3166_1'] for i in production_countries]
	content_ratings = data_get('content_ratings')
	release_dates = data_get('release_dates')
	if content_ratings:
		try: mpaa = next((i['rating'] for i in content_ratings['results'] if i['iso_3166_1'] == mpaa_country), '')
		except Exception: pass
	elif release_dates:
		try: mpaa = next((i['release_dates'][0]['certification'] for i in release_dates['results'] if i['iso_3166_1'] == mpaa_country), '')
		except Exception: pass
	credits = data_get('credits')
	if credits:
		all_cast = credits.get('cast')
		if all_cast:
			try: cast = [
				{'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (image_resolution['profile'], i['profile_path']) if i['profile_path'] else ''}
				for i in all_cast
			]
			except Exception: pass
		crew = credits.get('crew')
		if crew:
			try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
			except Exception: pass
			try: director = next((i['name'] for i in crew if i['job'] == 'Director'), '')
			except Exception: pass
	alternative_titles = data_get('alternative_titles')
	if alternative_titles:
		alternatives = alternative_titles['results']
		alternative_titles = [i['title'] for i in alternatives if i['iso_3166_1'] in alt_titles_test]
	videos = data_get('videos')
	if videos:
		all_trailers = videos['results']
		try: trailer = next((youtube_url % i['key'] for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test), '')
		except Exception: pass
	status, _type, homepage = data_get('status', 'N/A'), data_get('type', 'N/A'), data_get('homepage', 'N/A')
	created_by = data_get('created_by')
	if created_by:
		try: ei_created_by = ', '.join([i['name'] for i in created_by])
		except Exception: ei_created_by = 'N/A'
	else: ei_created_by = 'N/A'
	ei_next_ep = data_get('next_episode_to_air')
	ei_last_ep = data_get('last_episode_to_air')
	if ei_last_ep and not status in finished_show_check: total_aired_eps = ei_last_ep['episode_number'] + sum([
			i['episode_count'] for i in season_data if 0 < i['season_number'] < ei_last_ep['season_number']
		])
	extra_info = {'status': status, 'type': _type, 'homepage': homepage, 'created_by': ei_created_by, 'next_episode_to_air': ei_next_ep, 'last_episode_to_air': ei_last_ep}
	meta_dict = {
		'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id, 'imdbnumber': imdb_id, 'tmdblogo': tmdblogo,
		'poster': poster, 'fanart': fanart, 'year': year, 'title': title, 'rootname': rootname, 'tvshowtitle': title,
		'original_title': original_title, 'english_title': english_title, 'alternative_titles': alternative_titles,
		'tagline': tagline, 'plot': plot, 'mpaa': mpaa, 'studio': studio, 'director': director, 'writer': writer,
		'duration': duration, 'premiered': premiered, 'genre': genre, 'rating': rating, 'votes': votes,
		'country': country, 'country_codes': country_codes, 'trailer': trailer, 'all_trailers': all_trailers,
		'cast': cast, 'extra_info': extra_info, 'mediatype': 'tvshow', 'meta_language': user_info.get('language', ''),
		'status': status, 'total_aired_eps': total_aired_eps, 'total_seasons': total_seasons, 'season_data': season_data
	}
	if fanarttv_data: meta_dict.update(fanarttv_data)
	else: meta_dict.update(default_fanarttv_data)
	return meta_dict

def get_title(meta, language=None):
	if 'custom_title' in meta: return meta['custom_title']
	if not language: language = meta.get('meta_language', '')
	if language == 'en': title = meta['title']
	else: title = meta.get('english_title')
	if not title:
		try:
			from settings import metadata_user_info
			meta_user_info = metadata_user_info()
			media_type = 'movie' if meta['media_type'] == 'movie' else 'tv'
			english_title = tmdb_english_translation(media_type, meta['tmdb_id'], meta_user_info)
			if english_title: title = english_title
			else: title = meta['original_title']
		except Exception: pass
	if not title: title = meta['original_title']
	if '(' in title: title = title.split('(')[0]
	if '/' in title: title = title.replace('/', ' ')
	return title

def rpdb_get(media_type, media_id, api_key):
	if api_key and media_id:
		if media_id.startswith('tt'): id_type = 'imdb'
		else: id_type, media_id = 'tmdb', '%s-%s' % (media_type, media_id)
		url = 'https://api.ratingposterdb.com/%s/%s/poster-default/%s.jpg'
		rpdb_data = {'rpdb': url % (api_key, id_type, media_id), 'rpdb_added': True}
	else: rpdb_data = {'rpdb': '', 'rpdb_added': False}
	return rpdb_data

# Stremio Metadata Integration Functions

def stremio_meta_enabled():
	"""Check if Stremio metadata is enabled in settings"""
	return get_setting('stremio.meta.enabled', 'false') == 'true'

def stremio_meta_mode():
	"""Get Stremio metadata mode: 0=Fallback, 1=Supplement, 2=Primary"""
	return int(get_setting('stremio.meta.mode', '0'))

def _resolve_imdb_id(media_type, id_type, media_id, user_info):
	"""Resolve IMDb ID without full TMDB metadata fetch.
	Checks input params, metacache, then lightweight TMDB lookup."""
	if id_type == 'imdb_id':
		return media_id
	if id_type == 'trakt_dict':
		try:
			imdb = media_id.get('imdb')
			if imdb: return imdb
			# No imdb in trakt dict, try tmdb path for lightweight lookup
			tmdb = media_id.get('tmdb')
			if tmdb:
				id_type, media_id = 'tmdb_id', tmdb
			else:
				return None
		except Exception: return None
	# Check metacache for existing entry with imdb_id
	metacache = get_metacache()
	meta = metacache.get(media_type, id_type, str(media_id))
	if meta:
		imdb_id = meta.get('imdb_id')
		if imdb_id and imdb_id != 'tt0000000':
			return imdb_id
	# Lightweight TMDB lookup for just the imdb_id
	try:
		tmdb_api = user_info.get('tmdb_api')
		if media_type == 'movie':
			from indexers.tmdb_api import movie_imdb_id
			return movie_imdb_id(media_id, tmdb_api)
		else:
			from indexers.tmdb_api import tvshow_imdb_id
			return tvshow_imdb_id(media_id, tmdb_api)
	except Exception: pass
	return None

def get_stremio_movie_meta(imdb_id):
	"""Fetch movie metadata from Stremio addons"""
	try:
		from indexers.stremio_meta import StremioMetaProvider
		provider = StremioMetaProvider()
		try:
			return provider.get_movie_meta(imdb_id)
		finally:
			provider.cache.close()
	except Exception:
		pass
	return None

def get_stremio_tvshow_meta(imdb_id):
	"""Fetch TV show metadata from Stremio addons"""
	try:
		from indexers.stremio_meta import StremioMetaProvider
		provider = StremioMetaProvider()
		try:
			return provider.get_tvshow_meta(imdb_id)
		finally:
			provider.cache.close()
	except Exception:
		pass
	return None

def get_stremio_season_meta(imdb_id, season):
	"""Fetch season episodes metadata from Stremio addons"""
	try:
		from indexers.stremio_meta import StremioMetaProvider
		provider = StremioMetaProvider()
		try:
			return provider.get_season_episodes(imdb_id, int(season))
		finally:
			provider.cache.close()
	except Exception:
		pass
	return None

def merge_stremio_meta(tmdb_meta, stremio_meta, prefer_stremio=False):
	"""
	Merge Stremio metadata with TMDB metadata.

	Args:
		tmdb_meta: TMDB metadata dict
		stremio_meta: Stremio metadata dict
		prefer_stremio: If True, prefer Stremio data over TMDB

	Returns:
		dict: Merged metadata
	"""
	if not tmdb_meta:
		return stremio_meta
	if not stremio_meta:
		return tmdb_meta

	merged = dict(tmdb_meta)

	# Fields to potentially fill from Stremio if missing in TMDB
	fill_fields = [
		'poster', 'fanart', 'clearlogo', 'tmdblogo', 'plot', 'tagline',
		'trailer', 'director', 'writer', 'cast', 'genre', 'rating'
	]

	for field in fill_fields:
		tmdb_value = tmdb_meta.get(field)
		stremio_value = stremio_meta.get(field)

		if prefer_stremio and stremio_value:
			merged[field] = stremio_value
		elif not tmdb_value and stremio_value:
			merged[field] = stremio_value

	# Special handling for artwork
	if not merged.get('poster') and stremio_meta.get('poster'):
		merged['poster'] = stremio_meta['poster']
	if not merged.get('fanart') and stremio_meta.get('fanart'):
		merged['fanart'] = stremio_meta['fanart']
	if not merged.get('clearlogo') and stremio_meta.get('clearlogo'):
		merged['clearlogo'] = stremio_meta['clearlogo']
		merged['tmdblogo'] = stremio_meta['clearlogo']

	merged['stremio_supplemented'] = True
	return merged

def _apply_tmdb_ids(stremio_data, tmdb_meta):
	"""Preserve essential TMDB/TVDB IDs in Stremio metadata for proper addon integration."""
	if not tmdb_meta:
		return
	if not stremio_data.get('tmdb_id'):
		stremio_data['tmdb_id'] = tmdb_meta.get('tmdb_id', '')
	if not stremio_data.get('tvdb_id') or stremio_data.get('tvdb_id') == 'None':
		stremio_data['tvdb_id'] = tmdb_meta.get('tvdb_id', 'None')

def _ensure_tmdb_id(stremio_data, id_type, media_id):
	"""Ensure Stremio metadata has tmdb_id from the original input when available."""
	if stremio_data.get('tmdb_id'):
		return
	if id_type == 'tmdb_id':
		stremio_data['tmdb_id'] = str(media_id)
	elif id_type == 'trakt_dict':
		try:
			tmdb = media_id.get('tmdb')
			if tmdb: stremio_data['tmdb_id'] = str(tmdb)
		except Exception: pass

def movie_meta_with_stremio(id_type, media_id, user_info, current_date):
	"""
	Get movie metadata with Stremio integration.
	Uses configured mode: fallback, supplement, or primary.
	"""
	if not stremio_meta_enabled():
		return movie_meta(id_type, media_id, user_info, current_date)

	mode = stremio_meta_mode()

	# Mode 2: Primary - Try Stremio first, skip full TMDB unless needed
	if mode == 2:
		orig_id_type, orig_media_id = id_type, media_id
		imdb_id = _resolve_imdb_id('movie', id_type, media_id, user_info)
		if imdb_id:
			stremio_data = get_stremio_movie_meta(imdb_id)
			if stremio_data and not stremio_data.get('blank_entry'):
				_ensure_tmdb_id(stremio_data, orig_id_type, orig_media_id)
				return stremio_data
		# Stremio failed or no imdb_id - fall back to TMDB
		return movie_meta(id_type, media_id, user_info, current_date)

	# Modes 0 and 1 need TMDB metadata
	imdb_id = None
	if id_type == 'imdb_id':
		imdb_id = media_id
	elif id_type == 'trakt_dict':
		try: imdb_id = media_id.get('imdb')
		except Exception: pass

	tmdb_meta = movie_meta(id_type, media_id, user_info, current_date)

	if not imdb_id and tmdb_meta:
		imdb_id = tmdb_meta.get('imdb_id')

	if not imdb_id:
		return tmdb_meta

	# Mode 0: Fallback - Use Stremio only if TMDB failed
	if mode == 0:
		if not tmdb_meta or tmdb_meta.get('blank_entry'):
			stremio_data = get_stremio_movie_meta(imdb_id)
			if stremio_data:
				_apply_tmdb_ids(stremio_data, tmdb_meta)
				return stremio_data

	# Mode 1: Supplement - Merge Stremio data with TMDB
	elif mode == 1:
		stremio_data = get_stremio_movie_meta(imdb_id)
		if stremio_data:
			return merge_stremio_meta(tmdb_meta, stremio_data)

	return tmdb_meta

def tvshow_meta_with_stremio(id_type, media_id, user_info, current_date):
	"""
	Get TV show metadata with Stremio integration.
	Uses configured mode: fallback, supplement, or primary.
	"""
	if not stremio_meta_enabled():
		return tvshow_meta(id_type, media_id, user_info, current_date)

	mode = stremio_meta_mode()

	# Mode 2: Primary - Try Stremio first, skip full TMDB unless needed
	if mode == 2:
		orig_id_type, orig_media_id = id_type, media_id
		imdb_id = _resolve_imdb_id('tvshow', id_type, media_id, user_info)
		if imdb_id:
			stremio_data = get_stremio_tvshow_meta(imdb_id)
			if stremio_data and not stremio_data.get('blank_entry'):
				_ensure_tmdb_id(stremio_data, orig_id_type, orig_media_id)
				return stremio_data
		# Stremio failed or no imdb_id - fall back to TMDB
		return tvshow_meta(id_type, media_id, user_info, current_date)

	# Modes 0 and 1 need TMDB metadata
	imdb_id = None
	if id_type == 'imdb_id':
		imdb_id = media_id
	elif id_type == 'trakt_dict':
		try: imdb_id = media_id.get('imdb')
		except Exception: pass

	tmdb_meta = tvshow_meta(id_type, media_id, user_info, current_date)

	if not imdb_id and tmdb_meta:
		imdb_id = tmdb_meta.get('imdb_id')

	if not imdb_id:
		return tmdb_meta

	# Mode 0: Fallback - Use Stremio only if TMDB failed
	if mode == 0:
		if not tmdb_meta or tmdb_meta.get('blank_entry'):
			stremio_data = get_stremio_tvshow_meta(imdb_id)
			if stremio_data:
				_apply_tmdb_ids(stremio_data, tmdb_meta)
				return stremio_data

	# Mode 1: Supplement - Merge Stremio data with TMDB
	elif mode == 1:
		stremio_data = get_stremio_tvshow_meta(imdb_id)
		if stremio_data:
			return merge_stremio_meta(tmdb_meta, stremio_data)

	return tmdb_meta

def season_episodes_meta_with_stremio(season, meta, user_info):
	"""
	Get season episodes metadata with Stremio integration.
	Uses configured mode: fallback, supplement, or primary.
	"""
	if not stremio_meta_enabled():
		return season_episodes_meta(season, meta, user_info)

	mode = stremio_meta_mode()
	imdb_id = meta.get('imdb_id')

	# Mode 2: Primary - Try Stremio first
	if mode == 2 and imdb_id:
		stremio_data = get_stremio_season_meta(imdb_id, season)
		if stremio_data:
			return stremio_data

	# Get TMDB data
	tmdb_data = season_episodes_meta(season, meta, user_info)

	# Mode 0: Fallback - Use Stremio only if TMDB failed
	if mode == 0:
		if not tmdb_data and imdb_id:
			stremio_data = get_stremio_season_meta(imdb_id, season)
			if stremio_data:
				return stremio_data

	# Mode 2: Primary - already tried above, return TMDB as fallback
	return tmdb_data

def all_episodes_meta_with_stremio(meta, user_info, Thread):
	"""Get all episodes metadata with Stremio fallback."""
	if not stremio_meta_enabled():
		return all_episodes_meta(meta, user_info, Thread)
	def _get_episodes(season):
		try: data.extend(season_episodes_meta_with_stremio(season, meta, user_info))
		except Exception: pass
	try:
		data = []
		seasons = [(i['season_number'],) for i in meta['season_data']]
		for i in TaskPool().tasks(_get_episodes, seasons, Thread): i.join()
	except Exception: pass
	return data

