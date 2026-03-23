# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import re, requests
from fenom import source_utils

# Module-level session for connection pooling (HTTP keep-alive)
session = requests.Session()

# Pre-compiled regex patterns
_INFO_PATTERN = re.compile(r'💾.*')
_SEEDERS_PATTERN = re.compile(r'👤\s*(\d+)')
_SIZE_PATTERN = re.compile(r'((?:\d+\,\d+\.\d+|\d+\.\d+|\d+\,\d+|\d+)\s*(?:GB|GiB|Gb|MB|MiB|Mb))')


class source:
	timeout = 7
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://torrentsdb.com"
		self.movieSearch_link = '/stream/movie/%s.json'
		self.tvSearch_link = '/stream/series/%s:%s:%s.json'
		self.min_seeders = 0
# Currently supports YTS(+), EZTV(+), 1337x(+), TorrentCSV(+), 1lou(+), Nyaa(+), Sk-CzTorrent(+), 1TamilBlasters(+), LimeTorrent(+), 1TamilMV(+), RARBG(+), Knaben(+), ThePirateBay(+), KickassTorrents(+), AnimeTosho(+), ExtremlymTorrents(+), YggTorrent(+), TokyoTosho(+), Rutor(+), Rutracker(+), Torrent9(+), ilCorSaRoNeRo(+), Manual(+).

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
			if 'timeout' in data: self.timeout = max(1, min(int(data['timeout']), 60))
			results = session.get(url, timeout=self.timeout)
			if results.status_code != 200: return sources
			response_json = results.json()
			files = response_json.get('streams', [])
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except Exception:
			source_utils.scraper_error('TORRENTSDB')
			return sources

		for file in files:
			try:
				package, episode_start = None, 0
				hash = file.get('infoHash', '')
				if not hash: continue
				file_title = file.get('title', '').split('\n')
				file_info = next((x for x in file_title if _INFO_PATTERN.search(x)), '')

				name = source_utils.clean_name(file_title[0])

				if not source_utils.check_title(title, aliases, name, hdlr, year):
					if total_seasons is None: continue
					valid, last_season = source_utils.filter_show_pack(title, aliases, imdb, year, season, name, total_seasons)
					if not valid:
						valid, episode_start, episode_end = source_utils.filter_season_pack(title, aliases, year, season, name)
						if not valid: continue
						else: package = 'season'
					else: package = 'show'
				name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
				if source_utils.remove_lang(name_info, check_foreign_audio): continue
				if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue

				url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)

				try:
					seeders = int(_SEEDERS_PATTERN.search(file_info).group(1))
					if self.min_seeders > seeders: continue
				except Exception: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = _SIZE_PATTERN.search(file_info).group(0)
					dsize, isize = source_utils._size(size)
					info.insert(0, isize)
				except Exception: dsize = 0
				info = ' | '.join(info)

				item = {
					'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
					'provider': 'torrentsdb', 'hash': hash, 'url': url, 'name': name, 'name_info': name_info,
					'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
				}
				if package: item.update({'package': package, 'true_size': True})
				if package == 'show': item.update({'last_season': last_season})
				if episode_start: item.update({'episode_start': episode_start, 'episode_end': episode_end}) # for partial season packs
				sources_append(item)
			except Exception:
				source_utils.scraper_error('TORRENTSDB')
		return sources

