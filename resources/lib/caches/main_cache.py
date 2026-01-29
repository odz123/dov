from datetime import datetime, timedelta
from caches import BaseCache, maincache_db, get_property, set_property, clear_property, literal_eval
# from modules.kodi_utils import logger

BASE_GET = 'SELECT expires, data FROM maincache WHERE id = ?'
BASE_SET = 'INSERT OR REPLACE INTO maincache (id, data, expires) VALUES (?, ?, ?)'
BASE_DELETE = 'DELETE FROM maincache WHERE id = ?'
LIKE_SELECT = 'SELECT id from maincache where id LIKE %s'
LIKE_DELETE = 'DELETE FROM maincache WHERE id LIKE %s'
DELETE = 'DELETE FROM maincache WHERE id = ?'
ALL_LIST_ADD = ' OR id LIKE '

class MainCache(BaseCache):
	db_file = maincache_db

	def get(self, string):
		result = None
		try:
			current_time = self._get_timestamp(datetime.now())
			result = self.get_memory_cache(string, current_time)
			if result is None:
				self.dbcur.execute(BASE_GET, (string,))
				cache_data = self.dbcur.fetchone()
				if cache_data:
					if cache_data[0] > current_time:
						result = literal_eval(cache_data[1])
						self.set_memory_cache(result, string, cache_data[0])
					else:
						self.delete(string, dbcon=None)
		except Exception: pass
		return result

	def set(self, string, data, expiration=timedelta(days=30)):
		try:
			expires = self._get_timestamp(datetime.now() + expiration)
			self.dbcur.execute(BASE_SET, (string, repr(data), int(expires)))
			self.set_memory_cache(data, string, int(expires))
		except Exception: pass

	def get_memory_cache(self, string, current_time):
		result = None
		try:
			cachedata = get_property(string)
			if cachedata:
				cachedata = literal_eval(cachedata)
				if cachedata[0] > current_time: result = cachedata[1]
		except Exception: pass
		return result

	def set_memory_cache(self, data, string, expires):
		try:
			cachedata = (expires, data)
			cachedata = repr(cachedata)
			set_property(string, cachedata)
		except Exception: pass

	def delete(self, string, dbcon=None):
		try:
			self.dbcur.execute(BASE_DELETE, (string,))
			self.delete_memory_cache(string)
		except Exception: pass

	def delete_memory_cache(self, string):
		clear_property(string)

	def delete_all_lists(self):
		from modules.meta_lists import media_lists
		command = LIKE_SELECT % ALL_LIST_ADD.join(media_lists)
		self.dbcur.execute(command)
		results = self.dbcur.fetchall()
		try:
			# Batch delete using executemany instead of N+1 pattern
			if results:
				self.dbcur.executemany(DELETE, [(str(item[0]),) for item in results])
				for item in results:
					self.delete_memory_cache(str(item[0]))
			# VACUUM removed - should be run during maintenance only
		except Exception: pass

	def delete_all_folderscrapers(self):
		self.dbcur.execute(LIKE_SELECT % "'pov_FOLDERSCRAPER_%'")
		remove_list = [str(i[0]) for i in self.dbcur.fetchall()]
		if not remove_list: return 'success'
		try:
			self.dbcur.execute(LIKE_DELETE % "'pov_FOLDERSCRAPER_%'")
			# VACUUM removed - should be run during maintenance only
			for item in remove_list: self.delete_memory_cache(str(item))
		except Exception: pass

def cache_object(function, string, url, json=False, expiration=24):
	maincache = MainCache()
	cache = maincache.get(string)
	if cache: return cache
	if isinstance(url, list): args = tuple(url)
	else: args = (url,)
	if json: result = function(*args).json()
	else: result = function(*args)
	maincache.set(string, result, expiration=timedelta(hours=expiration))
	return result

