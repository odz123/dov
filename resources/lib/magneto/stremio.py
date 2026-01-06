# Stremio Addon Scraper for POV
"""
	Enhanced Stremio Addon integration for POV
	Supports any Stremio addon that provides stream resources
	Features:
	- Direct URL playback with proxyHeaders support
	- Debrid-integrated addon detection
	- Multiple stream types (torrent, direct, YouTube)
	- Subtitle integration
	- bingeGroup for autoplay optimization
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
RE_CODEC = re.compile(r'(hevc|h\.?265|x265|av1|h\.?264|x264)', re.I)
RE_HDR = re.compile(r'(hdr10\+?|dolby.?vision|dv|hlg)', re.I)
RE_AUDIO = re.compile(r'(atmos|truehd|dts-?hd|dd[p+]?5\.1|aac|eac3)', re.I)
RE_DEBRID_URL = re.compile(r'(real-?debrid|realdebrid|alldebrid|premiumize|torbox|debrid-link|easydebrid|offcloud)', re.I)


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
		self.fetch_subtitles = getSetting('stremio.subtitles', 'true') == 'true'
		self.prefer_debrid_direct = getSetting('stremio.debrid_direct', 'true') == 'true'

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

	def _parse_stream_info(self, stream, addon_info=None):
		"""Parse stream object to extract metadata with enhanced support"""
		info = {
			'hash': None,
			'url': None,
			'name': '',
			'seeders': 0,
			'size': 0,
			'size_str': '',
			'quality': 'SD',
			'provider': '',
			'file_idx': None,
			'is_debrid_resolved': False,
			'proxy_headers': None,
			'subtitles': [],
			'binge_group': None,
			'stream_type': 'unknown',
			'youtube_id': None,
			'external_url': None,
			'codec': '',
			'hdr': '',
			'audio': ''
		}

		# Determine stream type and extract source
		if 'infoHash' in stream:
			info['hash'] = stream['infoHash'].lower()
			info['stream_type'] = 'torrent'
			if 'fileIdx' in stream:
				info['file_idx'] = stream['fileIdx']

		if 'url' in stream:
			info['url'] = stream['url']
			if not info['hash']:
				info['stream_type'] = 'direct'
			# Check if URL is a debrid-resolved link
			if RE_DEBRID_URL.search(stream['url']):
				info['is_debrid_resolved'] = True
				info['stream_type'] = 'debrid_direct'

		if 'ytId' in stream:
			info['youtube_id'] = stream['ytId']
			info['stream_type'] = 'youtube'
			info['url'] = f"plugin://plugin.video.youtube/play/?video_id={stream['ytId']}"

		if 'externalUrl' in stream:
			info['external_url'] = stream['externalUrl']
			info['stream_type'] = 'external'

		# Get stream name/title for parsing
		name = stream.get('name', '') or stream.get('title', '') or ''
		description = stream.get('description', '') or stream.get('title', '') or ''
		full_text = f"{name}\n{description}"

		# Extract behavior hints
		behavior_hints = stream.get('behaviorHints', {})

		# Extract proxy headers for authenticated streams
		if 'proxyHeaders' in behavior_hints:
			proxy_headers = behavior_hints['proxyHeaders']
			if proxy_headers.get('request'):
				info['proxy_headers'] = proxy_headers['request']

		# Extract binge group for autoplay optimization
		if 'bingeGroup' in behavior_hints:
			info['binge_group'] = behavior_hints['bingeGroup']

		# Extract release name - prefer behaviorHints.filename if available
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

		# Check behaviorHints for size (videoSize in bytes)
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

		# Extract codec info
		codec_match = RE_CODEC.search(full_text) or RE_CODEC.search(info['name'])
		if codec_match:
			info['codec'] = codec_match.group(1).upper()

		# Extract HDR info
		hdr_match = RE_HDR.search(full_text) or RE_HDR.search(info['name'])
		if hdr_match:
			info['hdr'] = hdr_match.group(1).upper()

		# Extract audio info
		audio_match = RE_AUDIO.search(full_text) or RE_AUDIO.search(info['name'])
		if audio_match:
			info['audio'] = audio_match.group(1).upper()

		# Extract subtitles if available
		if 'subtitles' in stream:
			info['subtitles'] = stream['subtitles']

		return info

	def _fetch_streams(self, addon_url, media_type, media_id, addon_info=None):
		"""Fetch streams from a Stremio addon with enhanced error handling"""
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
		except requests.exceptions.Timeout:
			source_utils.scraper_error('STREMIO_TIMEOUT')
		except requests.exceptions.ConnectionError:
			source_utils.scraper_error('STREMIO_CONNECTION')
		except Exception as e:
			source_utils.scraper_error('STREMIO')
		return streams

	def _fetch_subtitles(self, addon_url, media_type, media_id):
		"""Fetch subtitles from a Stremio addon"""
		subtitles = []
		if not self.fetch_subtitles:
			return subtitles
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			endpoint = f"{base_url}/subtitles/{media_type}/{media_id}.json"

			response = requests.get(
				endpoint,
				timeout=5,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)

			if response.status_code == 200:
				data = response.json()
				subtitles = data.get('subtitles', [])
		except:
			pass
		return subtitles

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

	def _is_debrid_configured_addon(self, addon):
		"""Check if addon has debrid configuration in URL"""
		addon_url = addon.get('url', '') if isinstance(addon, dict) else addon
		config_url = addon.get('config_url', '') if isinstance(addon, dict) else ''

		# Check both URL and config URL for debrid patterns
		check_url = config_url or addon_url
		debrid_patterns = [
			'realdebrid=', 'rd=', 'debridKey=',
			'premiumize=', 'pm=',
			'alldebrid=', 'ad=',
			'torbox=', 'tb=',
			'offcloud=', 'oc=',
			'debrid-link=', 'dl=',
			'easydebrid=', 'ed='
		]
		return any(pattern in check_url.lower() for pattern in debrid_patterns)

	def _build_source_item(self, stream_info, addon_name, title, aliases, hdlr, year,
						   episode_title, total_seasons, season, undesirables, check_foreign_audio):
		"""Build a source item from parsed stream info"""
		package, episode_start, episode_end, last_season = None, 0, 0, 0

		# Skip if no valid source
		if not stream_info['hash'] and not stream_info['url'] and not stream_info['youtube_id']:
			return None

		# Skip external URLs (Netflix, etc.) - can't play directly
		if stream_info['stream_type'] == 'external':
			return None

		name = source_utils.clean_name(stream_info['name']) if stream_info['name'] else ''

		# Title validation
		if name:
			if not source_utils.check_title(title, aliases, name, hdlr, year):
				if total_seasons is None:
					return None
				valid, last_season = source_utils.filter_show_pack(title, aliases, '', year, season, name, total_seasons)
				if not valid:
					valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
					if not valid:
						return None
					else:
						package = 'season'
				else:
					package = 'show'

			name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
			if source_utils.remove_lang(name_info, check_foreign_audio):
				return None
			if undesirables and source_utils.remove_undesirables(name_info, undesirables):
				return None
		else:
			name_info = ''

		# Check seeders for torrents
		if stream_info['stream_type'] == 'torrent' and self.min_seeders > stream_info['seeders']:
			return None

		# Get quality
		quality = stream_info['quality']
		if name_info:
			detected_quality, info = source_utils.get_release_quality(name_info, stream_info.get('url', ''))
			if detected_quality != 'SD':
				quality = detected_quality
		else:
			info = []

		# Add codec/HDR/audio info
		if stream_info['codec']:
			info.append(f"[B]{stream_info['codec']}[/B]")
		if stream_info['hdr']:
			info.append(f"[B]{stream_info['hdr']}[/B]")
		if stream_info['audio']:
			info.append(stream_info['audio'])

		# Add size to info
		if stream_info['size_str']:
			info.insert(0, stream_info['size_str'])

		info_str = ' | '.join(info) if info else ''

		# Build source URL and determine type
		if stream_info['stream_type'] == 'torrent':
			url = 'magnet:?xt=urn:btih:%s&dn=%s' % (stream_info['hash'], name or stream_info['hash'])
			source_type = 'torrent'
			is_direct = False
			is_debridonly = True
		elif stream_info['stream_type'] == 'youtube':
			url = stream_info['url']
			source_type = 'youtube'
			is_direct = True
			is_debridonly = False
		elif stream_info['is_debrid_resolved']:
			url = stream_info['url']
			source_type = 'debrid_direct'
			is_direct = True
			is_debridonly = False
		else:
			url = stream_info['url']
			source_type = 'direct'
			is_direct = True
			is_debridonly = False

		item = {
			'source': source_type,
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

		# Add hash for torrents
		if stream_info['hash']:
			item['hash'] = stream_info['hash']

		# Add file index for multi-file torrents
		if stream_info['file_idx'] is not None:
			item['file_idx'] = stream_info['file_idx']

		# Add proxy headers for authenticated streams
		if stream_info['proxy_headers']:
			item['proxy_headers'] = stream_info['proxy_headers']

		# Add binge group for autoplay optimization
		if stream_info['binge_group']:
			item['binge_group'] = stream_info['binge_group']

		# Add subtitles if available
		if stream_info['subtitles']:
			item['stremio_subtitles'] = stream_info['subtitles']

		# Add debrid resolved flag
		if stream_info['is_debrid_resolved']:
			item['debrid_resolved'] = True

		# Add pack info
		if package:
			item['package'] = package
			item['true_size'] = True
		if package == 'show':
			item['last_season'] = last_season
		if episode_start:
			item['episode_start'] = episode_start
			item['episode_end'] = episode_end

		return item

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
				season = None
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

		# Sort addons - prefer debrid-configured addons if setting enabled
		sorted_addons = self.addons
		if self.prefer_debrid_direct:
			debrid_addons = [a for a in self.addons if self._is_debrid_configured_addon(a)]
			other_addons = [a for a in self.addons if not self._is_debrid_configured_addon(a)]
			sorted_addons = debrid_addons + other_addons

		# Process each configured addon
		for addon in sorted_addons:
			try:
				addon_url = addon.get('url', '') if isinstance(addon, dict) else addon
				config_url = addon.get('config_url', '') if isinstance(addon, dict) else ''
				if not addon_url:
					continue

				# Use config URL for fetching if available (has debrid settings)
				fetch_url = config_url if config_url else addon_url

				addon_name = addon.get('name', '') if isinstance(addon, dict) else ''
				if not addon_name:
					addon_name = self._get_addon_name(addon_url)

				addon_info = addon if isinstance(addon, dict) else {'url': addon}
				is_debrid_addon = self._is_debrid_configured_addon(addon)

				streams = self._fetch_streams(fetch_url, media_type, media_id, addon_info)

				for stream in streams:
					try:
						stream_info = self._parse_stream_info(stream, addon_info)

						# If this is a debrid-configured addon and we got a direct URL,
						# mark it as debrid resolved
						if is_debrid_addon and stream_info['url'] and not stream_info['hash']:
							stream_info['is_debrid_resolved'] = True
							stream_info['stream_type'] = 'debrid_direct'

						item = self._build_source_item(
							stream_info, addon_name, title, aliases, hdlr, year,
							episode_title, total_seasons, season, undesirables, check_foreign_audio
						)

						if item:
							sources_append(item)

					except:
						source_utils.scraper_error('STREMIO')
						continue

			except:
				source_utils.scraper_error('STREMIO')
				continue

		return sources

	def sources_packs(self, data, hostDict, search_series=False, total_seasons=None, bypass_filter=False):
		"""Handle season and show packs - delegate to sources() which handles packs"""
		return self.sources(data, hostDict)
