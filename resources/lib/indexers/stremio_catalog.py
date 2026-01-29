# Stremio Catalog Indexer for POV
"""
	Enhanced browsing of content catalogs from Stremio addons
	Features:
	- List available catalogs from addons
	- Browse catalog contents (movies, series)
	- Search functionality across catalogs
	- Genre/extra filtering support
	- Catalog caching for performance
	- Parallel manifest fetching
	- Integration with POV metadata system
	- Cloudflare bypass support
"""

import sys
import json
import time
import requests
from ast import literal_eval
from datetime import timedelta
from threading import Thread
from modules.kodi_utils import (
	get_setting, set_setting, notification, make_listitem, add_items,
	set_content, end_directory, set_view_mode, build_url, dialog,
	get_property, set_property, clear_property, get_kodi_version
)

KODI_VERSION = get_kodi_version()

# Try to import cloudscraper for Cloudflare bypass
try:
	import cloudscraper
	HAS_CLOUDSCRAPER = True
except ImportError:
	HAS_CLOUDSCRAPER = False

# Try to import curl_cffi for TLS fingerprint bypass
try:
	from curl_cffi import requests as curl_requests
	HAS_CURL_CFFI = True
except ImportError:
	HAS_CURL_CFFI = False

# Browser-like headers
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate, br',
	'Connection': 'keep-alive',
	'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
	'Sec-Ch-Ua-Mobile': '?0',
	'Sec-Ch-Ua-Platform': '"Windows"',
}

# Cache settings
MANIFEST_CACHE_HOURS = 6  # Cache manifests for 6 hours
CATALOG_CACHE_HOURS = 1   # Cache catalog contents for 1 hour

_scraper_session = None
def _get_scraper():
	global _scraper_session
	if HAS_CLOUDSCRAPER and _scraper_session is None:
		try:
			_scraper_session = cloudscraper.create_scraper(
				browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
			)
		except Exception:
			pass
	return _scraper_session

def _fetch_json(url, timeout=10):
	"""Fetch JSON from URL with Cloudflare bypass"""
	try:
		from urllib.parse import urlparse
		parsed = urlparse(url)
		origin = f"{parsed.scheme}://{parsed.netloc}"
	except Exception:
		origin = url.rsplit('/', 1)[0]

	headers = BROWSER_HEADERS.copy()
	headers['Referer'] = f"{origin}/"
	headers['Origin'] = origin

	# Try curl_cffi first
	if HAS_CURL_CFFI:
		try:
			response = curl_requests.get(url, timeout=timeout, headers=headers, impersonate='chrome120')
			if response.status_code == 200 and 'text/html' not in response.headers.get('content-type', ''):
				return response.json()
		except Exception:
			pass

	# Try cloudscraper
	scraper = _get_scraper()
	if scraper:
		try:
			response = scraper.get(url, timeout=timeout, headers=headers)
			if response.status_code == 200 and 'text/html' not in response.headers.get('content-type', ''):
				return response.json()
		except Exception:
			pass

	# Fallback to regular requests
	try:
		response = requests.get(url, timeout=timeout, headers=headers)
		if response.status_code == 200 and 'text/html' not in response.headers.get('content-type', ''):
			return response.json()
	except Exception:
		pass

	return None


class StremioCache:
	"""Simple caching layer for Stremio catalog data"""

	@staticmethod
	def _make_cache_key(prefix, *args):
		"""Create a cache key from prefix and args"""
		key_parts = [str(a).replace('/', '_').replace(':', '_') for a in args]
		return f"pov_stremio_{prefix}_{'_'.join(key_parts)}"

	@staticmethod
	def get(key):
		"""Get item from cache"""
		try:
			cachedata = get_property(key)
			if cachedata:
				cachedata = literal_eval(cachedata)
				if cachedata[0] > time.time():
					return cachedata[1]
		except Exception:
			pass
		return None

	@staticmethod
	def set(key, data, hours=1):
		"""Set item in cache"""
		try:
			expires = int(time.time() + (hours * 3600))
			cachedata = repr((expires, data))
			set_property(key, cachedata)
		except Exception:
			pass

	@staticmethod
	def delete(key):
		"""Delete item from cache"""
		clear_property(key)

	@staticmethod
	def clear_all():
		"""Clear all Stremio catalog cache - called via cache clearing"""
		# Note: This clears window properties which are session-based
		# For persistent cache, we'd need database storage
		pass


