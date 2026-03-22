# created by Venom for Fenomscrapers (updated 12-16-2021)
"""
	Fenomscrapers Project
"""

import re
from urllib.parse import quote_plus, unquote_plus
from fenom import cleantitle
from fenom import client
from fenom import source_utils


def _clean_anime_title(s, year):
	"""Clean title string by removing year, parentheses, and normalizing patterns."""
	return s.replace(year, '').replace('(', '').replace(')', '').replace('&', 'and').replace('.US.', '.').replace('.us.', '.')


class source:
	timeout = 5
	priority = 5
	pack_capable = False
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = "https://nyaa.si"
		self.search_link = '/?f=0&c=0_0&q=%s'
		self.min_seeders = 1

	def sources(self, data, hostDict):
		sources = []
		if not data: return sources
		sources_append = sources.append
		try:
			title = data['tvshowtitle'] if 'tvshowtitle' in data else data['title']
			title = title.replace('&', 'and').replace('Special Victims Unit', 'SVU').replace('/', ' ')
			aliases = data['aliases']
			episode_title = data['title'] if 'tvshowtitle' in data else None
			year = data['year']
			hdlr = 'S%02dE%02d' % (int(data['season']), int(data['episode'])) if 'tvshowtitle' in data else year
			hdlr2 = 'S%d - %d' % (int(data['season']), int(data['episode'])) if 'tvshowtitle' in data else year

			query = '%s %s' % (title, hdlr)
			query = re.sub(r'(\\\|/| -|:|;|\*|\?|"|\'|<|>|\|)', '', query)
			query2 = '%s %s' % (title, hdlr2)
			query2 = re.sub(r'(\\\|/| -|:|;|\*|\?|"|\'|<|>|\|)', '', query2)

			urls = []
			url = self.search_link % quote_plus(query)
			url = '%s%s' % (self.base_link, url)
			urls.append(url)
			url2 = self.search_link % quote_plus(query2)
			url2 = '%s%s' % (self.base_link, url2)
			urls.append(url2)
			if 'timeout' in data: self.timeout = max(1, min(int(data['timeout']), 60))
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except Exception:
			source_utils.scraper_error('NYAA')
			return sources

		for url in urls:
			try:
				results = client.request(url, timeout=self.timeout)
				if not results or 'magnet:' not in results: continue
				results = re.sub(r'[\n\t]', '', results)
				tbody = client.parseDOM(results, 'tbody')
				rows = client.parseDOM(tbody, 'tr')

				for row in rows:
					links = zip(
									re.findall(r'href\s*=\s*["\'](magnet:[^"\']+)["\']', row, re.DOTALL | re.I),
									re.findall(r'((?:\d+\,\d+\.\d+|\d+\.\d+|\d+\,\d+|\d+)\s*(?:GB|GiB|Gb|MB|MiB|Mb))', row, re.DOTALL),
									re.findall(r'<td class\s*=\s*["\']text-center["\']>([0-9]+)</td>', row, re.DOTALL))
					for link in links:
						url = unquote_plus(link[0]).replace('&amp;', '&').split('&tr')[0].replace(' ', '.')
						url = source_utils.strip_non_ascii_and_unprintable(url)
						hash_match = re.search(r'btih:(.*?)&', url, re.I)
						if not hash_match: continue
						hash = hash_match.group(1)
						dn_parts = url.split('&dn=')
						if len(dn_parts) < 2: continue
						name = source_utils.clean_name(dn_parts[1])

						if hdlr not in name and hdlr2 not in name: continue
						name_info = source_utils.info_from_name(name, title, year, hdlr, episode_title)
						if source_utils.remove_lang(name_info, check_foreign_audio): continue
						if undesirables and source_utils.remove_undesirables(name_info, undesirables): continue

						if hdlr in name:
							t = _clean_anime_title(name.split(hdlr)[0], year)
						elif hdlr2 in name:
							t = _clean_anime_title(name.split(hdlr2)[0], year)
						# if cleantitle.get(t) != cleantitle.get(title): continue # Anime title matching is a bitch!
						try:
							seeders = int(link[2])
							if self.min_seeders > seeders: continue
						except Exception: seeders = 0

						quality, info = source_utils.get_release_quality(name_info, url)
						try:
							size = link[1]
							dsize, isize = source_utils._size(size)
							info.insert(0, isize)
						except Exception: dsize = 0
						info = ' | '.join(info)

						sources_append({'provider': 'nyaa', 'source': 'torrent', 'seeders': seeders, 'hash': hash, 'name': name, 'name_info': name_info,
										'quality': quality, 'language': 'en', 'url': url, 'info': info, 'direct': False, 'debridonly': True, 'size': dsize})
			except Exception:
				source_utils.scraper_error('NYAA')
				continue
		return sources

