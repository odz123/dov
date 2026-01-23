# Stremio Addon Manager for POV
"""
	Enhanced manager for adding, removing, and configuring Stremio addons
	Features:
	- Debrid service configuration
	- Addon configuration URLs (for addons like Torrentio)
	- Popular addon presets
	- Connection testing
	- Cloudflare bypass via cloudscraper
"""

import json
import time
import requests
from modules.kodi_utils import (
	notification, ok_dialog, confirm_dialog, select_dialog,
	get_setting, set_setting, dialog, local_string
)

# Try to import cloudscraper for Cloudflare bypass
try:
	import cloudscraper
	HAS_CLOUDSCRAPER = True
except ImportError:
	HAS_CLOUDSCRAPER = False

# Try to import curl_cffi for TLS fingerprint bypass (stronger than cloudscraper)
try:
	from curl_cffi import requests as curl_requests
	HAS_CURL_CFFI = True
except ImportError:
	HAS_CURL_CFFI = False

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

# Cloudscraper session management
_scraper_session = None
_scraper_fail_count = 0

def _get_scraper(force_new=False):
	global _scraper_session, _scraper_fail_count
	if not HAS_CLOUDSCRAPER:
		return None
	if force_new or _scraper_session is None or _scraper_fail_count >= 3:
		try:
			_scraper_session = cloudscraper.create_scraper(
				browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
				delay=1
			)
			_scraper_fail_count = 0
		except Exception:
			_scraper_session = None
	return _scraper_session

def _mark_scraper_fail():
	global _scraper_fail_count
	_scraper_fail_count += 1


def _fetch_url(url, timeout=10):
	"""Fetch a URL with multi-method Cloudflare bypass and retry logic"""
	# Extract origin for Referer/Origin headers
	try:
		from urllib.parse import urlparse
		parsed = urlparse(url)
		origin = f"{parsed.scheme}://{parsed.netloc}"
	except:
		origin = url.rsplit('/', 1)[0] if '/' in url else url

	headers = BROWSER_HEADERS.copy()
	headers['Referer'] = f"{origin}/"
	headers['Origin'] = origin

	last_response = None

	# Method 1: curl_cffi with Chrome impersonation (best for TLS fingerprinting)
	if HAS_CURL_CFFI:
		try:
			for attempt in range(3):
				try:
					response = curl_requests.get(url, timeout=timeout, headers=headers, impersonate='chrome120')
					if response.status_code == 200:
						content_type = response.headers.get('content-type', '')
						if 'text/html' not in content_type:
							return response, None
					last_response = response
					if response.status_code in (403, 418, 503):
						if attempt < 2:
							time.sleep(0.5 * (attempt + 1))
							continue
					break
				except Exception:
					if attempt < 2:
						time.sleep(0.5 * (attempt + 1))
						continue
					raise
		except Exception:
			pass

	# Method 2: cloudscraper (JS challenge solver)
	scraper = _get_scraper()
	if scraper:
		try:
			for attempt in range(3):
				try:
					response = scraper.get(url, timeout=timeout, headers=headers)
					if response.status_code == 200:
						content_type = response.headers.get('content-type', '')
						if 'text/html' not in content_type:
							return response, None
					last_response = response
					if response.status_code in (403, 418, 503):
						_mark_scraper_fail()
						if attempt < 2:
							time.sleep(0.5 * (attempt + 1))
							if attempt == 1:
								scraper = _get_scraper(force_new=True)
								if not scraper:
									break
							continue
					break
				except Exception:
					_mark_scraper_fail()
					if attempt < 2:
						time.sleep(0.5 * (attempt + 1))
						continue
					raise
		except Exception:
			pass

	# Method 3: Regular requests (fallback)
	try:
		for attempt in range(2):
			try:
				response = requests.get(url, timeout=timeout, headers=headers)
				if response.status_code == 200:
					content_type = response.headers.get('content-type', '')
					if 'text/html' not in content_type:
						return response, None
				last_response = response
				if response.status_code in (403, 418, 503):
					if attempt == 0:
						time.sleep(0.5)
						continue
				break
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
				if attempt == 0:
					time.sleep(0.5)
					continue
				raise
	except Exception:
		pass

	# Return last response with error info
	if last_response:
		if last_response.status_code == 403:
			return last_response, "Blocked by Cloudflare - addon needs configuration"
		elif last_response.status_code == 418:
			return last_response, "Bot protection - use configured addon URL"
		elif last_response.status_code == 503:
			return last_response, "Service unavailable - addon may be down"
		elif last_response.status_code in (522, 524):
			return last_response, "Addon server timeout"
		elif 'text/html' in last_response.headers.get('content-type', ''):
			return last_response, "Cloudflare challenge - use configured URL"
		else:
			return last_response, "HTTP %d" % last_response.status_code
	return None, "Connection failed"


