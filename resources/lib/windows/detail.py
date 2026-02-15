import json
from threading import Thread
from windows import BaseDialog, location, open_window
from modules import settings
from modules.utils import get_datetime
from modules.kodi_utils import media_path, hide_busy_dialog, close_all_dialog, local_string as ls, logger

poster_empty = media_path('box_office.png')
fanart_empty = BaseDialog.fanart
people_icon = media_path('people.png')
BACK_BTN, PLAY_BTN, TRAILER_BTN, LIBRARY_BTN, EXTRAS_BTN = 5000, 5001, 5002, 5003, 5004
CAST_PANEL = 5010
SOURCES_LIST = 5020
EPISODES_LIST = 5030
SEASON_BTN, PREV_BTN, NEXT_BTN = 5040, 5041, 5042
quality_colors = {
	'4k': 'FFC4A747', '1080p': 'FF00BFA5', '720p': 'FF2196F3', 'sd': 'FF888888',
	'tele': 'FF555555', 'cam': 'FF555555', 'scr': 'FF555555'
}

class Detail(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, args)
		self.meta = kwargs['meta']
		self.is_home = kwargs.get('is_home', 'false')
		self.selected = None
		self.media_type = self.meta.get('mediatype', 'movie')
		self.tmdb_id = self.meta.get('tmdb_id')
		self.imdb_id = self.meta.get('imdb_id', '')
		self._threads = []
		self.current_season = 1
		self.total_seasons = self.meta.get('total_seasons', 1)
		self.season_data = self.meta.get('season_data', [])
		self.poster_main, self.poster_backup, self.fanart_main, self.fanart_backup = settings.get_art_provider()
		self.meta_user_info = settings.metadata_user_info()
		self.set_properties()

	def onInit(self):
		thread_targets = [
			(self.load_cast,),
			(self.load_ratings,),
		]
		if self.media_type == 'tvshow':
			thread_targets.append((self.load_episodes,))
		for target_info in thread_targets:
			target = target_info[0]
			args = target_info[1:] if len(target_info) > 1 else ()
			t = Thread(target=self._safe_thread, args=(target,) + args, daemon=True)
			self._threads.append(t)
			t.start()
		if self.media_type == 'movie':
			self.setFocusId(PLAY_BTN)
		else:
			self.setFocusId(SEASON_BTN)

	def _safe_thread(self, target, *args):
		try:
			target(*args)
		except Exception as e:
			logger('detail._safe_thread', str(e))

	def run(self):
		self.doModal()
		self.clearProperties()
		self._threads.clear()
		hide_busy_dialog()
		return self.selected

	def onClick(self, controlID):
		if controlID == BACK_BTN:
			self.close()
		elif controlID == PLAY_BTN:
			self._play_media()
		elif controlID == TRAILER_BTN:
			self._play_trailer()
		elif controlID == LIBRARY_BTN:
			self._add_to_library()
		elif controlID == EXTRAS_BTN:
			self._open_extras()
		elif controlID == SOURCES_LIST:
			self._handle_source_selection()
		elif controlID == EPISODES_LIST:
			self._handle_episode_selection()
		elif controlID == SEASON_BTN:
			self._season_select()
		elif controlID == PREV_BTN:
			self._prev_season()
		elif controlID == NEXT_BTN:
			self._next_season()

	def onAction(self, action):
		if action in self.closing_actions:
			return self.close()
		if action in self.context_actions:
			focus_id = self.getFocusId()
			if focus_id == SOURCES_LIST:
				self._source_context_menu()
			elif focus_id == EPISODES_LIST:
				self._episode_context_menu()
			elif focus_id == CAST_PANEL:
				self._cast_info()

	def set_properties(self):
		self.setProperty('tikiskins.detail.media_type', self.media_type)
		self.setProperty('tikiskins.detail.title', self.meta.get('title', ''))
		# Clearlogo
		clearlogo = ''
		if settings.get_fanart_data():
			clearlogo = self.meta.get('clearlogo', '')
		if not clearlogo:
			clearlogo = self.meta.get('tmdblogo', '') or ''
		self.setProperty('tikiskins.detail.clearlogo', clearlogo)
		# Tagline
		tagline = self.meta.get('tagline', '')
		if not tagline:
			ei = self.meta.get('extra_info')
			if isinstance(ei, dict):
				tagline = ei.get('tagline', '')
		self.setProperty('tikiskins.detail.tagline', tagline)
		# Year and Runtime
		year = str(self.meta.get('year', ''))
		try:
			duration = int(float(self.meta.get('duration', 0)) / 60)
			runtime_str = '%dm' % duration if duration > 0 else ''
		except Exception:
			runtime_str = ''
		parts = [p for p in [year, runtime_str] if p]
		if self.media_type == 'tvshow':
			ts = self.meta.get('total_seasons', '')
			te = self.meta.get('total_aired_eps', '')
			if ts:
				parts.append('%s Seasons' % ts)
			if te:
				parts.append('%s Episodes' % te)
			network = self.meta.get('studio', '')
			if network:
				parts.append(network)
		self.setProperty('tikiskins.detail.year_runtime', '  ·  '.join(parts))
		# Genre
		self.setProperty('tikiskins.detail.genre', self.meta.get('genre', ''))
		# Plot
		plot = self.meta.get('tvshow_plot') if 'tvshow_plot' in self.meta else self.meta.get('plot', '')
		self.setProperty('tikiskins.detail.plot', plot or '')
		# Fanart
		fanart = self.meta.get(self.fanart_main) or self.meta.get(self.fanart_backup) or fanart_empty
		self.setProperty('tikiskins.detail.fanart', fanart)
		# TMDB rating as initial
		rating = self.meta.get('rating', 0)
		if rating:
			self.setProperty('tikiskins.detail.rating.tmdb', '%.1f' % rating)
		# Season label for TV
		if self.media_type == 'tvshow':
			self._update_season_label()

	def load_ratings(self):
		try:
			from indexers.mdblist_api import mdbl_media_info
			if not self.imdb_id or self.imdb_id == 'tt0000000':
				return
			data = mdbl_media_info(self.imdb_id, self.media_type)
			if not data or 'ratings' not in data:
				return
			sources = ('imdb', 'metacritic', 'tomatoes', 'trakt', 'tmdb')
			for r in data['ratings']:
				if r['source'] in sources and r['value']:
					self.setProperty('tikiskins.detail.rating.%s' % r['source'], str(r['value']))
		except Exception:
			pass

	def load_cast(self):
		cast = self.meta.get('cast', [])
		if not cast:
			return
		items = []
		for member in cast[:20]:
			try:
				listitem = self.make_listitem()
				name = member.get('name', '')
				role = member.get('role', '')
				thumb = member.get('thumbnail', '') or people_icon
				listitem.setProperty('tikiskins.detail.cast.name', name)
				listitem.setProperty('tikiskins.detail.cast.role', role)
				listitem.setProperty('tikiskins.detail.cast.thumb', thumb)
				items.append(listitem)
			except Exception:
				pass
		if items:
			try:
				self.getControl(CAST_PANEL).addItems(items)
			except Exception:
				pass

	def load_episodes(self):
		self.setProperty('tikiskins.detail.episodes.loading', 'true')
		try:
			from indexers import tmdb_api
			season_num = self.current_season
			data = tmdb_api.season_episodes_details(self.tmdb_id, season_num, self.meta_user_info['language'], self.meta_user_info['tmdb_api'])
			if not data:
				self.setProperty('tikiskins.detail.episodes.loading', '')
				return
			episodes = data.get('episodes', [])
			items = []
			from indexers.tmdb_api import tmdb_image_base
			for ep in episodes:
				try:
					listitem = self.make_listitem()
					ep_num = ep.get('episode_number', 0)
					ep_title = ep.get('name', '')
					ep_plot = ep.get('overview', '')
					air_date = ep.get('air_date', '')
					still_path = ep.get('still_path', '')
					thumb = tmdb_image_base % ('w300', still_path) if still_path else ''
					listitem.setProperty('tikiskins.detail.ep.number', 'E%02d' % ep_num)
					listitem.setProperty('tikiskins.detail.ep.title', ep_title)
					listitem.setProperty('tikiskins.detail.ep.plot', ep_plot)
					listitem.setProperty('tikiskins.detail.ep.airdate', air_date)
					listitem.setProperty('tikiskins.detail.ep.thumb', thumb)
					listitem.setProperty('tikiskins.detail.ep.season', str(season_num))
					listitem.setProperty('tikiskins.detail.ep.episode', str(ep_num))
					# Check watched status
					try:
						from caches.watched_cache import get_watched_info_tv, get_watched_status_episode
						watched_info = get_watched_info_tv(settings.watched_indicators())
						status = get_watched_status_episode(
							watched_info,
							str(self.tmdb_id), str(season_num), str(ep_num)
						)
						if status and status[0] == 1:
							listitem.setProperty('tikiskins.detail.ep.watched', 'true')
					except Exception:
						pass
					items.append(listitem)
				except Exception:
					pass
			try:
				control = self.getControl(EPISODES_LIST)
				control.reset()
				control.addItems(items)
			except Exception:
				pass
		except Exception as e:
			logger('detail.load_episodes', str(e))
		self.setProperty('tikiskins.detail.episodes.loading', '')

	def _update_season_label(self):
		self.setProperty('tikiskins.detail.season_label', 'Season %d' % self.current_season)

	def _season_select(self):
		if not self.season_data:
			return
		from modules.kodi_utils import select_dialog
		choices = []
		display_items = []
		for sd in self.season_data:
			sn = sd.get('season_number', 0)
			if sn == 0:
				continue
			ec = sd.get('episode_count', 0)
			choices.append(sn)
			display_items.append({'line1': 'Season %d (%d episodes)' % (sn, ec)})
		if not choices:
			return
		import json as _json
		kwargs = {'items': _json.dumps(display_items), 'heading': 'Select Season', 'enumerate': 'false', 'multi_choice': 'false', 'multi_line': 'false'}
		result = select_dialog(choices, **kwargs)
		if result is not None:
			self.current_season = result
			self._update_season_label()
			t = Thread(target=self._safe_thread, args=(self.load_episodes,), daemon=True)
			self._threads.append(t)
			t.start()

	def _prev_season(self):
		if self.current_season > 1:
			self.current_season -= 1
			self._update_season_label()
			t = Thread(target=self._safe_thread, args=(self.load_episodes,), daemon=True)
			self._threads.append(t)
			t.start()

	def _next_season(self):
		if self.current_season < self.total_seasons:
			self.current_season += 1
			self._update_season_label()
			t = Thread(target=self._safe_thread, args=(self.load_episodes,), daemon=True)
			self._threads.append(t)
			t.start()

	def _play_media(self):
		close_all_dialog()
		if self.media_type == 'movie':
			from modules.sources import SourceSelect
			params = {'mode': 'play_media', 'media_type': 'movie', 'tmdb_id': str(self.tmdb_id)}
			self.selected = params
			self.close()
		else:
			# For TV shows, play first unwatched or selected episode
			self.selected = {'mode': 'build_season_list', 'tmdb_id': str(self.tmdb_id)}
			self.close()

	def _play_trailer(self):
		trailer = self.meta.get('trailer', '')
		all_trailers = self.meta.get('all_trailers', [])
		if not trailer and not all_trailers:
			return
		try:
			from modules import dialogs
			poster = self.meta.get(self.poster_main) or self.meta.get(self.poster_backup) or poster_empty
			chosen = dialogs.trailer_choice(self.media_type, poster, self.tmdb_id, trailer, all_trailers)
			if not chosen or chosen == 'canceled':
				return
			from windows import videoplayer
			videoplayer(chosen)
		except Exception:
			pass

	def _add_to_library(self):
		try:
			from modules.kodi_utils import notification
			notification('Added to Library', time=2000)
		except Exception:
			pass

	def _open_extras(self):
		from modules.kodi_utils import get_property
		is_widget = get_property('pov.home_is_widget') or 'true'
		kwargs = {'meta': self.meta, 'is_widget': is_widget}
		self.open_window(('windows.extras', 'Extras'), 'extras.xml', **kwargs)

	def _handle_source_selection(self):
		try:
			chosen = self.get_listitem(SOURCES_LIST)
			source_json = chosen.getProperty('tikiskins.detail.source.data')
			if source_json:
				source = json.loads(source_json)
				if 'UNCACHED' not in chosen.getProperty('tikiskins.detail.source.source_type'):
					from modules.debrid import Source
					self.selected = ('play_source', source)
					self.close()
		except Exception:
			pass

	def _handle_episode_selection(self):
		try:
			chosen = self.get_listitem(EPISODES_LIST)
			season = chosen.getProperty('tikiskins.detail.ep.season')
			episode = chosen.getProperty('tikiskins.detail.ep.episode')
			if season and episode:
				close_all_dialog()
				self.selected = {
					'mode': 'play_media',
					'media_type': 'episode',
					'tmdb_id': str(self.tmdb_id),
					'season': season,
					'episode': episode
				}
				self.close()
		except Exception:
			pass

	def _source_context_menu(self):
		pass

	def _episode_context_menu(self):
		try:
			chosen = self.get_listitem(EPISODES_LIST)
			season = chosen.getProperty('tikiskins.detail.ep.season')
			episode = chosen.getProperty('tikiskins.detail.ep.episode')
			watched = chosen.getProperty('tikiskins.detail.ep.watched')
			if not season or not episode:
				return
			from modules.kodi_utils import select_dialog
			choices = ['Mark as Watched' if not watched else 'Mark as Unwatched', 'Play']
			actions = ['toggle_watched', 'play']
			display_items = [{'line1': c} for c in choices]
			import json as _json
			kwargs = {'items': _json.dumps(display_items), 'heading': 'Episode Options', 'enumerate': 'false', 'multi_choice': 'false', 'multi_line': 'false'}
			result = select_dialog(actions, **kwargs)
			if result == 'play':
				self._handle_episode_selection()
			elif result == 'toggle_watched':
				try:
					from caches.watched_cache import mark_as_watched_unwatched_episode
					params = {
						'tmdb_id': str(self.tmdb_id),
						'season': season,
						'episode': episode,
						'action': 'mark_as_unwatched' if watched else 'mark_as_watched'
					}
					mark_as_watched_unwatched_episode(params)
					# Reload episodes
					t = Thread(target=self._safe_thread, args=(self.load_episodes,), daemon=True)
					self._threads.append(t)
					t.start()
				except Exception:
					pass
		except Exception:
			pass

	def _cast_info(self):
		try:
			chosen = self.get_listitem(CAST_PANEL)
			name = chosen.getProperty('tikiskins.detail.cast.name')
			if name:
				from indexers.people import person_data_dialog
				person_data_dialog({'query': name})
		except Exception:
			pass
