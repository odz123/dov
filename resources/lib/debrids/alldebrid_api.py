import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from caches.main_cache import cache_object
from modules import kodi_utils
# logger = kodi_utils.logger

ls, get_setting = kodi_utils.local_string, kodi_utils.get_setting
base_url = 'https://api.alldebrid.com/'
timeout = 10.0
_retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(502, 503, 504))
session = requests.Session()
session.mount('https://api.alldebrid.com', HTTPAdapter(max_retries=_retry))

class AllDebridAPI:
	icon = 'alldebrid.png'

	@staticmethod
	def flatten_magnet_files(files_list):
		def flatten(items):
			for i in items:
				if not isinstance(i, dict): continue
				if 'e' in i: flatten(i['e'])
				else: files_append(i)
		files = []
		files_append = files.append
		flatten(files_list)
		return files

	def __init__(self):
		self.token = get_setting('ad.token')
		session.headers['Authorization'] = 'Bearer %s' % self.token

	def _request(self, method, path, params=None, data=None):
		url = base_url + path
		try: response = session.request(method, url, params=params, data=data, timeout=timeout)
		except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
			return kodi_utils.notification('%s timeout' % self.__class__.__name__)
		if not response.ok: kodi_utils.logger(self.__class__.__name__, f"{response.reason}\n{response.url}")
		response = response.json() if 'json' in response.headers.get('Content-Type', '') else response
		if 'data' in response and response.get('status') == 'success': response = response['data']
		return response

	def _get(self, path, params=None):
		return self._request('get', path, params=params)

	def _post(self, path, data=None):
		return self._request('post', path, data=data)

	def days_remaining(self):
		import datetime
		try:
			account_info = self.account_info()['user']
			expires = datetime.datetime.fromtimestamp(account_info['premiumUntil'])
			days = (expires - datetime.datetime.today()).days
		except Exception: days = None
		return days

	def account_info(self):
		response = self._get('v4.1/user')
		return response

	def list_transfer(self, transfer_id):
		url = 'v4.1/magnet/status'
		params = {'id': transfer_id}
		result = self._get(url, params)
		if not result or not isinstance(result, dict): return []
		result = result['magnets']
		return result

	def delete_torrent(self, transfer_id):
		url = 'v4.1/magnet/delete'
		params = {'id': transfer_id}
		result = self._get(url, params)
		return True if result is not None and 'error' not in result else False

	def unrestrict_link(self, link):
		url = 'v4.1/link/unlock'
		params = {'link': link}
		response = self._get(url, params)
		try: return response['link']
		except Exception: return None

	def check_single_magnet(self, hash_string):
		cache_result = self.check_cache(hash_string)
		magnets = cache_result.get('magnets', []) if cache_result else []
		if not magnets: return False
		return magnets[0].get('instant', False)

	def check_cache(self, hashes):
		data = {'magnets[]': hashes}
		response = self._post('v4.1/magnet/instant', data)
		return response

	def create_transfer(self, magnet):
		url = 'v4.1/magnet/upload'
		params = {'magnet': magnet}
		result = self._get(url, params)
		magnets = result.get('magnets', []) if result else []
		if not magnets: return ''
		return magnets[0].get('id', '')

	def parse_magnet_pack(self, magnet_url, info_hash, errors=False):
		from modules.source_utils import supported_video_extensions
		torrent_id = None
		try:
			extensions = supported_video_extensions()
			torrent_id = self.create_transfer(magnet_url)
			for key in ['completionDate'] * 3:
				kodi_utils.sleep(500)
				transfer_info = self.list_transfer(torrent_id)
				if transfer_info[key]: break
			else: raise Exception('alldebrid uncached magnet')
			transfer_info['links'] = self.flatten_magnet_files(transfer_info['files'])
			torrent_files = [
				{'link': item['l'],
				 'size': item['s'],
				 'torrent_id': torrent_id,
				 'filename': item['n']}
				for item in transfer_info['links']
				if item['n'].lower().endswith(tuple(extensions))
			]
			return torrent_files
		except Exception as e:
			if torrent_id: self.delete_torrent(torrent_id)
			if errors: raise

	def downloads(self):
		url = 'v4.1/user/history'
		string = 'pov_ad_downloads'
		return cache_object(self._get, string, url, False, 0.5)

	def user_cloud(self, completed=True):
		url = 'v4.1/magnet/status'
		string = 'pov_ad_user_cloud'
		result = cache_object(self._get, string, url, False, 0.5)
		if completed: result['magnets'] = [i for i in result['magnets'] if i['statusCode'] == 4]
		return result

	def clear_cache(self):
		from modules.kodi_utils import clear_property, path_exists, database_connect, maincache_db
		if not path_exists(maincache_db): return True
		from caches.debrid_cache import DebridCache
		dbcon = database_connect(maincache_db)
		try:
			dbcur = dbcon.cursor()
			# USER CLOUD
			try:
				dbcur.execute("""DELETE FROM maincache WHERE id = ?""", ('pov_ad_user_cloud',))
				clear_property('pov_ad_user_cloud')
				dbcon.commit()
				user_cloud_success = True
			except Exception: user_cloud_success = False
			# DOWNLOAD LINKS
			try:
				dbcur.execute("""DELETE FROM maincache WHERE id = ?""", ('pov_ad_downloads',))
				clear_property('pov_ad_downloads')
				dbcon.commit()
				download_links_success = True
			except Exception: download_links_success = False
			# HOSTERS
			try:
				dbcur.execute("""DELETE FROM maincache WHERE id = ?""", ('pov_ad_valid_hosts',))
				clear_property('pov_ad_valid_hosts')
				dbcon.commit()
				hoster_links_success = True
			except Exception: hoster_links_success = False
		except Exception: return False
		finally:
			dbcon.close()
		# HASH CACHED STATUS
		try:
			DebridCache().clear_debrid_results('ad')
			hash_cache_status_success = True
		except Exception: hash_cache_status_success = False
		if False in (user_cloud_success, download_links_success, hoster_links_success, hash_cache_status_success): return False
		return True

