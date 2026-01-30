from ast import literal_eval
from modules.kodi_utils import simkl_db, database_connect
# from modules.kodi_utils import logger

timeout = 20
SELECT = 'SELECT id FROM simkl_data'
DELETE = 'DELETE FROM simkl_data WHERE id = ?'
DELETE_LIKE = 'DELETE FROM simkl_data WHERE id LIKE ?'
VALID_TABLES = frozenset(('simkl_data',))
SC_BASE_GET = 'SELECT data FROM simkl_data WHERE id = ?'
SC_BASE_SET = 'INSERT OR REPLACE INTO simkl_data (id, data) VALUES (?, ?)'
SC_BASE_DELETE = 'DELETE FROM simkl_data WHERE id = ?'

class SimklCache:
	def __init__(self):
		self._connect_database()
		self._set_PRAGMAS()

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.close()
		return False

	def close(self):
		try:
			if hasattr(self, 'dbcon') and self.dbcon:
				self.dbcon.close()
		except Exception:
			pass

	def _connect_database(self):
		self.dbcon = database_connect(simkl_db, timeout=timeout, isolation_level=None)

	def _set_PRAGMAS(self):
		self.dbcur = self.dbcon.cursor()
		self.dbcur.execute("""PRAGMA synchronous = OFF""")
		self.dbcur.execute("""PRAGMA journal_mode = OFF""")
		self.dbcur.execute("""PRAGMA mmap_size = 268435456""")

def cache_simkl_object(function, string, url):
	with SimklCache() as cache:
		cache.dbcur.execute(SC_BASE_GET, (string,))
		cached_data = cache.dbcur.fetchone()
		if cached_data: return literal_eval(cached_data[0])
		result = function(url)
		cache.dbcur.execute(SC_BASE_SET, (string, repr(result)))
		return result

def reset_activity(latest_activities):
	string = 'simkl_get_activity'
	cached_data = None
	try:
		with SimklCache() as cache:
			cache.dbcur.execute(SC_BASE_GET, (string,))
			cached_data = cache.dbcur.fetchone()
			if cached_data: cached_data = literal_eval(cached_data[0])
			else: cached_data = default_activities()
			cache.dbcur.execute(SC_BASE_SET, (string, repr(latest_activities)))
	except Exception: pass
	return cached_data

def clear_simkl_list_data():
	try:
		with SimklCache() as cache:
			cache.dbcur.execute(DELETE_LIKE, ('simkl_%',))
	except Exception: pass

def clear_all_simkl_cache_data(refresh=True):
	try:
		with SimklCache() as cache:
			for table in ('simkl_data',):
				if table in VALID_TABLES:
					cache.dbcur.execute('DELETE FROM %s' % table)
			cache.dbcur.execute("""VACUUM""")
		if not refresh: return True
		from indexers.simkl_api import simkl_sync_activities_thread
		simkl_sync_activities_thread()
		return True
	except Exception: return False

def default_activities():
	return {
		'movies': {'all': '2022-01-01T00:00:00Z'},
		'tv_shows': {'all': '2022-01-01T00:00:00Z'},
		'anime': {'all': '2022-01-01T00:00:00Z'}
	}
