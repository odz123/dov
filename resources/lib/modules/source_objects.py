import json
from caches.providers_cache import ExternalProvidersCache
from indexers.metadata import movie_meta, tvshow_meta, season_episodes_meta, get_title
from modules.kodi_utils import local_string as ls, get_setting
from modules.settings import metadata_user_info, date_offset
from modules.source_utils import get_cache_expiry, get_filename_match, get_file_info, normalize
from modules.utils import clean_file_name, safe_string, remove_accents, get_datetime, adjust_premiered_date
# from modules.kodi_utils import logger

season_display, show_display, resolutions = ls(32537), ls(32089), '4K 1080p 720p SD total'
pack_check = (season_display, show_display)

def get_source_meta(params):
	params_get = params.get
	media_type, tmdb_id = params_get('media_type'), params_get('tmdb_id')
	season = int(params_get('season')) if 'season' in params else ''
	episode = int(params_get('episode')) if 'episode' in params else ''
	custom_title, custom_year = params_get('custom_title'), params_get('custom_year')
	custom_season = int(params_get('custom_season')) if 'custom_season' in params else None
	custom_episode = int(params_get('custom_episode')) if 'custom_episode' in params else None
	background = params_get('background', 'false') == 'true'
	if 'meta' in params: meta = json.loads(params['meta'])
	else:
		meta_user_info, adjust_hours, current_date = metadata_user_info(), date_offset(), get_datetime()
		if media_type == 'episode':
			meta = tvshow_meta('tmdb_id', tmdb_id, meta_user_info, current_date)
			try:
				episodes_data = season_episodes_meta(season, meta, meta_user_info)
				ep_data = next((i for i in episodes_data if i['episode'] == int(episode)))
				meta.update({
					'mediatype': 'episode', 'season': ep_data['season'], 'episode': ep_data['episode'],
					'premiered': ep_data['premiered'], 'ep_name': ep_data['title'], 'plot': ep_data['plot']
				})
			except: pass
		else: meta = movie_meta('tmdb_id', tmdb_id, meta_user_info, current_date)
	meta.update({'background': background, 'media_type': media_type, 'season': season, 'episode': episode})
	if custom_title: meta['custom_title'] = custom_title
	if custom_year: meta['custom_year'] = custom_year
	if custom_season: meta['custom_season'] = custom_season
	if custom_episode: meta['custom_episode'] = custom_episode
	expiry_times = get_cache_expiry(media_type, meta, season)
	title = get_title(meta)
	aliases = _make_alias_dict(meta, title)
	year = _get_search_year(meta)
	ep_name = _get_ep_name(meta)
	meta['search_info'] = {
		'media_type': media_type, 'expiry_times': expiry_times, 'year': year, 'aliases': aliases,
		'tmdb_id': tmdb_id, 'imdb_id': meta.get('imdb_id'), 'tvdb_id': meta.get('tvdb_id'),
		'title': title, 'ep_name': ep_name, 'total_seasons': meta.get('total_seasons', ''),
		'season': custom_season or season, 'episode': custom_episode or episode
	}
	return meta

def _make_alias_dict(meta, title):
	aliases = []
	meta_title = meta['title']
	original_title = meta['original_title']
	alternative_titles = meta.get('alternative_titles', [])
	country_codes = set([i.replace('GB', 'UK') for i in meta.get('country_codes', [])])
	if meta_title not in alternative_titles: alternative_titles.append(meta_title)
	if original_title not in alternative_titles: alternative_titles.append(original_title)
	if alternative_titles: aliases = [{'title': i, 'country': ''} for i in alternative_titles]
	if country_codes: aliases.extend([{'title': '%s %s' % (title, i), 'country': ''} for i in country_codes])
	normalized = ({'title': normalize(i['title']), 'country': i['country']} for i in aliases)
	aliases.extend(i for i in normalized if not i in aliases)
	return aliases

def _get_search_year(meta):
	if 'custom_year' in meta: return meta['custom_year']
	year = meta.get('year') or '0'
#	if active_external and get_setting('search.enable.yearcheck', 'false') == 'true':
	if get_setting('search.enable.yearcheck', 'false') == 'true':
		from indexers.imdb_api import imdb_movie_year
		try: year = str(imdb_movie_year(meta.get('imdb_id')) or year)
		except: pass
	return year

def _get_ep_name(meta):
	if meta.get('media_type') == 'episode':
		ep_name = meta.get('ep_name')
		try: ep_name = safe_string(remove_accents(ep_name))
		except: ep_name = safe_string(ep_name)
	else: ep_name = None
	return ep_name

