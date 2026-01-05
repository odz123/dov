# created by kodifitzwell for Fenomscrapers
"""
	Fenomscrapers Project
"""

import requests
from fenom import source_utils
from fenom.control import setting as getSetting


class source:
	timeout = 10
	priority = 1
	pack_capable = False # packs parsed in sources function
	hasMovies = True
	hasEpisodes = True
	def __init__(self):
		self.language = ['en']
		self.base_link = (
			"https://aiostreams.stremio.ru",
			"https://aiostreamsfortheweebs.midnightignite.me"
		)[int(getSetting('aiostreams.url', '0'))]
		self.movieSearch_link = '/api/v1/search'
		self.tvSearch_link = '/api/v1/search'
		self.min_seeders = 0

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
				url = '%s%s' % (self.base_link, self.tvSearch_link)
				params = {'type': 'series', 'id': '%s:%s:%s' % (imdb, season, episode)}
			else:
				hdlr = year
				url = '%s%s' % (self.base_link, self.movieSearch_link)
				params = {'type': 'movie', 'id': '%s' % imdb}
			# log_utils.log('url = %s' % url)
			if 'timeout' in data: self.timeout = int(data['timeout'])
			results = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
			files = results.json()['data']['results']
			undesirables = source_utils.get_undesirables()
			check_foreign_audio = source_utils.check_foreign_audio()
		except:
			source_utils.scraper_error('AIOSTREAMS')
			return sources

		for file in files:
			try:
				package, episode_start = None, 0
				hash = file['infoHash']
				file_title = (file['folderName'] or file['filename']).replace('┈➤', '\n').split('\n')

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
					seeders = file['seeders']
					if self.min_seeders > seeders: continue
				except: seeders = 0

				quality, info = source_utils.get_release_quality(name_info, url)
				try:
					size = f"{float(file['size']) / 1073741824:.2f} GB"
					dsize, isize = source_utils._size(size)
					info.insert(0, isize)
				except: dsize = 0
				info = ' | '.join(info)

				item = {
					'source': 'torrent', 'language': 'en', 'direct': False, 'debridonly': True,
					'provider': 'aiostreams', 'hash': hash, 'url': url, 'name': name, 'name_info': name_info,
					'quality': quality, 'info': info, 'size': dsize, 'seeders': seeders
				}
				if package: item['package'] = package
				if package == 'show': item.update({'last_season': last_season})
				if episode_start: item.update({'episode_start': episode_start, 'episode_end': episode_end}) # for partial season packs
				sources_append(item)
			except:
				source_utils.scraper_error('AIOSTREAMS')
		return sources

	def _headers(self):
		return {'x-aiostreams-user-data': (
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
			'LA0KICAgICAgImluc3RhbmNlSWQiOiAiNDUwIiwNCiAgICAgICJlbmFibGVkIjogdHJ1ZSwNCiAgICAg'
			'ICJvcHRpb25zIjogew0KICAgICAgICAibmFtZSI6ICJNZWRpYUZ1c2lvbiIsDQogICAgICAgICJ0aW1l'
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
		)}

