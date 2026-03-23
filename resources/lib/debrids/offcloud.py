from debrids.offcloud_api import OffcloudAPI as Debrid
from debrids._common import (
	get_default_art, make_folder_listitem, finalize_directory,
	folder_str, file_str, delete_str, down_str, build_url, make_listitem, ls, KODI_VERSION
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
			return self.cloud_delete(params['folder_id'])
		elif '_browse_cloud' in params['mode']:
			items = self.user_cloud(params['folder_id']) or []
			_builder = self.browse_cloud
		elif '_torrent_cloud' in params['mode']:
			items = self.user_cloud() or []
			_builder = self.torrent_cloud
		else: return getattr(self, params['mode'].split('.')[-1])()
		finalize_directory(_builder, items)

	def torrent_cloud(self, items):
		for count, item in enumerate(items, 1):
			try:
				request_id, server = item['requestId'], item['server']
				folder_name, is_folder = item['fileName'], item['isDirectory']
				name = clean_file_name(normalize(folder_name)).upper()
				delete_params = {'mode': 'offcloud.oc_delete', 'folder_id': request_id}
				if is_folder:
					url_params = {'mode': 'offcloud.oc_browse_cloud', 'folder_id': request_id}
					yield make_folder_listitem(count, name, url_params, delete_params, default_art)
				else:
					link = self.requote_uri(self.build_url(server, request_id, folder_name))
					display = '%02d | [B]%s[/B] | [I]%s [/I]' % (count, file_str, name)
					url_params = {'mode': 'media_play', 'url': link, 'media_type': 'video'}
					down_file_params = {'mode': 'downloader', 'action': 'cloud.offcloud_direct', 'name': folder_name, 'url': link, 'image': default_icon}
					cm = [
						('[B]%s %s[/B]' % (delete_str, file_str.capitalize()), 'RunPlugin(%s)' % build_url(delete_params)),
						(down_str, 'RunPlugin(%s)' % build_url(down_file_params))
					]
					url = build_url(url_params)
					listitem = make_listitem()
					listitem.setLabel(display)
					listitem.addContextMenuItems(cm)
					listitem.setArt(default_art)
					yield (url, listitem, False)
			except Exception: pass

	def browse_cloud(self, items):
		for count, item in enumerate(items, 1):
			try:
				if not item.lower().endswith(extensions): continue
				name = clean_file_name(item.split('/')[-1]).upper()
				link = self.requote_uri(item)
				display = '%02d | [B]%s[/B] | [I]%s [/I]' % (count, file_str, name)
				params = {'name': name, 'url': link, 'image': default_icon}
				url_params = {**params, 'mode': 'media_play', 'media_type': 'video'}
				down_file_params = {**params, 'mode': 'downloader', 'action': 'cloud.offcloud_direct'}
				cm = [(down_str, 'RunPlugin(%s)' % build_url(down_file_params))]
				url = build_url(url_params)
				listitem = make_listitem()
				listitem.setLabel(display)
				listitem.addContextMenuItems(cm)
				listitem.setArt(default_art)
				listitem.setInfo('video', {}) if KODI_VERSION < 20 else listitem.getVideoInfoTag()
				yield (url, listitem, False)
			except Exception: pass

	def cloud_delete(self, folder_id):
		if not kodi_utils.confirm_dialog(): return
		result = self.delete_torrent(folder_id)
		if not result: return kodi_utils.notification(32574)
		self.clear_cache()
		kodi_utils.container_refresh()

	def user_cloud_clear(self):
		if not kodi_utils.confirm_dialog(): return
		from threading import Thread
		files = self.user_cloud(check_cache=False)
		if not files: return kodi_utils.notification(32760)
		len_files = len(files)
		progressBG = kodi_utils.progressDialogBG
		progressBG.create('Offcloud', 'Clearing cloud files')
		try:
			for count, i in enumerate(files, 1):
				try:
					req = Thread(target=self.delete_torrent, args=(i['requestId'],), name=i['fileName'])
					req.start()
					progressBG.update(int(count / len_files * 100), '%s: %s...' % (ls(32785), req.name))
					req.join(1)
				except Exception: pass
		finally:
			try: progressBG.close()
			except Exception: pass
		self.clear_cache()

	def show_account_info(self):
		try:
			kodi_utils.show_busy_dialog()
			account_info = self.account_info()
			if not account_info: raise Exception('Failed to retrieve account info')
			body = []
			append = body.append
			append(ls(32758) % account_info['email'])
			append(ls(32755) % account_info['userId'])
			append('[B]Premium[/B]: %s' % account_info['isPremium'])
			append(ls(32750) % account_info['expirationDate'])
			append('[B]Cloud Limit[/B]: {:,}'.format(account_info['limits']['cloud']))
			kodi_utils.hide_busy_dialog()
			return kodi_utils.show_text('Offcloud'.upper(), '\n\n'.join(body), font_size='large')
		except Exception: kodi_utils.hide_busy_dialog()
