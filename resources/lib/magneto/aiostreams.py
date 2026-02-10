# created by kodifitzwell for Fenomscrapers
# Updated to support custom AIOStreams instances and user configuration
"""
	Fenomscrapers Project

	AIOStreams is a Stremio super-addon that consolidates multiple streaming
	addons (Comet, MediaFusion, Torrentio, etc.) and debrid services into
	a single, customizable interface.

	API Documentation: https://github.com/Viren070/AIOStreams/wiki/API-Documentation

	Users can:
	1. Use pre-configured public instances
	2. Provide a custom AIOStreams URL (self-hosted or ElfHosted)
	3. Provide their own user data configuration from the AIOStreams configure page

	Full Stremio SDK stream support:
	- infoHash, url, ytId, nzbUrl, externalUrl
	- fileIdx, fileMustInclude
	- behaviorHints: proxyHeaders, bingeGroup, filename, videoSize, notWebReady, countryWhitelist
	- sources (tracker URLs)
	- subtitles (embedded)
	- description field (modern SDK)
	- Uses shared http_client module
"""

import re
from fenom import source_utils
from fenom.control import setting as getSetting
from modules import http_client

# Debrid domain patterns for detecting pre-resolved URLs (e.g., when addon is configured with debrid)
_RE_DEBRID_URL = re.compile(r'(real-?debrid|realdebrid|alldebrid|premiumize|torbox|debrid-link|easydebrid|offcloud)', re.I)

# Pre-configured public instances
# Note: Public instances may have rate limits or require configuration
PUBLIC_INSTANCES = (
	"https://aiostreams.kuu-lection.com",      # Kuu-lection instance
	"https://aiostreamsfortheweebs.midnightignite.me",  # Midnightignite instance
	"https://aiostreams.elfhosted.com"         # ElfHosted instance (requires subscription)
)

# Default user data configuration (Comet + MediaFusion enabled)
DEFAULT_USER_DATA = (
	'ew0KICAicHJlc2V0cyI6IFsNCiAgICB7DQogICAgICAidHlwZSI6ICJ0b3JyZW50aW8iLA0KICAgICAg'
	'Imluc3RhbmNlSWQiOiAiZTdiIiwNCiAgICAgICJlbmFibGVkIjogZmFsc2UsDQogICAgICAib3B0aW9u'
	'cyI6IHsNCiAgICAgICAgIm5hbWUiOiAiVG9ycmVudGlvIiwNCiAgICAgICAgInRpbWVvdXQiOiAxMDAw'
	'MCwNCiAgICAgICAgInJlc291cmNlcyI6IFsic3RyZWFtIl0sDQogICAgICAgICJwcm92aWRlcnMiOiBb'
	'XSwNCiAgICAgICAgInVzZU11bHRpcGxlSW5zdGFuY2VzIjogZmFsc2UNCiAgICAgIH0NCiAgICB9LA0K'
	'ICAgIHsNCiAgICAgICJ0eXBlIjogImNvbWV0IiwNCiAgICAgICJpbnN0YW5jZUlkIjogImY3YiIsDQog'
	'ICAgICAiZW5hYmxlZCI6IHRydWUsDQogICAgICAib3B0aW9ucyI6IHsNCiAgICAgICAgIm5hbWUiOiAi'
	'Q29tZXQiLA0KICAgICAgICAidGltZW91dCI6IDEwMDAwLA0KICAgICAgICAicmVzb3VyY2VzIjogWyJz'
	'dHJlYW0iXSwNCiAgICAgICAgImluY2x1ZGVQMlAiOiB0cnVlLA0KICAgICAgICAicmVtb3ZlVHJhc2gi'
	'OiBmYWxzZQ0KICAgICAgfQ0KICAgIH0sDQogICAgew0KICAgICAgInR5cGUiOiAibWVkaWFmdXNpb24i'
	'LA0KICAgICAgImluc3RhbmNlSWQiOiAiNDUwIiwNCiAgICAgICJlbmFibGVkIjogdHJ1ZSwNCiAgMDo'
	'gICJvcHRpb25zIjogew0KICAgICAgICAibmFtZSI6ICJNZWRpYUZ1c2lvbiIsDQogICAgICAgICJ0aW1l'
	'b3V0IjogMTAwMDAsDQogICAgICAgICJyZXNvdXJjZXMiOiBbInN0cmVhbSJdLA0KICAgICAgICAidXNl'
	'Q2FjaGVkUmVzdWx0c09ubHkiOiB0cnVlLA0KICAgICAgICAiZW5hYmxlV2F0Y2hsaXN0Q2F0YWxvZ3Mi'
	'OiBmYWxzZSwNCiAgICAgICAgImRvd25sb2FkVmlhQnJvd3NlciI6IGZhbHNlLA0KICAgICAgICAiY29u'
	'dHJpYnV0b3JTdHJlYW1zIjogZmFsc2UsDQogICAgICAgICJjZXJ0aWZpY2F0aW9uTGV2ZWxzRmlsdGVy'
	'IjogW10sDQogICAgICAgICJudWRpdHlGaWx0ZXIiOiBbXQ0KICAgICAgfQ0KICAgIH0NCiAgXSwNCiAg'
	'ImZvcm1hdHRlciI6IHsNCiAgICAiaWQiOiAidG9ycmVudGlvIiwNCiAgICAiZGVmaW5pdGlvbiI6IHsi'
	'bmFtZSI6ICIiLCAiZGVzY3JpcHRpb24iOiAiIn0NCiAgfSwNCiAgInNvcnRDcml0ZXJpYSI6IHsiZ2xv'
	'YmFsIjogW119LA0KICAiZGVkdXBsaWNhdG9yIjogew0KICAgICJlbmFibGVkIjogZmFsc2UsDQogICAg'
	'ImtleXMiOiBbImZpbGVuYW1lIiwgImluZm9IYXNoIl0sDQogICAgIm11bHRpR3JvdXBCZWhhdmlvdXIi'
	'OiAiYWdncmVzc2l2ZSIsDQogICAgImNhY2hlZCI6ICJzaW5nbGVfcmVzdWx0IiwNCiAgICAidW5jYWNo'
	'ZWQiOiAicGVyX3NlcnZpY2UiLA0KICAgICJwMnAiOiAic2luZ2xlX3Jlc3VsdCIsDQogICAgImV4Y2x1'
	'ZGVBZGRvbnMiOiBbXQ0KICB9DQp9'
)


