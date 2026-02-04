import re
import time
import random
import hashlib
import unicodedata
import _strptime  # fix bug in python import
from queue import SimpleQueue
from html import unescape
from importlib import import_module
from datetime import datetime, timedelta, date
from modules.kodi_utils import local_string as ls, get_setting, logger

days_translate = {'Monday': 32971, 'Tuesday': 32972, 'Wednesday': 32973, 'Thursday': 32974, 'Friday': 32975, 'Saturday': 32976, 'Sunday': 32977}

# Pre-compiled regex patterns for clean_title() - avoids recompilation on each call
_RE_HTML_ENTITY = re.compile(r'&#(\d+);')
_RE_HTML_ENTITY_FIX = re.compile(r'(&#[0-9]+)([^;^0-9]+)')
_RE_CLEAN_TITLE = re.compile(r'\n|([\[({].+?[})\]])|([:;–\-"\',!_.?~$@])|\s')
_RE_NON_ASCII = re.compile(r'[^\x00-\x7f]')
_RE_HTML_ENTITY_REPLACE = re.compile(r"(&#[0-9]+)([^;^0-9]+)")
_RE_ARTICLE = re.compile(r'^((\w+)\s+)')
_RE_SORT_ARTICLE = re.compile(r'(^the |^a |^an )')
_RE_SLUG_INVALID = re.compile(r'[^a-z0-9_]')
_RE_SLUG_MULTI_DASH = re.compile(r'--+')

class TaskPool:
	@staticmethod
	def process(_threads):
		for index, i in enumerate(_threads, 1):
			try: i.start()
			except Exception as e: logger('thread error', f"{index}: {e}")
			else: yield i

	def __init__(self, maxsize=None):
		self.maxsize = maxsize or int(get_setting('pov.max_threads', '100'))
		self._queue = SimpleQueue()

	def _thread_target(self, queue, target):
		while not queue.empty():
			try: target(*queue.get())
			except Exception as e: logger('queue error', f"{e}")

	def tasks(self, _target, _list, _thread):
		maxsize = min(len(_list), self.maxsize)
		for tag in _list:
			self._queue.put(tag)
		threads = [_thread(target=self._thread_target, args=(self._queue, _target)) for i in range(maxsize)]
		return list(self.process(threads))

	def tasks_enumerate(self, _target, _list, _thread):
		maxsize = min(len(_list), self.maxsize)
		for p, tag in enumerate(_list, 1):
			self._queue.put((p, tag))
		threads = [_thread(target=self._thread_target, args=(self._queue, _target)) for i in range(maxsize)]
		return list(self.process(threads))

def manual_function_import(location, function_name):
	return getattr(import_module(location), function_name)

def make_thread_list(_target, _list, _thread, max_threads=100):
	"""Create and start threads for each item in _list.

	Args:
		_target: Target function to call
		_list: List of items to process
		_thread: Thread class to use
		max_threads: Maximum concurrent threads (default 100). If list is larger,
					 threads are started in batches and joined before next batch.

	Yields:
		Thread objects (already started)
	"""
	_list = list(_list)  # Materialize if generator
	if len(_list) <= max_threads:
		# Original behavior for small lists - start all at once
		for item in _list:
			threaded_object = _thread(target=_target, args=(item,))
			threaded_object.start()
			yield threaded_object
	else:
		# Bounded behavior for large lists - process in batches
		all_threads = []
		for i in range(0, len(_list), max_threads):
			batch = _list[i:i + max_threads]
			batch_threads = []
			for item in batch:
				threaded_object = _thread(target=_target, args=(item,))
				threaded_object.start()
				batch_threads.append(threaded_object)
			all_threads.extend(batch_threads)
			# Join current batch before starting next if not the last batch
			if i + max_threads < len(_list):
				for t in batch_threads:
					t.join()
		for t in all_threads:
			yield t

