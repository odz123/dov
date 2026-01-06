# Stremio Addon Scraper for POV
"""
	Generic Stremio Addon integration for POV
	Supports any Stremio addon that provides stream resources
"""

import re
import requests
from json import loads as jsloads
from fenom import source_utils
from fenom.control import setting as getSetting


# Pre-compiled regex patterns for parsing stream metadata
RE_SEEDERS = re.compile(r'(?:👤|seeders?[:\s]*|peers?[:\s]*)(\d+)', re.I)
RE_SIZE = re.compile(r'((?:\d+[,.]?\d*)\s*(?:GB|GiB|MB|MiB|TB|TiB))', re.I)
RE_QUALITY = re.compile(r'(2160p|4k|uhd|1080p|720p|480p|360p)', re.I)


class source:
	timeout = 8
	priority = 1
	pack_capable = False  # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True

	def __init__(self):
		self.language = ['en']
		self.min_seeders = 0
		self.addons = self._load_addons()

	def _load_addons(self):
		"""Load configured Stremio addons from settings"""
		addons = []
		try:
			import ast
			addons_str = getSetting('stremio.addons', '')
			if addons_str:
				addons = ast.literal_eval(addons_str)
		except:
			pass
		return addons if isinstance(addons, list) else []

	def _parse_stream_info(self, stream):
		"""Parse stream object to extract metadata"""
		info = {
			'hash': None,
			'url': None,
			'name': '',
			'seeders': 0,
			'size': 0,
			'size_str': '',
			'quality': 'SD',
			'provider': '',
			'file_idx': None
		}

		# Get info hash for torrents
		if 'infoHash' in stream:
			info['hash'] = stream['infoHash'].lower()
			if 'fileIdx' in stream:
				info['file_idx'] = stream['fileIdx']

		# Get direct URL if available
		if 'url' in stream:
			info['url'] = stream['url']

		# Get stream name/title for parsing
		name = stream.get('name', '') or stream.get('title', '') or ''
		description = stream.get('description', '') or stream.get('title', '') or ''
		full_text = f"{name}\n{description}"

		# Extract release name - prefer behaviorHints.filename if available
		behavior_hints = stream.get('behaviorHints', {})
		if behavior_hints.get('filename'):
			info['name'] = behavior_hints['filename']
		elif name:
			# Parse name from first line (common Stremio format)
			lines = name.split('\n')
			info['name'] = lines[0].strip() if lines else name

		# Extract seeders
		seeders_match = RE_SEEDERS.search(full_text)
		if seeders_match:
			try:
				info['seeders'] = int(seeders_match.group(1))
			except:
				pass

		# Extract size
		size_match = RE_SIZE.search(full_text)
		if size_match:
			info['size_str'] = size_match.group(1)
			try:
				dsize, isize = source_utils._size(info['size_str'])
				info['size'] = dsize
				info['size_str'] = isize
			except:
				pass

		# Check behaviorHints for size
		if not info['size'] and behavior_hints.get('videoSize'):
			try:
				video_size = int(behavior_hints['videoSize'])
				info['size'] = round(video_size / (1024 * 1024 * 1024), 2)
				info['size_str'] = f"{info['size']:.2f} GB"
			except:
				pass

		# Extract quality
		quality_match = RE_QUALITY.search(full_text) or RE_QUALITY.search(info['name'])
		if quality_match:
			q = quality_match.group(1).lower()
			if q in ('2160p', '4k', 'uhd'):
				info['quality'] = '4K'
			elif q == '1080p':
				info['quality'] = '1080p'
			elif q == '720p':
				info['quality'] = '720p'
			else:
				info['quality'] = 'SD'

		return info

	def _fetch_streams(self, addon_url, media_type, media_id):
		"""Fetch streams from a Stremio addon"""
		streams = []
		try:
			# Clean up addon URL
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			# Build stream endpoint
			endpoint = f"{base_url}/stream/{media_type}/{media_id}.json"

			response = requests.get(
				endpoint,
				timeout=self.timeout,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)

			if response.status_code == 200:
				data = response.json()
				streams = data.get('streams', [])
		except Exception as e:
			source_utils.scraper_error('STREMIO')
		return streams

	def _get_addon_name(self, addon_url):
		"""Extract addon name from URL or fetch from manifest"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			# Try to fetch manifest for name
			response = requests.get(
				f"{base_url}/manifest.json",
				timeout=3,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)
			if response.status_code == 200:
				manifest = response.json()
				return manifest.get('name', 'stremio')
		except:
			pass

		# Fallback: extract from URL
		try:
			from urllib.parse import urlparse
			parsed = urlparse(addon_url)
			return parsed.netloc.split('.')[0]
		except:
			return 'stremio'

	def sources(self, data, hostDict):
		sources = []
		if not data:
			return sources
		if not self.addons:
			return sources

		sources_append = sources.append

		try:
			title = data['tvshowtitle'] if 'tvshowtitle' in data else data['title']
			title = title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' ')
			aliases = data['aliases']
			episode_title = data['title'] if 'tvshowtitle' in data else None
			total_seasons = data['total_seasons'] if 'tvshowtitle' in data else None
			year = data['year']
			imdb = data['imdb']

			if 'tvshowtitle' in data:
				season = data['season']
				episode = data['episode']
				hdlr = 'S%02dE%02d' % (int(season), int(episode))
				media_type = 'series'
				media_id = f"{imdb}:{season}:{episode}"
			else:
				hdlr = year
				media_type = 'movie'
				media_id = imdb

			if 'timeout' in data:
				self.timeout = int(data['timeout'])

			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()

		except:
			source_utils.scraper_error('STREMIO')
			return sources

		# Process each configured addon
		for addon in self.addons:
			try:
				addon_url = addon.get('url', '') if isinstance(addon, dict) else addon
				if not addon_url:
					continue

				addon_name = addon.get('name', '') if isinstance(addon, dict) else ''
				if not addon_name:
					addon_name = self._get_addon_name(addon_url)

				streams = self._fetch_streams(addon_url, media_type, media_id)

				for stream in streams:
					try:
						package, episode_start = None, 0
						stream_info = self._parse_stream_info(stream)

						# Skip if no valid source
						if not stream_info['hash'] and not stream_info['url']:
							continue

						name = source_utils.clean_name(stream_info['name']) if stream_info['name'] else ''

						# Title validation
						if name:
							if not source_utils.check_title(title, aliases, name, hdlr, year):
								if total_seasons is None:
									continue
								valid, last_season = source_utils.filter_show_pack(title, aliases, imdb, year, season, name, total_seasons)
								if not valid:
									valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
									if not valid:
										continue
									else:
										package = 'season'
								else:
									package = 'show'

							name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
							if source_utils.remove_lang(name_info, check_foreign_audio):
								continue
							if undesirables and source_utils.remove_undesirables(name_info, undesirables):
								continue
						else:
							name_info = ''

						# Check seeders
						if self.min_seeders > stream_info['seeders']:
							continue

						# Get quality
						quality = stream_info['quality']
						if name_info:
							detected_quality, info = source_utils.get_release_quality(name_info, stream_info.get('url', ''))
							if detected_quality != 'SD':
								quality = detected_quality
						else:
							info = []

						# Add size to info
						if stream_info['size_str']:
							info.insert(0, stream_info['size_str'])

						info_str = ' | '.join(info) if info else ''

						# Build source URL
						if stream_info['hash']:
							url = 'magnet:?xt=urn:btih:%s&dn=%s' % (stream_info['hash'], name or stream_info['hash'])
							is_direct = False
							is_debridonly = True
						else:
							url = stream_info['url']
							is_direct = True
							is_debridonly = False

						item = {
							'source': 'torrent' if stream_info['hash'] else 'direct',
							'language': 'en',
							'direct': is_direct,
							'debridonly': is_debridonly,
							'provider': f"stremio_{addon_name}",
							'url': url,
							'name': name or url,
							'name_info': name_info,
							'quality': quality,
							'info': info_str,
							'size': stream_info['size'],
							'seeders': stream_info['seeders']
						}

						if stream_info['hash']:
							item['hash'] = stream_info['hash']

						if stream_info['file_idx'] is not None:
							item['file_idx'] = stream_info['file_idx']

						if package:
							item['package'] = package
							item['true_size'] = True
						if package == 'show':
							item['last_season'] = last_season
						if episode_start:
							item['episode_start'] = episode_start
							item['episode_end'] = episode_end

						sources_append(item)

					except:
						source_utils.scraper_error('STREMIO')
						continue

			except:
				source_utils.scraper_error('STREMIO')
				continue

		return sources
