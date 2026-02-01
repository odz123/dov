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
	- Full Stremio SDK meta format support:
	  - Meta links array (actors, directors, writers, genres)
	  - Trailers with YouTube playback
	  - behaviorHints.defaultVideoId for stream fetching
	  - Video available flag for episode filtering
	  - Video embedded streams support
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
		"""Fetch JSON from URL"""
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
			if not addon_url:
				return
			# Check idPrefixes filtering (per-resource meta idPrefixes, then manifest-level)
			meta_id_prefixes = addon.get('meta_id_prefixes', [])
			id_prefixes = meta_id_prefixes if meta_id_prefixes else addon.get('id_prefixes', [])
			if id_prefixes and not any(media_id.startswith(p) for p in id_prefixes):
				return
			# Check per-resource meta types filtering
			meta_types = addon.get('meta_types', [])
			if meta_types and media_type not in meta_types:
				return
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
			if meta.get('links'): score += 5  # Modern SDK links
			if meta.get('trailers'): score += 3  # YouTube trailers
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

	def _parse_links(self, links):
		"""Parse Stremio SDK links array to extract cast, directors, writers, genres.
		Links array is the modern way Stremio provides this data (replacing deprecated
		cast/director/genres arrays). Each link has: name, category, url."""
		result = {'cast': [], 'directors': [], 'writers': [], 'genres': []}
		if not links or not isinstance(links, list):
			return result
		for link in links:
			if not isinstance(link, dict):
				continue
			name = link.get('name', '')
			category = link.get('category', '')
			if not name or not category:
				continue
			cat_lower = category.lower()
			if cat_lower == 'actor':
				result['cast'].append({'name': name, 'role': '', 'thumbnail': ''})
			elif cat_lower == 'director':
				result['directors'].append(name)
			elif cat_lower == 'writer':
				result['writers'].append(name)
			elif cat_lower == 'genre':
				result['genres'].append(name)
		return result

	def _parse_trailers(self, trailers):
		"""Parse Stremio SDK trailers array. Each trailer has: source (YouTube ID), type.
		Returns (first_trailer_url, all_trailers_list)."""
		if not trailers or not isinstance(trailers, list):
			return '', []
		all_trailers = []
		first_url = ''
		for trailer in trailers:
			if not isinstance(trailer, dict):
				continue
			source = trailer.get('source', '')
			trailer_type = trailer.get('type', 'Trailer')
			if source:
				yt_url = f"plugin://plugin.video.youtube/play/?video_id={source}"
				all_trailers.append({
					'source': source,
					'type': trailer_type,
					'url': yt_url
				})
				if not first_url:
					first_url = yt_url
		return first_url, all_trailers

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

			# Process genres - from both 'genres' array and 'links' array
			genres_list = list(meta_get('genres', []) or [])
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
			rating = 0
			imdb_rating = meta_get('imdbRating')
			if imdb_rating:
				try:
					rating = float(imdb_rating)
				except Exception:
					rating = 0

			# Process votes
			votes = 0
			popularity = meta_get('popularity', 0)
			if popularity:
				try:
					votes = int(popularity)
				except (ValueError, TypeError):
					votes = 0

			# Process cast from deprecated 'cast' array
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

			# Process director from deprecated 'director' array
			director = ''
			director_data = meta_get('director', [])
			if director_data:
				if isinstance(director_data, list):
					director = director_data[0] if director_data else ''
				elif isinstance(director_data, str):
					director = director_data

			# Process writer from deprecated 'writer' array
			writer = ''
			writer_data = meta_get('writer', [])
			if writer_data:
				if isinstance(writer_data, list):
					writer = ', '.join(writer_data[:3])
				elif isinstance(writer_data, str):
					writer = writer_data

			# Parse links array (modern Stremio SDK way to provide cast/director/writer/genre)
			links_data = self._parse_links(meta_get('links', []))
			if links_data['cast'] and not cast:
				cast = links_data['cast']
			if links_data['directors'] and not director:
				director = links_data['directors'][0]
			if links_data['writers'] and not writer:
				writer = ', '.join(links_data['writers'][:3])
			if links_data['genres'] and not genres_list:
				genre = ', '.join(self._normalize_genres(links_data['genres']))

			# Parse trailers (YouTube IDs)
			trailers_raw = meta_get('trailers', [])
			trailer_url, all_trailers = self._parse_trailers(trailers_raw)
			# Fallback to legacy 'trailer' field
			if not trailer_url:
				trailer_url = meta_get('trailer', '')

			# Extract behaviorHints
			behavior_hints = meta_get('behaviorHints', {}) or {}
			default_video_id = behavior_hints.get('defaultVideoId', '')

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
				'votes': votes,
				'duration': duration,
				'premiered': meta_get('released', meta_get('releaseInfo', '')),
				'genre': genre,
				'mpaa': meta_get('certification', ''),
				'studio': meta_get('productionCompany', ''),
				'director': director,
				'writer': writer,
				'country': meta_get('country', []),
				'country_codes': [],
				'trailer': trailer_url,
				'all_trailers': all_trailers,
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

			# Store defaultVideoId for stream fetching (SDK behaviorHints)
			if default_video_id:
				pov_meta['default_video_id'] = default_video_id

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

			# Process genres - from both 'genres' array and 'links' array
			genres_list = list(meta_get('genres', []) or [])
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
			rating = 0
			imdb_rating = meta_get('imdbRating')
			if imdb_rating:
				try:
					rating = float(imdb_rating)
				except Exception:
					rating = 0

			# Process votes
			votes = 0
			popularity = meta_get('popularity', 0)
			if popularity:
				try:
					votes = int(popularity)
				except (ValueError, TypeError):
					votes = 0

			# Process cast from deprecated 'cast' array
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

			# Process director/creator from deprecated arrays
			director = ''
			creator_data = meta_get('director', meta_get('creator', []))
			if creator_data:
				if isinstance(creator_data, list):
					director = creator_data[0] if creator_data else ''
				elif isinstance(creator_data, str):
					director = creator_data

			# Process writer from deprecated array
			writer = ''
			writer_data = meta_get('writer', [])
			if writer_data:
				if isinstance(writer_data, list):
					writer = ', '.join(writer_data[:3])
				elif isinstance(writer_data, str):
					writer = writer_data

			# Parse links array (modern Stremio SDK way to provide cast/director/writer/genre)
			links_data = self._parse_links(meta_get('links', []))
			if links_data['cast'] and not cast:
				cast = links_data['cast']
			if links_data['directors'] and not director:
				director = links_data['directors'][0]
			if links_data['writers'] and not writer:
				writer = ', '.join(links_data['writers'][:3])
			if links_data['genres'] and not genres_list:
				genre = ', '.join(self._normalize_genres(links_data['genres']))

			# Parse trailers (YouTube IDs)
			trailers_raw = meta_get('trailers', [])
			trailer_url, all_trailers = self._parse_trailers(trailers_raw)
			if not trailer_url:
				trailer_url = meta_get('trailer', '')

			# Extract behaviorHints
			behavior_hints = meta_get('behaviorHints', {}) or {}
			default_video_id = behavior_hints.get('defaultVideoId', '')

			# Process videos (episodes) to extract season info
			videos = meta_get('videos', [])
			season_data = []
			total_seasons = 0
			# Filter to only available episodes (per SDK: video.available flag)
			available_videos = [v for v in videos if v.get('available', True)]
			total_aired_eps = len(available_videos)

			if available_videos:
				seasons_set = set()
				for video in available_videos:
					season_num = video.get('season', 0)
					if season_num and season_num > 0:
						seasons_set.add(season_num)

				if seasons_set:
					total_seasons = max(seasons_set)
					for s in sorted(seasons_set):
						season_eps = [v for v in available_videos if v.get('season') == s]
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
				'votes': votes,
				'duration': duration,
				'premiered': meta_get('released', meta_get('releaseInfo', '')),
				'genre': genre,
				'mpaa': meta_get('certification', ''),
				'studio': meta_get('productionCompany', ''),
				'director': director,
				'writer': writer,
				'country': meta_get('country', []),
				'country_codes': [],
				'trailer': trailer_url,
				'all_trailers': all_trailers,
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

			# Store defaultVideoId for stream fetching (SDK behaviorHints)
			if default_video_id:
				pov_meta['default_video_id'] = default_video_id

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

		# Filter to requested season, respecting video.available flag
		season_episodes = [v for v in videos if v.get('season') == season_num and v.get('available', True)]
		if not season_episodes:
			return None

		# Convert to POV episode format
		episodes = []
		for ep in sorted(season_episodes, key=lambda x: x.get('episode', 0)):
			ep_get = ep.get
			ep_rating = 0
			ep_rating_val = ep_get('rating', 0)
			if ep_rating_val:
				try:
					ep_rating = float(ep_rating_val)
				except (ValueError, TypeError):
					ep_rating = 0
			ep_data = {
				'title': ep_get('title', ep_get('name', f'Episode {ep_get("episode", "")}')),
				'plot': ep_get('overview', ep_get('description', '')),
				'premiered': ep_get('released', ep_get('firstAired', '')),
				'season': season_num,
				'episode': ep_get('episode', 0),
				'rating': ep_rating,
				'votes': 0,
				'thumb': ep_get('thumbnail', ''),
				'duration': 0,
				'director': '',
				'writer': '',
				'guest_stars': [],
				'mediatype': 'episode',
				'episode_type': ''
			}

			# Per Stremio SDK: video.id is used for stream requests
			video_id = ep_get('id', '')
			if video_id:
				ep_data['stremio_video_id'] = video_id

			# Per Stremio SDK: video.streams contains exclusive embedded streams
			video_streams = ep_get('streams', [])
			if video_streams:
				ep_data['stremio_embedded_streams'] = video_streams

			episodes.append(ep_data)

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
