import sys
from modules.kodi_utils import parse_qsl, logger, get_property, get_infolabel, external_browse

_episode_build_modes = frozenset(('build_in_progress_episode', 'build_next_episode', 'build_my_calendar', 'build_my_anime_calendar', 'build_anime_calendar'))

def runmode(cls, params, mode):
	call = getattr(cls(params), mode, None)
	return call() if callable(call) else None

class Router:
	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		if get_property('pov_rli_fix') == 'true' and external_browse():
			message = f"pov not in '{get_infolabel('Container.PluginName')}'"
			raise SystemExit(message)

	def routing(self, sys):
		try: params = dict(parse_qsl(sys.argv[2][1:]))
		except Exception as e:
			logger('routing parse error', f'{e} - argv: {sys.argv}')
			return

		params_get = params.get
		mode = params_get('mode', 'navigator.main')
		# Pre-split mode once for reuse across routing branches
		mode_parts = mode.split('.')
		mode_action = mode_parts[-1] if len(mode_parts) > 1 else mode
		if 'navigator.' in mode:
			from indexers.navigator import Navigator
			runmode(Navigator, params, mode_action)
		elif 'menu_editor.' in mode:
			from modules.menu_editor import MenuEditor
			runmode(MenuEditor, params, mode_action)
		elif 'discover.' in mode:
			from indexers.discover import Discover
			runmode(Discover, params, mode_action)
		elif mode == 'media_play':
			from modules.kodi_utils import player, close_all_dialog
			close_all_dialog()
			player.play(params.get('url', ''))
		elif mode == 'play_media':
			from modules.sources import SourceSelect
			SourceSelect.factory(params)
		elif 'choice' in mode:
			from modules import dialogs
			# Dict dispatch for O(1) choice routing instead of O(n) elif chain
			_choice_dispatch = {
				'scraper_color_choice': lambda: dialogs.scraper_color_choice(params['setting']),
				'scraper_dialog_color_choice': lambda: dialogs.scraper_dialog_color_choice(params['setting']),
				'scraper_quality_color_choice': lambda: dialogs.scraper_quality_color_choice(params['setting']),
				'imdb_images_choice': lambda: dialogs.imdb_images_choice(params['imdb_id'], params['rootname']),
				'set_quality_choice': lambda: dialogs.set_quality_choice(params['quality_setting']),
				'results_sorting_choice': lambda: dialogs.results_sorting_choice(),
				'results_layout_choice': lambda: dialogs.results_layout_choice(),
				'options_menu_choice': lambda: dialogs.options_menu(params),
				'meta_language_choice': lambda: dialogs.meta_language_choice(),
				'extras_menu_choice': lambda: dialogs.extras_menu(params),
				'favourites_choice': lambda: dialogs.favourites_choice(params),
				'trakt_manager_choice': lambda: dialogs.trakt_manager_choice(params),
				'tmdb_manager_choice': lambda: dialogs.tmdb_manager_choice(params),
				'mdbl_manager_choice': lambda: dialogs.mdbl_manager_choice(params),
				'folder_scraper_manager_choice': lambda: dialogs.folder_scraper_manager_choice(),
				'set_language_filter_choice': lambda: dialogs.set_language_filter_choice(params['filter_setting']),
				'extras_lists_choice': lambda: dialogs.extras_lists_choice(),
				'random_choice': lambda: dialogs.random_choice(params['mode'], params),
			}
			handler = _choice_dispatch.get(mode)
			if handler: handler()
		elif 'trakt.' in mode:
			if 'trakt_account_info' in mode:
				from indexers.trakt import trakt_account_info
				trakt_account_info()
			elif 'hide_unhide_trakt_items' in mode:
				from indexers.trakt_api import hide_unhide_trakt_items
				hide_unhide_trakt_items(params['action'], params['media_type'], params['media_id'], params['section'])
			else:
				from modules.utils import manual_function_import
				function = manual_function_import('indexers.trakt_api', mode_action)
				function(params)
		elif 'mdblist.' in mode:
			if 'mdbl_account_info' in mode:
				from indexers.mdblist import mdbl_account_info
				mdbl_account_info()
		elif 'simkl.' in mode:
			if 'simkl_account_info' in mode:
				from indexers.simkl import simkl_account_info
				simkl_account_info()
		elif 'tmdb.' in mode:
			if 'edit_tmdb_list' in mode:
				from indexers.tmdb import edit_tmdb_list
				edit_tmdb_list(params)
			elif 'update_tmdb_list' in mode:
				from indexers.tmdb import update_tmdb_list
				update_tmdb_list(params)
			else:
				from modules.utils import manual_function_import
				function = manual_function_import('indexers.tmdb_api', mode_action)
				function(params)
		elif 'build' in mode:
			_build_list_map = {'build_trakt_list': 'indexers.trakt', 'build_mdb_list': 'indexers.mdblist', 'build_simkl_list': 'indexers.simkl', 'build_tmdb_list': 'indexers.tmdb'}
			_build_match = _build_list_map.get(mode)
			if _build_match:
				from modules.utils import manual_function_import
				function = manual_function_import(_build_match, mode_action)
				function(params)
			elif mode == 'build_movie_list':
				from indexers.movies import Indexer
				Indexer(params).run()
			elif mode == 'build_tvshow_list':
				from indexers.tvshows import Indexer
				Indexer(params).run()
			elif mode in ('build_season_list', 'build_episode_list'):
				from indexers.seasons import Seasons
				Seasons(params).run()
			elif mode in _episode_build_modes:
				from indexers.episodes import Indexer
				Indexer(params).run()
			elif mode == 'build_navigate_to_page':
				from modules.dialogs import build_navigate_to_page
				build_navigate_to_page(params)
			elif mode == 'imdb_build_user_lists':
				from indexers.imdb_api import imdb_build_user_lists
				imdb_build_user_lists(params_get('media_type'))
			elif mode == 'build_popular_people':
				from indexers.people import popular_people
				popular_people()
			elif mode == 'imdb_build_keyword_results':
				from indexers.imdb_api import imdb_build_keyword_results
				imdb_build_keyword_results(params['media_type'], params['query'])
		elif 'watched_unwatched' in mode:
			from caches import watched_cache as wc
			_watched_dispatch = {
				'mark_as_watched_unwatched_episode': lambda: wc.mark_as_watched_unwatched_episode(params),
				'mark_as_watched_unwatched_season': lambda: wc.mark_as_watched_unwatched_season(params),
				'mark_as_watched_unwatched_tvshow': lambda: wc.mark_as_watched_unwatched_tvshow(params),
				'mark_as_watched_unwatched_movie': lambda: wc.mark_as_watched_unwatched_movie(params),
				'watched_unwatched_erase_bookmark': lambda: wc.erase_bookmark(params_get('media_type'), params_get('tmdb_id'), params_get('season', ''), params_get('episode', ''), params_get('refresh', 'false')),
			}
			handler = _watched_dispatch.get(mode)
			if handler: handler()
		elif 'toggle' in mode:
			if mode == 'toggle_language_invoker':
				from modules.kodi_utils import toggle_language_invoker
				toggle_language_invoker()
		elif 'history' in mode:
			if mode == 'search_history':
				from indexers.history import search_history
				search_history(params)
			elif mode == 'clear_search_history':
				from indexers.history import clear_search_history
				clear_search_history()
			elif mode == 'remove_from_history':
				from indexers.history import remove_from_search_history
				remove_from_search_history(params)
			elif mode == 'discover_remove_from_history':
				from indexers.discover import remove_from_history
				remove_from_history(params)
			elif mode == 'discover_remove_all_history':
				from indexers.discover import remove_all_history
				remove_all_history(params)
		elif 'easynews.' in mode:
			from modules.utils import manual_function_import
			function = manual_function_import('debrids.easynews', mode_action)
			function(params)
		elif 'alldebrid' in mode:
			from debrids.alldebrid import Indexer, resolve_ad
			if 'resolve_' in mode: resolve_ad(params)
			else: Indexer().run(params)
		elif 'premiumize' in mode:
			from debrids.premiumize import Indexer
			Indexer().run(params)
		elif 'real_debrid' in mode:
			from debrids.real_debrid import Indexer, resolve_rd
			if 'resolve_' in mode: resolve_rd(params)
			else: Indexer().run(params)
		elif 'torbox' in mode:
			from debrids.torbox import Indexer, resolve_tb
			if 'resolve_' in mode: resolve_tb(params)
			else: Indexer().run(params)
		elif 'offcloud' in mode:
			from debrids.offcloud import Indexer
			Indexer().run(params)
		elif 'easydebrid' in mode:
			from debrids.easydebrid import Indexer
			Indexer().run(params)
		elif '_settings' in mode:
			from modules import kodi_utils as ku
			_settings_dispatch = {
				'open_settings': lambda: ku.open_settings(params_get('query')),
				'clean_settings': lambda: ku.clean_settings(),
				'clean_settings_window_properties': lambda: ku.clean_settings_window_properties(),
			}
			handler = _settings_dispatch.get(mode)
			if handler: handler()
		elif '_cache' in mode:
			from modules.cache import clear_all_cache, clear_cache
			if mode == 'clear_all_cache': clear_all_cache()
			else: clear_cache(params_get('cache'))
		elif '_image' in mode:
			from indexers.images import Images
			Images().run(params)
		elif '_text' in mode:
			from modules.kodi_utils import show_text
			show_text(params_get('heading'), params_get('text'), params_get('file'), params_get('font_size', 'small'), params_get('kodi_log', 'false') == 'true')
		elif '_view' in mode:
			from modules import kodi_utils as kv
			_view_dispatch = {
				'choose_view': lambda: kv.choose_view(params['view_type'], params_get('content', '')),
				'set_view': lambda: kv.set_view(params['view_type']),
				'clear_view': lambda: kv.clear_view(params['view_type']),
			}
			handler = _view_dispatch.get(mode)
			if handler: handler()
		##EXTRA modes##
		elif mode == 'get_search_term':
			from indexers.history import get_search_term
			get_search_term(params)
		elif mode == 'person_search':
			from indexers.people import person_search
			person_search(params['query'])
		elif 'person_data_dialog' in mode:
			from indexers.people import person_data_dialog
			person_data_dialog(params)
		elif mode == 'downloader':
			from modules.downloader import runner
			runner(params)
		elif mode == 'clean_databases':
			from modules.cache import clean_databases
			clean_databases()
		elif mode == 'clean_thumbnails':
			from modules.thumbnails import thumb_cleaner
			thumb_cleaner()
		elif mode == 'manual_add_nzb_to_cloud':
			from modules.debrid import Source
			Source(params).manual_add_nzb_to_cloud()
		elif mode == 'upload_logfile':
			from modules.kodi_utils import upload_logfile
			upload_logfile()
		elif mode == 'myservices':
			from modules.myservices import authorize
			authorize()
		elif 'refer_link' in mode:
			from modules.myservices import refer_link
			refer_link(params['query'])
		##FENOM modes###
		elif mode == 'undesirablesInput':
			from caches.undesirables_cache import undesirablesInput
			undesirablesInput()
		elif mode == 'undesirablesUserRemove':
			from caches.undesirables_cache import undesirablesUserRemove
			undesirablesUserRemove()
		elif mode == 'speedTest':
			from fenom.speedtest import magneto
			magneto()
		elif mode == 'pov_home':
			from windows import open_window
			result = open_window(('windows.home', 'Home'), 'home.xml')
			if result:
				from modules.kodi_utils import execute_builtin
				execute_builtin(result)
		elif mode == 'pov_detail':
			from windows import open_window
			import json as _json
			meta_str = params_get('meta')
			if not meta_str: return
			meta = _json.loads(meta_str)
			kwargs = {'meta': meta, 'is_home': params_get('is_home', 'false')}
			result = open_window(('windows.detail', 'Detail'), 'detail.xml', **kwargs)
			if result:
				if isinstance(result, dict):
					mode_action = result.get('mode')
					if mode_action == 'play_media':
						from modules.sources import SourceSelect
						SourceSelect.factory(result)
					elif mode_action:
						from modules.kodi_utils import execute_builtin
						execute_builtin('ActivateWindow(Videos,plugin://plugin.video.pov/?%s,return)' % '&'.join('%s=%s' % (k, v) for k, v in result.items()))
				elif isinstance(result, tuple) and result[0] == 'play_source':
					from modules.sources import SourceSelect
					SourceSelect.play_source(result[1], _json.loads(params_get('meta')))
		elif mode == 'stremio_addon_manager':
			from modules.stremio_manager import stremio_addon_manager
			stremio_addon_manager()
		elif mode == 'stremio_catalog':
			from indexers.stremio_catalog import StremioIndexer
			StremioIndexer(params).run()
		elif mode == 'stremio_clear_subtitles':
			from modules.stremio_subtitles import clear_subtitle_cache
			clear_subtitle_cache()
		elif mode == 'stremio_reconfigure_debrid':
			from modules.stremio_manager import reconfigure_all_addons_debrid
			reconfigure_all_addons_debrid()
		elif mode == 'stremio_debug_loop':
			from modules.stremio_manager import stremio_debug_loop
			stremio_debug_loop()


if __name__ == '__main__':
	with Router() as r: r.routing(sys)