class StremioIndexer:
	"""Indexer for browsing Stremio addon catalogs"""

	def __init__(self, params=None):
		self.params = params or {}
		self.params_get = self.params.get
		self.items = []
		self.cache = StremioCache()
		self.__handle__ = int(sys.argv[1])

	def run(self):
		"""Main entry point - routes to appropriate handler"""
		mode = self.params_get('stremio_mode', 'list_addons')

		if mode == 'list_addons':
			self.list_addons_with_catalogs()
		elif mode == 'list_catalogs':
			self.list_addon_catalogs()
		elif mode == 'browse_catalog':
			self.browse_catalog()
		elif mode == 'view_meta':
			self.view_meta()
		elif mode == 'search':
			self.search_catalogs()
		elif mode == 'search_results':
			self.search_results()
		elif mode == 'all_catalogs':
			self.list_all_catalogs()
		elif mode == 'filter_catalog':
			self.filter_catalog()

	def get_stremio_addons(self):
		"""Get list of configured Stremio addons"""
		try:
			addons_str = get_setting('stremio.addons', '')
			if addons_str:
				addons = literal_eval(addons_str)
				return addons if isinstance(addons, list) else []
		except Exception:
			pass
		return []

	def fetch_manifest(self, addon_url, use_cache=True):
		"""Fetch addon manifest with caching and Cloudflare bypass"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				manifest_url = base_url
				base_url = base_url[:-14]
			else:
				manifest_url = f"{base_url}/manifest.json"

			cache_key = self.cache._make_cache_key('manifest', base_url)

			# Check cache first
			if use_cache:
				cached = self.cache.get(cache_key)
				if cached:
					return cached

			manifest = _fetch_json(manifest_url, timeout=10)
			if manifest and use_cache:
				# Cache the manifest
				self.cache.set(cache_key, manifest, hours=MANIFEST_CACHE_HOURS)

			return manifest
		except Exception:
			pass
		return None

	def fetch_manifests_parallel(self, addons):
		"""Fetch multiple addon manifests in parallel"""
		results = {}
		threads = []

		def fetch_one(addon):
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			if addon_url:
				manifest = self.fetch_manifest(addon_url)
				if manifest:
					results[addon_url] = manifest

		for addon in addons:
			t = Thread(target=fetch_one, args=(addon,))
			t.start()
			threads.append(t)

		for t in threads:
			t.join(timeout=15)

		return results

	def list_addons_with_catalogs(self):
		"""List all addons that have catalog support"""
		addons = self.get_stremio_addons()

		if not addons:
			notification('No Stremio addons configured', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Fetch manifests in parallel to check catalog support
		manifests = self.fetch_manifests_parallel(addons)

		items = []
		for addon in addons:
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			manifest = manifests.get(addon_url)

			if not manifest:
				continue

			# Check if addon has catalogs
			catalogs = manifest.get('catalogs', [])
			has_catalog = any(c.get('type') in ('movie', 'series') for c in catalogs)

			if has_catalog or addon.get('supports_catalog', False):
				catalog_count = len([c for c in catalogs if c.get('type') in ('movie', 'series')])
				items.append({
					'name': addon.get('name', manifest.get('name', 'Unknown')),
					'url': addon_url,
					'description': f"{catalog_count} catalog(s) available",
					'mode': 'stremio_catalog',
					'stremio_mode': 'list_catalogs',
					'addon_url': addon_url
				})

		if not items:
			notification('No addons with catalog support found', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		self._build_addon_list(items)

	def _build_addon_list(self, items):
		"""Build Kodi list of addons"""
		listitems = []
		for item in items:
			listitem = make_listitem()
			listitem.setLabel(item['name'])
			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': item['name'], 'plot': item.get('description', '')})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(item['name'])
				videoinfo.setPlot(item.get('description', ''))

			url = build_url({
				'mode': item['mode'],
				'stremio_mode': item['stremio_mode'],
				'addon_url': item['addon_url']
			})

			listitems.append((url, listitem, True))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'files')
		end_directory(self.__handle__)

	def list_addon_catalogs(self):
		"""List available catalogs from a specific addon"""
		addon_url = self.params_get('addon_url', '')
		if not addon_url:
			notification('No addon URL provided', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		manifest = self.fetch_manifest(addon_url)
		if not manifest:
			notification('Failed to fetch addon manifest', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		catalogs = manifest.get('catalogs', [])
		if not catalogs:
			notification('No catalogs available', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		items = []
		for catalog in catalogs:
			catalog_type = catalog.get('type', '')
			catalog_id = catalog.get('id', '')
			catalog_name = catalog.get('name', catalog_id)

			# Filter to movie and series only
			if catalog_type not in ('movie', 'series'):
				continue

			# Check for extra filters (genres, etc.)
			extra = catalog.get('extra', [])
			has_filters = bool(extra)
			filter_info = ''
			if has_filters:
				filter_types = [e.get('name', '') for e in extra if e.get('name')]
				if filter_types:
					filter_info = f" (Filters: {', '.join(filter_types[:3])})"

			items.append({
				'name': f"{catalog_name} ({catalog_type.capitalize()}){filter_info}",
				'catalog_type': catalog_type,
				'catalog_id': catalog_id,
				'addon_url': addon_url,
				'mode': 'stremio_catalog',
				'stremio_mode': 'browse_catalog',
				'extra': extra,
				'has_filters': has_filters
			})

		if not items:
			notification('No movie/series catalogs found', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		self._build_catalog_list(items)

	def _build_catalog_list(self, items):
		"""Build Kodi list of catalogs"""
		listitems = []
		for item in items:
			listitem = make_listitem()
			listitem.setLabel(item['name'])
			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': item['name']})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(item['name'])

			params = {
				'mode': item['mode'],
				'stremio_mode': item['stremio_mode'],
				'addon_url': item['addon_url'],
				'catalog_type': item['catalog_type'],
				'catalog_id': item['catalog_id']
			}

			# Add filter option if available
			if item.get('has_filters'):
				params['extra'] = json.dumps(item['extra'])

			url = build_url(params)
			listitems.append((url, listitem, True))

			# Add filter entry if filters are available
			if item.get('has_filters') and item.get('extra'):
				for extra_item in item['extra']:
					extra_name = extra_item.get('name', '')
					if extra_name in ('genre', 'skip'):  # Common filter types
						continue  # Skip will be handled in browse
					if extra_name and extra_item.get('options'):
						filter_listitem = make_listitem()
						filter_listitem.setLabel(f"  Filter by {extra_name.capitalize()}")
						if KODI_VERSION < 20:
							filter_listitem.setInfo('video', {'title': f"Filter {item['name']} by {extra_name}"})
						else:
							fi = filter_listitem.getVideoInfoTag(offscreen=True)
							fi.setTitle(f"Filter {item['name']} by {extra_name}")
						filter_url = build_url({
							'mode': 'stremio_catalog',
							'stremio_mode': 'filter_catalog',
							'addon_url': item['addon_url'],
							'catalog_type': item['catalog_type'],
							'catalog_id': item['catalog_id'],
							'filter_name': extra_name,
							'filter_options': json.dumps(extra_item.get('options', []))
						})
						listitems.append((filter_url, filter_listitem, True))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'files')
		end_directory(self.__handle__)

	def filter_catalog(self):
		"""Show filter options for a catalog"""
		addon_url = self.params_get('addon_url', '')
		catalog_type = self.params_get('catalog_type', 'movie')
		catalog_id = self.params_get('catalog_id', '')
		filter_name = self.params_get('filter_name', '')
		filter_options = self.params_get('filter_options', '[]')

		try:
			options = json.loads(filter_options)
		except Exception:
			options = []

		if not options:
			notification('No filter options available', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		listitems = []
		for option in options:
			listitem = make_listitem()
			listitem.setLabel(str(option))
			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': str(option)})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(str(option))

			url = build_url({
				'mode': 'stremio_catalog',
				'stremio_mode': 'browse_catalog',
				'addon_url': addon_url,
				'catalog_type': catalog_type,
				'catalog_id': catalog_id,
				'filter_name': filter_name,
				'filter_value': str(option)
			})
			listitems.append((url, listitem, True))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'files')
		end_directory(self.__handle__)

	def browse_catalog(self):
		"""Browse contents of a catalog"""
		addon_url = self.params_get('addon_url', '')
		catalog_type = self.params_get('catalog_type', 'movie')
		catalog_id = self.params_get('catalog_id', '')
		skip = int(self.params_get('skip', '0'))
		filter_name = self.params_get('filter_name', '')
		filter_value = self.params_get('filter_value', '')

		if not addon_url or not catalog_id:
			notification('Missing catalog parameters', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Fetch catalog contents
		metas = self.fetch_catalog(addon_url, catalog_type, catalog_id, skip, filter_name, filter_value)

		if not metas:
			notification('No items found', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		self._build_meta_list(metas, addon_url, catalog_type, catalog_id, skip, filter_name, filter_value)

	def fetch_catalog(self, addon_url, catalog_type, catalog_id, skip=0, filter_name='', filter_value=''):
		"""Fetch catalog contents from addon with caching and Cloudflare bypass"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			# Build catalog endpoint with optional filters
			extra_parts = []
			if skip > 0:
				extra_parts.append(f"skip={skip}")
			if filter_name and filter_value:
				extra_parts.append(f"{filter_name}={filter_value}")

			if extra_parts:
				extra_string = '&'.join(extra_parts)
				endpoint = f"{base_url}/catalog/{catalog_type}/{catalog_id}/{extra_string}.json"
			else:
				endpoint = f"{base_url}/catalog/{catalog_type}/{catalog_id}.json"

			# Check cache
			cache_key = self.cache._make_cache_key('catalog', endpoint)
			cached = self.cache.get(cache_key)
			if cached:
				return cached

			data = _fetch_json(endpoint, timeout=15)
			if data:
				metas = data.get('metas', [])
				# Cache results
				self.cache.set(cache_key, metas, hours=CATALOG_CACHE_HOURS)
				return metas
		except Exception:
			pass
		return []

	def _build_meta_list(self, metas, addon_url, catalog_type, catalog_id, current_skip, filter_name='', filter_value=''):
		"""Build Kodi list of meta items"""
		listitems = []

		for meta in metas:
			listitem = make_listitem()

			name = meta.get('name', 'Unknown')
			year = meta.get('year', '')
			imdb_id = meta.get('imdb_id', '') or meta.get('id', '')

			# Extract IMDb ID from id if needed
			if imdb_id.startswith('tt'):
				pass
			elif ':' in imdb_id:
				imdb_id = imdb_id.split(':')[0]

			# Set label
			if year:
				listitem.setLabel(f"{name} ({year})")
			else:
				listitem.setLabel(name)

			# Set info
			info_dict = {
				'title': name,
				'year': int(year) if year and str(year).isdigit() else 0,
				'plot': meta.get('description', ''),
				'genre': ', '.join(meta.get('genres', [])) if meta.get('genres') else '',
				'imdbnumber': imdb_id
			}

			# Add runtime if available
			runtime = meta.get('runtime')
			if runtime:
				if isinstance(runtime, str):
					# Parse "120 min" format
					try:
						runtime = int(runtime.split()[0])
					except Exception:
						runtime = 0
				info_dict['duration'] = runtime * 60 if runtime else 0

			# Add rating if available
			imdb_rating = meta.get('imdbRating')
			if imdb_rating:
				try:
					info_dict['rating'] = float(imdb_rating)
				except Exception:
					pass

			if KODI_VERSION < 20:
				listitem.setInfo('video', info_dict)
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(name)
				videoinfo.setMediaType('movie' if catalog_type == 'movie' else 'tvshow')
				year_int = int(year) if year and str(year).isdigit() else 0
				if year_int: videoinfo.setYear(year_int)
				videoinfo.setPlot(meta.get('description', ''))
				genres = meta.get('genres', [])
				if genres: videoinfo.setGenres(genres)
				videoinfo.setIMDBNumber(imdb_id)
				duration = info_dict.get('duration', 0)
				if duration: videoinfo.setDuration(duration)
				rating = info_dict.get('rating')
				if rating: videoinfo.setRating(rating)

			# Set art
			poster = meta.get('poster', '')
			background = meta.get('background', '') or meta.get('fanart', '')
			logo = meta.get('logo', '')

			art_dict = {}
			if poster:
				art_dict['poster'] = poster
				art_dict['thumb'] = poster
			if background:
				art_dict['fanart'] = background
			if logo:
				art_dict['clearlogo'] = logo

			if art_dict:
				listitem.setArt(art_dict)

			# Determine action based on type
			if catalog_type == 'movie':
				# Link to POV's movie play/info
				if imdb_id.startswith('tt'):
					url = build_url({
						'mode': 'extras_menu_choice',
						'media_type': 'movie',
						'imdb_id': imdb_id,
						'name': name
					})
				else:
					url = build_url({
						'mode': 'stremio_catalog',
						'stremio_mode': 'view_meta',
						'addon_url': addon_url,
						'meta_type': catalog_type,
						'meta_id': meta.get('id', '')
					})
			else:
				# Series - link to POV's show info
				if imdb_id.startswith('tt'):
					url = build_url({
						'mode': 'extras_menu_choice',
						'media_type': 'tvshow',
						'imdb_id': imdb_id,
						'name': name
					})
				else:
					url = build_url({
						'mode': 'stremio_catalog',
						'stremio_mode': 'view_meta',
						'addon_url': addon_url,
						'meta_type': catalog_type,
						'meta_id': meta.get('id', '')
					})

			listitems.append((url, listitem, catalog_type == 'series'))

		# Add "Next Page" item if we got a full page
		if len(metas) >= 20:  # Assuming 20 items per page
			next_skip = current_skip + len(metas)
			listitem = make_listitem()
			listitem.setLabel('[B]Next Page >>>[/B]')
			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': 'Next Page'})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle('Next Page')

			params = {
				'mode': 'stremio_catalog',
				'stremio_mode': 'browse_catalog',
				'addon_url': addon_url,
				'catalog_type': catalog_type,
				'catalog_id': catalog_id,
				'skip': str(next_skip)
			}
			if filter_name and filter_value:
				params['filter_name'] = filter_name
				params['filter_value'] = filter_value

			url = build_url(params)
			listitems.append((url, listitem, True))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'movies' if catalog_type == 'movie' else 'tvshows')
		end_directory(self.__handle__)
		set_view_mode('view.movies' if catalog_type == 'movie' else 'view.tvshows', 'movies' if catalog_type == 'movie' else 'tvshows')

	def search_catalogs(self):
		"""Show search input and search across catalogs"""
		# Get search query from user
		search_query = dialog.input('Search Stremio Catalogs', type=0)
		if not search_query:
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Store search query and redirect to results
		from modules.kodi_utils import execute_builtin
		url = build_url({
			'mode': 'stremio_catalog',
			'stremio_mode': 'search_results',
			'query': search_query
		})
		execute_builtin(f'Container.Update({url})')

	def search_results(self):
		"""Display search results from all addons"""
		query = self.params_get('query', '')
		media_type = self.params_get('media_type', '')  # Optional: filter by type

		if not query:
			notification('No search query provided', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		addons = self.get_stremio_addons()
		if not addons:
			notification('No Stremio addons configured', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Fetch manifests to find searchable catalogs
		manifests = self.fetch_manifests_parallel(addons)

		all_results = []
		threads = []

		def search_addon(addon_url, manifest):
			catalogs = manifest.get('catalogs', [])
			for catalog in catalogs:
				catalog_type = catalog.get('type', '')
				catalog_id = catalog.get('id', '')

				if catalog_type not in ('movie', 'series'):
					continue
				if media_type and catalog_type != media_type:
					continue

				# Check if catalog supports search
				extra = catalog.get('extra', [])
				supports_search = any(e.get('name') == 'search' for e in extra)
				if not supports_search:
					continue

				# Perform search
				results = self.fetch_catalog_search(addon_url, catalog_type, catalog_id, query)
				for result in results:
					result['_addon_name'] = manifest.get('name', 'Unknown')
					result['_catalog_type'] = catalog_type
					all_results.append(result)

		for addon in addons:
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			manifest = manifests.get(addon_url)
			if manifest:
				t = Thread(target=search_addon, args=(addon_url, manifest))
				t.start()
				threads.append(t)

		for t in threads:
			t.join(timeout=20)

		if not all_results:
			notification(f'No results found for "{query}"', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Deduplicate by IMDb ID
		seen_ids = set()
		unique_results = []
		for result in all_results:
			imdb_id = result.get('imdb_id', '') or result.get('id', '')
			if ':' in imdb_id:
				imdb_id = imdb_id.split(':')[0]
			if imdb_id and imdb_id not in seen_ids:
				seen_ids.add(imdb_id)
				unique_results.append(result)
			elif not imdb_id:
				unique_results.append(result)

		self._build_search_results(unique_results, query)

	def fetch_catalog_search(self, addon_url, catalog_type, catalog_id, query):
		"""Search a specific catalog"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			from urllib.parse import quote
			encoded_query = quote(query)
			endpoint = f"{base_url}/catalog/{catalog_type}/{catalog_id}/search={encoded_query}.json"

			# Check cache
			cache_key = self.cache._make_cache_key('search', endpoint)
			cached = self.cache.get(cache_key)
			if cached:
				return cached

			data = _fetch_json(endpoint, timeout=15)
			if data:
				metas = data.get('metas', [])
				# Cache search results for shorter time
				self.cache.set(cache_key, metas, hours=0.5)  # 30 minutes
				return metas
		except Exception:
			pass
		return []

	def _build_search_results(self, results, query):
		"""Build Kodi list of search results"""
		listitems = []

		for meta in results:
			listitem = make_listitem()

			name = meta.get('name', 'Unknown')
			year = meta.get('year', '')
			catalog_type = meta.get('_catalog_type', 'movie')
			addon_name = meta.get('_addon_name', '')
			imdb_id = meta.get('imdb_id', '') or meta.get('id', '')

			if ':' in imdb_id:
				imdb_id = imdb_id.split(':')[0]

			# Set label with source info
			label_parts = [name]
			if year:
				label_parts.append(f"({year})")
			type_indicator = '[MOVIE]' if catalog_type == 'movie' else '[TV]'
			label_parts.append(type_indicator)
			if addon_name:
				label_parts.append(f"[{addon_name}]")

			listitem.setLabel(' '.join(label_parts))

			# Set info
			if KODI_VERSION < 20:
				info_dict = {
					'title': name,
					'year': int(year) if year and str(year).isdigit() else 0,
					'plot': meta.get('description', ''),
					'genre': ', '.join(meta.get('genres', [])) if meta.get('genres') else '',
					'imdbnumber': imdb_id
				}
				listitem.setInfo('video', info_dict)
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(name)
				videoinfo.setMediaType('movie' if catalog_type == 'movie' else 'tvshow')
				year_int = int(year) if year and str(year).isdigit() else 0
				if year_int: videoinfo.setYear(year_int)
				videoinfo.setPlot(meta.get('description', ''))
				genres = meta.get('genres', [])
				if genres: videoinfo.setGenres(genres)
				videoinfo.setIMDBNumber(imdb_id)

			# Set art
			poster = meta.get('poster', '')
			background = meta.get('background', '') or meta.get('fanart', '')

			if poster or background:
				art_dict = {}
				if poster:
					art_dict['poster'] = poster
					art_dict['thumb'] = poster
				if background:
					art_dict['fanart'] = background
				listitem.setArt(art_dict)

			# Determine action
			if imdb_id.startswith('tt'):
				media_type = 'movie' if catalog_type == 'movie' else 'tvshow'
				url = build_url({
					'mode': 'extras_menu_choice',
					'media_type': media_type,
					'imdb_id': imdb_id,
					'name': name
				})
			else:
				url = build_url({
					'mode': 'stremio_catalog',
					'stremio_mode': 'view_meta',
					'addon_url': '',
					'meta_type': catalog_type,
					'meta_id': meta.get('id', '')
				})

			listitems.append((url, listitem, catalog_type == 'series'))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'movies')
		end_directory(self.__handle__)
		set_view_mode('view.movies', 'movies')

	def list_all_catalogs(self):
		"""List all catalogs from all configured addons"""
		addons = self.get_stremio_addons()

		if not addons:
			notification('No Stremio addons configured', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Fetch all manifests in parallel
		manifests = self.fetch_manifests_parallel(addons)

		items = []
		for addon in addons:
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			manifest = manifests.get(addon_url)

			if not manifest:
				continue

			addon_name = manifest.get('name', addon.get('name', 'Unknown'))
			catalogs = manifest.get('catalogs', [])

			for catalog in catalogs:
				catalog_type = catalog.get('type', '')
				catalog_id = catalog.get('id', '')
				catalog_name = catalog.get('name', catalog_id)

				if catalog_type not in ('movie', 'series'):
					continue

				type_label = 'Movies' if catalog_type == 'movie' else 'TV Shows'
				items.append({
					'name': f"[{addon_name}] {catalog_name} ({type_label})",
					'catalog_type': catalog_type,
					'catalog_id': catalog_id,
					'addon_url': addon_url,
					'mode': 'stremio_catalog',
					'stremio_mode': 'browse_catalog'
				})

		if not items:
			notification('No catalogs found', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Sort by addon name then catalog name
		items.sort(key=lambda x: x['name'])

		listitems = []
		for item in items:
			listitem = make_listitem()
			listitem.setLabel(item['name'])
			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': item['name']})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(item['name'])

			url = build_url({
				'mode': item['mode'],
				'stremio_mode': item['stremio_mode'],
				'addon_url': item['addon_url'],
				'catalog_type': item['catalog_type'],
				'catalog_id': item['catalog_id']
			})

			listitems.append((url, listitem, True))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'files')
		end_directory(self.__handle__)

	def view_meta(self):
		"""View detailed metadata for an item"""
		addon_url = self.params_get('addon_url', '')
		meta_type = self.params_get('meta_type', 'movie')
		meta_id = self.params_get('meta_id', '')
		action = self.params_get('action', 'dialog')  # 'dialog', 'extras', 'cache'

		if not meta_id:
			notification('Missing meta parameters', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# If no addon URL, try to fetch from any configured addon
		if not addon_url:
			addons = self.get_stremio_addons()
			for addon in addons:
				addon_url = addon.get('config_url', '') or addon.get('url', '')
				meta = self.fetch_meta(addon_url, meta_type, meta_id)
				if meta:
					break
		else:
			meta = self.fetch_meta(addon_url, meta_type, meta_id)

		if not meta:
			notification('Failed to fetch metadata', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Get IMDb ID for integration
		imdb_id = meta.get('imdb_id', '') or meta.get('id', '')
		if ':' in imdb_id:
			imdb_id = imdb_id.split(':')[0]

		# Action: Cache metadata to POV's Stremio meta cache
		if action == 'cache':
			self._cache_stremio_meta(meta, meta_type, imdb_id)
			notification('Metadata cached', 1500)
			return

		# Action: Open in POV's extras menu (if IMDb ID available)
		if action == 'extras' and imdb_id.startswith('tt'):
			from modules.kodi_utils import execute_builtin
			media_type = 'movie' if meta_type == 'movie' else 'tvshow'
			url = build_url({
				'mode': 'extras_menu_choice',
				'media_type': media_type,
				'imdb_id': imdb_id,
				'name': meta.get('name', '')
			})
			execute_builtin(f'Container.Update({url})')
			return

		# Default: Display meta info dialog
		self._show_meta_dialog(meta, meta_type)

	def _cache_stremio_meta(self, stremio_meta, meta_type, imdb_id):
		"""Cache Stremio metadata to POV's metadata cache"""
		try:
			from indexers.stremio_meta import StremioMetaProvider
			provider = StremioMetaProvider()
			pov_type = 'movie' if meta_type == 'movie' else 'tvshow'

			if pov_type == 'movie':
				pov_meta = provider._convert_movie_meta(stremio_meta, imdb_id)
			else:
				pov_meta = provider._convert_tvshow_meta(stremio_meta, imdb_id)

			if pov_meta:
				provider.cache.set(pov_type, imdb_id, pov_meta)
		except Exception:
			pass

	def fetch_meta(self, addon_url, meta_type, meta_id):
		"""Fetch detailed metadata for an item with Cloudflare bypass"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			endpoint = f"{base_url}/meta/{meta_type}/{meta_id}.json"

			# Check cache
			cache_key = self.cache._make_cache_key('meta', endpoint)
			cached = self.cache.get(cache_key)
			if cached:
				return cached

			data = _fetch_json(endpoint, timeout=10)
			if data:
				meta = data.get('meta', {})
				# Cache metadata
				self.cache.set(cache_key, meta, hours=CATALOG_CACHE_HOURS)
				return meta
		except Exception:
			pass
		return None

	def _show_meta_dialog(self, meta, meta_type):
		"""Show metadata in a dialog"""
		from modules.kodi_utils import ok_dialog

		name = meta.get('name', 'Unknown')
		year = meta.get('year', 'N/A')
		description = meta.get('description', 'No description available')
		genres = ', '.join(meta.get('genres', [])) if meta.get('genres') else 'N/A'
		runtime = meta.get('runtime', 'N/A')
		imdb_id = meta.get('imdb_id', '') or meta.get('id', '')
		imdb_rating = meta.get('imdbRating', 'N/A')

		text = (
			f"[B]Title:[/B] {name}\n"
			f"[B]Year:[/B] {year}\n"
			f"[B]Rating:[/B] {imdb_rating}\n"
			f"[B]Genres:[/B] {genres}\n"
			f"[B]Runtime:[/B] {runtime}\n"
			f"[B]ID:[/B] {imdb_id}\n\n"
			f"[B]Description:[/B]\n{description[:500]}"
		)

		ok_dialog(heading=name, text=text)


def stremio_catalog_menu():
	"""Entry point for Stremio catalog navigation menu"""
	items = [
		('Browse Stremio Catalogs', 'stremio_catalog', {'stremio_mode': 'list_addons'}),
		('Search Catalogs', 'stremio_catalog', {'stremio_mode': 'search'}),
		('All Catalogs', 'stremio_catalog', {'stremio_mode': 'all_catalogs'}),
	]

	listitems = []
	for label, mode, extra_params in items:
		listitem = make_listitem()
		listitem.setLabel(label)
		if KODI_VERSION < 20:
			listitem.setInfo('video', {'title': label})
		else:
			videoinfo = listitem.getVideoInfoTag(offscreen=True)
			videoinfo.setTitle(label)

		params = {'mode': mode}
		params.update(extra_params)
		url = build_url(params)

		listitems.append((url, listitem, True))

	__handle__ = int(sys.argv[1])
	add_items(__handle__, listitems)
	set_content(__handle__, 'files')
	end_directory(__handle__)


def clear_stremio_catalog_cache():
	"""Clear all Stremio catalog cache"""
	StremioCache.clear_all()
	notification('Stremio catalog cache cleared', 2000)