# Debrid service definitions
DEBRID_SERVICES = {
	'realdebrid': {
		'name': 'Real-Debrid',
		'setting_id': 'rd.token',
		'param_names': ['realdebrid', 'rd', 'RD'],
		'enabled_setting': 'rd.enabled'
	},
	'premiumize': {
		'name': 'Premiumize',
		'setting_id': 'pm.token',
		'param_names': ['premiumize', 'pm', 'PM'],
		'enabled_setting': 'pm.enabled'
	},
	'alldebrid': {
		'name': 'AllDebrid',
		'setting_id': 'ad.token',
		'param_names': ['alldebrid', 'ad', 'AD'],
		'enabled_setting': 'ad.enabled'
	},
	'torbox': {
		'name': 'TorBox',
		'setting_id': 'tb.token',
		'param_names': ['torbox', 'tb', 'TB'],
		'enabled_setting': 'tb.enabled'
	},
	'offcloud': {
		'name': 'Offcloud',
		'setting_id': 'oc.token',
		'param_names': ['offcloud', 'oc', 'OC'],
		'enabled_setting': 'oc.enabled'
	},
	'easydebrid': {
		'name': 'EasyDebrid',
		'setting_id': 'ed.token',
		'param_names': ['easydebrid', 'ed', 'ED'],
		'enabled_setting': 'ed.enabled'
	},
	'debridlink': {
		'name': 'Debrid-Link',
		'setting_id': None,  # Not configured in POV
		'param_names': ['debridlink', 'dl', 'DL'],
		'enabled_setting': None
	}
}


def extract_base_url_and_config(url):
	"""
	Extract the base addon URL and any existing configuration from a URL.
	Handles URLs like:
	- https://torrentio.strem.fun/manifest.json
	- https://torrentio.strem.fun/sort=qualitysize|realdebrid=xxx/manifest.json
	- https://torrentio.strem.fun/realdebrid=xxx
	Returns: (base_url, config_string or None, has_debrid_config)
	"""
	from urllib.parse import urlparse, unquote

	url = url.strip().rstrip('/')
	if not url.startswith(('http://', 'https://')):
		url = 'https://' + url

	# Remove manifest.json suffix
	if url.endswith('/manifest.json'):
		url = url[:-14]

	parsed = urlparse(url)
	path_parts = parsed.path.strip('/').split('/')

	# Check if path contains configuration (look for = signs which indicate config params)
	config_string = None
	base_path_parts = []
	has_debrid_config = False

	# Known debrid parameter patterns
	debrid_patterns = [
		'realdebrid=', 'rd=', 'debridkey=',
		'premiumize=', 'pm=',
		'alldebrid=', 'ad=',
		'torbox=', 'tb=',
		'offcloud=', 'oc=',
		'debrid-link=', 'dl=',
		'easydebrid=', 'ed='
	]

	for part in path_parts:
		# Decode URL-encoded parts
		decoded_part = unquote(part)
		# Check if this part looks like configuration (contains = sign)
		if '=' in decoded_part:
			config_string = decoded_part
			# Check for debrid config
			part_lower = decoded_part.lower()
			if any(pattern in part_lower for pattern in debrid_patterns):
				has_debrid_config = True
		else:
			base_path_parts.append(part)

	# Reconstruct base URL
	base_path = '/'.join(base_path_parts)
	if base_path:
		base_url = f"{parsed.scheme}://{parsed.netloc}/{base_path}"
	else:
		base_url = f"{parsed.scheme}://{parsed.netloc}"

	return base_url, config_string, has_debrid_config


