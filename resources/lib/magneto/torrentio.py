# created by Venom for Fenomscrapers (updated 3-02-2022)
"""
	Fenomscrapers Project
	Uses shared http_client module
	Full Stremio SDK stream support:
	- infoHash, url, ytId, nzbUrl, externalUrl
	- fileIdx, fileMustInclude
	- behaviorHints: proxyHeaders, bingeGroup, filename, videoSize, notWebReady, countryWhitelist
	- sources (tracker URLs)
	- subtitles (embedded)
	- description field (modern SDK)
"""

import re
from fenom import source_utils
from fenom.control import setting as getSetting
from modules import http_client

# Debrid domain patterns for detecting pre-resolved URLs (e.g., when addon is configured with debrid)
_RE_DEBRID_URL = re.compile(r'(real-?debrid|realdebrid|alldebrid|premiumize|torbox|debrid-link|easydebrid|offcloud)', re.I)
_RE_INFO = re.compile(r'👤.*')
_RE_SEEDERS = re.compile(r'(\d+)', re.I)
_RE_SIZE = re.compile(r'((?:\d+[,.]?\d*)\s*(?:GB|GiB|Gb|MB|MiB|Mb))', re.I)


class source:
	timeout = 8
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = self._get_base_url()
		self.movieSearch_link = '/stream/movie/%s.json'
		self.tvSearch_link = '/stream/series/%s:%s:%s.json'
		self.min_seeders = 0

	def _get_base_url(self):
		"""
		Get the Torrentio instance URL based on user settings.
		Users can configure their own Torrentio URL from torrentio.strem.fun/configure
		which includes debrid service configuration.
		"""
		custom_url = getSetting('torrentio.url', '').strip()
		if custom_url:
			# Clean up URL - remove trailing slash and manifest.json if present
			url = custom_url.rstrip('/')
			if url.endswith('/manifest.json'):
				url = url[:-14]
			return url
		# Fallback to default
		return "https://torrentio.strem.fun"
