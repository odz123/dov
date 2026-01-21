# created by Venom for Fenomscrapers (updated 3-02-2022)
"""
	Fenomscrapers Project
"""

import re
import requests
from fenom import source_utils
from fenom.control import setting as getSetting


# Browser-like headers to help bypass Cloudflare protection
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate, br',
	'Connection': 'keep-alive',
	'Sec-Fetch-Dest': 'empty',
	'Sec-Fetch-Mode': 'cors',
	'Sec-Fetch-Site': 'cross-site',
}


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
		# Fallback to default (may be blocked by Cloudflare)
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
				url = '%s%s' % (self.base_link, self.tvSearch_link % (imdb, season, episode))
			else:
				hdlr = year
				url = '%s%s' % (self.base_link, self.movieSearch_link % imdb)
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			response = requests.get(url, timeout=self.timeout, headers=BROWSER_HEADERS)
			if response.status_code == 403:
				# Cloudflare block - check if user has configured a custom URL
				if not getSetting('torrentio.url', '').strip():
					source_utils.scraper_error('TORRENTIO: Blocked by Cloudflare. Configure your Torrentio URL in settings (get it from torrentio.strem.fun/configure)')
				else:
					source_utils.scraper_error('TORRENTIO: HTTP 403 Forbidden from %s' % self.base_link)
				return sources
			if response.status_code != 200:
				source_utils.scraper_error('TORRENTIO: HTTP %s from %s' % (response.status_code, self.base_link))
				return sources
			# Check if response is HTML (Cloudflare challenge page)
			content_type = response.headers.get('content-type', '')
			if 'text/html' in content_type:
				source_utils.scraper_error('TORRENTIO: Cloudflare challenge detected. Configure your Torrentio URL in settings.')
				return sources
			files = response.json().get('streams', [])
			if not files:
				return sources
			_INFO = re.compile(r'👤.*')
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except requests.exceptions.Timeout:
			source_utils.scraper_error('TORRENTIO: Timeout connecting to %s' % self.base_link)
			return sources
		except requests.exceptions.ConnectionError:
			source_utils.scraper_error('TORRENTIO: Connection error to %s' % self.base_link)
			return sources
		except Exception as e:
			source_utils.scraper_error('TORRENTIO: %s' % str(e))
			return sources

		for file in files:
			try:
				package, episode_start = None, 0
				hash = file.get('infoHash')
				direct_url = file.get('url')

				# Skip results without either infoHash or url
				if not hash and not direct_url:
					continue

				# Check if this is a debrid-resolved direct link
				is_debrid_direct = False
				if direct_url and not hash:
					is_debrid_direct = True

				file_title = file.get('title', '').split('\n')
				file_info_matches = [x for x in file_title if _INFO.match(x)]
				file_info = file_info_matches[0] if file_info_matches else ''
				# try:
					# index = file_title.index(file_info)
					# if index == 1: combo = file_title[0].replace(' ', '.')
					# else: combo = ''.join(file_title[0:2]).replace(' ', '.')
					# if '🇷🇺' in file_title[index+1] and not any(value in combo for value in ('.en.', '.eng.', 'english')): continue
				# except: pass

				name = source_utils.clean_name(file_title[0]) if file_title else ''

				# For debrid-resolved direct links, be extra lenient as they often have minimal names
				if is_debrid_direct and not name:
					name = 'Direct.Link'

				# Title validation - Stremio/Torrentio filters by IMDB ID so content is correct
				# We use lenient validation since many results have simplified names
				title_check = source_utils.check_title(title, aliases, name, hdlr, year)
				if not title_check and not is_debrid_direct:
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

				# Build URL based on stream type
				if hash:
					url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)
				else:
					url = direct_url
				# if not episode_title: #filter for eps returned in movie query (rare but movie and show exists for Run in 2020)
					# ep_strings = [r'(?:\.|\-)s\d{2}e\d{2}(?:\.|\-|$)', r'(?:\.|\-)s\d{2}(?:\.|\-|$)', r'(?:\.|\-)season(?:\.|\-)\d{1,2}(?:\.|\-|$)']
					# name_lower = name.lower()
					# if any(re.search(item, name_lower) for item in ep_strings): continue

				try:
					seeders = int(re.search(r'(\d+)', file_info).group(1))
					# Only apply seeder filter to torrents, not direct links
					if hash and self.min_seeders > seeders: continue
				except: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = re.search(r'((?:\d+\,\d+\.\d+|\d+\.\d+|\d+\,\d+|\d+)\s*(?:GB|GiB|Gb|MB|MiB|Mb))', file_info).group(0)
					dsize, isize = source_utils._size(size)
					info.insert(0, isize)
				except: dsize = 0
				info = ' | '.join(info)

				# Build item based on stream type
				if is_debrid_direct:
					item = {
						'source': 'direct', 'language': 'en', 'direct': True, 'debridonly': False,
						'provider': 'torrentio', 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
					}
				else:
					item = {
						'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
						'provider': 'torrentio', 'hash': hash, 'url': url, 'name': name, 'name_info': name_info,
						'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
					}
				if package: item.update({'package': package, 'true_size': True})
				if package == 'show': item.update({'last_season': last_season})
				if episode_start: item.update({'episode_start': episode_start, 'episode_end': episode_end}) # for partial season packs
				sources_append(item)
			except:
				source_utils.scraper_error('TORRENTIO')
		return sources