def get_stremio_addons():
	"""Get list of configured Stremio addons"""
	try:
		import ast
		addons_str = get_setting('stremio.addons', '')
		if addons_str:
			addons = ast.literal_eval(addons_str)
			return addons if isinstance(addons, list) else []
	except:
		pass
	return []


def save_stremio_addons(addons):
	"""Save Stremio addons list to settings"""
	set_setting('stremio.addons', repr(addons))


def get_enabled_debrid_services():
	"""Get list of debrid services that are enabled and have API keys"""
	enabled = []
	for service_id, service in DEBRID_SERVICES.items():
		if service['setting_id'] and service['enabled_setting']:
			token = get_setting(service['setting_id'], '')
			is_enabled = get_setting(service['enabled_setting'], 'false') == 'true'
			if token and is_enabled:
				enabled.append({
					'id': service_id,
					'name': service['name'],
					'token': token,
					'param_names': service['param_names']
				})
	return enabled


def validate_stremio_addon(url, return_config_info=False):
	"""
	Validate a Stremio addon URL by fetching its manifest.
	If return_config_info=True, also returns extracted config information.
	"""
	try:
		# Extract base URL and any existing configuration
		base_url, existing_config, has_debrid_config = extract_base_url_and_config(url)

		# If URL had config, use the full URL for manifest fetch
		if existing_config:
			manifest_url = f"{base_url}/{existing_config}/manifest.json"
		else:
			manifest_url = f"{base_url}/manifest.json"

		response, error = _fetch_url(manifest_url, timeout=10)

		if response is None or response.status_code != 200:
			error_msg = error or ("HTTP %d" % response.status_code if response else "Connection failed")
			return None, "Failed to fetch manifest: %s" % error_msg

		# Check for HTML response (Cloudflare)
		content_type = response.headers.get('content-type', '')
		if 'text/html' in content_type:
			return None, "Blocked by Cloudflare - addon needs debrid configuration"

		manifest = response.json()

		# Validate required fields
		if not manifest.get('id'):
			return None, "Invalid manifest: missing 'id'"
		if not manifest.get('name'):
			return None, "Invalid manifest: missing 'name'"
		if not manifest.get('resources'):
			return None, "Invalid manifest: missing 'resources'"

		# Check if addon supports streams
		resources = manifest.get('resources', [])
		supports_stream = False
		supports_catalog = False
		supports_subtitles = False

		for res in resources:
			res_name = res if isinstance(res, str) else res.get('name', '')
			if res_name == 'stream':
				supports_stream = True
			elif res_name == 'catalog':
				supports_catalog = True
			elif res_name == 'subtitles':
				supports_subtitles = True

		if not supports_stream:
			return None, "Addon does not provide stream resources"

		# Check supported types
		types = manifest.get('types', [])
		has_movie_or_series = 'movie' in types or 'series' in types
		if not has_movie_or_series:
			return None, "Addon does not support movies or series"

		# Check if addon has a configure page
		behavior_hints = manifest.get('behaviorHints', {})
		configurable = behavior_hints.get('configurable', False)

		addon_info = {
			'url': base_url,
			'name': manifest.get('name', 'Unknown'),
			'id': manifest.get('id', ''),
			'version': manifest.get('version', '1.0.0'),
			'description': manifest.get('description', ''),
			'types': types,
			'has_movies': 'movie' in types,
			'has_series': 'series' in types,
			'supports_catalog': supports_catalog,
			'supports_subtitles': supports_subtitles,
			'configurable': configurable,
			'config_url': ''  # Will be set during configuration
		}

		# If the URL already had configuration, preserve it
		if existing_config:
			addon_info['config_url'] = f"{base_url}/{existing_config}"
			addon_info['existing_config'] = existing_config
			if has_debrid_config:
				addon_info['has_debrid_config'] = True

		if return_config_info:
			return addon_info, None, existing_config, has_debrid_config
		return addon_info, None

	except requests.exceptions.Timeout:
		return None, "Connection timed out"
	except requests.exceptions.ConnectionError:
		return None, "Could not connect to server"
	except json.JSONDecodeError:
		return None, "Invalid JSON response"
	except Exception as e:
		return None, str(e)


