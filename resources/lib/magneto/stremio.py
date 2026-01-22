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
	- Cloudflare bypass via cloudscraper (if available)
"""

import re
import time
import requests
from json import loads as jsloads
from fenom import source_utils
from fenom.control import setting as getSetting

# Try to import cloudscraper for Cloudflare bypass
try:
	import cloudscraper
	HAS_CLOUDSCRAPER = True
except ImportError:
	HAS_CLOUDSCRAPER = False

# Try to import curl_cffi for TLS fingerprint bypass (stronger than cloudscraper)
try:
	from curl_cffi import requests as curl_requests
	HAS_CURL_CFFI = True
except ImportError:
	HAS_CURL_CFFI = False

# Browser-like headers to help bypass Cloudflare and other protections
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate, br',
	'Connection': 'keep-alive',
	'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
	'Sec-Ch-Ua-Mobile': '?0',
	'Sec-Ch-Ua-Platform': '"Windows"',
	'Sec-Fetch-Dest': 'empty',
	'Sec-Fetch-Mode': 'cors',
	'Sec-Fetch-Site': 'cross-site',
}

# Cloudscraper session management - refresh stale sessions
_scraper_session = None
_scraper_request_count = 0
_scraper_fail_count = 0
_SCRAPER_MAX_REQUESTS = 50  # Refresh session after this many requests
_SCRAPER_MAX_FAILS = 3  # Refresh session after consecutive failures

def _get_scraper(force_new=False):
	global _scraper_session, _scraper_request_count, _scraper_fail_count
	if not HAS_CLOUDSCRAPER:
		return None
	# Create new session if needed
	if force_new or _scraper_session is None or _scraper_request_count >= _SCRAPER_MAX_REQUESTS or _scraper_fail_count >= _SCRAPER_MAX_FAILS:
		try:
			_scraper_session = cloudscraper.create_scraper(
				browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
				delay=1
			)
			_scraper_request_count = 0
			_scraper_fail_count = 0
		except Exception:
			_scraper_session = None
	return _scraper_session

def _mark_scraper_success():
	global _scraper_request_count, _scraper_fail_count
	_scraper_request_count += 1
	_scraper_fail_count = 0

def _mark_scraper_fail():
	global _scraper_fail_count
	_scraper_fail_count += 1

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
		# IMPORTANT: In Stremio protocol:
		# - 'name' = addon/source name (e.g., "Torrentio\n4K")
		# - 'title' = actual torrent/release name (e.g., "Movie.2023.2160p.WEB-DL\n👤 50")
		stream_name = stream.get('name', '') or ''
		stream_title = stream.get('title', '') or ''
		description = stream.get('description', '') or ''
		full_text = f"{stream_name}\n{stream_title}\n{description}"

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

		# Extract release name - priority order:
		# 1. behaviorHints.filename (most accurate)
		# 2. First line of 'title' field (contains actual torrent/release name)
		# 3. First line of 'name' field (fallback)
		if behavior_hints.get('filename'):
			info['name'] = behavior_hints['filename']
		elif stream_title:
			# Parse release name from first line of title (where Stremio addons put torrent names)
			lines = stream_title.split('\n')
			info['name'] = lines[0].strip() if lines else stream_title
		elif stream_name:
			# Fallback to name field
			lines = stream_name.split('\n')
			info['name'] = lines[0].strip() if lines else stream_name

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
		"""Fetch streams from a Stremio addon with enhanced error handling and Cloudflare bypass"""
		streams = []
		try:
			# Clean up addon URL
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			# Build stream endpoint
			endpoint = f"{base_url}/stream/{media_type}/{media_id}.json"

			# Extract domain for Referer/Origin headers
			try:
				from urllib.parse import urlparse
				parsed = urlparse(base_url)
				origin = f"{parsed.scheme}://{parsed.netloc}"
			except:
				origin = base_url

			# Enhanced headers with Referer and Origin
			headers = BROWSER_HEADERS.copy()
			headers['Referer'] = f"{origin}/"
			headers['Origin'] = origin

			# Try multiple methods in order of effectiveness:
			# 1. curl_cffi (best TLS fingerprint bypass)
			# 2. cloudscraper (good JS challenge bypass)
			# 3. requests with browser headers (basic)
			response = None
			last_error = None
			cloudflare_blocked = False

			# Method 1: curl_cffi with Chrome impersonation (best for TLS fingerprinting)
			if HAS_CURL_CFFI:
				try:
					for attempt in range(3):
						try:
							response = curl_requests.get(
								endpoint,
								timeout=self.timeout,
								headers=headers,
								impersonate='chrome120'
							)
							if response.status_code == 200:
								content_type = response.headers.get('content-type', '')
								if 'text/html' not in content_type:
									data = response.json()
									streams = data.get('streams', [])
									if streams or data:  # Success even if empty
										return streams
							if response.status_code in (403, 418, 503) or 'text/html' in response.headers.get('content-type', ''):
								cloudflare_blocked = True
								if attempt < 2:
									time.sleep(0.5 * (attempt + 1))
									continue
							break
						except Exception:
							if attempt < 2:
								time.sleep(0.5 * (attempt + 1))
								continue
							raise
				except Exception as e:
					last_error = e

			# Method 2: cloudscraper (JS challenge solver)
			if not streams:
				scraper = _get_scraper()
				if scraper:
					try:
						for attempt in range(3):
							try:
								response = scraper.get(endpoint, timeout=self.timeout, headers=headers)
								if response.status_code == 200:
									content_type = response.headers.get('content-type', '')
									if 'text/html' not in content_type:
										try:
											data = response.json()
											streams = data.get('streams', [])
											_mark_scraper_success()
											if streams or data:
												return streams
										except ValueError:
											pass  # Invalid JSON, try next method
								if response.status_code in (403, 418, 503) or 'text/html' in response.headers.get('content-type', ''):
									cloudflare_blocked = True
									_mark_scraper_fail()
									if attempt < 2:
										time.sleep(0.5 * (attempt + 1))
										# Try with fresh session on last attempt
										if attempt == 1:
											scraper = _get_scraper(force_new=True)
											if not scraper:
												break
										continue
								break
							except Exception:
								_mark_scraper_fail()
								if attempt < 2:
									time.sleep(0.5 * (attempt + 1))
									continue
								raise
					except Exception as e:
						last_error = e

			# Method 3: Regular requests with browser headers (fallback)
			if not streams:
				try:
					for attempt in range(2):
						try:
							response = requests.get(endpoint, timeout=self.timeout, headers=headers)
							if response.status_code == 200:
								content_type = response.headers.get('content-type', '')
								if 'text/html' not in content_type:
									try:
										data = response.json()
										streams = data.get('streams', [])
										if streams or data:
											return streams
									except ValueError:
										pass
							if response.status_code in (403, 418, 503):
								cloudflare_blocked = True
								if attempt == 0:
									time.sleep(0.5)
									continue
							break
						except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
							if attempt == 0:
								time.sleep(0.5)
								continue
							raise
				except Exception as e:
					last_error = e

			# All methods failed - log appropriate error
			if response is not None:
				status = response.status_code
				content_type = response.headers.get('content-type', '')
				if status == 403 or (status == 200 and 'text/html' in content_type):
					if cloudflare_blocked:
						source_utils.scraper_error('STREMIO: Cloudflare blocked %s - try configuring addon with debrid credentials' % base_url)
					else:
						source_utils.scraper_error('STREMIO: HTTP 403 from %s' % base_url)
				elif status == 418:
					source_utils.scraper_error('STREMIO: Bot protection at %s - use configured addon URL with debrid' % base_url)
				elif status == 503:
					source_utils.scraper_error('STREMIO: Service unavailable %s - addon may be down' % base_url)
				elif status == 522 or status == 524:
					source_utils.scraper_error('STREMIO: Timeout at origin %s - addon server slow' % base_url)
				elif status != 200:
					source_utils.scraper_error('STREMIO: HTTP %d from %s' % (status, base_url))
			elif last_error:
				source_utils.scraper_error('STREMIO: %s - %s' % (base_url, str(last_error)[:80]))

		except requests.exceptions.Timeout:
			source_utils.scraper_error('STREMIO_TIMEOUT: %s' % base_url)
		except requests.exceptions.ConnectionError:
			source_utils.scraper_error('STREMIO_CONNECTION: %s' % base_url)
		except Exception as e:
			source_utils.scraper_error('STREMIO: %s' % str(e)[:100])
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

			# Enhanced headers
			try:
				from urllib.parse import urlparse
				parsed = urlparse(base_url)
				origin = f"{parsed.scheme}://{parsed.netloc}"
			except:
				origin = base_url

			headers = BROWSER_HEADERS.copy()
			headers['Referer'] = f"{origin}/"
			headers['Origin'] = origin

			# Try curl_cffi first, then cloudscraper, then requests
			response = None
			if HAS_CURL_CFFI:
				try:
					response = curl_requests.get(endpoint, timeout=5, headers=headers, impersonate='chrome120')
				except:
					pass

			if response is None or response.status_code != 200:
				scraper = _get_scraper()
				if scraper:
					try:
						response = scraper.get(endpoint, timeout=5, headers=headers)
					except:
						pass

			if response is None or response.status_code != 200:
				response = requests.get(endpoint, timeout=5, headers=headers)

			if response and response.status_code == 200:
				content_type = response.headers.get('content-type', '')
				if 'text/html' not in content_type:
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

			manifest_url = f"{base_url}/manifest.json"

			# Enhanced headers
			try:
				from urllib.parse import urlparse
				parsed = urlparse(base_url)
				origin = f"{parsed.scheme}://{parsed.netloc}"
			except:
				origin = base_url

			headers = BROWSER_HEADERS.copy()
			headers['Referer'] = f"{origin}/"
			headers['Origin'] = origin

			# Try to fetch manifest for name with multiple methods
			response = None
			if HAS_CURL_CFFI:
				try:
					response = curl_requests.get(manifest_url, timeout=3, headers=headers, impersonate='chrome120')
				except:
					pass

			if response is None or response.status_code != 200:
				scraper = _get_scraper()
				if scraper:
					try:
						response = scraper.get(manifest_url, timeout=3, headers=headers)
					except:
						pass

			if response is None or response.status_code != 200:
				response = requests.get(manifest_url, timeout=3, headers=headers)

			if response and response.status_code == 200:
				content_type = response.headers.get('content-type', '')
				if 'text/html' not in content_type:
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

		# Title validation - Stremio already filters by IMDB ID so content is correct
		# We only need to check for packs and apply language/undesirable filters
		if name:
			# Check if this is a pack (season/show pack) by trying pack filters first
			# This helps identify multi-episode releases for proper handling
			is_pack = False
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

			# For standard title check, we're lenient since Stremio uses IMDB ID filtering
			# Only validate if the name appears to be a proper release name (contains title-like info)
			# Skip strict validation for debrid-resolved direct links which often have minimal names
			if not is_pack and not stream_info['is_debrid_resolved']:
				title_check = source_utils.check_title(title, aliases, name, hdlr, year)
				# If title check fails but name is very short/minimal, allow it through
				# (likely a debrid addon with simplified naming)
				if not title_check:
					# Allow through if name is short (<30 chars) or doesn't look like a release name
					name_len = len(name.replace('.', '').replace('-', '').replace(' ', ''))
					has_quality_info = any(q in name.lower() for q in ('1080', '720', '2160', '4k', 'hdr', 'web', 'bluray'))
					if name_len > 30 and not has_quality_info:
						# Looks like a full release name but doesn't match - might be wrong content
						# Still allow through for movies with year in name, or TV with episode format
						if total_seasons is None:
							# Movie - check if year is somewhere in the name
							if year not in name and str(int(year)-1) not in name and str(int(year)+1) not in name:
								return None
						# TV shows - be lenient as IMDB+season+episode already filters

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
