import json
from threading import Thread
from windows import BaseDialog, location, open_window
from modules import settings
from modules.utils import get_datetime
from modules.kodi_utils import media_path, hide_busy_dialog, close_all_dialog, local_string as ls
# from modules.kodi_utils import logger

poster_empty = media_path('box_office.png')
fanart_empty = BaseDialog.fanart
ROW1_ID, ROW2_ID = 3001, 3002
WATCH_BTN, EXTRAS_BTN, TRAILER_BTN = 3000, 3010, 3011
ANIME_BTN, SETTINGS_BTN = 9001, 9002

class Home(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.selected = None
		self.hero_items = []
		self.hero_index = 0
		self._threads = []
		self.meta_user_info = settings.metadata_user_info()
		self.poster_resolution = self.meta_user_info['image_resolution']['poster']
		self.fanart_resolution = self.meta_user_info['image_resolution']['fanart']
		self.poster_main, self.poster_backup, self.fanart_main, self.fanart_backup = settings.get_art_provider()

	def onInit(self):
		thread_targets = [
			(self.load_hero_items,),
			(self.load_row, 'row1', self._get_row1_data, 'Popular Movies'),
			(self.load_row, 'row2', self._get_row2_data, 'Trending TV'),
		]
		for target_info in thread_targets:
			target = target_info[0]
			args = target_info[1:] if len(target_info) > 1 else ()
			t = Thread(target=self._safe_thread, args=(target,) + args, daemon=True)
			self._threads.append(t)
			t.start()
		self.setFocusId(WATCH_BTN)

	def _safe_thread(self, target, *args):
		try:
			target(*args)
		except Exception:
			pass

	def run(self):
		self.doModal()
		self.clearProperties()
		self._threads.clear()
		hide_busy_dialog()
		if self.selected:
			from modules.kodi_utils import execute_builtin
			execute_builtin(self.selected)

	def onClick(self, controlID):
		if controlID == WATCH_BTN:
			self._play_current_hero()
		elif controlID == EXTRAS_BTN:
			self._open_extras()
		elif controlID == TRAILER_BTN:
			self._play_trailer()
		elif controlID == ANIME_BTN:
			close_all_dialog()
			self.selected = 'ActivateWindow(Videos,plugin://plugin.video.pov/?mode=build_movie_list&action=tmdb_moviesanime_popular&name=Anime+Movies,return)'
			self.close()
		elif controlID == SETTINGS_BTN:
			close_all_dialog()
			self.selected = 'RunPlugin(plugin://plugin.video.pov/?mode=open_settings)'
			self.close()
		elif controlID in (ROW1_ID, ROW2_ID):
			self._handle_row_selection(controlID)

	def onAction(self, action):
		if action in self.closing_actions:
			return self.close()
		focus_id = self.getFocusId()
		if focus_id in (WATCH_BTN, EXTRAS_BTN, TRAILER_BTN):
			if action in self.left_actions:
				if focus_id == WATCH_BTN:
					self._hero_prev()
			elif action in self.right_actions:
				if focus_id == TRAILER_BTN:
					self._hero_next()
		if action in self.context_actions:
			if focus_id in (ROW1_ID, ROW2_ID):
				self._handle_row_context(focus_id)

	def _hero_prev(self):
		if self.hero_index > 0:
			self.hero_index -= 1
			self._update_hero_display()

	def _hero_next(self):
		if self.hero_index < len(self.hero_items) - 1:
			self.hero_index += 1
			self._update_hero_display()

	def _play_current_hero(self):
		if not self.hero_items:
			return
		meta = self.hero_items[self.hero_index]
		media_type = meta.get('mediatype', 'movie')
		tmdb_id = meta.get('tmdb_id')
		if not tmdb_id:
			return
		if media_type == 'movie':
			# For movies, go directly to source selection
			close_all_dialog()
			self.selected = 'RunPlugin(%s)' % self.build_url({'mode': 'play_media', 'media_type': 'movie', 'tmdb_id': tmdb_id})
			self.close()
		else:
			# For TV shows, open detail view for season/episode selection
			kwargs = {'meta': meta, 'is_home': 'true'}
			result = self.open_window(('windows.detail', 'Detail'), 'detail.xml', **kwargs)
			if result and isinstance(result, dict):
				mode = result.get('mode', '')
				if mode == 'play_media':
					close_all_dialog()
					from modules.sources import SourceSelect
					SourceSelect.factory(result)
				elif mode:
					close_all_dialog()
					from modules.kodi_utils import execute_builtin
					url_str = '&'.join('%s=%s' % (k, v) for k, v in result.items())
					execute_builtin('ActivateWindow(Videos,plugin://plugin.video.pov/?%s,return)' % url_str)
					self.close()

	def _open_extras(self):
		if not self.hero_items:
			return
		meta = self.hero_items[self.hero_index]
		kwargs = {'meta': meta, 'is_home': 'true'}
		result = self.open_window(('windows.detail', 'Detail'), 'detail.xml', **kwargs)
		if result:
			if isinstance(result, dict):
				mode = result.get('mode', '')
				if mode == 'play_media':
					close_all_dialog()
					from modules.sources import SourceSelect
					SourceSelect.factory(result)
				elif mode:
					close_all_dialog()
					from modules.kodi_utils import execute_builtin
					url_str = '&'.join('%s=%s' % (k, v) for k, v in result.items())
					execute_builtin('ActivateWindow(Videos,plugin://plugin.video.pov/?%s,return)' % url_str)
					self.close()

	def _play_trailer(self):
		if not self.hero_items:
			return
		meta = self.hero_items[self.hero_index]
		trailer = meta.get('trailer', '')
		all_trailers = meta.get('all_trailers', [])
		if not trailer and not all_trailers:
			return
		from modules import dialogs
		media_type = meta.get('mediatype', 'movie')
		poster = meta.get(self.poster_main) or meta.get(self.poster_backup) or poster_empty
		tmdb_id = meta.get('tmdb_id')
		chosen = dialogs.trailer_choice(media_type, poster, tmdb_id, trailer, all_trailers)
		if not chosen or chosen == 'canceled':
			return
		from windows import videoplayer
		videoplayer(chosen)

	def _handle_row_selection(self, control_id):
		try:
			chosen_listitem = self.get_listitem(control_id)
			tmdb_id = chosen_listitem.getProperty('tikiskins.home.row.tmdb_id')
			media_type = chosen_listitem.getProperty('tikiskins.home.row.media_type')
			if not tmdb_id:
				return
			from indexers import metadata
			current_date = get_datetime()
			if media_type == 'movie':
				meta = metadata.movie_meta('tmdb_id', int(tmdb_id), self.meta_user_info, current_date)
			else:
				meta = metadata.tvshow_meta('tmdb_id', int(tmdb_id), self.meta_user_info, current_date)
			if not meta or meta.get('blank_entry'):
				return
			kwargs = {'meta': meta, 'is_home': 'true'}
			result = self.open_window(('windows.detail', 'Detail'), 'detail.xml', **kwargs)
			if result:
				if isinstance(result, dict):
					mode = result.get('mode', '')
					if mode == 'play_media':
						close_all_dialog()
						from modules.sources import SourceSelect
						SourceSelect.factory(result)
					elif mode:
						close_all_dialog()
						from modules.kodi_utils import execute_builtin
						url_str = '&'.join('%s=%s' % (k, v) for k, v in result.items())
						execute_builtin('ActivateWindow(Videos,plugin://plugin.video.pov/?%s,return)' % url_str)
						self.close()
		except Exception:
			pass

	def _handle_row_context(self, control_id):
		try:
			chosen_listitem = self.get_listitem(control_id)
			tmdb_id = chosen_listitem.getProperty('tikiskins.home.row.tmdb_id')
			media_type = chosen_listitem.getProperty('tikiskins.home.row.media_type')
			if not tmdb_id:
				return
			close_all_dialog()
			if media_type == 'movie':
				url_params = {'mode': 'play_media', 'media_type': 'movie', 'tmdb_id': tmdb_id}
			else:
				url_params = {'mode': 'build_season_list', 'tmdb_id': tmdb_id}
			self.selected = 'ActivateWindow(Videos,%s,return)' % self.build_url(url_params)
			self.close()
		except Exception:
			pass

	def load_hero_items(self):
		from indexers import metadata, tmdb_api
		from indexers.mdblist_api import mdbl_media_info
		current_date = get_datetime()
		try:
			popular = tmdb_api.tmdb_movies_popular(1)
			results = popular.get('results', [])[:8]
		except Exception:
			results = []
		try:
			tv_popular = tmdb_api.tmdb_tv_popular(1)
			tv_results = tv_popular.get('results', [])[:4]
		except Exception:
			tv_results = []
		combined = []
		for item in results:
			try:
				meta = metadata.movie_meta('tmdb_id', item['id'], self.meta_user_info, current_date)
				if meta and not meta.get('blank_entry'):
					combined.append(meta)
			except Exception:
				pass
		for item in tv_results:
			try:
				meta = metadata.tvshow_meta('tmdb_id', item['id'], self.meta_user_info, current_date)
				if meta and not meta.get('blank_entry'):
					combined.append(meta)
			except Exception:
				pass
		self.hero_items = combined
		if self.hero_items:
			self._update_hero_display()
			# Load ratings for current hero item in background
			t = Thread(target=self._safe_thread, args=(self._load_hero_ratings,), daemon=True)
			self._threads.append(t)
			t.start()

	def _load_hero_ratings(self):
		try:
			from indexers.mdblist_api import mdbl_media_info
			meta = self.hero_items[self.hero_index]
			imdb_id = meta.get('imdb_id', '')
			media_type = meta.get('mediatype', 'movie')
			if not imdb_id or imdb_id == 'tt0000000':
				return
			data = mdbl_media_info(imdb_id, media_type)
			if data and 'ratings' in data:
				sources = ('imdb', 'metacritic', 'tomatoes', 'trakt', 'tmdb')
				ratings = data['ratings']
				if 'score' in data:
					ratings.append({'source': 'mdblist', 'value': data['score']})
				for r in ratings:
					if r['source'] in sources and r['value']:
						self.setProperty('tikiskins.home.hero.rating.%s' % r['source'], str(r['value']))
		except Exception:
			pass

	def _update_hero_display(self):
		if not self.hero_items:
			return
		meta = self.hero_items[self.hero_index]
		media_type = meta.get('mediatype', 'movie')
		# Clear previous ratings
		for src in ('imdb', 'metacritic', 'tomatoes', 'trakt', 'tmdb', 'mdblist'):
			self.setProperty('tikiskins.home.hero.rating.%s' % src, '')
		# Basic info
		self.setProperty('tikiskins.home.hero.title', meta.get('title', ''))
		self.setProperty('tikiskins.home.hero.media_type', media_type)
		# Tagline
		tagline = meta.get('tagline', '')
		if not tagline:
			tagline = meta.get('extra_info', {}).get('tagline', '')
		self.setProperty('tikiskins.home.hero.tagline', tagline)
		# Images
		fanart = meta.get(self.fanart_main) or meta.get(self.fanart_backup) or fanart_empty
		self.setProperty('tikiskins.home.hero.fanart', fanart)
		clearlogo = ''
		if settings.get_fanart_data():
			clearlogo = meta.get('clearlogo', '')
		if not clearlogo:
			clearlogo = meta.get('tmdblogo', '') or ''
		self.setProperty('tikiskins.home.hero.clearlogo', clearlogo)
		# Year
		self.setProperty('tikiskins.home.hero.year', str(meta.get('year', '')))
		# Duration
		try:
			duration_mins = int(float(meta.get('duration', 0)) / 60)
			if duration_mins > 0:
				self.setProperty('tikiskins.home.hero.duration', '%dm' % duration_mins)
			else:
				self.setProperty('tikiskins.home.hero.duration', '')
		except Exception:
			self.setProperty('tikiskins.home.hero.duration', '')
		# TV-specific
		if media_type == 'tvshow':
			total_seasons = meta.get('total_seasons', '')
			total_eps = meta.get('total_aired_eps', '')
			if total_seasons:
				self.setProperty('tikiskins.home.hero.seasons', '%s Seasons' % total_seasons)
			else:
				self.setProperty('tikiskins.home.hero.seasons', '')
			if total_eps:
				self.setProperty('tikiskins.home.hero.episodes', '%s Episodes' % total_eps)
			else:
				self.setProperty('tikiskins.home.hero.episodes', '')
		else:
			self.setProperty('tikiskins.home.hero.seasons', '')
			self.setProperty('tikiskins.home.hero.episodes', '')
		# Network
		self.setProperty('tikiskins.home.hero.network', meta.get('studio', ''))
		# Genre
		self.setProperty('tikiskins.home.hero.genre', meta.get('genre', ''))
		# Plot
		plot = meta.get('tvshow_plot') if 'tvshow_plot' in meta else meta.get('plot', '')
		self.setProperty('tikiskins.home.hero.plot', plot or '')
		# TMDB rating as initial display
		rating = meta.get('rating', 0)
		if rating:
			self.setProperty('tikiskins.home.hero.rating.tmdb', '%.1f' % rating)
		# Navigation indicators
		self.setProperty('tikiskins.home.hero.has_prev', 'true' if self.hero_index > 0 else '')
		self.setProperty('tikiskins.home.hero.has_next', 'true' if self.hero_index < len(self.hero_items) - 1 else '')
		self.setProperty('tikiskins.home.hero.position', '%d / %d' % (self.hero_index + 1, len(self.hero_items)))
		# Load ratings in background
		t = Thread(target=self._safe_thread, args=(self._load_hero_ratings,), daemon=True)
		self._threads.append(t)
		t.start()

	def load_row(self, row_key, data_func, title):
		try:
			from indexers import tmdb_api
			self.setProperty('tikiskins.home.%s.title' % row_key, title)
			data, media_type = data_func(tmdb_api)
			items = self._build_row_items(data, media_type)
			panel_id = ROW1_ID if row_key == 'row1' else ROW2_ID
			self.getControl(panel_id).addItems(items)
		except Exception:
			pass

	def _get_row1_data(self, tmdb_api):
		result = tmdb_api.tmdb_movies_popular(1)
		return result.get('results', [])[:20], 'movie'

	def _get_row2_data(self, tmdb_api):
		result = tmdb_api.tmdb_tv_popular(1)
		return result.get('results', [])[:20], 'tvshow'

	def _build_row_items(self, data, media_type):
		from indexers.tmdb_api import tmdb_image_base
		items = []
		name_key = 'title' if media_type == 'movie' else 'name'
		for item in data:
			try:
				listitem = self.make_listitem()
				set_prop = listitem.setProperty
				title = item.get(name_key, '')
				poster_path = item.get('poster_path', '')
				if poster_path:
					poster = tmdb_image_base % (self.poster_resolution, poster_path)
				else:
					poster = poster_empty
				set_prop('tikiskins.home.row.title', title)
				set_prop('tikiskins.home.row.poster', poster)
				set_prop('tikiskins.home.row.tmdb_id', str(item.get('id', '')))
				set_prop('tikiskins.home.row.media_type', media_type)
				items.append(listitem)
			except Exception:
				pass
		return items