def build_addon_config_url(base_url, debrid_service=None, custom_config=None, existing_config=None):
	"""
	Build a configuration URL for an addon with debrid settings.
	Stremio addons typically use the format: /{config}/manifest.json
	where config can contain multiple params separated by | or %7C
	"""
	from urllib.parse import quote

	config_parts = []

	# Preserve existing config parts if provided (but remove old debrid configs)
	if existing_config:
		# Split by | or %7C
		existing_parts = existing_config.replace('%7C', '|').split('|')
		debrid_patterns = [
			'realdebrid=', 'rd=', 'debridkey=',
			'premiumize=', 'pm=',
			'alldebrid=', 'ad=',
			'torbox=', 'tb=',
			'offcloud=', 'oc=',
			'debrid-link=', 'dl=',
			'easydebrid=', 'ed='
		]
		for part in existing_parts:
			# Skip old debrid config parts if we're adding new debrid config
			if debrid_service:
				part_lower = part.lower()
				if any(pattern in part_lower for pattern in debrid_patterns):
					continue
			config_parts.append(part)

	# Add debrid configuration if provided
	if debrid_service:
		param_name = debrid_service['param_names'][0]
		token = debrid_service['token']
		config_parts.append(f"{param_name}={token}")

	# Add custom configuration parts
	if custom_config:
		for key, value in custom_config.items():
			config_parts.append(f"{key}={value}")

	if config_parts:
		# Join with | and URL-encode the whole config string
		config_string = '|'.join(config_parts)
		# Most Stremio addons use /{config}/manifest.json format
		return f"{base_url.rstrip('/')}/{config_string}"

	return base_url


def stremio_addon_manager():
	"""Main Stremio addon manager dialog"""
	addons = get_stremio_addons()

	while True:
		# Build menu items
		items = []
		items.append({'line1': '[B]+ Add New Stremio Addon[/B]', 'line2': 'Add an addon by URL'})
		items.append({'line1': '[B]+ Add Popular Addon[/B]', 'line2': 'Choose from popular addons'})

		for addon in addons:
			name = addon.get('name', 'Unknown')
			url = addon.get('url', '')
			has_debrid = bool(addon.get('config_url', ''))
			types = []
			if addon.get('has_movies', True):
				types.append('Movies')
			if addon.get('has_series', True):
				types.append('Series')
			type_str = ', '.join(types) if types else 'Unknown'
			debrid_str = ' [COLOR green][Debrid][/COLOR]' if has_debrid else ''
			items.append({
				'line1': f'[B]{name}[/B]{debrid_str}',
				'line2': f'{type_str} | {url}'
			})

		if addons:
			items.append({'line1': '[B]- Remove All Addons[/B]', 'line2': 'Clear all configured Stremio addons'})

		# Create selection list
		labels = ['+ Add New Stremio Addon', '+ Add Popular Addon'] + [a.get('name', 'Unknown') for a in addons]
		if addons:
			labels.append('- Remove All Addons')

		kwargs = {
			'items': json.dumps(items),
			'heading': 'Stremio Addon Manager',
			'multi_line': 'true'
		}

		selection = select_dialog(list(range(len(labels))), **kwargs)

		if selection is None:
			break

		if selection == 0:
			# Add new addon
			add_stremio_addon()
			addons = get_stremio_addons()
		elif selection == 1:
			# Add popular addon
			add_popular_addon()
			addons = get_stremio_addons()
		elif addons and selection == len(addons) + 2:
			# Remove all
			if confirm_dialog(text='Remove all Stremio addons?'):
				save_stremio_addons([])
				notification('All Stremio addons removed', 2000)
				addons = []
		elif selection > 1 and selection <= len(addons) + 1:
			# Edit/remove specific addon
			addon_idx = selection - 2
			manage_single_addon(addon_idx)
			addons = get_stremio_addons()