def chunks(item_list, limit):
	"""
	Yield successive limit-sized chunks from item_list.
	"""
	for i in range(0, len(item_list), limit): yield item_list[i:i + limit]

def string_to_float(string, default_return):
	"""
	Remove all alpha from string and return a float.
	Returns float of "default_return" upon ValueError.
	"""
	try: return float(''.join(c for c in string if (c.isdigit() or c =='.')))
	except ValueError: return float(default_return)

def string_alphanum_to_num(string):
	"""
	Remove all alpha from string and return remaining string.
	Returns original string upon ValueError.
	"""
	try: return ''.join(c for c in string if c.isdigit())
	except ValueError: return string

def jsondate_to_datetime(jsondate_object, resformat, remove_time=False):
	if remove_time: datetime_object = datetime_workaround(jsondate_object, resformat).date()
	else: datetime_object = datetime_workaround(jsondate_object, resformat)
	return datetime_object

def get_datetime(string=False, dt=False):
	d = datetime.now()
	if dt: return d
	if string: return d.strftime('%Y-%m-%d')
	return datetime.date(d)

def adjust_premiered_date(orig_date, adjust_hours):
	if not orig_date: return None, None
	orig_date += ' 20:00:00'
	datetime_object = jsondate_to_datetime(orig_date, '%Y-%m-%d %H:%M:%S')
	adjusted_datetime = datetime_object + timedelta(hours=adjust_hours)
	adjusted_string = adjusted_datetime.strftime('%Y-%m-%d')
	return adjusted_datetime.date(), adjusted_string

def make_day(today, date, date_format, use_words=True):
	day_diff = (date - today).days
	try: day = date.strftime(date_format)
	except ValueError: day = date.strftime('%Y-%m-%d')
	if use_words:
		if day_diff == -1: day = ls(32848).upper()
		elif day_diff == 0: day = ls(32849).upper()
		elif day_diff == 1: day = ls(32850).upper()
		elif 1 < day_diff < 7: day = ls(days_translate[date.strftime('%A')])
	return day

def subtract_dates(date1, date2):
	return (date1 - date2).days

def datetime_workaround(data, str_format):
	try: datetime_object = datetime.strptime(data, str_format)
	except Exception: datetime_object = datetime(*(time.strptime(data, str_format)[0:6]))
	return datetime_object

def date_difference(current_date, compare_date, difference_tolerance, allow_postive_difference=False):
	try:
		difference = subtract_dates(current_date, compare_date)
		if not allow_postive_difference and difference > 0: return False
		else: difference = abs(difference)
		if difference > difference_tolerance: return False
		return True
	except Exception: return True

def calculate_age(born, str_format, died=None):
	''' born and died are str objects e.g. '1972-05-28' '''
	born = datetime_workaround(born, str_format)
	if not died: today = date.today()
	else: today = datetime_workaround(died, str_format)
	return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

def batch_replace(s, replace_info):
	for r in replace_info:
		s = str(s).replace(r[0], r[1])
	return s

def clean_file_name(s, use_encoding=False, use_blanks=True):
	try:
		hex_entities = [['&#x26;', '&'], ['&#x27;', '\''], ['&#xC6;', 'AE'], ['&#xC7;', 'C'],
					['&#xF4;', 'o'], ['&#xE9;', 'e'], ['&#xEB;', 'e'], ['&#xED;', 'i'],
					['&#xEE;', 'i'], ['&#xA2;', 'c'], ['&#xE2;', 'a'], ['&#xEF;', 'i'],
					['&#xE1;', 'a'], ['&#xE8;', 'e'], ['%2E', '.'], ['&frac12;', '%BD'],
					['&#xBD;', '%BD'], ['&#xB3;', '%B3'], ['&#xB0;', '%B0'], ['&amp;', '&'],
					['&#xB7;', '.'], ['&#xE4;', 'A'], ['\xe2\x80\x99', '']]
		special_encoded = [['"', '%22'], ['*', '%2A'], ['/', '%2F'], [':', ','], ['<', '%3C'],
							['>', '%3E'], ['?', '%3F'], ['\\', '%5C'], ['|', '%7C']]

		special_blanks = [['"', ' '], ['/', ' '], [':', ''], ['<', ' '],
							['>', ' '], ['?', ' '], ['\\', ' '], ['|', ' '], ['%BD;', ' '],
							['%B3;', ' '], ['%B0;', ' '], ["'", ""], [' - ', ' '], ['.', ' '],
							['!', ''], [';', ''], [',', '']]
		s = batch_replace(s, hex_entities)
		if use_encoding: s = batch_replace(s, special_encoded)
		if use_blanks: s = batch_replace(s, special_blanks)
		s = s.strip()
	except Exception: pass
	return s

