import sqlite3
from ast import literal_eval
from datetime import datetime, timedelta
from caches import BaseCache, metacache_db, get_property, set_property, clear_property

# Use literal_eval instead of eval for security - only evaluates literals, not arbitrary code
safe_eval = literal_eval

all_tables = ('metadata', 'season_metadata', 'function_cache', 'stremio_metadata')
movie_show = ('movie', 'tvshow')
id_types = ('tmdb_id', 'imdb_id', 'tvdb_id')
# Valid column and table names for safe SQL construction (prevents SQL injection)
VALID_ID_TYPES = frozenset(id_types)
VALID_TABLES = frozenset(all_tables)
GET_MOVIE_SHOW = 'SELECT meta, expires FROM metadata WHERE db_type = ? AND %s = ?'
GET_SEASON = 'SELECT meta, expires FROM season_metadata WHERE tmdb_id = ?'
GET_FUNCTION = 'SELECT string_id, data, expires FROM function_cache WHERE string_id = ?'
GET_ALL = 'SELECT db_type, tmdb_id FROM metadata'
SET_MOVIE_SHOW = 'INSERT OR REPLACE INTO metadata VALUES (?, ?, ?, ?, ?, ?)'
SET_SEASON = 'INSERT INTO season_metadata VALUES (?, ?, ?)'
SET_FUNCTION = 'INSERT INTO function_cache VALUES (?, ?, ?)'
DELETE_MOVIE_SHOW = 'DELETE FROM metadata WHERE db_type = ? AND %s = ?'
DELETE_SEASON = 'DELETE FROM season_metadata WHERE tmdb_id = ?'
DELETE_SEASONS = 'DELETE FROM season_metadata WHERE tmdb_id LIKE ?'
DELETE_FUNCTION = 'DELETE FROM function_cache WHERE string_id = ?'
DELETE_ALL = 'DELETE FROM %s'
# Stremio metadata queries
GET_STREMIO_META = 'SELECT meta, expires FROM stremio_metadata WHERE db_type = ? AND imdb_id = ?'
SET_STREMIO_META = 'INSERT OR REPLACE INTO stremio_metadata VALUES (?, ?, ?, ?)'
DELETE_STREMIO_META = 'DELETE FROM stremio_metadata WHERE db_type = ? AND imdb_id = ?'
GET_ALL_STREMIO = 'SELECT db_type, imdb_id FROM stremio_metadata'
string = str