class ExternalSource:
	def __init__(self, meta, args):
		self.sources = []
		self.meta, self.args = meta, args

	def results(self, info):
		try:
			self.media_type, self.tmdb_id, self.year = info['media_type'], str(info['tmdb_id']), info['year']
			self.season, self.episode, self.total_seasons = info['season'], info['episode'], info['total_seasons']
			self.title, self.orig_title, aliases = normalize(info['title']), info['title'], info['aliases']
			self.single_expiry, self.season_expiry, self.show_expiry = info['expiry_times']
			if self.media_type == 'episode':
				season_divider = (
					i['episode_count'] for i in self.meta['season_data']
					if int(i['season_number']) == int(self.meta['season'])
				)
				self.season_divider = int(next(season_divider, 1))
				self.show_divider = int(self.meta['total_aired_eps'])
				self.data = {
					'timeout': self.timeout, 'imdb': info['imdb_id'], 'tvdb': info['tvdb_id'], 'aliases': aliases,
					'title': normalize(info['ep_name']), 'tvshowtitle': self.title, 'year': self.year,
					'season': str(self.season), 'episode': str(self.episode), 'total_seasons': self.total_seasons
				}
				self.get_episode_source(*self.args)
			else:
				self.season_divider, self.show_divider, self.data = 1, 1, {
					'timeout': self.timeout, 'imdb': info['imdb_id'], 'aliases': aliases,
					'title': self.title, 'year': self.year
				}
				self.get_movie_source(*self.args)
		except: pass
		return self.sources

	def get_movie_source(self, provider, module):
		epc = ExternalProvidersCache()
		sources = epc.get(provider, self.media_type, self.tmdb_id, self.title, self.year, '', '')
		if sources is None:
			sources = module().sources(self.data, self.hostDict)
			sources = self.process_sources(provider, sources)
			epc.set(provider, self.media_type, self.tmdb_id, self.title, self.year, '', '', sources, self.single_expiry)
		if sources:
			self.sources.extend(sources)

	def get_episode_source(self, provider, module, pack):
		if pack in pack_check: s_check, e_check = '' if pack == show_display else self.season, ''
		else: s_check, e_check = self.season, self.episode
		epc = ExternalProvidersCache()
		sources = epc.get(provider, self.media_type, self.tmdb_id, self.title, self.year, s_check, e_check)
		if sources is None:
			if pack == show_display:
				expiry_hours = self.show_expiry
				sources = module().sources_packs(self.data, self.hostDict, search_series=True, total_seasons=self.total_seasons)
			elif pack == season_display:
				expiry_hours = self.season_expiry
				sources = module().sources_packs(self.data, self.hostDict)
			else:
				expiry_hours = self.single_expiry
				sources = module().sources(self.data, self.hostDict)
			sources = self.process_sources(provider, sources)
			epc.set(provider, self.media_type, self.tmdb_id, self.title, self.year, s_check, e_check, sources, expiry_hours)
		if sources:
			if pack == season_display: sources = [i for i in sources if not 'episode_start' in i or i['episode_start'] <= self.episode <= i['episode_end']]
			elif pack == show_display: sources = [i for i in sources if i['last_season'] >= self.season]
			self.sources.extend(sources)

	def process_sources(self, provider, sources):
		try:
			for i in sources:
				try:
					i_get = i.get
					if 'hash' in i: i['hash'] = str(i['hash']).lower()
					size, size_label, divider = 0, None, None
					if 'name' in i: URLName = clean_file_name(i_get('name')).replace('html', ' ').replace('+', ' ').replace('-', ' ')
					else: URLName = get_filename_match(self.orig_title, i_get('url'), i_get('name'))
					if 'name_info' in i: quality, extraInfo = get_file_info(name_info=i_get('name_info'))
					else: quality, extraInfo = get_file_info(url=i_get('url'))
					try:
						size = i_get('size')
						if 'package' in i and not i_get('true_size', False):
							if i_get('package') == 'season': divider = self.season_divider
							else: divider = self.show_divider
							size = float(size) / divider
							size_label = '%.2f GB' % size
						else: size_label = '%.2f GB' % size
					except: pass
					i.update({
						'external': True, 'provider': provider, 'scrape_provider': self.scrape_provider, 'URLName': URLName,
						'extraInfo': extraInfo, 'quality': quality, 'size_label': size_label, 'size': round(size, 2)
					})
					if not quality in self.resolutions: self.resolutions['SD'] += 1
					else: self.resolutions[quality] += 1
					self.resolutions['total'] += 1
				except: pass
		except: pass
		return sources

	scrape_provider = 'external'
	timeout = 10
	hostDict = {}
	resolutions = dict.fromkeys(resolutions.split(), 0)

