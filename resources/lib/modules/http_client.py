# Shared HTTP client utilities for POV
"""
Centralized HTTP client for POV.
Provides session management, retry logic, and browser-like headers.
"""

import requests
from threading import Lock


# Browser-like headers for HTTP requests
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate, br',
	'Connection': 'keep-alive',
}


def get_headers_for_url(base_url, extra_headers=None):
	"""Generate browser-like headers with Referer/Origin for a URL."""
	try:
		from urllib.parse import urlparse
		parsed = urlparse(base_url)
		origin = f"{parsed.scheme}://{parsed.netloc}"
	except Exception:
		origin = base_url

	headers = BROWSER_HEADERS.copy()
	headers['Referer'] = f"{origin}/"
	headers['Origin'] = origin

	if extra_headers:
		headers.update(extra_headers)

	return headers


def fetch_json(url, timeout=8, headers=None, max_retries=2, error_callback=None):
	"""
	Fetch JSON from URL.

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

	response = None
	last_error = None

	try:
		for attempt in range(max_retries):
			try:
				response = requests.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					try:
						return response.json()
					except ValueError:
						pass
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				if attempt < max_retries - 1:
					import time
					time.sleep(0.5)
					continue
				break
	except Exception as e:
		last_error = e

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
	Fetch URL returning raw response.

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

	response = None
	last_error = None

	try:
		for attempt in range(max_retries):
			try:
				response = requests.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					return response, None
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				if attempt < max_retries - 1:
					import time
					time.sleep(0.5)
					continue
				break
	except Exception as e:
		last_error = e

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


# Reusable session for general API requests
_api_session = None
_api_session_lock = Lock()


def get_api_session():
	"""Get or create a reusable requests session for API calls."""
	global _api_session
	with _api_session_lock:
		if _api_session is None:
			_api_session = requests.Session()
			_api_session.headers.update({
				'User-Agent': BROWSER_HEADERS['User-Agent'],
				'Accept': 'application/json'
			})
		return _api_session
