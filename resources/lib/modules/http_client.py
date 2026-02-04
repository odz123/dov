# Shared HTTP client utilities for POV
"""
Centralized HTTP client for POV.
Provides session management, retry logic, and browser-like headers.
Uses Chrome-like TLS fingerprint to avoid Cloudflare 403 blocks.
"""

import requests
import xbmc
from threading import Lock


# Chrome 131 TLS cipher suites for JA3 fingerprint matching
CHROME_CIPHERS = ':'.join([
	'TLS_AES_128_GCM_SHA256',
	'TLS_AES_256_GCM_SHA384',
	'TLS_CHACHA20_POLY1305_SHA256',
	'ECDHE-ECDSA-AES128-GCM-SHA256',
	'ECDHE-RSA-AES128-GCM-SHA256',
	'ECDHE-ECDSA-AES256-GCM-SHA384',
	'ECDHE-RSA-AES256-GCM-SHA384',
	'ECDHE-ECDSA-CHACHA20-POLY1305',
	'ECDHE-RSA-CHACHA20-POLY1305',
	'ECDHE-RSA-AES128-SHA',
	'ECDHE-RSA-AES256-SHA',
	'AES128-GCM-SHA256',
	'AES256-GCM-SHA384',
	'AES128-SHA',
	'AES256-SHA',
])


def _create_chrome_adapter():
	"""Create an HTTPAdapter with Chrome-like TLS configuration."""
	try:
		import ssl
		from requests.adapters import HTTPAdapter
		from urllib3.util.ssl_ import create_urllib3_context

		class ChromeTLSAdapter(HTTPAdapter):
			def init_poolmanager(self, *args, **kwargs):
				try:
					ctx = create_urllib3_context(ciphers=CHROME_CIPHERS)
					ctx.minimum_version = ssl.TLSVersion.TLSv1_2
					ctx.set_alpn_protocols(['http/1.1'])
					kwargs['ssl_context'] = ctx
				except Exception:
					pass
				super().init_poolmanager(*args, **kwargs)

			def proxy_manager_for(self, proxy, **proxy_kwargs):
				try:
					ctx = create_urllib3_context(ciphers=CHROME_CIPHERS)
					ctx.minimum_version = ssl.TLSVersion.TLSv1_2
					ctx.set_alpn_protocols(['http/1.1'])
					proxy_kwargs['ssl_context'] = ctx
				except Exception:
					pass
				return super().proxy_manager_for(proxy, **proxy_kwargs)

		return ChromeTLSAdapter()
	except Exception:
		return None


# Reusable session with Chrome TLS fingerprint
_session = None
_session_lock = Lock()


def _get_session():
	"""Get or create a requests session with Chrome-like TLS fingerprint."""
	global _session
	with _session_lock:
		if _session is None:
			_session = requests.Session()
			adapter = _create_chrome_adapter()
			if adapter:
				_session.mount('https://', adapter)
				_session.mount('http://', adapter)
		return _session


# Browser-like headers matching Chrome 131
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate',
	'Connection': 'keep-alive',
	'sec-ch-ua': '"Google Chrome";v="131", "Not_A Brand";v="24", "Chromium";v="131"',
	'sec-ch-ua-mobile': '?0',
	'sec-ch-ua-platform': '"Windows"',
	'sec-fetch-dest': 'empty',
	'sec-fetch-mode': 'cors',
	'sec-fetch-site': 'none',
}


def get_headers_for_url(base_url, extra_headers=None):
	"""Generate browser-like headers for a URL.
	Does not include Origin header since browsers don't send it for GET requests."""
	headers = BROWSER_HEADERS.copy()

	if extra_headers:
		headers.update(extra_headers)

	return headers


