from datetime import datetime
from debrids.alldebrid_api import AllDebridAPI as Debrid
from debrids._common import (
	get_default_art, make_folder_listitem, make_file_listitem, finalize_directory,
	file_str, delete_str, down_str, build_url, make_listitem, ls
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
			return self.cloud_delete(params['id'])
		elif '_browse_cloud' in params['mode']:
			transfer_data = self.list_transfer(params['id'])
			files = transfer_data.get('files', []) if transfer_data else []
			items = self.flatten_magnet_files(files)
			_builder = self.browse_cloud
		elif '_torrent_cloud' in params['mode']:
			cloud_data = self.user_cloud()
			items = cloud_data.get('magnets', []) if cloud_data else []
			_builder = self.torrent_cloud
		elif '_downloads' in params['mode']:
			downloads_data = self.downloads()
			items = downloads_data.get('links', []) if downloads_data else []
			_builder = self.browse_downloads
		else: return getattr(self, params['mode'].split('.')[-1])()
		finalize_directory(_builder, items)

	def torrent_cloud(self, items):
		items.sort(key=lambda k: (k['uploadDate'], k['id']), reverse=True)
		for count, item in enumerate(items, 1):
			try:
				name = clean_file_name(normalize(item['filename'])).upper()
				url_params = {'mode': 'alldebrid.ad_browse_cloud', 'id': item['id']}
				delete_params = {'mode': 'alldebrid.ad_delete', 'id': item['id']}
				yield make_folder_listitem(count, name, url_params, delete_params, default_art)
			except Exception: pass

	def browse_cloud(self, items):
		for count, item in enumerate(items, 1):
			try:
				if not item['n'].lower().endswith(extensions): continue
				name = clean_file_name(item['n']).upper()
				size = float(int(item['s']))/1073741824
				params = {'name': name, 'url': item['l'], 'image': default_icon}
				url_params = {**params, 'mode': 'alldebrid.resolve_ad', 'play': 'true'}
				down_file_params = {**params, 'mode': 'downloader', 'action': 'cloud.alldebrid'}
				yield make_file_listitem(count, name, size, url_params, down_file_params, default_art)
			except Exception: pass

	def browse_downloads(self, items):
		items.sort(key=lambda k: k['date'], reverse=True)
		for count, item in enumerate(items, 1):
			try:
				if not item['filename'].lower().endswith(extensions): continue
				name = clean_file_name(item['filename']).upper()
				size = float(int(item['size']))/1073741824
				datetime_object = datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d')
				display = '%02d | %.2f GB | %s | [I]%s [/I]' % (count, size, datetime_object, name)
				params = {'name': name, 'url': item['link_dl'], 'image': default_icon}
				url_params = {**params, 'mode': 'media_play', 'media_type': 'video'}
				down_file_params = {**params, 'mode': 'downloader', 'action': 'cloud.alldebrid_direct'}
				cm = [(down_str, 'RunPlugin(%s)' % build_url(down_file_params))]
				url = build_url(url_params)
				listitem = make_listitem()
				listitem.setLabel(display)
				listitem.addContextMenuItems(cm)
				listitem.setArt(default_art)
				yield (url, listitem, False)
			except Exception: pass

	def cloud_delete(self, file_id):
		if not kodi_utils.confirm_dialog(): return
		result = self.delete_torrent(file_id)
		if not result: return kodi_utils.notification(32574)
		self.clear_cache()
		kodi_utils.container_refresh()

	def show_account_info(self):
		try:
			kodi_utils.show_busy_dialog()
			account_info = self.account_info()
			if not account_info or 'user' not in account_info:
				kodi_utils.hide_busy_dialog()
				return kodi_utils.notification(32574)
			account_info = account_info['user']
			username = account_info['username']
			email = account_info['email']
			status = 'Premium' if account_info['isPremium'] else 'Not Active'
			expires = datetime.fromtimestamp(account_info['premiumUntil'])
			days_remaining = (expires - datetime.now()).days
			body = []
			append = body.append
			append(ls(32755) % username)
			append(ls(32756) % email)
			append(ls(32757) % status)
			append(ls(32750) % expires)
			append(ls(32751) % days_remaining)
			kodi_utils.hide_busy_dialog()
			return kodi_utils.show_text(ls(32063).upper(), '\n\n'.join(body), font_size='large')
		except Exception: kodi_utils.hide_busy_dialog()

def resolve_ad(params):
	url = params['url']
	resolved_link = Debrid().unrestrict_link(url)
	if params.get('play', 'false') != 'true' : return resolved_link
	kodi_utils.player.play(resolved_link)