def add_stremio_addon():
	"""Add a new Stremio addon"""
	# Get URL from user
	url = dialog.input('Enter Stremio Addon URL', type=0)
	if not url:
		return

	notification('Validating addon...', 2000)

	# Validate the addon with config info
	result = validate_stremio_addon(url, return_config_info=True)
	if len(result) == 4:
		addon_info, error, existing_config, has_debrid_config = result
	else:
		addon_info, error = result
		existing_config, has_debrid_config = None, False

	if error:
		ok_dialog(heading='Error', text=f'Failed to add addon:\n{error}')
		return

	# Check if addon already exists
	addons = get_stremio_addons()
	existing_idx = None
	for idx, existing in enumerate(addons):
		if existing.get('id') == addon_info.get('id') or existing.get('url') == addon_info.get('url'):
			existing_idx = idx
			break

	if existing_idx is not None:
		# Offer to update instead of just showing error
		existing_addon = addons[existing_idx]
		update_msg = f"'{addon_info['name']}' is already configured."
		if has_debrid_config or existing_config:
			update_msg += "\n\nThe URL you entered contains configuration. Update the addon with this configuration?"
		else:
			update_msg += "\n\nWould you like to update or reconfigure it?"

		if confirm_dialog(heading='Addon Exists', text=update_msg):
			# Update the existing addon
			addon_info['config_url'] = existing_addon.get('config_url', '')
			addon_info['debrid_service'] = existing_addon.get('debrid_service', '')

			# If new URL had config, use it
			if existing_config:
				addon_info['config_url'] = f"{addon_info['url']}/{existing_config}"
				if has_debrid_config:
					addon_info['has_debrid_config'] = True

			addons[existing_idx] = addon_info
			save_stremio_addons(addons)
			notification(f"Updated: {addon_info['name']}", 2000)
		return

	# If URL already had debrid config, inform user
	if has_debrid_config:
		notification(f"Detected existing debrid configuration", 2000)
	# Ask if user wants to configure debrid (only if not already configured)
	elif not existing_config:
		enabled_debrids = get_enabled_debrid_services()
		if enabled_debrids:
			if confirm_dialog(heading='Debrid Configuration', text='Would you like to configure this addon with your debrid service?'):
				addon_info = configure_addon_debrid(addon_info, enabled_debrids)

	# Show addon info and confirm
	debrid_status = '[COLOR green]Configured[/COLOR]' if addon_info.get('config_url') else '[COLOR gray]Not configured[/COLOR]'
	info_text = (
		f"[B]Name:[/B] {addon_info['name']}\n"
		f"[B]Version:[/B] {addon_info['version']}\n"
		f"[B]ID:[/B] {addon_info['id']}\n"
		f"[B]Supports:[/B] {'Movies' if addon_info['has_movies'] else ''}"
		f"{', ' if addon_info['has_movies'] and addon_info['has_series'] else ''}"
		f"{'Series' if addon_info['has_series'] else ''}\n"
		f"[B]Debrid:[/B] {debrid_status}\n"
		f"[B]Description:[/B] {addon_info.get('description', 'N/A')[:100]}"
	)

	if not confirm_dialog(heading='Add Stremio Addon?', text=info_text):
		return

	# Add to list
	addons.append(addon_info)
	save_stremio_addons(addons)
	notification(f"Added: {addon_info['name']}", 2000)


def configure_addon_debrid(addon_info, enabled_debrids):
	"""Configure an addon with debrid settings"""
	# Let user select which debrid service to use
	items = [
		{'line1': f"[B]{d['name']}[/B]", 'line2': 'Use this debrid service'}
		for d in enabled_debrids
	]
	items.append({'line1': '[B]Skip[/B]', 'line2': 'Do not configure debrid'})

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Select Debrid Service',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(items))), **kwargs)

	if selection is None or selection == len(enabled_debrids):
		return addon_info

	selected_debrid = enabled_debrids[selection]

	# Preserve existing non-debrid config if any
	existing_config = addon_info.get('existing_config', '')
	if not existing_config and addon_info.get('config_url'):
		# Extract config from existing config_url
		_, existing_config, _ = extract_base_url_and_config(addon_info['config_url'])

	# Build config URL preserving existing config
	config_url = build_addon_config_url(addon_info['url'], selected_debrid, existing_config=existing_config)
	addon_info['config_url'] = config_url
	addon_info['debrid_service'] = selected_debrid['id']
	addon_info['has_debrid_config'] = True

	notification(f"Configured with {selected_debrid['name']}", 2000)
	return addon_info


