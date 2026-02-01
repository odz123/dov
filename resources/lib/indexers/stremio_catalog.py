# Stremio Catalog Indexer for POV
"""
	Full Stremio SDK catalog browsing for POV
	Features:
	- List available catalogs from addons
	- Browse catalog contents (movies, series, anime, tv, channel, other)
	- Search functionality across catalogs
	- Genre/extra filtering support with extraRequired handling
	- Catalog caching with addon-specified cacheMaxAge support
	- Parallel manifest fetching
	- Integration with POV metadata system
	- HTTP client via shared http_client module
	- addon_catalog resource: discover and install addons from addon catalogs
	- posterShape handling (square, poster, landscape)
"""

import sys
import json
import time
from ast import literal_eval
from threading import Thread
from modules.kodi_utils import (
	get_setting, set_setting, notification, make_listitem, add_items,
	set_content, end_directory, set_view_mode, build_url, dialog,
	get_property, set_property, clear_property, get_kodi_version
)
from modules import http_client

KODI_VERSION = get_kodi_version()

# Cache settings
MANIFEST_CACHE_HOURS = 6  # Cache manifests for 6 hours
CATALOG_CACHE_HOURS = 1   # Cache catalog contents for 1 hour

# Stremio content types supported by the SDK
_CATALOG_TYPES = ('movie', 'series', 'anime', 'tv', 'channel', 'other')
_SERIES_LIKE_TYPES = ('series', 'anime', 'tv', 'channel', 'other')


