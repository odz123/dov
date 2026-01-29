# Stremio Metadata Provider for POV
"""
	Fetches and converts metadata from Stremio addons to POV format.

	Features:
	- Fetch metadata from Stremio addon /meta endpoint
	- Convert Stremio meta format to POV metadata format
	- Parallel fetching from multiple addons
	- Caching support for Stremio metadata
	- Fallback/supplement to TMDB metadata
	- Support for movies and TV series
"""

import time
from ast import literal_eval
from threading import Thread
from modules.kodi_utils import get_setting, get_property, set_property, clear_property
# from modules.kodi_utils import logger


# Stremio to POV genre mapping
GENRE_MAP = {
	'action': 'Action',
	'adventure': 'Adventure',
	'animation': 'Animation',
	'biography': 'Biography',
	'comedy': 'Comedy',
	'crime': 'Crime',
	'documentary': 'Documentary',
	'drama': 'Drama',
	'family': 'Family',
	'fantasy': 'Fantasy',
	'film-noir': 'Film-Noir',
	'game-show': 'Game-Show',
	'history': 'History',
	'horror': 'Horror',
	'music': 'Music',
	'musical': 'Musical',
	'mystery': 'Mystery',
	'news': 'News',
	'reality-tv': 'Reality-TV',
	'romance': 'Romance',
	'sci-fi': 'Sci-Fi',
	'science fiction': 'Sci-Fi',
	'short': 'Short',
	'sport': 'Sport',
	'talk-show': 'Talk-Show',
	'thriller': 'Thriller',
	'war': 'War',
	'western': 'Western',
}

# Default fanart data structure
DEFAULT_FANART_DATA = {
	'poster2': '', 'fanart2': '', 'banner': '', 'clearart': '',
	'clearlogo': '', 'landscape': '', 'discart': '', 'fanart_added': False
}

# Cache settings
META_CACHE_HOURS = 24  # Cache Stremio metadata for 24 hours
MANIFEST_CACHE_HOURS = 6


class StremioMetaCache:
	"""Cache layer for Stremio metadata using SQLite database via MetaCache"""

	def __init__(self):
		from caches.meta_cache import MetaCache
		self._metacache = MetaCache()

	def get(self, media_type, media_id):
		"""Get metadata from cache (SQLite + memory)"""
		try:
			return self._metacache.get_stremio(media_type, media_id)
		except Exception:
			pass
		return None

	def set(self, media_type, media_id, meta, hours=META_CACHE_HOURS):
		"""Set metadata in cache (SQLite + memory)"""
		try:
			# Convert hours to days for the MetaCache API
			days = max(1, int(hours / 24)) if hours >= 24 else 7
			self._metacache.set_stremio(media_type, media_id, meta, expiration=days)
		except Exception:
			pass

	def delete(self, media_type, media_id):
		"""Delete metadata from cache"""
		try:
			self._metacache.delete_stremio(media_type, media_id)
		except Exception:
			pass

	def delete_all(self):
		"""Delete all Stremio metadata from cache"""
		try:
			self._metacache.delete_all_stremio()
		except Exception:
			pass


