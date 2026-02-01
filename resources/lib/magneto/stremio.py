# Stremio Addon Scraper for POV
"""
	Full Stremio SDK addon integration for POV
	Supports any Stremio addon that provides stream resources
	Features:
	- Direct URL playback with proxyHeaders support
	- Debrid-integrated addon detection
	- Multiple stream types (torrent, direct, YouTube, usenet, external)
	- Subtitle integration
	- bingeGroup for autoplay optimization
	- HTTP client via shared http_client module
	- Parallel addon scraping for performance
	- Per-resource type and idPrefixes filtering from manifest
	- fileMustInclude regex matching for archive/torrent files
	- countryWhitelist/countryBlacklist geo-filtering
	- externalUrl support (opens in system browser)
"""

import re
from threading import Thread, Lock
from fenom import source_utils
from fenom.control import setting as getSetting
from modules import http_client

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
		except Exception:
			pass
		return addons if isinstance(addons, list) else []

	def _parse_stream_info(self, stream, addon_info=None):
		"""Parse stream object to extract metadata with full SDK support"""
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
			'file_must_include': None,
			'is_debrid_resolved': False,
			'proxy_headers': None,
			'subtitles': [],
			'binge_group': None,
			'stream_type': 'unknown',
			'youtube_id': None,
			'external_url': None,
			'codec': '',
			'hdr': '',
			'audio': '',
			'trackers': [],
			'not_web_ready': False,
			'country_whitelist': [],
			'country_blacklist': []
		}

		# Determine stream type and extract source
		if 'infoHash' in stream:
			info['hash'] = stream['infoHash'].lower()
			info['stream_type'] = 'torrent'
			if 'fileIdx' in stream:
				info['file_idx'] = stream['fileIdx']
			# fileMustInclude: regex pattern to match video files in archives/torrents
			if 'fileMustInclude' in stream:
				info['file_must_include'] = stream['fileMustInclude']

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

		# Handle nzbUrl for usenet streams
		if 'nzbUrl' in stream and not info['url'] and not info['hash']:
			info['url'] = stream['nzbUrl']
			info['stream_type'] = 'usenet'

		if 'externalUrl' in stream:
			info['external_url'] = stream['externalUrl']
			info['stream_type'] = 'external'

		# Extract tracker URLs from sources field (for torrent peer discovery)
		if 'sources' in stream and isinstance(stream['sources'], list):
			for src in stream['sources']:
				if isinstance(src, str):
					if src.startswith('tracker:'):
						info['trackers'].append(src[8:])
					elif src.startswith('dht:'):
						pass  # DHT nodes not needed for magnet links

		# Get stream name/title for parsing
		stream_name = stream.get('name', '') or ''
		stream_title = stream.get('title', '') or ''
		description = stream.get('description', '') or ''
		full_text = f"{stream_name}\n{stream_title}\n{description}"

		# Extract behavior hints
		behavior_hints = stream.get('behaviorHints', {}) or {}

		# Extract proxy headers for authenticated streams
		if 'proxyHeaders' in behavior_hints:
			proxy_headers = behavior_hints['proxyHeaders']
			if proxy_headers.get('request'):
				info['proxy_headers'] = proxy_headers['request']

		# Extract notWebReady flag (stream requires special handling)
		if behavior_hints.get('notWebReady'):
			info['not_web_ready'] = True

		# Extract binge group for autoplay optimization
		if 'bingeGroup' in behavior_hints:
			info['binge_group'] = behavior_hints['bingeGroup']

		# Extract geo-filtering hints (ISO 3166-1 alpha-3 country codes)
		if 'countryWhitelist' in behavior_hints:
			info['country_whitelist'] = behavior_hints['countryWhitelist']
		if 'countryBlacklist' in behavior_hints:
			info['country_blacklist'] = behavior_hints['countryBlacklist']

		# Extract release name - smart detection between 'name' and 'title' fields
		# Different Stremio addons use these fields inconsistently:
		# - Torrentio: name="Torrentio\n4K", title="Movie.2023.1080p.WEB\n👤 50"
		# - Some addons: name="Movie.2023.1080p.WEB", title="HD Stream" or no title
		# Strategy: Use behaviorHints.filename if available, else pick the field
		# that looks most like a release name (contains quality/codec markers)
		# Per SDK spec: filename can appear in behaviorHints or at top level
		bh_filename = behavior_hints.get('filename') or stream.get('filename')
		if bh_filename:
			info['name'] = bh_filename
		else:
			# Get first line from both fields
			name_line = stream_name.split('\n')[0].strip() if stream_name else ''
			title_line = stream_title.split('\n')[0].strip() if stream_title else ''

			# Check which one looks more like a release name
			def looks_like_release_name(text):
				"""Score how much a string looks like a release name"""
				if not text:
					return 0
				text_lower = text.lower()
				score = 0
				# Quality markers
				if any(q in text_lower for q in ('2160p', '1080p', '720p', '480p', '4k', 'uhd')):
					score += 3
				# Source markers
				if any(s in text_lower for s in ('web-dl', 'webrip', 'bluray', 'bdrip', 'hdtv', 'hdrip', 'dvdrip')):
					score += 3
				# Codec markers
				if any(c in text_lower for c in ('x264', 'x265', 'hevc', 'h264', 'h265', 'avc', 'av1')):
					score += 2
				# Audio markers
				if any(a in text_lower for a in ('aac', 'dts', 'atmos', 'truehd', 'dd5', 'ac3')):
					score += 1
				# Contains dots/dashes typical of release names
				if text.count('.') >= 3 or text.count('-') >= 2:
					score += 2
				# Negative: looks like addon name (short, common addon names)
				addon_names = ('torrentio', 'comet', 'mediafusion', 'annatar', 'stremio', 'debrid', 'cached', 'instant')
				if any(a in text_lower for a in addon_names) and len(text) < 30:
					score -= 3
				return score

			name_score = looks_like_release_name(name_line)
			title_score = looks_like_release_name(title_line)

			# Pick the one with higher score, prefer title on tie (Torrentio convention)
			if name_score > title_score:
				info['name'] = name_line
			elif title_line:
				info['name'] = title_line
			elif name_line:
				info['name'] = name_line

		# Extract seeders
		seeders_match = RE_SEEDERS.search(full_text)
		if seeders_match:
			try:
				info['seeders'] = int(seeders_match.group(1))
			except Exception:
				pass

		# Extract size
		size_match = RE_SIZE.search(full_text)
		if size_match:
			info['size_str'] = size_match.group(1)
			try:
				dsize, isize = source_utils._size(info['size_str'])
				info['size'] = dsize
				info['size_str'] = isize
			except Exception:
				pass

		# Check behaviorHints for size (videoSize in bytes), then top-level per SDK spec
		if not info['size']:
			video_size = behavior_hints.get('videoSize') or stream.get('videoSize')
			if video_size:
				try:
					video_size = int(video_size)
					info['size'] = round(video_size / (1024 * 1024 * 1024), 2)
					info['size_str'] = f"{info['size']:.2f} GB"
				except Exception:
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

		# Extract videoHash for subtitle matching (per SDK spec: top-level or behaviorHints)
		video_hash = behavior_hints.get('videoHash') or stream.get('videoHash')
		if video_hash:
			info['video_hash'] = video_hash

		# Extract subtitles if available
		if 'subtitles' in stream:
			info['subtitles'] = stream['subtitles']

		return info

	def _fetch_streams(self, addon_url, media_type, media_id, addon_info=None):
		"""Fetch streams from a Stremio addon with error handling"""
		def error_callback(msg):
			source_utils.scraper_error('STREMIO: %s - %s' % (addon_url, msg))

		return http_client.fetch_streams(
			addon_url, media_type, media_id,
			timeout=self.timeout,
			error_callback=error_callback
		)

	def _fetch_subtitles(self, addon_url, media_type, media_id):
		"""Fetch subtitles from a Stremio addon"""
		if not self.fetch_subtitles:
			return []
		return http_client.fetch_subtitles(addon_url, media_type, media_id, timeout=5)

	def _get_addon_name(self, addon_url):
		"""Extract addon name from URL or fetch from manifest"""
		manifest = http_client.fetch_manifest(addon_url, timeout=3)
		if manifest:
			return manifest.get('name', 'stremio')

		# Fallback: extract from URL
		try:
			from urllib.parse import urlparse
			parsed = urlparse(addon_url)
			return parsed.netloc.split('.')[0]
		except Exception:
			return 'stremio'

	def _is_debrid_configured_addon(self, addon):
		"""Check if addon has debrid configuration in URL"""
		addon_url = addon.get('url', '') if isinstance(addon, dict) else addon
		config_url = addon.get('config_url', '') if isinstance(addon, dict) else ''

		# Check both URL and config URL for debrid patterns
		check_url = config_url or addon_url
		debrid_patterns = [
			'realdebrid=', 'rd=', 'debridkey=',
			'premiumize=', 'pm=',
			'alldebrid=', 'ad=',
			'torbox=', 'tb=',
			'offcloud=', 'oc=',
			'debrid-link=', 'dl=',
			'easydebrid=', 'ed='
		]
		return any(pattern in check_url.lower() for pattern in debrid_patterns)

	def _check_id_prefix_match(self, addon_info, media_id):
		"""Check if addon supports the given media ID based on idPrefixes filtering.
		Per Stremio SDK: if idPrefixes is set, only handle IDs with those prefixes.
		Returns True if the addon should handle this ID (or has no prefix filter)."""
		if not isinstance(addon_info, dict):
			return True
		# Check per-resource stream idPrefixes first, then manifest-level
		stream_id_prefixes = addon_info.get('stream_id_prefixes', [])
		id_prefixes = stream_id_prefixes if stream_id_prefixes else addon_info.get('id_prefixes', [])
		if not id_prefixes:
			return True  # No prefix filter means accept all IDs
		# Check if the media ID starts with any of the allowed prefixes
		return any(media_id.startswith(prefix) for prefix in id_prefixes)

	def _get_addon_stream_types(self, addon_info, media_type):
		"""Determine which content types to query for streams from this addon.
		Respects per-resource type filtering from manifest resource objects."""
		addon_types = addon_info.get('types', []) if isinstance(addon_info, dict) else []
		# Check if addon stores per-resource type info (from manifest resource objects)
		stream_types = addon_info.get('stream_types', []) if isinstance(addon_info, dict) else []
		# Use stream-specific types if available, otherwise fall back to manifest-level types
		effective_types = stream_types if stream_types else addon_types
		fetch_types = [media_type]
		if effective_types and media_type not in effective_types:
			alt_types = [t for t in effective_types if t in ('anime', 'tv', 'channel', 'other')]
			fetch_types = alt_types if alt_types else [media_type]
		return fetch_types

	def _build_source_item(self, stream_info, addon_name, title, aliases, hdlr, year,
						   episode_title, total_seasons, season, undesirables, check_foreign_audio):
		"""Build a source item from parsed stream info"""
		package, episode_start, episode_end, last_season = None, 0, 0, 0

		# Skip if no valid source
		if not stream_info['hash'] and not stream_info['url'] and not stream_info['youtube_id'] and not stream_info.get('external_url'):
			return None

		name = source_utils.clean_name(stream_info['name']) if stream_info['name'] else ''

		# IMPORTANT: Stremio addons filter by IMDB ID at the API level, so the content
		# returned is already for the correct movie/show. We should NOT do strict title
		# validation that might incorrectly reject valid streams. We only need to:
		# 1. Detect packs for proper handling
		# 2. Apply user's language/undesirable filters
		# 3. Skip streams that are clearly for different content (if detectable)

		is_pack = False
		if name:
			# Check if this is a pack (season/show pack) by trying pack filters first
			if total_seasons is not None:
				valid, last_season = source_utils.filter_show_pack(title, aliases, '', year, season, name, total_seasons)
				if valid:
					package = 'show'
					is_pack = True
				else:
					valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
					if valid:
						package = 'season'
						is_pack = True

			# Extract name_info for quality detection and filtering
			name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)

			# Apply user's language filter
			if source_utils.remove_lang(name_info, check_foreign_audio):
				return None

			# Apply user's undesirable filter
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
			from urllib.parse import quote_plus
			url = 'magnet:?xt=urn:btih:%s&dn=%s' % (stream_info['hash'], name or stream_info['hash'])
			# Add tracker URLs from sources field for peer discovery
			for tracker in stream_info.get('trackers', []):
				url += '&tr=%s' % quote_plus(tracker)
			source_type = 'torrent'
			is_direct = False
			is_debridonly = True
		elif stream_info['stream_type'] == 'usenet':
			url = stream_info['url']
			source_type = 'usenet'
			is_direct = False
			is_debridonly = True
		elif stream_info['stream_type'] == 'youtube':
			url = stream_info['url']
			source_type = 'youtube'
			is_direct = True
			is_debridonly = False
		elif stream_info['stream_type'] == 'external':
			# externalUrl - opens in system browser (for Netflix, etc.)
			url = stream_info['external_url']
			source_type = 'external'
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

		# Add hash for torrents (skip for debrid-direct - prevents incorrect torrent routing)
		if stream_info['hash'] and not stream_info['is_debrid_resolved']:
			item['hash'] = stream_info['hash']

		# Add file index for multi-file torrents
		if stream_info['file_idx'] is not None:
			item['file_idx'] = stream_info['file_idx']

		# Add fileMustInclude regex for matching video files in archives/torrents
		if stream_info.get('file_must_include'):
			item['file_must_include'] = stream_info['file_must_include']

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

		# Add notWebReady flag for streams requiring special handling
		if stream_info.get('not_web_ready'):
			item['not_web_ready'] = True

		# Add videoHash for subtitle matching (per SDK spec)
		if stream_info.get('video_hash'):
			item['video_hash'] = stream_info['video_hash']

		# Add geo-filtering info (ISO 3166-1 alpha-3 country codes)
		if stream_info.get('country_whitelist'):
			item['country_whitelist'] = stream_info['country_whitelist']
		if stream_info.get('country_blacklist'):
			item['country_blacklist'] = stream_info['country_blacklist']

		# Mark external URLs for browser opening
		if stream_info['stream_type'] == 'external':
			item['external_url'] = True

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

		except Exception:
			source_utils.scraper_error('STREMIO')
			return sources

		# Sort addons - prefer debrid-configured addons if setting enabled
		sorted_addons = self.addons
		if self.prefer_debrid_direct:
			debrid_addons = [a for a in self.addons if self._is_debrid_configured_addon(a)]
			other_addons = [a for a in self.addons if not self._is_debrid_configured_addon(a)]
			sorted_addons = debrid_addons + other_addons

		# Process addons in parallel using threads for performance
		sources_lock = Lock()

		def _scrape_addon(addon):
			try:
				addon_url = addon.get('url', '') if isinstance(addon, dict) else addon
				config_url = addon.get('config_url', '') if isinstance(addon, dict) else ''
				if not addon_url:
					return

				# Use config URL for fetching if available (has debrid settings)
				fetch_url = config_url if config_url else addon_url

				addon_name = addon.get('name', '') if isinstance(addon, dict) else ''
				if not addon_name:
					addon_name = self._get_addon_name(addon_url)

				addon_info = addon if isinstance(addon, dict) else {'url': addon}

				# Check idPrefixes filtering - skip addon if ID doesn't match
				if not self._check_id_prefix_match(addon_info, media_id):
					return

				is_debrid_addon = self._is_debrid_configured_addon(addon)

				# Determine which content types to query using per-resource filtering
				fetch_types = self._get_addon_stream_types(addon_info, media_type)

				streams = []
				for ft in fetch_types:
					result = self._fetch_streams(fetch_url, ft, media_id, addon_info)
					if result:
						streams.extend(result)
						break  # Got results, no need to try other types

				addon_sources = []
				for stream in streams:
					try:
						stream_info = self._parse_stream_info(stream, addon_info)

						# If this is a debrid-configured addon and we got a direct URL,
						# mark it as debrid resolved (even with infoHash present,
						# since debrid addons return resolved URLs alongside hashes)
						if is_debrid_addon and stream_info['url'] and not stream_info['is_debrid_resolved']:
							stream_info['is_debrid_resolved'] = True
							stream_info['stream_type'] = 'debrid_direct'

						item = self._build_source_item(
							stream_info, addon_name, title, aliases, hdlr, year,
							episode_title, total_seasons, season, undesirables, check_foreign_audio
						)

						if item:
							addon_sources.append(item)

					except Exception:
						source_utils.scraper_error('STREMIO')
						continue

				if addon_sources:
					with sources_lock:
						sources.extend(addon_sources)

			except Exception:
				source_utils.scraper_error('STREMIO')

		threads = [Thread(target=_scrape_addon, args=(addon,)) for addon in sorted_addons]
		for t in threads:
			t.start()
		for t in threads:
			t.join(timeout=self.timeout + 2)

		return sources

	def sources_packs(self, data, hostDict, search_series=False, total_seasons=None, bypass_filter=False):
		"""Handle season and show packs - delegate to sources() which handles packs"""
		return self.sources(data, hostDict)
