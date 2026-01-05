import requests
from datetime import timedelta
from caches.main_cache import MainCache
from modules.kodi_utils import notification, sleep
# from modules.kodi_utils import logger

search_url = 'https://sub.wyzie.ru/search'
timeout = 5.05

def _get(url, params=None, stream=False, retry=False):
	response = requests.get(url, params=params, stream=stream, timeout=timeout)
	if retry and response.status_code in (403, 429):
		notification(32740)
		sleep(10000)
		return _get(url, params=params, stream=stream)
	return response

def download(url):
	response = _get(url, stream=True, retry=True)
	return response if response.ok else response.reason

def search(imdb_id, season=None, episode=None):
	cache_name = 'subtitles_wyzie_%s' % imdb_id
	params = {'id': imdb_id, 'format': 'srt'}
	if season:
		cache_name += '_%s_%s' % (season, episode)
		params.update({'season': season, 'episode': episode})
	maincache = MainCache()
	cache = maincache.get(cache_name)
	if cache: return cache
	response = _get(search_url, params=params, retry=True)
	if not response.ok: return []
	response = response.json()
	if response: maincache.set(cache_name, response, expiration=timedelta(hours=24))
	return response

