from debrids.torbox_api import TorBoxAPI as Debrid
from debrids._common import (
	get_default_art, make_folder_listitem, make_file_listitem, finalize_directory,
	down_str, build_url, ls
)
from modules import kodi_utils
from modules.source_utils import supported_video_extensions
from modules.utils import clean_file_name, normalize

get_setting, set_setting = kodi_utils.get_setting, kodi_utils.set_setting
default_icon, default_art = get_default_art(Debrid.icon)
extensions = supported_video_extensions()

class Indexer(Debrid):
	def run(self, params):
		if   '_delete' in params['mode']:
			return self.cloud_delete(params['folder_id'], params['media_type'])
		elif '_browse_cloud' in params['mode']:
			folder_id, media_type = params['folder_id'], params['media_type']
			if   media_type == 'usenet': cloud_data = self.user_cloud_usenet(folder_id)
			elif media_type == 'webdl': cloud_data = self.user_cloud_webdl(folder_id)
			else: cloud_data = self.user_cloud(folder_id)
			files = cloud_data.get('files', []) if cloud_data else []
			items = [{**i, 'url': '%d,%d' % (int(folder_id), i['id']), 'media_type': media_type} for i in files]
			_builder = self.browse_cloud
		elif '_torrent_cloud' in params['mode']:
			media_type = params['media_type']
			if   media_type == 'usenet': cloud_items = self.user_cloud_usenet()
			elif media_type == 'webdl': cloud_items = self.user_cloud_webdl()
			else: cloud_items = self.user_cloud()
			items = [{**i, 'media_type': media_type} for i in (cloud_items or [])]
			_builder = self.torrent_cloud
		else: return getattr(self, params['mode'].split('.')[-1])()
		finalize_directory(_builder, items)

	def torrent_cloud(self, items):
		items.sort(key=lambda k: k['updated_at'], reverse=True)
		for count, item in enumerate(items, 1):
			try:
				name = clean_file_name(normalize(item['name'])).upper()
				url_params = {'mode': 'torbox.tb_browse_cloud', 'folder_id': item['id'], 'media_type': item['media_type']}
				delete_params = {'mode': 'torbox.tb_delete', 'folder_id': item['id'], 'media_type': item['media_type']}
				yield make_folder_listitem(count, name, url_params, delete_params, default_art)
			except Exception: pass

	def browse_cloud(self, items):
		for count, item in enumerate(items, 1):
			try:
				if not item['short_name'].lower().endswith(extensions): continue
				name = clean_file_name(item['short_name']).upper()
				size = float(int(item['size']))/1073741824
				params = {'name': name, 'url': item['url'], 'media_type': item['media_type'], 'image': default_icon}
				url_params = {**params, 'mode': 'torbox.resolve_tb', 'play': 'true'}
				down_file_params = {**params, 'mode': 'downloader', 'action': 'cloud.torbox'}
				yield make_file_listitem(count, name, size, url_params, down_file_params, default_art)
			except Exception: pass

	def cloud_delete(self, folder_id, media_type):
		if not kodi_utils.confirm_dialog(): return
		if   media_type == 'usenet': result = self.delete_usenet(folder_id)
		elif media_type == 'webdl': result = self.delete_webdl(folder_id)
		else: result = self.delete_torrent(folder_id)
		if not result: return kodi_utils.notification(32574)
		self.clear_cache()
		kodi_utils.container_refresh()

	def show_account_info(self):
		from datetime import datetime
		from modules.utils import datetime_workaround
		try:
			kodi_utils.show_busy_dialog()
			plans = {0: 'Free', 1: 'Essential', 2: 'Pro', 3: 'Standard'}
			account_info = self.account_info()
			expires = datetime_workaround(account_info['premium_expires_at'], '%Y-%m-%dT%H:%M:%SZ')
			days_remaining = (expires - datetime.now()).days
			body = []
			append = body.append
			append(ls(32758) % account_info['email'])
			append(ls(32755) % account_info['customer'])
			append(ls(32757) % plans.get(account_info['plan'], 'Unknown'))
			append(ls(32750) % expires.strftime('%Y-%m-%d'))
			append(ls(32751) % days_remaining)
			append('[B]Downloaded[/B]: %s' % account_info['total_downloaded'])
			kodi_utils.hide_busy_dialog()
			return kodi_utils.show_text('TorBox'.upper(), '\n\n'.join(body), font_size='large')
		except Exception: kodi_utils.hide_busy_dialog()

def resolve_tb(params):
	file_id, media_type = params['url'], params['media_type']
	if   media_type == 'usenet': resolved_link = Debrid().unrestrict_usenet(file_id)
	elif media_type == 'webdl': resolved_link = Debrid().unrestrict_webdl(file_id)
	else: resolved_link = Debrid().unrestrict_link(file_id)
	if params.get('play', 'false') != 'true': return resolved_link
	if resolved_link: kodi_utils.player.play(resolved_link)
	else: kodi_utils.notification(32574)
