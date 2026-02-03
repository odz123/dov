import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from caches.main_cache import cache_object
from modules import kodi_utils
# logger = kodi_utils.logger

ls, get_setting = kodi_utils.local_string, kodi_utils.get_setting
user_agent = 'POV/%s' % kodi_utils.get_addoninfo('version')
client_id = '663882072'
base_url = 'https://www.premiumize.me/api/'
timeout = 10.0
_retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(502, 503, 504))
session = requests.Session()
session.mount('https://www.premiumize.me', HTTPAdapter(max_retries=_retry))

class PremiumizeAPI:
	icon = 'premiumize.png'

	def __init__(self):
		self.token = get_setting('pm.token')
		session.headers.update(self.headers())

	def _request(self, method, path, data=None):
		url = base_url + path
		try: response = session.request(method, url, data=data, timeout=timeout)
		except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
			return kodi_utils.notification('%s timeout' % self.__class__.__name__)
		if not response.ok: kodi_utils.logger(self.__class__.__name__, f"{response.reason}\n{response.url}")
		if 'json' in response.headers.get('Content-Type', ''):
			try: return response.json()
			except ValueError: pass
		return response

	def _get(self, path):
		return self._request('get', path)

	def _post(self, path, data=None):
		return self._request('post', path, data=data)

	def add_headers_to_url(self, url):
		return url + '|' + kodi_utils.urlencode(self.headers())

	def headers(self):
		return {'User-Agent': user_agent, 'Authorization': 'Bearer %s' % self.token}

	def days_remaining(self):
		import datetime
		try:
			account_info = self.account_info()
			expires = datetime.datetime.fromtimestamp(account_info['premium_until'])
			days = (expires - datetime.datetime.now()).days
		except Exception: days = None
		return days

	def account_info(self):
		url = 'account/info'
		response = self._get(url)
		return response

	def item_listall(self):
		url = 'item/listall'
		return self._get(url)

	def delete_torrent(self, transfer_id):
		return self.delete_object('transfer', transfer_id)

	def unrestrict_link(self, link):
		data = {'src': link}
		url = 'transfer/directdl'
		response = self._post(url, data)
		try: return self.add_headers_to_url(response['content'][0]['link'])
		except (KeyError, TypeError, IndexError): return None

	def check_single_magnet(self, hash_string):
		cache_info = self.check_cache([hash_string])
		return hash_string in cache_info

	def check_cache(self, hashes):
		data = {'items[]': hashes}
		url = 'cache/check'
		response = self._post(url, data)
		if not response or not isinstance(response, dict): return []
		return [h for h, cached in zip(hashes, response.get('response', [])) if cached]

	def instant_transfer(self, magnet):
		data = {'src': magnet}
		url = 'transfer/directdl'
		return self._post(url, data)

	def create_transfer(self, magnet):
		data = {'src': magnet, 'folder_id': 0}
		url = 'transfer/create'
		response = self._post(url, data)
		return response.get('id', '')

	def parse_magnet_pack(self, magnet_url, info_hash):
		from modules.source_utils import supported_video_extensions
		try:
			extensions = supported_video_extensions()
			torrent = self.instant_transfer(magnet_url)
			if not torrent or not isinstance(torrent, dict):
				return None
			torrent_files = torrent.get('content', [])
			torrent_files = [
				{'link': item['link'],
				 'size': item['size'],
				 'filename': item['path'].split('/')[-1]}
				for item in torrent_files
				if item.get('path', '').lower().endswith(tuple(extensions))
			]
			return torrent_files
		except Exception as e:
			kodi_utils.logger('POV Premiumize', 'parse_magnet_pack error: %s' % str(e))

	def zip_folder(self, folder_id):
		url = 'zip/generate'
		data = {'folders[]': folder_id}
		response = self._post(url, data)
		return response

	def download_link_magnet_zip(self, magnet_url, info_hash):
		try:
#			result = self.create_transfer(magnet_url)
#			if not 'status' in result or result['status'] != 'success': return None
#			transfer_id = result['id']
			transfer_id = self.create_transfer(magnet_url)
			if not transfer_id: return None
			transfers = self.downloads().get('transfers', [])
			matching_transfers = [i['folder_id'] for i in transfers if i['id'] == transfer_id]
			if not matching_transfers: return None
			folder_id = matching_transfers[0]
			result = self.zip_folder(folder_id)
			if result['status'] == 'success':
				return result['location']
			else: return None
		except Exception:
			pass

	def rename_cache_item(self, file_type, file_id, new_name):
		if file_type == 'folder': url = 'folder/rename'
		else: url = 'item/rename'
		data = {'id': file_id , 'name': new_name}
		response = self._post(url, data)
		return True if response is not None and response['status'] == 'success' else False

	def delete_object(self, object_type, object_id):
		data = {'id': object_id}
		url = '%s/delete' % object_type
		response = self._post(url, data)
		return True if response is not None and response['status'] == 'success' else False

	def get_item_details(self, item_id):
		string = 'pov_pm_item_details_%s' % item_id
		url = 'item/details'
		data = {'id': item_id}
		args = [url, data]
		return cache_object(self._post, string, args, False, 24)

	def downloads(self):
		string = 'pov_pm_downloads'
		url = 'transfer/list'
		return cache_object(self._get, string, url, False, 0.5)

	def user_cloud(self, folder_id=None):
		if folder_id:
			string = 'pov_pm_user_cloud_%s' % folder_id
			url = 'folder/list?id=%s' % folder_id
		else:
			string = 'pov_pm_user_cloud_root'
			url = 'folder/list'
		return cache_object(self._get, string, url, False, 0.5)

	def clear_cache(self):
		from modules.kodi_utils import clear_property, path_exists, database_connect, maincache_db
		if not path_exists(maincache_db): return True
		from caches.debrid_cache import DebridCache
		dbcon = database_connect(maincache_db)
		try:
			dbcur = dbcon.cursor()
			# USER CLOUD
			try:
				dbcur.execute("""SELECT id FROM maincache WHERE id LIKE ?""", ('pov_pm_user_cloud%',))
				user_cloud_cache = [str(i[0]) for i in dbcur.fetchall()]
				if user_cloud_cache:
					dbcur.execute("""DELETE FROM maincache WHERE id LIKE ?""", ('pov_pm_user_cloud%',))
					for i in user_cloud_cache: clear_property(i)
					dbcon.commit()
				user_cloud_success = True
			except Exception: user_cloud_success = False
			# DOWNLOAD LINKS
			try:
				dbcur.execute("""DELETE FROM maincache WHERE id = ?""", ('pov_pm_downloads',))
				clear_property('pov_pm_downloads')
				dbcon.commit()
				download_links_success = True
			except Exception: download_links_success = False
			# HOSTERS
			try:
				dbcur.execute("""DELETE FROM maincache WHERE id = ?""", ('pov_pm_valid_hosts',))
				clear_property('pov_pm_valid_hosts')
				dbcon.commit()
				hoster_links_success = True
			except Exception: hoster_links_success = False
		except Exception: return False
		finally:
			dbcon.close()
		# HASH CACHED STATUS
		try:
			with DebridCache() as dc:
				dc.clear_debrid_results('pm')
			hash_cache_status_success = True
		except Exception: hash_cache_status_success = False
		if False in (user_cloud_success, download_links_success, hoster_links_success, hash_cache_status_success): return False
		return True

