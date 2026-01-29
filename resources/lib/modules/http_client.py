# Shared HTTP client utilities for POV
"""
Centralized HTTP client with Cloudflare bypass capabilities.
Provides connection pooling, session management, and retry logic.

Features:
- Cloudscraper integration for Cloudflare JS challenge bypass
- curl_cffi integration for TLS fingerprint bypass
- Session pooling and recycling
- Browser-like headers
- Thread-safe operation
"""

import time
import requests
from threading import Lock

# Try to import curl_cffi for TLS fingerprint bypass (strongest method)
try:
	from curl_cffi import requests as curl_requests
	HAS_CURL_CFFI = True
except ImportError:
	HAS_CURL_CFFI = False

# Try to import cloudscraper for Cloudflare bypass
try:
	import cloudscraper
	HAS_CLOUDSCRAPER = True
except ImportError:
	HAS_CLOUDSCRAPER = False

# Browser-like headers to help bypass Cloudflare and other protections
BROWSER_HEADERS = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
	'Accept': 'application/json, text/plain, */*',
	'Accept-Language': 'en-US,en;q=0.9',
	'Accept-Encoding': 'gzip, deflate, br',
	'Connection': 'keep-alive',
	'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
	'Sec-Ch-Ua-Mobile': '?0',
	'Sec-Ch-Ua-Platform': '"Windows"',
	'Sec-Fetch-Dest': 'empty',
	'Sec-Fetch-Mode': 'cors',
	'Sec-Fetch-Site': 'cross-site',
}

# Cloudscraper session management - refresh stale sessions
_scraper_lock = Lock()
_scraper_session = None
_scraper_request_count = 0
_scraper_fail_count = 0
_SCRAPER_MAX_REQUESTS = 50  # Refresh session after this many requests
_SCRAPER_MAX_FAILS = 3  # Refresh session after consecutive failures


def get_scraper(force_new=False):
	"""Get or create a cloudscraper session (thread-safe)."""
	global _scraper_session, _scraper_request_count, _scraper_fail_count
	if not HAS_CLOUDSCRAPER:
		return None
	with _scraper_lock:
		# Create new session if needed
		if force_new or _scraper_session is None or _scraper_request_count >= _SCRAPER_MAX_REQUESTS or _scraper_fail_count >= _SCRAPER_MAX_FAILS:
			try:
				_scraper_session = cloudscraper.create_scraper(
					browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
					delay=1
				)
				_scraper_request_count = 0
				_scraper_fail_count = 0
			except Exception:
				_scraper_session = None
		return _scraper_session


def mark_scraper_success():
	"""Mark a successful scraper request (thread-safe)."""
	global _scraper_request_count, _scraper_fail_count
	with _scraper_lock:
		_scraper_request_count += 1
		_scraper_fail_count = 0


def mark_scraper_fail():
	"""Mark a failed scraper request (thread-safe)."""
	global _scraper_fail_count
	with _scraper_lock:
		_scraper_fail_count += 1


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


def fetch_json(url, timeout=8, headers=None, max_retries=3, error_callback=None):
	"""
	Fetch JSON from URL with automatic Cloudflare bypass.

	Tries methods in order of effectiveness:
	1. curl_cffi (best TLS fingerprint bypass)
	2. cloudscraper (good JS challenge bypass)
	3. requests with browser headers (basic)

	Args:
		url: URL to fetch
		timeout: Request timeout in seconds
		headers: Optional custom headers (merged with browser headers)
		max_retries: Max retry attempts per method
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
	cloudflare_blocked = False

	# Method 1: curl_cffi with Chrome impersonation (best for TLS fingerprinting)
	if HAS_CURL_CFFI:
		try:
			for attempt in range(max_retries):
				try:
					response = curl_requests.get(
						url,
						timeout=timeout,
						headers=headers,
						impersonate='chrome120'
					)
					if response.status_code == 200:
						content_type = response.headers.get('content-type', '')
						if 'text/html' not in content_type:
							try:
								return response.json()
							except ValueError:
								pass
					if response.status_code in (403, 418, 503) or 'text/html' in response.headers.get('content-type', ''):
						cloudflare_blocked = True
						if attempt < max_retries - 1:
							time.sleep(0.5 * (attempt + 1))
							continue
					break
				except Exception as e:
					last_error = e
					if attempt < max_retries - 1:
						time.sleep(0.5 * (attempt + 1))
						continue
					break
		except Exception as e:
			last_error = e

	# Method 2: cloudscraper (JS challenge solver)
	scraper = get_scraper()
	if scraper:
		try:
			for attempt in range(max_retries):
				try:
					response = scraper.get(url, timeout=timeout, headers=headers)
					if response.status_code == 200:
						content_type = response.headers.get('content-type', '')
						if 'text/html' not in content_type:
							try:
								data = response.json()
								mark_scraper_success()
								return data
							except ValueError:
								pass
					if response.status_code in (403, 418, 503) or 'text/html' in response.headers.get('content-type', ''):
						cloudflare_blocked = True
						mark_scraper_fail()
						if attempt < max_retries - 1:
							time.sleep(0.5 * (attempt + 1))
							# Try with fresh session on last attempt
							if attempt == max_retries - 2:
								scraper = get_scraper(force_new=True)
								if not scraper:
									break
							continue
					break
				except Exception as e:
					last_error = e
					mark_scraper_fail()
					if attempt < max_retries - 1:
						time.sleep(0.5 * (attempt + 1))
						continue
					break
		except Exception as e:
			last_error = e

	# Method 3: Regular requests (fallback)
	try:
		for attempt in range(2):
			try:
				response = requests.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					content_type = response.headers.get('content-type', '')
					if 'text/html' not in content_type:
						try:
							return response.json()
						except ValueError:
							pass
				if response.status_code in (403, 418, 503):
					cloudflare_blocked = True
					if attempt == 0:
						time.sleep(0.5)
						continue
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				if attempt == 0:
					time.sleep(0.5)
					continue
				break
	except Exception as e:
		last_error = e

	# Log error if callback provided
	if error_callback and (response is not None or last_error):
		if response is not None:
			status = response.status_code
			if status == 403:
				error_callback('Cloudflare blocked' if cloudflare_blocked else f'HTTP 403')
			elif status == 418:
				error_callback('Bot protection active')
			elif status == 503:
				error_callback('Service unavailable')
			elif status in (522, 524):
				error_callback('Timeout at origin')
			elif status != 200:
				error_callback(f'HTTP {status}')
		elif last_error:
			error_callback(str(last_error)[:80])

	return None


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