class MetaCache(BaseCache):
	db_file = metacache_db

	def get(self, media_type, id_type, media_id):
		meta, fanarttv_data = None, None
		try:
			media_id = string(media_id)
			current_time = self._get_timestamp(datetime.now())
			meta = self.get_memory_cache(media_type, id_type, media_id, current_time)
			if meta is None:
				if media_type in movie_show:
					# Validate id_type against whitelist to prevent SQL injection
					if id_type not in VALID_ID_TYPES: return None
					cache_data = self.dbcur.execute(GET_MOVIE_SHOW % id_type, (media_type, media_id)).fetchone()
				else: cache_data = self.dbcur.execute(GET_SEASON, (media_id,)).fetchone()
				if cache_data:
					meta, expiry = safe_eval(cache_data[0]), cache_data[1]
					if expiry < current_time:
						fanarttv_data = self.make_fanart_dict(meta)
						self.delete(media_type, id_type, media_id, meta=meta, dbcon=None)
						meta = None
					else: self.set_memory_cache(media_type, id_type, meta, expiry, media_id)
		except (ValueError, SyntaxError, TypeError, KeyError, sqlite3.ProgrammingError, sqlite3.OperationalError): pass
		return fanarttv_data or meta

	def set(self, media_type, id_type, meta, expiration=30, tmdb_id=None):
		try:
			expires = self._get_timestamp(datetime.now() + timedelta(days=expiration))
			if media_type in movie_show:
				media_id = string(meta[id_type])
				self.dbcur.execute(SET_MOVIE_SHOW, (media_type, string(meta['tmdb_id']), meta['imdb_id'], string(meta['tvdb_id']), repr(meta), expires))
			else:
				media_id = string(tmdb_id)
				self.dbcur.execute(SET_SEASON, (media_id, repr(meta), int(expires)))
		except (KeyError, TypeError, ValueError, sqlite3.ProgrammingError, sqlite3.OperationalError): return None
		self.set_memory_cache(media_type, id_type, meta, expires, media_id)

	def delete(self, media_type, id_type, media_id, meta=None, dbcon=None):
		try:
			media_id = string(media_id)
			if media_type in movie_show:
				# Validate id_type against whitelist to prevent SQL injection
				if id_type not in VALID_ID_TYPES: return
				self.dbcur.execute(DELETE_MOVIE_SHOW % id_type, (media_type, media_id))
				for item in id_types: self.delete_memory_cache(media_type, item, meta[item])
				if media_type == 'tvshow': self.dbcur.execute(DELETE_SEASONS, (media_id+'%',))
			else:
				self.dbcur.execute(DELETE_SEASON, (media_id,))
				self.delete_memory_cache(media_type, id_type, media_id)
		except (KeyError, TypeError, sqlite3.ProgrammingError, sqlite3.OperationalError): return

	def get_memory_cache(self, media_type, id_type, media_id, current_time):
		result = None
		try:
			if media_type in movie_show: prop_string = 'pov_%s_%s_%s' % (media_type, id_type, media_id)
			else: prop_string = 'pov_meta_season_%s' % media_id
			cachedata = get_property(prop_string)
			if cachedata:
				cachedata = safe_eval(cachedata)
				if cachedata[0] > current_time: result = cachedata[1]
		except (ValueError, SyntaxError, TypeError, IndexError): pass
		return result

	def set_memory_cache(self, media_type, id_type, meta, expires, media_id):
		try:
			media_id = string(media_id)
			if media_type in movie_show: cachedata, prop_string = (expires, meta), 'pov_%s_%s_%s' % (media_type, id_type, string(media_id))
			else: cachedata, prop_string = (expires, meta), 'pov_meta_season_%s' % string(media_id)
			set_property(prop_string, repr(cachedata))
		except (TypeError, ValueError): pass

	def delete_memory_cache(self, media_type, id_type, media_id):
		try:
			if media_type in movie_show: clear_property('pov_%s_%s_%s' % (media_type, id_type, media_id))
			else: clear_property('pov_meta_season_%s' % media_id)
		except (TypeError, ValueError): pass

	def get_function(self, prop_string):
		result = None
		try:
			current_time = self._get_timestamp(datetime.now())
			self.dbcur.execute(GET_FUNCTION, (prop_string,))
			cache_data = self.dbcur.fetchone()
			if cache_data and cache_data[2] > current_time: result = safe_eval(cache_data[1])
			else: self.dbcur.execute(DELETE_FUNCTION, (prop_string,))
		except (ValueError, SyntaxError, TypeError, sqlite3.ProgrammingError, sqlite3.OperationalError): pass
		return result

	def set_function(self, prop_string, result, expiration=timedelta(days=1)):
		try:
			expires = self._get_timestamp(datetime.now() + expiration)
			self.dbcur.execute(SET_FUNCTION, (prop_string, repr(result), expires))
		except (TypeError, ValueError, sqlite3.ProgrammingError, sqlite3.OperationalError): return

	def delete_all_seasons_memory_cache(self, media_id, max_seasons=100):
		for item in range(1, max_seasons + 1): clear_property('pov_meta_season_%s_%s' % (string(media_id), string(item)))

	def delete_all(self):
		try:
			self.dbcur.execute(GET_ALL)
			all_entries = self.dbcur.fetchall()
			for i in all_tables:
				# Validate table name against whitelist to prevent SQL injection
				if i in VALID_TABLES:
					self.dbcur.execute(DELETE_ALL % i)
			self.dbcur.execute("""VACUUM""")
			for i in all_entries:
				try:
					tmdb_id = string(i[1])
					self.delete_memory_cache(str(i[0]), 'tmdb_id', tmdb_id)
					self.delete_all_seasons_memory_cache(tmdb_id)
				except (IndexError, TypeError): pass
		except Exception: return

	def make_fanart_dict(self, meta):
		if meta.get('fanart_added', False):
			return {'poster2': meta['poster2'], 'fanart2': meta['fanart2'], 'banner': meta['banner'], 'clearart': meta['clearart'],
					'clearlogo': meta['clearlogo'], 'landscape': meta['landscape'], 'discart': meta['discart'], 'fanart_added': True}
		else: return None

	# Stremio metadata methods
	def get_stremio(self, media_type, imdb_id):
		"""Get Stremio metadata from cache"""
		try:
			imdb_id = string(imdb_id)
			current_time = self._get_timestamp(datetime.now())
			# Check memory cache first
			mem_meta = self.get_stremio_memory_cache(media_type, imdb_id, current_time)
			if mem_meta is not None:
				return mem_meta
			# Check database
			cache_data = self.dbcur.execute(GET_STREMIO_META, (media_type, imdb_id)).fetchone()
			if cache_data:
				meta, expiry = safe_eval(cache_data[0]), cache_data[1]
				if expiry < current_time:
					self.delete_stremio(media_type, imdb_id)
					return None
				self.set_stremio_memory_cache(media_type, imdb_id, meta, expiry)
				return meta
		except (ValueError, SyntaxError, TypeError, KeyError, sqlite3.ProgrammingError, sqlite3.OperationalError):
			pass
		return None

	def set_stremio(self, media_type, imdb_id, meta, expiration=7):
		"""Set Stremio metadata in cache"""
		try:
			imdb_id = string(imdb_id)
			expires = self._get_timestamp(datetime.now() + timedelta(days=expiration))
			self.dbcur.execute(SET_STREMIO_META, (media_type, imdb_id, repr(meta), expires))
			self.set_stremio_memory_cache(media_type, imdb_id, meta, expires)
		except (KeyError, TypeError, ValueError, sqlite3.ProgrammingError, sqlite3.OperationalError):
			return None

	def delete_stremio(self, media_type, imdb_id):
		"""Delete Stremio metadata from cache"""
		try:
			imdb_id = string(imdb_id)
			self.dbcur.execute(DELETE_STREMIO_META, (media_type, imdb_id))
			self.delete_stremio_memory_cache(media_type, imdb_id)
		except (KeyError, TypeError, sqlite3.ProgrammingError, sqlite3.OperationalError):
			return

	def get_stremio_memory_cache(self, media_type, imdb_id, current_time):
		"""Get Stremio metadata from memory cache"""
		result = None
		try:
			prop_string = 'pov_stremio_%s_%s' % (media_type, imdb_id)
			cachedata = get_property(prop_string)
			if cachedata:
				cachedata = safe_eval(cachedata)
				if cachedata[0] > current_time:
					result = cachedata[1]
		except (ValueError, SyntaxError, TypeError, IndexError):
			pass
		return result

	def set_stremio_memory_cache(self, media_type, imdb_id, meta, expires):
		"""Set Stremio metadata in memory cache"""
		try:
			prop_string = 'pov_stremio_%s_%s' % (media_type, imdb_id)
			cachedata = (expires, meta)
			set_property(prop_string, repr(cachedata))
		except (TypeError, ValueError):
			pass

	def delete_stremio_memory_cache(self, media_type, imdb_id):
		"""Delete Stremio metadata from memory cache"""
		try:
			clear_property('pov_stremio_%s_%s' % (media_type, imdb_id))
		except (TypeError, ValueError):
			pass

	def delete_all_stremio(self):
		"""Delete all Stremio metadata from cache"""
		try:
			self.dbcur.execute(GET_ALL_STREMIO)
			all_entries = self.dbcur.fetchall()
			self.dbcur.execute(DELETE_ALL % 'stremio_metadata')
			for entry in all_entries:
				try:
					self.delete_stremio_memory_cache(str(entry[0]), str(entry[1]))
				except (IndexError, TypeError):
					pass
		except Exception:
			return

def cache_function(function, prop_string, url, expiration=96, json=False):
	metacache = MetaCache()
	try:
		data = metacache.get_function(prop_string)
		if data: return data
		if json: result = function(url).json()
		else: result = function(url)
		metacache.set_function(prop_string, result, expiration=timedelta(hours=expiration))
		return result
	finally:
		metacache.close()