def manage_single_addon(addon_idx):
	"""Manage a single Stremio addon (edit/remove/configure)"""
	addons = get_stremio_addons()
	if addon_idx >= len(addons):
		return

	addon = addons[addon_idx]
	has_debrid = bool(addon.get('config_url', ''))

	items = [
		{'line1': '[B]Test Connection[/B]', 'line2': 'Verify addon is working'},
		{'line1': '[B]View Details[/B]', 'line2': 'Show addon information'},
		{'line1': '[B]Configure Debrid[/B]', 'line2': 'Set up or change debrid configuration'},
		{'line1': '[B]Enter Config URL[/B]', 'line2': 'Manually enter a configuration URL'},
		{'line1': '[B]Remove Addon[/B]', 'line2': 'Delete this addon'}
	]

	if has_debrid:
		items.insert(3, {'line1': '[B]Remove Debrid Config[/B]', 'line2': 'Clear debrid configuration'})

	kwargs = {
		'items': json.dumps(items),
		'heading': addon.get('name', 'Unknown'),
		'multi_line': 'true'
	}

	num_items = len(items)
	selection = select_dialog(list(range(num_items)), **kwargs)

	if selection == 0:
		# Test connection
		test_stremio_addon(addon)
	elif selection == 1:
		# View details
		view_addon_details(addon)
	elif selection == 2:
		# Configure debrid
		enabled_debrids = get_enabled_debrid_services()
		if enabled_debrids:
			updated_addon = configure_addon_debrid(addon.copy(), enabled_debrids)
			if updated_addon.get('config_url') != addon.get('config_url'):
				addons[addon_idx] = updated_addon
				save_stremio_addons(addons)
				notification('Debrid configuration updated', 2000)
		else:
			ok_dialog(heading='No Debrid Services', text='No debrid services are configured in POV settings.')
	elif has_debrid and selection == 3:
		# Remove debrid config
		if confirm_dialog(text='Remove debrid configuration?'):
			addon['config_url'] = ''
			addon.pop('debrid_service', None)
			addons[addon_idx] = addon
			save_stremio_addons(addons)
			notification('Debrid configuration removed', 2000)
	elif (has_debrid and selection == 4) or (not has_debrid and selection == 3):
		# Enter config URL manually
		enter_config_url(addon_idx)
	elif (has_debrid and selection == 5) or (not has_debrid and selection == 4):
		# Remove addon
		if confirm_dialog(text=f"Remove '{addon.get('name', 'Unknown')}'?"):
			addons.pop(addon_idx)
			save_stremio_addons(addons)
			notification('Addon removed', 2000)


def enter_config_url(addon_idx):
	"""Manually enter a configuration URL for an addon"""
	addons = get_stremio_addons()
	if addon_idx >= len(addons):
		return

	addon = addons[addon_idx]

	# Show current config URL if any
	current = addon.get('config_url', '')
	default_text = current if current else addon.get('url', '')

	url = dialog.input('Enter Configuration URL', defaultt=default_text, type=0)
	if not url:
		return

	# Validate the config URL
	notification('Validating configuration...', 2000)

	try:
		# Extract base URL and config from the entered URL
		extracted_base, existing_config, has_debrid = extract_base_url_and_config(url)

		# Build manifest URL
		if existing_config:
			manifest_url = f"{extracted_base}/{existing_config}/manifest.json"
			config_url = f"{extracted_base}/{existing_config}"
		else:
			manifest_url = f"{extracted_base}/manifest.json"
			config_url = extracted_base

		# Use _fetch_url for Cloudflare bypass
		response, error = _fetch_url(manifest_url, timeout=10)

		if response is not None and response.status_code == 200:
			content_type = response.headers.get('content-type', '')
			if 'text/html' not in content_type:
				addon['config_url'] = config_url
				if has_debrid:
					addon['has_debrid_config'] = True
				addons[addon_idx] = addon
				save_stremio_addons(addons)
				notification('Configuration URL saved', 2000)
			else:
				ok_dialog(heading='Error', text='Blocked by Cloudflare - configuration may still work')
				# Still save it since it might work with proper headers during playback
				addon['config_url'] = config_url
				addons[addon_idx] = addon
				save_stremio_addons(addons)
		else:
			error_msg = error or ('HTTP %d' % response.status_code if response else 'Connection failed')
			ok_dialog(heading='Error', text=f'Failed to validate URL:\n{error_msg}')
	except Exception as e:
		ok_dialog(heading='Error', text=f'Failed to validate URL:\n{str(e)}')


