import time
from ast import literal_eval
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from modules import kodi_utils

debridcache_db = kodi_utils.debridcache_db
favourites_db = kodi_utils.favourites_db
maincache_db = kodi_utils.maincache_db
metacache_db = kodi_utils.metacache_db
navigator_db = kodi_utils.navigator_db
external_db = kodi_utils.external_db
views_db = kodi_utils.views_db
watched_db = kodi_utils.watched_db
database_connect = kodi_utils.database_connect
container_refresh = kodi_utils.container_refresh
get_property, set_property, clear_property = kodi_utils.get_property, kodi_utils.set_property, kodi_utils.clear_property


_PRAGMA_STATEMENTS = (
	'PRAGMA synchronous = OFF',
	'PRAGMA journal_mode = OFF',
	'PRAGMA mmap_size = 268435456',
	'PRAGMA cache_size = -8000',
)


class ConnectionPool:
	"""Thread-safe connection pool for SQLite databases.
	Reuses connections to avoid repeated connection overhead."""
	_pools = {}
	_pool_lock = Lock()
	_max_pool_size = 5

	@classmethod
	def get_connection(cls, db_file, isolation_level=None):
		"""Get a connection from the pool or create a new one."""
		conn = None
		with cls._pool_lock:
			if db_file not in cls._pools:
				cls._pools[db_file] = []
			pool = cls._pools[db_file]
			if pool:
				conn = pool.pop()
		# Test connection validity outside the lock to avoid blocking other threads
		if conn is not None:
			try:
				conn.execute('SELECT 1')
				# Ensure isolation_level matches what caller expects
				if conn.isolation_level != isolation_level:
					conn.isolation_level = isolation_level
				return conn
			except Exception:
				try: conn.close()
				except Exception: pass
		# Create new connection outside lock
		return database_connect(db_file, isolation_level=isolation_level)

	@classmethod
	def return_connection(cls, db_file, conn):
		"""Return a connection to the pool for reuse."""
		if conn is None:
			return
		with cls._pool_lock:
			if db_file not in cls._pools:
				cls._pools[db_file] = []
			pool = cls._pools[db_file]
			if len(pool) < cls._max_pool_size:
				pool.append(conn)
			else:
				# Pool full, close connection
				try:
					conn.close()
				except Exception:
					pass

	@classmethod
	def clear_pool(cls, db_file=None):
		"""Clear connections from pool, optionally for specific database."""
		with cls._pool_lock:
			if db_file:
				if db_file in cls._pools:
					for conn in cls._pools[db_file]:
						try:
							conn.close()
						except Exception:
							pass
					cls._pools[db_file] = []
			else:
				for pool in cls._pools.values():
					for conn in pool:
						try:
							conn.close()
						except Exception:
							pass
				cls._pools.clear()


@contextmanager
def pooled_connection(db_file):
	"""Context manager for pooled database connections with PRAGMA setup.
	Usage:
		with pooled_connection(db_file) as (dbcon, dbcur):
			dbcur.execute(...)
	"""
	dbcon = None
	try:
		dbcon = ConnectionPool.get_connection(db_file, isolation_level=None)
		if not getattr(dbcon, '_pragmas_set', False):
			dbcur = dbcon.cursor()
			for stmt in _PRAGMA_STATEMENTS:
				dbcur.execute(stmt)
			dbcon._pragmas_set = True
		else:
			dbcur = dbcon.cursor()
		yield dbcon, dbcur
	finally:
		if dbcon:
			ConnectionPool.return_connection(db_file, dbcon)


class BaseCache:
	db_file = ':memory:'
	_use_pooling = True

	def __init__(self):
		if self._use_pooling and self.db_file != ':memory:':
			self.dbcon = ConnectionPool.get_connection(self.db_file, isolation_level=None)
		else:
			self.dbcon = database_connect(self.db_file, isolation_level=None)
		self.dbcur = self.dbcon.cursor()
		self._set_PRAGMAS()

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		self.close()

	def close(self):
		"""Return connection to pool or close it."""
		try:
			if self.dbcur:
				self.dbcur.close()
				self.dbcur = None
			if self.dbcon:
				if self._use_pooling and self.db_file != ':memory:':
					ConnectionPool.return_connection(self.db_file, self.dbcon)
				else:
					self.dbcon.close()
				self.dbcon = None
		except Exception:
			pass

	def _set_PRAGMAS(self):
		# Skip if PRAGMAs already set on this pooled connection
		if getattr(self.dbcon, '_pragmas_set', False):
			return
		for stmt in _PRAGMA_STATEMENTS:
			self.dbcur.execute(stmt)
		self.dbcon._pragmas_set = True

	def _get_timestamp(self, date_time):
		return int(time.mktime(date_time.timetuple()))