# Currently supports YTS(+), EZTV(+), RARBG(+), 1337x(+), ThePirateBay(+), KickassTorrents(+), TorrentGalaxy(+), HorribleSubs(+), NyaaSi(+), NyaaPantsu(+), Rutor(+), Comando(+), ComoEuBaixo(+), Lapumia(+), OndeBaixa(+), Torrent9(+).

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
			if 'timeout' in data: self.timeout = int(data['timeout'])

			def error_callback(msg):
				source_utils.scraper_error('TORRENTIO: %s - %s' % (self.base_link, msg))

			files = http_client.fetch_streams(
				self.base_link, media_type, media_id,
				timeout=self.timeout,
				error_callback=error_callback
			)
			if not files:
				return sources

			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except Exception as e:
			source_utils.scraper_error('TORRENTIO: %s' % str(e))
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

				# Extract proxy headers for authenticated streams
				proxy_headers = None
				if 'proxyHeaders' in behavior_hints:
					ph = behavior_hints['proxyHeaders']
					if ph.get('request'):
						proxy_headers = ph['request']

				# Extract tracker URLs from sources field (for torrent peer discovery)
				trackers = []
				if 'sources' in file and isinstance(file['sources'], list):
					for src in file['sources']:
						if isinstance(src, str) and src.startswith('tracker:'):
							trackers.append(src[8:])

				# Extract fileIdx for multi-file torrents per SDK spec
				file_idx = file.get('fileIdx')

				# Extract fileMustInclude regex for matching video files in archives/torrents
				file_must_include = file.get('fileMustInclude')

				# Use description field (modern SDK) in addition to deprecated title field
				file_title = file.get('title', '') or file.get('description', '')
				file_title = file_title.split('\n')
				file_info_matches = [x for x in file_title if _RE_INFO.match(x)]
				file_info = file_info_matches[0] if file_info_matches else ''

				# Use behaviorHints.filename for best name detection
				bh_filename = behavior_hints.get('filename', '')
				# Also check top-level filename per SDK spec
				if not bh_filename:
					bh_filename = file.get('filename', '')
				if bh_filename:
					name = source_utils.clean_name(bh_filename)
				else:
					name = source_utils.clean_name(file_title[0]) if file_title else ''

				# For debrid-resolved direct links, be extra lenient as they often have minimal names
				if is_debrid_direct and not name:
					name = 'Direct.Link'

				# Title validation - Stremio/Torrentio filters by IMDB ID so content is correct
				# We use lenient validation since many results have simplified names
				title_check = source_utils.check_title(title, aliases, name, hdlr, year)
				if not title_check and not is_debrid_direct and not is_youtube and not is_external:
					if total_seasons is not None:
						# TV show - try pack detection first
						valid, last_season = source_utils.filter_show_pack(title, aliases, imdb, year, season, name, total_seasons)
						if valid:
							package = 'show'
						else:
							valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
							if valid:
								package = 'season'
							else:
								# Lenient fallback - allow short names or names with quality info
								name_len = len(name.replace('.', '').replace('-', '').replace(' ', ''))
								has_quality_info = any(q in name.lower() for q in ('1080', '720', '2160', '4k', 'hdr', 'web', 'bluray'))
								if name_len > 30 and not has_quality_info:
									continue
					else:
						# Movie - lenient validation
						name_len = len(name.replace('.', '').replace('-', '').replace(' ', ''))
						has_quality_info = any(q in name.lower() for q in ('1080', '720', '2160', '4k', 'hdr', 'web', 'bluray'))
						if name_len > 30 and not has_quality_info:
							# Check if year is somewhere in the name
							if year not in name and str(int(year)-1) not in name and str(int(year)+1) not in name:
								continue
				name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
				if source_utils.remove_lang(name_info, check_foreign_audio): continue
				if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue

				# Build URL based on stream type per Stremio SDK
				if is_youtube:
					url = f"plugin://plugin.video.youtube/play/?video_id={yt_id}"
				elif is_external:
					url = external_url
				elif is_usenet:
					url = nzb_url
				elif is_debrid_direct:
					url = direct_url
				elif hash:
					from urllib.parse import quote_plus
					url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)
					# Add tracker URLs for peer discovery
					for tracker in trackers:
						url += '&tr=%s' % quote_plus(tracker)
				else:
					url = direct_url

				try:
					seeders = int(_RE_SEEDERS.search(file_info).group(1)) if file_info else 0
					# Only apply seeder filter to torrents, not direct links
					if hash and not is_debrid_direct and self.min_seeders > seeders: continue
				except Exception: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = _RE_SIZE.search(file_info)
					if size:
						size = size.group(0)
						dsize, isize = source_utils._size(size)
						info.insert(0, isize)
					else:
						raise ValueError
				except Exception:
					dsize = 0
					# Fallback to behaviorHints.videoSize, then top-level videoSize (per SDK spec)
					video_size = behavior_hints.get('videoSize') or file.get('videoSize')
					if video_size:
						try:
							size_str = '%.2f GB' % (int(video_size) / 1073741824)
							dsize, isize = source_utils._size(size_str)
							info.insert(0, isize)
						except Exception: pass
				info = ' | '.join(info)

				# Build item based on stream type
				if is_youtube:
					item = {
						'source': 'youtube', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'torrentio', 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0
					}
				elif is_external:
					item = {
						'source': 'external', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'torrentio', 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0,
						'external_url': True
					}
				elif is_usenet:
					item = {
						'source': 'usenet', 'language': 'en', 'direct': False, 'debridonly': True,
						'provider': 'torrentio', 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': 0
					}
				elif is_debrid_direct:
					item = {
						'source': 'debrid_direct', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'torrentio', 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders,
						'debrid_resolved': True
					}
				else:
					item = {
						'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
						'provider': 'torrentio', 'hash': hash, 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
					}

				# Add proxy headers for authenticated streams
				if proxy_headers:
					item['proxy_headers'] = proxy_headers

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

				if package: item.update({'package': package, 'true_size': True})
				if package == 'show': item.update({'last_season': last_season})
				if episode_start: item.update({'episode_start': episode_start, 'episode_end': episode_end}) # for partial season packs
				sources_append(item)
			except Exception:
				source_utils.scraper_error('TORRENTIO')
		return sources