def test_stremio_addon(addon):
	"""Test a Stremio addon connection"""
	notification('Testing addon...', 2000)

	# Test both base URL and config URL if available
	test_url = addon.get('config_url', '') or addon.get('url', '')
	addon_info, error = validate_stremio_addon(test_url)

	if error:
		ok_dialog(heading='Connection Failed', text=error)
	else:
		config_status = 'with debrid config' if addon.get('config_url') else 'base URL'
		ok_dialog(heading='Connection Successful', text=f"'{addon_info['name']}' is working correctly ({config_status})")


def view_addon_details(addon):
	"""View detailed information about an addon"""
	debrid_status = 'Configured' if addon.get('config_url') else 'Not configured'
	debrid_service = addon.get('debrid_service', 'None')

	text = (
		f"[B]Name:[/B] {addon.get('name', 'Unknown')}\n"
		f"[B]ID:[/B] {addon.get('id', 'N/A')}\n"
		f"[B]Version:[/B] {addon.get('version', 'N/A')}\n"
		f"[B]URL:[/B] {addon.get('url', 'N/A')}\n"
		f"[B]Movies:[/B] {'Yes' if addon.get('has_movies', True) else 'No'}\n"
		f"[B]Series:[/B] {'Yes' if addon.get('has_series', True) else 'No'}\n"
		f"[B]Catalogs:[/B] {'Yes' if addon.get('supports_catalog', False) else 'No'}\n"
		f"[B]Subtitles:[/B] {'Yes' if addon.get('supports_subtitles', False) else 'No'}\n"
		f"[B]Debrid:[/B] {debrid_status}\n"
		f"[B]Debrid Service:[/B] {debrid_service.capitalize() if debrid_service != 'None' else 'None'}\n"
		f"[B]Description:[/B] {addon.get('description', 'N/A')}"
	)
	ok_dialog(heading='Addon Details', text=text)


def get_popular_stremio_addons():
	"""Return a list of popular/known Stremio addons with their features"""
	return [
		{
			'name': 'Torrentio',
			'url': 'https://torrentio.strem.fun',
			'description': 'Search torrent indexers for movies and shows. Supports debrid services.',
			'configurable': True,
			'debrid_support': True
		},
		{
			'name': 'Comet',
			'url': 'https://comet.elfhosted.com',
			'description': 'Fast debrid-focused addon with quality filtering.',
			'configurable': True,
			'debrid_support': True
		},
		{
			'name': 'MediaFusion',
			'url': 'https://mediafusion.elfhosted.com',
			'description': 'All-in-one addon with torrent and non-torrent sources.',
			'configurable': True,
			'debrid_support': True
		},
		{
			'name': 'AIOStreams',
			'url': 'https://aiostreams.stremio.ru',
			'description': 'Consolidates multiple addons with debrid and proxy support.',
			'configurable': True,
			'debrid_support': True
		},
		{
			'name': 'Annatar',
			'url': 'https://annatar.elfhosted.com',
			'description': 'Fast search addon using multiple indexers.',
			'configurable': True,
			'debrid_support': True
		},
		{
			'name': 'Cinemeta',
			'url': 'https://v3-cinemeta.strem.io',
			'description': 'Official Stremio metadata addon (no streams).',
			'configurable': False,
			'debrid_support': False
		},
		{
			'name': 'OpenSubtitles',
			'url': 'https://opensubtitles-v3.strem.io',
			'description': 'Subtitle addon from OpenSubtitles database.',
			'configurable': False,
			'debrid_support': False
		}
	]


