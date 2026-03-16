import sys
from queue import SimpleQueue
from threading import Thread
from indexers import simkl_api
from indexers.movies import Movies
from indexers.tvshows import TVShows
from modules import kodi_utils
from modules.utils import TaskPool
from modules.settings import nav_jump_use_alphabet
# logger = kodi_utils.logger

ls = kodi_utils.local_string
item_jump = kodi_utils.media_path('item_jump.png')
nextpage_str, jump2_str, simkl_str = ls(32799), ls(32964), 'Simkl'

status_labels = {
	'plantowatch': 'Plan to Watch',
	'watching': 'Watching',
	'completed': 'Completed',
	'hold': 'On Hold',
	'dropped': 'Dropped'
}

def get_simkl_watchlist(params):
	"""Build a Kodi directory of Simkl watchlist items for a given media_type and status."""
	def _thread_target(q):
		while True:
			try: target, *args = q.get_nowait()
			except Exception: break
			try: target(*args)
			except Exception: pass
	__handle__, _queue, is_widget = int(sys.argv[1]), SimpleQueue(), kodi_utils.external_browse()
	max_threads = int(kodi_utils.get_setting('pov.max_threads', '100'))
	use_alphabet = nav_jump_use_alphabet() > 0
	media_type = params.get('media_type', 'movies')
	status = params.get('status', '') or params.get('slug', 'plantowatch')
	name = params.get('name', status_labels.get(status, status))
	letter, page = params.get('new_letter', 'None'), int(params.get('new_page', '1'))
	results, total_pages = simkl_api.simkl_watchlist_items(media_type, status, page, letter)
	if not results:
		kodi_utils.set_content(__handle__, 'files')
		kodi_utils.end_directory(__handle__)
		return
	movies, tvshows = Movies({'id_type': 'trakt_dict'}), TVShows({'id_type': 'trakt_dict'})
	for idx, tag in enumerate(results, 1):
		mtype = tag.get('mediatype', '')
		if mtype == 'movie':
			_queue.put((movies.build_movie_content, idx, {'imdb': tag.get('imdb_id', ''), 'tmdb': tag.get('tmdb_id', '')}))
		elif mtype == 'show':
			_queue.put((tvshows.build_tvshow_content, idx, {'imdb': tag.get('imdb_id', ''), 'tmdb': tag.get('tmdb_id', '')}))
	if _queue.qsize() > 0:
		max_threads = min(_queue.qsize(), max_threads)
		threads = (Thread(target=_thread_target, args=(_queue,)) for i in range(max_threads))
		threads = list(TaskPool.process(threads))
		for i in threads: i.join(timeout=30)
	items = movies.items + tvshows.items
	items.sort(key=lambda k: int(k[1].getProperty('pov_sort_order') or '0'))
	content, total = max(
		('movies', movies), ('tvshows', tvshows), key=lambda k: len(k[1].items)
	)
	if total_pages > 2 and not is_widget and use_alphabet:
		url = {'mode': 'build_navigate_to_page', 'current_page': page, 'total_pages': total_pages,
				'media_type': media_type, 'slug': status, 'name': name,
				'transfer_mode': 'build_simkl_list.get_simkl_watchlist'}
		kodi_utils.add_dir(__handle__, url, jump2_str, iconImage=item_jump, isFolder=False)
	kodi_utils.add_items(__handle__, items)
	if total_pages > page:
		url = {'mode': 'build_simkl_list.get_simkl_watchlist', 'new_page': page + 1, 'new_letter': letter,
				'media_type': media_type, 'status': status, 'name': name}
		kodi_utils.add_dir(__handle__, url, nextpage_str)
	kodi_utils.set_category(__handle__, name)
	kodi_utils.set_content(__handle__, content)
	kodi_utils.end_directory(__handle__, False if is_widget else None)
	kodi_utils.set_view_mode('view.%s' % content, content)

def simkl_account_info():
	try:
		kodi_utils.show_busy_dialog()
		account_info = simkl_api.simkl_user_settings()
		rate_limit = simkl_api.get_rate_limit_status()
		if not account_info or not isinstance(account_info, dict):
			kodi_utils.hide_busy_dialog()
			return kodi_utils.notification('Simkl: Failed to retrieve account info')
		user = account_info.get('user') or {}
		account = account_info.get('account') or {}
		body = []
		append = body.append
		append('[B]Username:[/B] %s' % user.get('name', 'N/A'))
		append('[B]Joined:[/B] %s' % account.get('joined_at', 'N/A'))
		append('')
		append('[B]Current Session Rate Limit:[/B]')
		append('  Remaining: %s' % rate_limit['remaining'])
		kodi_utils.hide_busy_dialog()
		return kodi_utils.show_text(simkl_str.upper(), '\n\n'.join(body), font_size='large')
	except Exception as e:
		kodi_utils.hide_busy_dialog()
		kodi_utils.logger('simkl_account_info', str(e))
		kodi_utils.notification('Simkl: Failed to retrieve account info')