def clean_title(title):
	try:
		if not title: return
		title = title.lower()
		title = _RE_HTML_ENTITY.sub('', title)
		title = _RE_HTML_ENTITY_FIX.sub('\\1;\\2', title)
		title = _RE_HTML_ENTITY_FIX.sub('\\1;\\2', title)
		title = title.replace('&quot;', '\"').replace('&amp;', '&')
		title = _RE_CLEAN_TITLE.sub('', title)
	except Exception: pass
	return title

def byteify(data, ignore_dicts=False):
	try:
		if isinstance(data, bytes): return data.decode('utf-8')
		if isinstance(data, list): return [byteify(item, ignore_dicts=True) for item in data]
		if isinstance(data, dict) and not ignore_dicts:
			return dict([(byteify(key, ignore_dicts=True), byteify(value, ignore_dicts=True)) for key, value in data.items()])
	except Exception: pass
	return data

def normalize(txt):
	txt = _RE_NON_ASCII.sub('', txt)
	return txt

def safe_string(obj):
	try:
		try: return str(obj)
		except UnicodeEncodeError: return obj.encode('utf-8', 'ignore').decode('ascii', 'ignore')
		except Exception: return ""
	except Exception: return obj

def remove_accents(obj):
	try:
		try: obj = u'%s' % obj
		except Exception: pass
		obj = ''.join(c for c in unicodedata.normalize('NFD', obj) if unicodedata.category(c) != 'Mn')
	except Exception: pass
	return obj

def regex_from_to(text, from_string, to_string, excluding=True):
	if excluding:
		match = re.search(r"(?i)" + from_string + r"([\S\s]+?)" + to_string, text)
		r = match.group(1) if match else ''
	else:
		match = re.search(r"(?i)(" + from_string + r"[\S\s]+?" + to_string + ")", text)
		r = match.group(1) if match else ''
	return r

def regex_get_all(text, start_with, end_with):
	r = re.findall(r"(?i)(" + start_with + r"[\S\s]+?" + end_with + ")", text)
	return r

def replace_html_codes(txt):
	txt = _RE_HTML_ENTITY_REPLACE.sub("\\1;\\2", txt)
	txt = unescape(txt)
	txt = txt.replace("&quot;", "\"")
	txt = txt.replace("&amp;", "&")
	return txt

def gen_file_hash(file):
	try:
		md5_hash = hashlib.md5()
		with open(file, 'rb') as afile:
			for chunk in iter(lambda: afile.read(8192), b''):
				md5_hash.update(chunk)
			return md5_hash.hexdigest()
	except Exception: pass

def sec2time(sec, n_msec=3):
	''' Convert seconds to 'D days, HH:MM:SS.FFF' '''
	if hasattr(sec,'__len__'): return [sec2time(s) for s in sec]
	m, s = divmod(sec, 60)
	h, m = divmod(m, 60)
	d, h = divmod(h, 24)
	if n_msec > 0: pattern = '%%02d:%%02d:%%0%d.%df' % (n_msec+3, n_msec)
	else: pattern = '%02d:%02d:%02d'
	if d == 0: return pattern % (h, m, s)
	return ('%d days, ' + pattern) % (d, h, m, s)