def add_popular_addon():
	"""Show dialog to add a popular/known addon"""
	popular = get_popular_stremio_addons()

	items = []
	for addon in popular:
		debrid_tag = ' [COLOR green][Debrid][/COLOR]' if addon['debrid_support'] else ''
		items.append({
			'line1': f"[B]{addon['name']}[/B]{debrid_tag}",
			'line2': addon['description']
		})

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Popular Stremio Addons',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(popular))), **kwargs)

	if selection is None:
		return

	selected = popular[selection]
	notification('Validating addon...', 2000)

	addon_info, error = validate_stremio_addon(selected['url'])

	if error:
		ok_dialog(heading='Error', text=f'Failed to add addon:\n{error}')
		return

	# Check if already exists (check both id and url for consistency)
	addons = get_stremio_addons()
	existing_idx = None
	for idx, existing in enumerate(addons):
		if existing.get('id') == addon_info.get('id') or existing.get('url') == addon_info.get('url'):
			existing_idx = idx
			break

	if existing_idx is not None:
		# Offer to reconfigure instead of just showing error
		if confirm_dialog(heading='Addon Exists',
						  text=f"'{addon_info['name']}' is already configured.\nWould you like to reconfigure it?"):
			# Keep existing config if any
			existing_addon = addons[existing_idx]
			addon_info['config_url'] = existing_addon.get('config_url', '')
			addon_info['debrid_service'] = existing_addon.get('debrid_service', '')

			# Offer to reconfigure debrid
			if selected['debrid_support']:
				enabled_debrids = get_enabled_debrid_services()
				if enabled_debrids:
					if confirm_dialog(heading='Debrid Configuration',
									  text='Would you like to reconfigure debrid settings?'):
						addon_info = configure_addon_debrid(addon_info, enabled_debrids)

			addons[existing_idx] = addon_info
			save_stremio_addons(addons)
			notification(f"Updated: {addon_info['name']}", 2000)
		return

	# If addon supports debrid, ask to configure
	if selected['debrid_support']:
		enabled_debrids = get_enabled_debrid_services()
		if enabled_debrids:
			if confirm_dialog(heading='Debrid Configuration',
							  text=f"{selected['name']} supports debrid services.\nWould you like to configure it now?"):
				addon_info = configure_addon_debrid(addon_info, enabled_debrids)

	# Add to list
	addons.append(addon_info)
	save_stremio_addons(addons)
	notification(f"Added: {addon_info['name']}", 2000)


def reconfigure_all_addons_debrid():
	"""Reconfigure all addons with a debrid service"""
	addons = get_stremio_addons()
	if not addons:
		notification('No addons configured', 2000)
		return

	enabled_debrids = get_enabled_debrid_services()
	if not enabled_debrids:
		ok_dialog(heading='No Debrid Services', text='No debrid services are configured in POV settings.')
		return

	# Select debrid service
	items = [
		{'line1': f"[B]{d['name']}[/B]", 'line2': 'Apply to all addons'}
		for d in enabled_debrids
	]

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Select Debrid Service',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(items))), **kwargs)
	if selection is None:
		return

	selected_debrid = enabled_debrids[selection]
	updated_count = 0

	for addon in addons:
		# Only update addons that support debrid (have configurable URLs)
		if addon.get('url'):
			# Preserve existing non-debrid config if any
			existing_config = addon.get('existing_config', '')
			if not existing_config and addon.get('config_url'):
				_, existing_config, _ = extract_base_url_and_config(addon['config_url'])

			config_url = build_addon_config_url(addon['url'], selected_debrid, existing_config=existing_config)
			addon['config_url'] = config_url
			addon['debrid_service'] = selected_debrid['id']
			addon['has_debrid_config'] = True
			updated_count += 1

	save_stremio_addons(addons)
	notification(f"Updated {updated_count} addons with {selected_debrid['name']}", 2000)