def _urllib_fallback_core(url, timeout=8, return_raw=False):
	"""Core fallback fetch using urllib.request (different TLS fingerprint).
	Used when requests library gets blocked by Cloudflare.

	Args:
		url: URL to fetch
		timeout: Request timeout in seconds
		return_raw: If True, returns response-like object; if False, returns parsed JSON

	Returns:
		Parsed JSON dict/list, or response-like object, or None on failure
	"""
	import json
	import gzip
	from io import BytesIO
	import urllib.request

	headers = {
		'User-Agent': BROWSER_HEADERS['User-Agent'],
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'en-US,en;q=0.9',
		'Accept-Encoding': 'gzip, deflate',
		'sec-ch-ua': BROWSER_HEADERS['sec-ch-ua'],
		'sec-ch-ua-mobile': '?0',
		'sec-ch-ua-platform': '"Windows"',
	}

	try:
		req = urllib.request.Request(url)
		for key, value in headers.items():
			req.add_header(key, value)
		response = urllib.request.urlopen(req, timeout=int(timeout))
		data = response.read(5242880)
		try:
			encoding = response.headers.get('Content-Encoding', '')
		except Exception:
			encoding = ''
		if encoding == 'gzip':
			data = gzip.GzipFile(fileobj=BytesIO(data)).read()

		if return_raw:
			content_type = ''
			try:
				content_type = response.headers.get('Content-Type', '')
			except Exception:
				pass

			class UrllibResponse:
				"""Simple response-like object compatible with requests.Response."""
				def __init__(self, data, content_type):
					self.status_code = 200
					self.headers = {'content-type': content_type}
					self.content = data
					self.text = data.decode('utf-8', errors='ignore')
				def json(self):
					return json.loads(self.content.decode('utf-8', errors='ignore'))

			return UrllibResponse(data, content_type)
		else:
			return json.loads(data.decode('utf-8', errors='ignore'))
	except Exception:
		return None


def _urllib_fallback(url, timeout=8):
	"""Fallback fetch using urllib.request - returns parsed JSON."""
	return _urllib_fallback_core(url, timeout, return_raw=False)


def fetch_json(url, timeout=8, headers=None, max_retries=2, error_callback=None):
	"""
	Fetch JSON from URL using session with Chrome TLS fingerprint.

	Args:
		url: URL to fetch
		timeout: Request timeout in seconds
		headers: Optional custom headers (merged with browser headers)
		max_retries: Max retry attempts
		error_callback: Optional function(error_message) for error logging

	Returns:
		dict/list: Parsed JSON data, or None on failure
	"""
	if headers is None:
		headers = get_headers_for_url(url)
	else:
		base_headers = get_headers_for_url(url)
		base_headers.update(headers)
		headers = base_headers

	session = _get_session()
	response = None
	last_error = None

	try:
		for attempt in range(max_retries):
			try:
				response = session.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					try:
						return response.json()
					except ValueError:
						pass
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				if attempt < max_retries - 1:
					xbmc.Monitor().waitForAbort(0.5)
					continue
				break
	except Exception as e:
		last_error = e

	# If blocked (403), try urllib fallback with different TLS fingerprint
	if response is not None and response.status_code == 403:
		fallback_result = _urllib_fallback(url, timeout=timeout)
		if fallback_result is not None:
			return fallback_result

	# Log error if callback provided
	if error_callback and (response is not None or last_error):
		if response is not None:
			status = response.status_code
			if status != 200:
				error_callback('HTTP %d' % status)
		elif last_error:
			error_callback(str(last_error)[:80])

	return None


def fetch_raw(url, timeout=8, headers=None, max_retries=2):
	"""
	Fetch URL returning raw response using session with Chrome TLS fingerprint.

	Args:
		url: URL to fetch
		timeout: Request timeout in seconds
		headers: Optional custom headers (merged with browser headers)
		max_retries: Max retry attempts

	Returns:
		tuple: (response, error_message) - error_message is None on success,
		       response may be None on complete failure
	"""
	if headers is None:
		headers = get_headers_for_url(url)
	else:
		base_headers = get_headers_for_url(url)
		base_headers.update(headers)
		headers = base_headers

	session = _get_session()
	response = None
	last_error = None

	try:
		for attempt in range(max_retries):
			try:
				response = session.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					return response, None
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				if attempt < max_retries - 1:
					xbmc.Monitor().waitForAbort(0.5)
					continue
				break
	except Exception as e:
		last_error = e

	# If blocked (403), try urllib fallback with different TLS fingerprint
	if response is not None and response.status_code == 403:
		fallback_result = _urllib_fallback_raw(url, timeout=timeout)
		if fallback_result is not None:
			return fallback_result, None

	# Build error message
	error_msg = None
	if response is not None:
		if response.status_code != 200:
			error_msg = 'HTTP %d' % response.status_code
	elif last_error:
		error_msg = str(last_error)[:80]
	else:
		error_msg = 'Connection failed'

	return response, error_msg