class source:
	timeout = 10
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = self._get_base_url()
		self.movieSearch_link = '/api/v1/search'
		self.tvSearch_link = '/api/v1/search'
		self.min_seeders = 0

	def _get_base_url(self):
		"""
		Get the AIOStreams instance URL based on user settings.
		Supports:
		- Pre-configured public instances (index 0-2)
		- Custom user-provided URL (index 3)
		"""
		instance_index = int(getSetting('aiostreams.url', '0'))
		if instance_index < len(PUBLIC_INSTANCES):
			return PUBLIC_INSTANCES[instance_index]
		# Custom URL
		custom_url = getSetting('aiostreams.custom_url', '').strip()
		if custom_url:
			# Remove trailing slash if present
			return custom_url.rstrip('/')
		# Fallback to first public instance
		return PUBLIC_INSTANCES[0]

	def _get_user_data(self):
		"""
		Get the user data configuration.
		If user has provided custom configuration, use that.
		Otherwise, use the default configuration.
		"""
		custom_data = getSetting('aiostreams.user_data', '').strip()
		if custom_data:
			return custom_data
		return DEFAULT_USER_DATA

	def _fetch_results(self, url, params, headers):
		"""Fetch results from AIOStreams API for given params.
		Returns list of result files or empty list."""
		from urllib.parse import urlencode
		full_url = '%s?%s' % (url, urlencode(params))
		response = http_client.fetch_json(full_url, timeout=self.timeout, headers=headers)
		if not response:
			return []
		if not response.get('success', True):
			error = response.get('error', {})
			source_utils.scraper_error('AIOSTREAMS: %s' % error.get('message', 'Unknown error'))
			return []
		return response.get('data', {}).get('results', [])

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			title = data['tvshowtitle'] if 'tvshowtitle' in data else data['title']
			title = title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' ')
			aliases = data['aliases']
			episode_title = data['title'] if 'tvshowtitle' in data else None
			total_seasons = data['total_seasons'] if 'tvshowtitle' in data else None
			year = data['year']
			imdb = data['imdb']
			tvdb = data.get('tvdb') if 'tvshowtitle' in data else None
			if 'tvshowtitle' in data:
				season = data['season']
				episode = data['episode']
				hdlr = 'S%02dE%02d' % (int(season), int(episode))
				url = '%s%s' % (self.base_link, self.tvSearch_link)
				params = {'type': 'series', 'id': '%s:%s:%s' % (imdb, season, episode)}
			else:
				hdlr = year
				url = '%s%s' % (self.base_link, self.movieSearch_link)
				params = {'type': 'movie', 'id': '%s' % imdb}
			if 'timeout' in data: self.timeout = max(1, min(int(data['timeout']), 60))

			base_headers = self._headers()
			has_valid_tvdb = tvdb and str(tvdb) not in ('', '0', '0000000', 'None')
			has_valid_imdb = imdb and str(imdb) not in ('', 'None', 'tt0000000')

			# Fetch results with TVDB fallback for series
			files = []
			if has_valid_imdb:
				files = self._fetch_results(url, params, base_headers)
			if not files and has_valid_tvdb:
				tvdb_params = {'type': 'series', 'id': '%s:%s:%s' % (tvdb, season, episode)}
				files = self._fetch_results(url, tvdb_params, base_headers)

			if not files:
				return sources
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except Exception as e:
			source_utils.scraper_error('AIOSTREAMS: %s' % str(e))
			return sources

		for file in files:
			try:
				package, episode_start = None, 0
				hash = file.get('infoHash')
				direct_url = file.get('url')
				yt_id = file.get('ytId')
				nzb_url = file.get('nzbUrl')
				external_url = file.get('externalUrl')

				# Skip results without any valid source per Stremio SDK
				if not hash and not direct_url and not yt_id and not nzb_url and not external_url:
					continue

				# Determine stream type
				is_debrid_direct = False
				is_youtube = bool(yt_id)
				is_usenet = bool(nzb_url) and not direct_url and not hash
				is_external = bool(external_url) and not direct_url and not hash and not yt_id

				if not is_youtube and not is_usenet and not is_external:
					if direct_url and not hash:
						is_debrid_direct = True
					elif direct_url and hash:
						if _RE_DEBRID_URL.search(direct_url):
							is_debrid_direct = True

				# Extract behaviorHints (full SDK support)
				behavior_hints = file.get('behaviorHints', {}) or {}

				# Extract proxy headers for authenticated streams (per SDK spec)
				# proxyHeaders.request: headers to send with the request
				# proxyHeaders.response: headers expected in the response (requires notWebReady: true)
				proxy_headers = None
				proxy_headers_response = None
				if 'proxyHeaders' in behavior_hints:
					ph = behavior_hints['proxyHeaders']
					if ph.get('request'):
						proxy_headers = ph['request']
					if ph.get('response'):
						proxy_headers_response = ph['response']

				# Extract tracker URLs from sources field (for torrent peer discovery)
				trackers = []
				if 'sources' in file and isinstance(file['sources'], list):
					for src in file['sources']:
						if isinstance(src, str) and src.startswith('tracker:'):
							trackers.append(src[8:])

				# Extract fileIdx for multi-file torrents per SDK spec
				file_idx = file.get('fileIdx')

				# Extract fileMustInclude regex for matching video files
				file_must_include = file.get('fileMustInclude')

				# Get filename - prioritize behaviorHints.filename, then top-level filename per SDK spec
				bh_filename = behavior_hints.get('filename', '')
				if not bh_filename:
					bh_filename = file.get('filename', '')
				if bh_filename:
					name = source_utils.clean_name(bh_filename)
				else:
					file_title = file.get('folderName') or file.get('filename') or file.get('name', '')
					file_title = file_title.replace('┈➤', '\n').split('\n')
					name = source_utils.clean_name(file_title[0])

				# Title validation - AIOStreams filters by IMDB ID so content is correct
				if is_debrid_direct and not name:
					name = 'Direct.Link'

				title_check = source_utils.check_title(title, aliases, name, hdlr, year)
				if not title_check and not is_debrid_direct and not is_youtube and not is_external:
					if total_seasons is not None:
						valid, last_season = source_utils.filter_show_pack(title, aliases, imdb, year, season, name, total_seasons)
						if valid:
							package = 'show'
						else:
							valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
							if valid:
								package = 'season'
							else:
								name_len = len(name.replace('.', '').replace('-', '').replace(' ', ''))
								has_quality_info = any(q in name.lower() for q in ('1080', '720', '2160', '4k', 'hdr', 'web', 'bluray'))
								if name_len > 30 and not has_quality_info:
									continue
					else:
						name_len = len(name.replace('.', '').replace('-', '').replace(' ', ''))
						has_quality_info = any(q in name.lower() for q in ('1080', '720', '2160', '4k', 'hdr', 'web', 'bluray'))
						if name_len > 30 and not has_quality_info:
							if year not in name and str(int(year)-1) not in name and str(int(year)+1) not in name:
								continue
				name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
				if source_utils.remove_lang(name_info, check_foreign_audio): continue
				if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue

				# Build URL based on stream type per Stremio SDK
				if is_youtube:
					stream_url = f"plugin://plugin.video.youtube/play/?video_id={yt_id}"
				elif is_external:
					stream_url = external_url
				elif is_usenet:
					stream_url = nzb_url
				elif is_debrid_direct:
					stream_url = direct_url
				elif hash:
					from urllib.parse import quote_plus
					stream_url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)
					for tracker in trackers:
						stream_url += '&tr=%s' % quote_plus(tracker)
				else:
					stream_url = direct_url

				try:
					seeders = file.get('seeders', 0)
					if seeders is None: seeders = 0
					if hash and not is_debrid_direct and self.min_seeders > seeders: continue
				except Exception: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, stream_url)
				try:
					size = file.get('size', 0)
					# Fallback to behaviorHints.videoSize, then top-level videoSize (per SDK spec)
					if not size:
						size = behavior_hints.get('videoSize') or file.get('videoSize', 0)
					if size:
						size_str = '%.2f GB' % (float(size) / 1073741824)
						dsize, isize = source_utils._size(size_str)
						info.insert(0, isize)
					else:
						dsize = 0
				except Exception: dsize = 0
				info = ' | '.join(info)

				# Build item based on stream type
				if is_youtube:
					item = {
						'source': 'youtube', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'aiostreams', 'url': stream_url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0
					}
				elif is_external:
					item = {
						'source': 'external', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'aiostreams', 'url': stream_url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0,
						'external_url': True
					}
				elif is_usenet:
					item = {
						'source': 'usenet', 'language': 'en', 'direct': False, 'debridonly': True,
						'provider': 'aiostreams', 'url': stream_url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0
					}
				elif is_debrid_direct:
					item = {
						'source': 'debrid_direct', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'aiostreams', 'url': stream_url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders,
						'debrid_resolved': True
					}
				else:
					item = {
						'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
						'provider': 'aiostreams', 'hash': hash, 'url': stream_url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
					}

				# Add proxy headers for authenticated streams
				if proxy_headers:
					item['proxy_headers'] = proxy_headers

				# Add response proxy headers (per SDK spec: used with notWebReady streams)
				if proxy_headers_response:
					item['proxy_headers_response'] = proxy_headers_response

				# Add fileIdx for multi-file torrents (per SDK spec)
				if file_idx is not None:
					item['file_idx'] = file_idx

				# Add fileMustInclude regex for video file matching (per SDK spec)
				if file_must_include:
					item['file_must_include'] = file_must_include

				# Add bingeGroup for autoplay optimization (per SDK spec)
				binge_group = behavior_hints.get('bingeGroup')
				if binge_group:
					item['binge_group'] = binge_group

				# Add notWebReady flag (per SDK spec)
				if behavior_hints.get('notWebReady'):
					item['not_web_ready'] = True

				# Add geo-filtering hints (ISO 3166-1 alpha-3 country codes, per SDK spec)
				if behavior_hints.get('countryWhitelist'):
					item['country_whitelist'] = behavior_hints['countryWhitelist']
				if behavior_hints.get('countryBlacklist'):
					item['country_blacklist'] = behavior_hints['countryBlacklist']

				# Add embedded subtitles from stream (per SDK spec)
				if file.get('subtitles'):
					item['stremio_subtitles'] = file['subtitles']

				if package: item['package'] = package
				if package == 'show': item.update({'last_season': last_season})
				if episode_start: item.update({'episode_start': episode_start, 'episode_end': episode_end})
				sources_append(item)
			except Exception:
				source_utils.scraper_error('AIOSTREAMS')
		return sources

	def _headers(self):
		headers = http_client.BROWSER_HEADERS.copy()
		headers['x-aiostreams-user-data'] = self._get_user_data()
		return headers
