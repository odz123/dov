import sys
from debrids.easydebrid_api import EasyDebridAPI as Debrid
from modules import kodi_utils
# from modules.kodi_utils import logger

ls, get_setting, set_setting = kodi_utils.local_string, kodi_utils.get_setting, kodi_utils.set_setting

class Indexer(Debrid):
	def run(self, params):
		if any(x in params['mode'] for x in ('_delete', '_browse_cloud', '_torrent_cloud')):
			kodi_utils.notification('EasyDebrid does not support cloud storage', 3000)
			return
		return getattr(self, params['mode'].split('.')[-1])()

	def show_account_info(self):
		from datetime import datetime
		try:
			kodi_utils.show_busy_dialog()
			account_info = self.account_info()
			if account_info['paid_until']:
				expires = datetime.fromtimestamp(account_info['paid_until'])
				days_remaining = (expires - datetime.today()).days
				expires = expires.strftime('%Y-%m-%d')
			else: expires, days_remaining = 'Expired', 'None'
			body = []
			append = body.append
			append(ls(32755) % account_info['id'])
			append(ls(32750) % expires)
			append(ls(32751) % days_remaining)
			kodi_utils.hide_busy_dialog()
			return kodi_utils.show_text('EasyDebrid'.upper(), '\n\n'.join(body), font_size='large')
		except: kodi_utils.hide_busy_dialog()

