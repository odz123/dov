import time
from ast import literal_eval
from modules.kodi_utils import simkl_db, database_connect
# from modules.kodi_utils import logger

timeout = 20
CACHE_TTL = 14400  # 4 hours
VALID_TABLES = frozenset(('simkl_data',))
SC_BASE_GET = 'SELECT data FROM simkl_data WHERE id = ?'
SC_BASE_SET = 'INSERT OR REPLACE INTO simkl_data (id, data) VALUES (?, ?)'

class SimklCache:
	def __init__(self):
		self._connect_database()
		try:
			self._set_PRAGMAS()
		except Exception:
			self.close()
			raise

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
	result = None
	try:
		with SimklCache() as cache:
			cache.dbcur.execute(SC_BASE_GET, (string,))
			cached_data = cache.dbcur.fetchone()
			if cached_data:
				try:
					parsed = literal_eval(cached_data[0])
					if isinstance(parsed, tuple) and len(parsed) == 2 and isinstance(parsed[0], (int, float)):
						ts, data = parsed
						if time.time() - ts < CACHE_TTL:
							return data
						cache.dbcur.execute("DELETE FROM simkl_data WHERE id = ?", (string,))
					else:
						return parsed
				except (ValueError, SyntaxError):
					cache.dbcur.execute("DELETE FROM simkl_data WHERE id = ?", (string,))
			result = function(url)
			if result is not None:
				cache.dbcur.execute(SC_BASE_SET, (string, repr((int(time.time()), result))))
			return result
	except Exception:
		return result if result is not None else function(url)

def get_cached_activity():
	"""Read the cached activity data without modifying it."""
	cached_data = default_activities()
	try:
		with SimklCache() as cache:
			cache.dbcur.execute(SC_BASE_GET, ('simkl_get_activity',))
			result = cache.dbcur.fetchone()
			if result:
				try: cached_data = literal_eval(result[0])
				except (ValueError, SyntaxError): pass
	except Exception: pass
	return cached_data

def save_activity(latest_activities):
	"""Save the latest activity data to cache."""
	try:
		with SimklCache() as cache:
			cache.dbcur.execute(SC_BASE_SET, ('simkl_get_activity', repr(latest_activities)))
	except Exception: pass

def clear_simkl_list_data():
	"""Clear cached list data but preserve activity tracking."""
	try:
		with SimklCache() as cache:
			cache.dbcur.execute("DELETE FROM simkl_data WHERE id LIKE ? AND id != ?", ('simkl_%', 'simkl_get_activity'))
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
		'shows': {'all': '2022-01-01T00:00:00Z'},
		'anime': {'all': '2022-01-01T00:00:00Z'}
	}