def released_key(item):
	if 'released' in item: return item['released'] or '2050-01-01'
	if 'first_aired' in item: return item['first_aired'] or '2050-01-01'
	return '2050-01-01'

def title_key(title, ignore_articles):
	if not ignore_articles: return title
	try:
		if title is None: title = ''
		articles = ('the', 'a', 'an')
		match = _RE_ARTICLE.match(title.lower())
		if match and match.group(2) in articles: offset = len(match.group(1))
		else: offset = 0
		return title[offset:]
	except Exception: return title

def sort_for_article(_list, _key, ignore_articles):
	if not ignore_articles: _list.sort(key=lambda k: k[_key])
	else: _list.sort(key=lambda k: _RE_SORT_ARTICLE.sub('', k[_key].lower()))
	return _list

def sort_list(sort_key, sort_direction, list_data, ignore_articles):
	try:
		reverse = sort_direction != 'asc'
		if sort_key == 'rank': return sorted(list_data, key=lambda x: x['rank'], reverse=reverse)
		elif sort_key == 'added': return sorted(list_data, key=lambda x: x['listed_at'], reverse=reverse)
#		elif sort_key == 'title': return sorted(list_data, key=lambda x: title_key(x[x['type']].get('title'), ignore_articles), reverse=reverse)
		elif sort_key == 'title': return sorted(list_data, key=lambda x: title_key(x['movie' if x['type'] == 'movie' else 'show'].get('title'), ignore_articles), reverse=reverse)
		elif sort_key == 'released': return sorted(list_data, key=lambda x: released_key(x[x['type']]), reverse=reverse)
		elif sort_key == 'runtime': return sorted(list_data, key=lambda x: x[x['type']].get('runtime', 0), reverse=reverse)
		elif sort_key == 'popularity': return sorted(list_data, key=lambda x: x[x['type']].get('votes', 0), reverse=reverse)
		elif sort_key == 'percentage': return sorted(list_data, key=lambda x: x[x['type']].get('rating', 0), reverse=reverse)
		elif sort_key == 'votes': return sorted(list_data, key=lambda x: x[x['type']].get('votes', 0), reverse=reverse)
		elif sort_key == 'random': return sorted(list_data, key=lambda k: random.random())
		else: return list_data
	except Exception: return list_data

def paginate_list(item_list, page, letter, limit=20):
	def _get_start_index(letter):
		if letter == 't':
			try:
				beginswith_tuple = ('s', 'the s', 'a s', 'an s')
				indexes = [i for i, v in enumerate(title_list) if v.startswith(beginswith_tuple)]
				start_index = indexes[-1] + 1 if indexes else None
			except Exception: start_index = None
		else:
			beginswith_tuple = (letter, 'the %s' % letter, 'a %s' % letter, 'an %s' % letter)
			try: start_index = next(i for i, v in enumerate(title_list) if v.startswith(beginswith_tuple))
			except Exception: start_index = None
		return start_index
	if letter != 'None':
		from itertools import chain, zip_longest
		title_list = [i['title'].lower() for i in item_list]
		start_list = [chr(i) for i in range(97, 123)]
		letter_index = start_list.index(letter)
		base_list = [element for element in chain.from_iterable(zip_longest(start_list[letter_index:], start_list[:letter_index][::-1])) if element is not None]
		for i in base_list:
			start_index = _get_start_index(i)
			if start_index is not None: break
		item_list = item_list[start_index:]
	pages = list(chunks(item_list, limit))
	total_pages = len(pages)
	return pages[page - 1], total_pages

def make_title_slug(name):
	name = name.strip()
	name = name.lower()
	name = _RE_SLUG_INVALID.sub('-', name)
	name = _RE_SLUG_MULTI_DASH.sub('-', name)
	return name