class StremioMetaProvider:
	"""Provider for fetching metadata from Stremio addons"""

	def __init__(self):
		self.cache = StremioMetaCache()
		self.timeout = int(get_setting('stremio.timeout', '8'))
		self._addons = None

	def get_addons(self):
		"""Get list of configured Stremio addons with meta support"""
		if self._addons is not None:
			return self._addons

		try:
			addons_str = get_setting('stremio.addons', '')
			if addons_str:
				addons = literal_eval(addons_str)
				if isinstance(addons, list):
					self._addons = addons
					return addons
		except Exception:
			pass
		self._addons = []
		return []

	def get_addons_with_meta_support(self):
		"""Get addons that support the meta resource"""
		addons = self.get_addons()
		meta_addons = []

		for addon in addons:
			# Check if addon supports meta resource
			supports_meta = addon.get('supports_meta', False)
			if supports_meta:
				meta_addons.append(addon)
				continue

			# Fallback: fetch manifest and check resources
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			if addon_url:
				manifest = self._fetch_manifest_cached(addon_url)
				if manifest:
					resources = manifest.get('resources', [])
					if 'meta' in resources or any(
						isinstance(r, dict) and r.get('name') == 'meta' for r in resources
					):
						meta_addons.append(addon)

		return meta_addons

	def _fetch_manifest_cached(self, addon_url):
		"""Fetch addon manifest with caching"""
		try:
			from modules.http_client import fetch_manifest
			cache_key = f'pov_stremio_manifest_{addon_url.replace("/", "_").replace(":", "_")}'

			cached = get_property(cache_key)
			if cached:
				cachedata = literal_eval(cached)
				if cachedata[0] > time.time():
					return cachedata[1]

			manifest = fetch_manifest(addon_url, timeout=5)
			if manifest:
				expires = int(time.time() + (MANIFEST_CACHE_HOURS * 3600))
				set_property(cache_key, repr((expires, manifest)))
			return manifest
		except Exception:
			pass
		return None

	def _fetch_json(self, url, timeout=None):
		"""Fetch JSON from URL with Cloudflare bypass"""
		try:
			from modules.http_client import fetch_json
			return fetch_json(url, timeout=timeout or self.timeout)
		except Exception:
			pass
		return None

	def fetch_meta(self, addon_url, media_type, media_id):
		"""
		Fetch metadata from a single Stremio addon.

		Args:
			addon_url: Base URL of the addon
			media_type: 'movie' or 'series'
			media_id: IMDb ID (e.g., 'tt1234567')

		Returns:
			dict: Stremio meta object or None
		"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			endpoint = f'{base_url}/meta/{media_type}/{media_id}.json'
			data = self._fetch_json(endpoint)

			if data:
				return data.get('meta', {})
		except Exception:
			pass
		return None

	def fetch_meta_parallel(self, media_type, media_id, addons=None):
		"""
		Fetch metadata from multiple addons in parallel.

		Args:
			media_type: 'movie' or 'series'
			media_id: IMDb ID
			addons: Optional list of addons to use (defaults to all with meta support)

		Returns:
			dict: Best metadata found, or None
		"""
		if addons is None:
			addons = self.get_addons_with_meta_support()

		if not addons:
			return None

		results = []
		threads = []

		def fetch_one(addon):
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			if addon_url:
				meta = self.fetch_meta(addon_url, media_type, media_id)
				if meta:
					results.append(meta)

		for addon in addons:
			t = Thread(target=fetch_one, args=(addon,))
			t.start()
			threads.append(t)

		for t in threads:
			t.join(timeout=self.timeout + 2)

		if not results:
			return None

		# Return the most complete result
		return self._select_best_meta(results)

	def _select_best_meta(self, results):
		"""Select the most complete metadata from multiple results"""
		if not results:
			return None
		if len(results) == 1:
			return results[0]

		# Score each result by completeness
		def score_meta(meta):
			score = 0
			if meta.get('name'): score += 10
			if meta.get('year'): score += 5
			if meta.get('description'): score += 10
			if meta.get('poster'): score += 8
			if meta.get('background'): score += 5
			if meta.get('logo'): score += 3
			if meta.get('genres'): score += 5
			if meta.get('imdbRating'): score += 5
			if meta.get('runtime'): score += 3
			if meta.get('cast'): score += 5
			if meta.get('director'): score += 3
			if meta.get('videos'): score += 5  # For series
			return score

		return max(results, key=score_meta)

	def get_movie_meta(self, imdb_id, use_cache=True):
		"""
		Get movie metadata from Stremio addons.

		Args:
			imdb_id: IMDb ID (e.g., 'tt1234567')
			use_cache: Whether to use cache

		Returns:
			dict: POV-formatted metadata or None
		"""
		if not imdb_id or not imdb_id.startswith('tt'):
			return None

		# Check cache
		if use_cache:
			cached = self.cache.get('movie', imdb_id)
			if cached:
				return cached

		# Fetch from addons
		stremio_meta = self.fetch_meta_parallel('movie', imdb_id)
		if not stremio_meta:
			return None

		# Convert to POV format
		pov_meta = self._convert_movie_meta(stremio_meta, imdb_id)

		# Cache result
		if pov_meta:
			self.cache.set('movie', imdb_id, pov_meta)

		return pov_meta

	def get_tvshow_meta(self, imdb_id, use_cache=True):
		"""
		Get TV show metadata from Stremio addons.

		Args:
			imdb_id: IMDb ID
			use_cache: Whether to use cache

		Returns:
			dict: POV-formatted metadata or None
		"""
		if not imdb_id or not imdb_id.startswith('tt'):
			return None

		# Check cache
		if use_cache:
			cached = self.cache.get('tvshow', imdb_id)
			if cached:
				return cached

		# Fetch from addons
		stremio_meta = self.fetch_meta_parallel('series', imdb_id)
		if not stremio_meta:
			return None

		# Convert to POV format
		pov_meta = self._convert_tvshow_meta(stremio_meta, imdb_id)

		# Cache result
		if pov_meta:
			self.cache.set('tvshow', imdb_id, pov_meta)

		return pov_meta

	def _convert_movie_meta(self, stremio_meta, imdb_id):
		"""Convert Stremio movie metadata to POV format"""
		try:
			meta_get = stremio_meta.get

			# Basic info
			title = meta_get('name', '')
			if not title:
				return None

			year = meta_get('year', '')
			if year:
				year = str(year)

			# Process genres
			genres_list = meta_get('genres', [])
			genre = ', '.join(self._normalize_genres(genres_list))

			# Process runtime
			runtime = meta_get('runtime', '')
			duration = 0
			if runtime:
				if isinstance(runtime, str):
					# Parse "120 min" or "2h 0m" format
					runtime_lower = runtime.lower()
					if 'min' in runtime_lower:
						try:
							duration = int(runtime_lower.split('min')[0].strip().split()[-1]) * 60
						except Exception:
							pass
					elif 'h' in runtime_lower:
						try:
							parts = runtime_lower.replace('m', '').split('h')
							hours = int(parts[0].strip())
							mins = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 0
							duration = (hours * 60 + mins) * 60
						except Exception:
							pass
				elif isinstance(runtime, (int, float)):
					duration = int(runtime) * 60

			# Process rating
			rating = ''
			imdb_rating = meta_get('imdbRating')
			if imdb_rating:
				try:
					rating = float(imdb_rating)
				except Exception:
					pass

			# Process cast
			cast = []
			cast_list = meta_get('cast', [])
			if cast_list:
				for person in cast_list:
					if isinstance(person, str):
						cast.append({'name': person, 'role': '', 'thumbnail': ''})
					elif isinstance(person, dict):
						cast.append({
							'name': person.get('name', ''),
							'role': person.get('character', ''),
							'thumbnail': person.get('profile', person.get('photo', ''))
						})

			# Process director
			director = ''
			director_data = meta_get('director', [])
			if director_data:
				if isinstance(director_data, list):
					director = director_data[0] if director_data else ''
				elif isinstance(director_data, str):
					director = director_data

			# Process writer
			writer = ''
			writer_data = meta_get('writer', [])
			if writer_data:
				if isinstance(writer_data, list):
					writer = ', '.join(writer_data[:3])
				elif isinstance(writer_data, str):
					writer = writer_data

			# Build POV metadata
			rootname = f'{title} ({year})' if year else title

			pov_meta = {
				'tmdb_id': meta_get('tmdb_id', ''),
				'imdb_id': imdb_id,
				'tvdb_id': 'None',
				'imdbnumber': imdb_id,
				'title': title,
				'original_title': meta_get('original_name', title),
				'english_title': title,
				'alternative_titles': meta_get('alternateTitles', []),
				'year': year,
				'rootname': rootname,
				'tagline': meta_get('tagline', ''),
				'plot': meta_get('description', ''),
				'poster': meta_get('poster', ''),
				'fanart': meta_get('background', meta_get('fanart', '')),
				'tmdblogo': meta_get('logo', ''),
				'clearlogo': meta_get('logo', ''),
				'rating': rating,
				'votes': meta_get('popularity', ''),
				'duration': duration,
				'premiered': meta_get('released', meta_get('releaseInfo', '')),
				'genre': genre,
				'mpaa': meta_get('certification', ''),
				'studio': meta_get('productionCompany', ''),
				'director': director,
				'writer': writer,
				'country': meta_get('country', []),
				'country_codes': [],
				'trailer': meta_get('trailer', ''),
				'all_trailers': meta_get('trailers', []),
				'cast': cast,
				'extra_info': {
					'status': meta_get('status', 'N/A'),
					'collection_name': None,
					'collection_id': None,
					'budget': 'N/A',
					'revenue': 'N/A',
					'homepage': meta_get('website', 'N/A')
				},
				'mediatype': 'movie',
				'meta_language': 'en',
				'stremio_source': True,  # Flag to indicate Stremio source
			}

			# Add default fanart data
			pov_meta.update(DEFAULT_FANART_DATA)

			return pov_meta
		except Exception:
			pass
		return None

	def _convert_tvshow_meta(self, stremio_meta, imdb_id):
		"""Convert Stremio series metadata to POV format"""
		try:
			meta_get = stremio_meta.get

			# Basic info
			title = meta_get('name', '')
			if not title:
				return None

			year = meta_get('year', '')
			if year:
				year = str(year)

			# Process genres
			genres_list = meta_get('genres', [])
			genre = ', '.join(self._normalize_genres(genres_list))

			# Process runtime
			runtime = meta_get('runtime', '')
			duration = 0
			if runtime:
				if isinstance(runtime, str):
					runtime_lower = runtime.lower()
					if 'min' in runtime_lower:
						try:
							duration = int(runtime_lower.split('min')[0].strip().split()[-1]) * 60
						except Exception:
							pass
				elif isinstance(runtime, (int, float)):
					duration = int(runtime) * 60

			# Process rating
			rating = ''
			imdb_rating = meta_get('imdbRating')
			if imdb_rating:
				try:
					rating = float(imdb_rating)
				except Exception:
					pass

			# Process cast
			cast = []
			cast_list = meta_get('cast', [])
			if cast_list:
				for person in cast_list:
					if isinstance(person, str):
						cast.append({'name': person, 'role': '', 'thumbnail': ''})
					elif isinstance(person, dict):
						cast.append({
							'name': person.get('name', ''),
							'role': person.get('character', ''),
							'thumbnail': person.get('profile', person.get('photo', ''))
						})

			# Process director/creator
			director = ''
			creator_data = meta_get('director', meta_get('creator', []))
			if creator_data:
				if isinstance(creator_data, list):
					director = creator_data[0] if creator_data else ''
				elif isinstance(creator_data, str):
					director = creator_data

			# Process videos (episodes) to extract season info
			videos = meta_get('videos', [])
			season_data = []
			total_seasons = 0
			total_aired_eps = len(videos)

			if videos:
				seasons_set = set()
				for video in videos:
					season_num = video.get('season', 0)
					if season_num and season_num > 0:
						seasons_set.add(season_num)

				if seasons_set:
					total_seasons = max(seasons_set)
					for s in sorted(seasons_set):
						season_eps = [v for v in videos if v.get('season') == s]
						season_data.append({
							'season_number': s,
							'episode_count': len(season_eps),
							'name': f'Season {s}',
							'air_date': season_eps[0].get('released', '') if season_eps else ''
						})

			# Build POV metadata
			rootname = f'{title} ({year})' if year else title
			status = meta_get('status', 'N/A')

			pov_meta = {
				'tmdb_id': meta_get('tmdb_id', ''),
				'imdb_id': imdb_id,
				'tvdb_id': meta_get('tvdb_id', 'None'),
				'imdbnumber': imdb_id,
				'title': title,
				'tvshowtitle': title,
				'original_title': meta_get('original_name', title),
				'english_title': title,
				'alternative_titles': meta_get('alternateTitles', []),
				'year': year,
				'rootname': rootname,
				'tagline': meta_get('tagline', ''),
				'plot': meta_get('description', ''),
				'poster': meta_get('poster', ''),
				'fanart': meta_get('background', meta_get('fanart', '')),
				'tmdblogo': meta_get('logo', ''),
				'clearlogo': meta_get('logo', ''),
				'rating': rating,
				'votes': meta_get('popularity', ''),
				'duration': duration,
				'premiered': meta_get('released', meta_get('releaseInfo', '')),
				'genre': genre,
				'mpaa': meta_get('certification', ''),
				'studio': meta_get('productionCompany', ''),
				'director': director,
				'writer': '',
				'country': meta_get('country', []),
				'country_codes': [],
				'trailer': meta_get('trailer', ''),
				'all_trailers': meta_get('trailers', []),
				'cast': cast,
				'extra_info': {
					'status': status,
					'type': meta_get('type', 'N/A'),
					'homepage': meta_get('website', 'N/A'),
					'created_by': director,
					'next_episode_to_air': None,
					'last_episode_to_air': None
				},
				'mediatype': 'tvshow',
				'meta_language': 'en',
				'status': status,
				'total_aired_eps': total_aired_eps,
				'total_seasons': total_seasons,
				'season_data': season_data,
				'stremio_source': True,  # Flag to indicate Stremio source
			}

			# Add default fanart data
			pov_meta.update(DEFAULT_FANART_DATA)

			return pov_meta
		except Exception:
			pass
		return None

	def _normalize_genres(self, genres):
		"""Normalize genre names to standard format"""
		normalized = []
		for genre in genres:
			if isinstance(genre, str):
				genre_lower = genre.lower()
				if genre_lower in GENRE_MAP:
					normalized.append(GENRE_MAP[genre_lower])
				else:
					# Capitalize first letter of each word
					normalized.append(genre.title())
		return normalized

	def get_season_episodes(self, imdb_id, season_num):
		"""
		Get episode metadata for a season from Stremio.

		Args:
			imdb_id: IMDb ID of the show
			season_num: Season number

		Returns:
			list: List of episode metadata dicts
		"""
		if not imdb_id or not imdb_id.startswith('tt'):
			return None

		# Check cache
		cache_key = f'{imdb_id}_{season_num}'
		cached = self.cache.get('season', cache_key)
		if cached:
			return cached

		# Fetch show metadata (includes videos/episodes)
		stremio_meta = self.fetch_meta_parallel('series', imdb_id)
		if not stremio_meta:
			return None

		videos = stremio_meta.get('videos', [])
		if not videos:
			return None

		# Filter to requested season
		season_episodes = [v for v in videos if v.get('season') == season_num]
		if not season_episodes:
			return None

		# Convert to POV episode format
		episodes = []
		for ep in sorted(season_episodes, key=lambda x: x.get('episode', 0)):
			ep_get = ep.get
			episodes.append({
				'title': ep_get('title', ep_get('name', f'Episode {ep_get("episode", "")}')),
				'plot': ep_get('overview', ep_get('description', '')),
				'premiered': ep_get('released', ep_get('firstAired', '')),
				'season': season_num,
				'episode': ep_get('episode', 0),
				'rating': ep_get('rating', ''),
				'votes': '',
				'thumb': ep_get('thumbnail', ''),
				'duration': 0,
				'director': '',
				'writer': '',
				'guest_stars': [],
				'mediatype': 'episode',
				'episode_type': ''
			})

		# Cache result
		if episodes:
			self.cache.set('season', cache_key, episodes)

		return episodes


def stremio_movie_meta(imdb_id, use_cache=True):
	"""
	Convenience function to get movie metadata from Stremio.

	Args:
		imdb_id: IMDb ID
		use_cache: Whether to use cache

	Returns:
		dict: POV-formatted movie metadata or None
	"""
	if not get_setting('stremio.meta.enabled', 'false') == 'true':
		return None

	provider = StremioMetaProvider()
	return provider.get_movie_meta(imdb_id, use_cache=use_cache)


def stremio_tvshow_meta(imdb_id, use_cache=True):
	"""
	Convenience function to get TV show metadata from Stremio.

	Args:
		imdb_id: IMDb ID
		use_cache: Whether to use cache

	Returns:
		dict: POV-formatted TV show metadata or None
	"""
	if not get_setting('stremio.meta.enabled', 'false') == 'true':
		return None

	provider = StremioMetaProvider()
	return provider.get_tvshow_meta(imdb_id, use_cache=use_cache)


def stremio_season_episodes_meta(imdb_id, season_num):
	"""
	Convenience function to get season episodes from Stremio.

	Args:
		imdb_id: IMDb ID of the show
		season_num: Season number

	Returns:
		list: List of episode metadata dicts or None
	"""
	if not get_setting('stremio.meta.enabled', 'false') == 'true':
		return None

	provider = StremioMetaProvider()
	return provider.get_season_episodes(imdb_id, season_num)


def update_addon_meta_support(addon_info, manifest):
	"""
	Update addon info with meta support flag based on manifest.
	Called when adding/updating addons in stremio_manager.

	Args:
		addon_info: Addon info dict to update
		manifest: Addon manifest dict

	Returns:
		dict: Updated addon info
	"""
	resources = manifest.get('resources', [])
	supports_meta = 'meta' in resources or any(
		isinstance(r, dict) and r.get('name') == 'meta' for r in resources
	)
	addon_info['supports_meta'] = supports_meta

	# Also check for specific meta types
	types = manifest.get('types', [])
	addon_info['meta_types'] = types

	return addon_info


def merge_metadata(tmdb_meta, stremio_meta, prefer_stremio=False):
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

	# Create merged result
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

	# Special handling for artwork - use Stremio if TMDB is empty
	if not merged.get('poster') and stremio_meta.get('poster'):
		merged['poster'] = stremio_meta['poster']
	if not merged.get('fanart') and stremio_meta.get('fanart'):
		merged['fanart'] = stremio_meta['fanart']
	if not merged.get('clearlogo') and stremio_meta.get('clearlogo'):
		merged['clearlogo'] = stremio_meta['clearlogo']
		merged['tmdblogo'] = stremio_meta['clearlogo']

	# Mark as having Stremio supplement
	merged['stremio_supplemented'] = True

	return merged


def clear_stremio_meta_cache():
	"""Clear all cached Stremio metadata from SQLite database"""
	try:
		cache = StremioMetaCache()
		cache.delete_all()
	except Exception:
		pass