def _urllib_fallback_raw(url, timeout=8):
	"""Fallback raw fetch using urllib.request - returns response-like object."""
	return _urllib_fallback_core(url, timeout, return_raw=True)


def fetch_streams(base_url, media_type, media_id, timeout=8, error_callback=None):
	"""
	Fetch streams from a Stremio addon endpoint.

	Args:
		base_url: Base addon URL (will strip /manifest.json if present)
		media_type: 'movie' or 'series'
		media_id: IMDB ID for movies, or 'imdb:season:episode' for series
		timeout: Request timeout
		error_callback: Optional function(error_message) for logging

	Returns:
		list: Stream objects, or empty list on failure
	"""
	# Clean up addon URL
	url = base_url.rstrip('/')
	if url.endswith('/manifest.json'):
		url = url[:-14]

	endpoint = f"{url}/stream/{media_type}/{media_id}.json"

	data = fetch_json(endpoint, timeout=timeout, error_callback=error_callback)
	if data:
		return data.get('streams') or []
	return []


def fetch_subtitles(base_url, media_type, media_id, timeout=5):
	"""
	Fetch subtitles from a Stremio addon endpoint.

	Args:
		base_url: Base addon URL
		media_type: 'movie' or 'series'
		media_id: IMDB ID for movies, or 'imdb:season:episode' for series
		timeout: Request timeout

	Returns:
		list: Subtitle objects, or empty list on failure
	"""
	url = base_url.rstrip('/')
	if url.endswith('/manifest.json'):
		url = url[:-14]

	endpoint = f"{url}/subtitles/{media_type}/{media_id}.json"

	data = fetch_json(endpoint, timeout=timeout)
	if data:
		return data.get('subtitles') or []
	return []


def fetch_manifest(base_url, timeout=3):
	"""
	Fetch manifest from a Stremio addon.

	Args:
		base_url: Base addon URL
		timeout: Request timeout

	Returns:
		dict: Manifest data, or None on failure
	"""
	url = base_url.rstrip('/')
	if url.endswith('/manifest.json'):
		url = url[:-14]

	endpoint = f"{url}/manifest.json"
	return fetch_json(endpoint, timeout=timeout)


def fetch_meta(base_url, media_type, meta_id, timeout=8, error_callback=None):
	"""
	Fetch metadata from a Stremio addon endpoint.

	Args:
		base_url: Base addon URL (will strip /manifest.json if present)
		media_type: Content type (e.g., 'movie', 'series')
		meta_id: Content ID (e.g., IMDB ID)
		timeout: Request timeout
		error_callback: Optional function(error_message) for logging

	Returns:
		dict: Meta object, or None on failure
	"""
	url = base_url.rstrip('/')
	if url.endswith('/manifest.json'):
		url = url[:-14]

	endpoint = f"{url}/meta/{media_type}/{meta_id}.json"

	data = fetch_json(endpoint, timeout=timeout, error_callback=error_callback)
	if data:
		return data.get('meta')
	return None


# Reusable session for general API requests
_api_session = None
_api_session_lock = Lock()


def get_api_session():
	"""Get or create a reusable requests session for API calls."""
	global _api_session
	with _api_session_lock:
		if _api_session is None:
			_api_session = requests.Session()
			adapter = _create_chrome_adapter()
			if adapter:
				_api_session.mount('https://', adapter)
				_api_session.mount('http://', adapter)
			_api_session.headers.update({
				'User-Agent': BROWSER_HEADERS['User-Agent'],
				'Accept': 'application/json'
			})
		return _api_session