class StremioCache:
	"""Simple caching layer for Stremio catalog data using Kodi window properties.
	Tracks all cache keys in a registry property so they can be cleared."""

	_REGISTRY_KEY = 'pov_stremio_cache_keys'

	@staticmethod
	def _make_cache_key(prefix, *args):
		"""Create a cache key from prefix and args"""
		key_parts = [str(a).replace('/', '_').replace(':', '_') for a in args]
		return f"pov_stremio_{prefix}_{'_'.join(key_parts)}"

	@staticmethod
	def _register_key(key):
		"""Track a cache key in the registry for later clearing"""
		try:
			registry = get_property(StremioCache._REGISTRY_KEY)
			if registry:
				keys = json.loads(registry)
			else:
				keys = []
			if key not in keys:
				keys.append(key)
				set_property(StremioCache._REGISTRY_KEY, json.dumps(keys))
		except Exception:
			try:
				set_property(StremioCache._REGISTRY_KEY, json.dumps([key]))
			except Exception:
				pass

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
			StremioCache._register_key(key)
		except Exception:
			pass

	@staticmethod
	def delete(key):
		"""Delete item from cache"""
		clear_property(key)

	@staticmethod
	def clear_all():
		"""Clear all Stremio catalog cache by iterating tracked keys"""
		try:
			registry = get_property(StremioCache._REGISTRY_KEY)
			if registry:
				keys = json.loads(registry)
				for key in keys:
					try:
						clear_property(key)
					except Exception:
						pass
			clear_property(StremioCache._REGISTRY_KEY)
		except Exception:
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
		elif mode == 'open_item':
			self.open_item()
		elif mode == 'addon_catalog':
			self.browse_addon_catalog()
		elif mode == 'install_addon':
			self.install_addon_from_catalog()

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
		"""Fetch addon manifest with caching"""
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

			manifest = http_client.fetch_json(manifest_url, timeout=10)
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
			has_catalog = any(c.get('type') in _CATALOG_TYPES for c in catalogs)

			if has_catalog or addon.get('supports_catalog', False):
				catalog_count = len([c for c in catalogs if c.get('type') in _CATALOG_TYPES])
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

			# Filter to supported Stremio content types
			if catalog_type not in _CATALOG_TYPES:
				continue

			# Check for extra filters (genres, etc.)
			extra = catalog.get('extra', [])
			has_filters = bool(extra)

			# Per Stremio SDK: skip catalogs where search is required (search-only catalogs)
			# These catalogs should only appear in search, not in browsable lists
			has_required_search = any(
				e.get('name') == 'search' and e.get('isRequired', False)
				for e in extra
			)
			if has_required_search:
				continue

			filter_info = ''
			if has_filters:
				filter_types = [e.get('name', '') for e in extra if e.get('name') and e.get('name') != 'skip']
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
			notification('No supported catalogs found', 2000)
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
		"""Fetch catalog contents from addon with caching"""
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

			data = http_client.fetch_json(endpoint, timeout=15)
			if data:
				metas = data.get('metas', [])
				# Honor cacheMaxAge from response if available (per Stremio SDK)
				cache_max_age = data.get('cacheMaxAge', 0)
				if cache_max_age and cache_max_age > 0:
					cache_hours = cache_max_age / 3600.0
				else:
					cache_hours = CATALOG_CACHE_HOURS
				self.cache.set(cache_key, metas, hours=cache_hours)
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
			# Stremio SDK uses releaseInfo for year/date range (e.g. "2020", "2020-2024", "2020-")
			if not year and meta.get('releaseInfo'):
				release_info = str(meta['releaseInfo'])
				# Extract first 4-digit year from releaseInfo
				year_match = release_info[:4] if release_info[:4].isdigit() else ''
				year = year_match
			imdb_id = meta.get('imdb_id', '')
			tmdb_id = ''
			stremio_id = meta.get('id', '')

			# Parse IDs from stremio meta
			if not imdb_id and stremio_id:
				if stremio_id.startswith('tt'):
					imdb_id = stremio_id
				elif stremio_id.startswith('tmdb:'):
					tmdb_id = stremio_id.split(':', 1)[1]

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

			# Set art - handle Stremio poster shapes (square, poster, landscape)
			poster = meta.get('poster', '')
			background = meta.get('background', '') or meta.get('fanart', '')
			logo = meta.get('logo', '')
			poster_shape = meta.get('posterShape', 'poster')

			art_dict = {}
			if poster:
				art_dict['poster'] = poster
				# For landscape shape, use poster as fanart/thumb rather than poster
				if poster_shape == 'landscape':
					art_dict['thumb'] = poster
					if not background:
						art_dict['fanart'] = poster
				else:
					art_dict['thumb'] = poster
			if background:
				art_dict['fanart'] = background
			if logo:
				art_dict['clearlogo'] = logo

			if art_dict:
				listitem.setArt(art_dict)

			# Determine action based on available IDs and content type
			# Map Stremio types to POV types: movie stays movie, all others are tvshow
			# 'tv' type in Stremio is live TV - treat as direct playback if possible
			media_type = 'movie' if catalog_type == 'movie' else 'tvshow'
			if tmdb_id:
				if media_type == 'movie':
					url = build_url({'mode': 'play_media', 'media_type': 'movie', 'tmdb_id': tmdb_id})
				else:
					url = build_url({'mode': 'build_season_list', 'tmdb_id': tmdb_id})
			elif imdb_id.startswith('tt'):
				url = build_url({
					'mode': 'stremio_catalog',
					'stremio_mode': 'open_item',
					'media_type': media_type,
					'imdb_id': imdb_id
				})
			else:
				url = build_url({
					'mode': 'stremio_catalog',
					'stremio_mode': 'view_meta',
					'addon_url': addon_url,
					'meta_type': catalog_type,
					'meta_id': stremio_id
				})

			listitems.append((url, listitem, catalog_type in _SERIES_LIKE_TYPES))

		# Add "Next Page" if we got items (Stremio SDK: < 100 items signals end of catalog)
		# Use len(metas) >= 20 as minimum threshold - addons return varying page sizes
		if len(metas) >= 20:
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

				if catalog_type not in _CATALOG_TYPES:
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

			data = http_client.fetch_json(endpoint, timeout=15)
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
			# Stremio SDK uses releaseInfo for year/date range
			if not year and meta.get('releaseInfo'):
				release_info = str(meta['releaseInfo'])
				year_match = release_info[:4] if release_info[:4].isdigit() else ''
				year = year_match
			catalog_type = meta.get('_catalog_type', 'movie')
			addon_name = meta.get('_addon_name', '')
			imdb_id = meta.get('imdb_id', '')
			tmdb_id = ''
			stremio_id = meta.get('id', '')

			# Parse IDs from stremio meta
			if not imdb_id and stremio_id:
				if stremio_id.startswith('tt'):
					imdb_id = stremio_id
				elif stremio_id.startswith('tmdb:'):
					tmdb_id = stremio_id.split(':', 1)[1]

			# Set label with source info
			label_parts = [name]
			if year:
				label_parts.append(f"({year})")
			type_indicator = '[MOVIE]' if catalog_type == 'movie' else '[TV]' if catalog_type == 'series' else f'[{catalog_type.upper()}]'
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
			media_type = 'movie' if catalog_type == 'movie' else 'tvshow'
			if tmdb_id:
				if media_type == 'movie':
					url = build_url({'mode': 'play_media', 'media_type': 'movie', 'tmdb_id': tmdb_id})
				else:
					url = build_url({'mode': 'build_season_list', 'tmdb_id': tmdb_id})
			elif imdb_id.startswith('tt'):
				url = build_url({
					'mode': 'stremio_catalog',
					'stremio_mode': 'open_item',
					'media_type': media_type,
					'imdb_id': imdb_id
				})
			else:
				url = build_url({
					'mode': 'stremio_catalog',
					'stremio_mode': 'view_meta',
					'addon_url': '',
					'meta_type': catalog_type,
					'meta_id': stremio_id
				})

			listitems.append((url, listitem, catalog_type in _SERIES_LIKE_TYPES))

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

				if catalog_type not in _CATALOG_TYPES:
					continue

				type_label = 'Movies' if catalog_type == 'movie' else 'TV Shows' if catalog_type == 'series' else catalog_type.capitalize()
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

	def open_item(self):
		"""Resolve TMDB ID and open item the same way trending lists do"""
		media_type = self.params_get('media_type', 'movie')
		imdb_id = self.params_get('imdb_id', '')
		tmdb_id = self.params_get('tmdb_id', '')
		if not tmdb_id and imdb_id:
			from indexers import metadata
			from modules import settings
			from modules.utils import get_datetime
			function = metadata.movie_meta if media_type == 'movie' else metadata.tvshow_meta
			meta = function('imdb_id', imdb_id, settings.metadata_user_info(), get_datetime())
			tmdb_id = str(meta.get('tmdb_id', ''))
		if not tmdb_id:
			notification('Could not resolve TMDB ID', 2000)
			return
		if media_type == 'movie':
			from modules.sources import SourceSelect
			SourceSelect.factory({'media_type': 'movie', 'tmdb_id': tmdb_id})
		else:
			from indexers.seasons import Seasons
			Seasons({'tmdb_id': tmdb_id}).run()

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
		"""Fetch detailed metadata for an item"""
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

			data = http_client.fetch_json(endpoint, timeout=10)
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


	def browse_addon_catalog(self):
		"""Browse addon_catalog resource - discover and install addons from other addons.
		Per Stremio SDK: addon_catalog resource returns {addons: [{transportUrl, manifest}]}."""
		addon_url = self.params_get('addon_url', '')

		if not addon_url:
			# Find all addons that support addon_catalog
			addons = self.get_stremio_addons()
			addon_catalog_addons = [a for a in addons if a.get('supports_addon_catalog', False)]

			if not addon_catalog_addons:
				notification('No addons with addon catalog support found', 2000)
				set_content(self.__handle__, 'files')
				end_directory(self.__handle__)
				return

			# If only one, browse it directly; otherwise list them
			if len(addon_catalog_addons) == 1:
				addon_url = addon_catalog_addons[0].get('config_url', '') or addon_catalog_addons[0].get('url', '')
			else:
				items = []
				for addon in addon_catalog_addons:
					url = addon.get('config_url', '') or addon.get('url', '')
					items.append({
						'name': addon.get('name', 'Unknown'),
						'url': url,
						'description': 'Browse addon catalogs',
						'mode': 'stremio_catalog',
						'stremio_mode': 'addon_catalog',
						'addon_url': url
					})
				self._build_addon_list(items)
				return

		# Fetch manifest to get addonCatalogs
		manifest = self.fetch_manifest(addon_url)
		if not manifest:
			notification('Failed to fetch addon manifest', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		addon_catalogs = manifest.get('addonCatalogs', manifest.get('catalogs', []))

		# Fetch each addon catalog
		base_url = addon_url.rstrip('/')
		if base_url.endswith('/manifest.json'):
			base_url = base_url[:-14]

		all_addons = []
		for catalog in addon_catalogs:
			cat_type = catalog.get('type', '')
			cat_id = catalog.get('id', '')
			if not cat_id:
				continue

			endpoint = f"{base_url}/addon_catalog/{cat_type}/{cat_id}.json"
			data = http_client.fetch_json(endpoint, timeout=10)
			if data and 'addons' in data:
				for addon_entry in data['addons']:
					addon_manifest = addon_entry.get('manifest', {})
					transport_url = addon_entry.get('transportUrl', '')
					if addon_manifest and transport_url:
						addon_manifest['_transportUrl'] = transport_url
						all_addons.append(addon_manifest)

		if not all_addons:
			notification('No addons found in catalog', 2000)
			set_content(self.__handle__, 'files')
			end_directory(self.__handle__)
			return

		# Build list of discoverable addons
		listitems = []
		for addon_manifest in all_addons:
			listitem = make_listitem()
			name = addon_manifest.get('name', 'Unknown Addon')
			version = addon_manifest.get('version', '')
			description = addon_manifest.get('description', '')
			types = ', '.join(addon_manifest.get('types', []))
			transport_url = addon_manifest.get('_transportUrl', '')

			label = f"{name} v{version}" if version else name
			listitem.setLabel(label)

			if KODI_VERSION < 20:
				listitem.setInfo('video', {'title': name, 'plot': f"{description}\nTypes: {types}"})
			else:
				videoinfo = listitem.getVideoInfoTag(offscreen=True)
				videoinfo.setTitle(name)
				videoinfo.setPlot(f"{description}\nTypes: {types}")

			# Set logo as art if available
			logo = addon_manifest.get('logo', '')
			background = addon_manifest.get('background', '')
			if logo or background:
				art = {}
				if logo: art['icon'] = logo
				if background: art['fanart'] = background
				listitem.setArt(art)

			url = build_url({
				'mode': 'stremio_catalog',
				'stremio_mode': 'install_addon',
				'install_url': transport_url,
				'addon_name': name
			})
			listitems.append((url, listitem, False))

		add_items(self.__handle__, listitems)
		set_content(self.__handle__, 'files')
		end_directory(self.__handle__)

	def install_addon_from_catalog(self):
		"""Install a Stremio addon discovered from an addon_catalog resource."""
		from modules.kodi_utils import ok_dialog, confirm_dialog
		from modules.stremio_manager import validate_stremio_addon, get_stremio_addons, save_stremio_addons

		install_url = self.params_get('install_url', '')
		addon_name = self.params_get('addon_name', 'Unknown')

		if not install_url:
			notification('No addon URL provided', 2000)
			return

		# Confirm installation
		if not confirm_dialog(heading='Install Stremio Addon', text=f"Install '{addon_name}' from addon catalog?"):
			return

		notification('Validating addon...', 2000)

		# Validate and add the addon
		addon_info, error = validate_stremio_addon(install_url)

		if error:
			ok_dialog(heading='Error', text=f'Failed to install addon:\n{error}')
			return

		# Check if already exists
		addons = get_stremio_addons()
		for existing in addons:
			if existing.get('id') == addon_info.get('id') or existing.get('url') == addon_info.get('url'):
				notification(f"'{addon_info['name']}' is already installed", 2000)
				return

		addons.append(addon_info)
		save_stremio_addons(addons)
		notification(f"Installed: {addon_info['name']}", 2000)


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
