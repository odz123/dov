# Stremio Addon Manager for POV
"""
	Enhanced manager for adding, removing, and configuring Stremio addons
	Features:
	- Debrid service configuration
	- Addon configuration URLs (for addons like Torrentio)
	- Popular addon presets
	- Connection testing
	- HTTP client via shared http_client module
"""

import json
from modules.kodi_utils import (
	notification, ok_dialog, confirm_dialog, select_dialog,
	get_setting, set_setting, dialog, local_string
)
from modules import http_client


def _fetch_url(url, timeout=10):
	"""Fetch a URL via shared http_client.
	Returns (response, error_message) tuple."""
	return http_client.fetch_raw(url, timeout=timeout)


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
	except Exception:
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

		# Check for HTML response (not JSON)
		content_type = response.headers.get('content-type', '')
		if 'text/html' in content_type:
			return None, "Received HTML instead of JSON - addon may need configuration"

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
		supports_meta = False

		for res in resources:
			res_name = res if isinstance(res, str) else res.get('name', '')
			if res_name == 'stream':
				supports_stream = True
			elif res_name == 'catalog':
				supports_catalog = True
			elif res_name == 'subtitles':
				supports_subtitles = True
			elif res_name == 'meta':
				supports_meta = True

		if not supports_stream and not supports_catalog and not supports_subtitles and not supports_meta:
			return None, "Addon does not provide any recognized resources (stream, catalog, meta, subtitles)"

		# Check supported types - accept all Stremio content types
		types = manifest.get('types', [])
		supported_types = ('movie', 'series', 'anime', 'tv', 'channel', 'other')
		has_supported_type = any(t in types for t in supported_types)
		if not has_supported_type:
			return None, "Addon does not support any recognized media types"

		# Check if addon has a configure page and behaviorHints
		behavior_hints = manifest.get('behaviorHints', {}) or {}
		configurable = behavior_hints.get('configurable', False)
		configuration_required = behavior_hints.get('configurationRequired', False)
		is_adult = behavior_hints.get('adult', False)
		is_p2p = behavior_hints.get('p2p', False)

		# Extract per-resource type and idPrefixes filtering from manifest resource objects
		# Resources can be objects with per-resource type/idPrefix filtering
		stream_types = []
		catalog_types = []
		meta_types = []
		subtitles_types = []
		stream_id_prefixes = []
		meta_id_prefixes = []
		subtitles_id_prefixes = []
		supports_addon_catalog = False

		for res in resources:
			if isinstance(res, dict):
				res_name = res.get('name', '')
				res_types = res.get('types', [])
				res_id_prefixes = res.get('idPrefixes', [])
				if res_name == 'stream':
					if res_types: stream_types = res_types
					if res_id_prefixes: stream_id_prefixes = res_id_prefixes
				elif res_name == 'catalog':
					if res_types: catalog_types = res_types
				elif res_name == 'meta':
					if res_types: meta_types = res_types
					if res_id_prefixes: meta_id_prefixes = res_id_prefixes
				elif res_name == 'subtitles':
					if res_types: subtitles_types = res_types
					if res_id_prefixes: subtitles_id_prefixes = res_id_prefixes
				elif res_name == 'addon_catalog':
					supports_addon_catalog = True
			elif isinstance(res, str) and res == 'addon_catalog':
				supports_addon_catalog = True

		addon_info = {
			'url': base_url,
			'name': manifest.get('name', 'Unknown'),
			'id': manifest.get('id', ''),
			'version': manifest.get('version', '1.0.0'),
			'description': manifest.get('description', ''),
			'types': types,
			'has_movies': any(t in types for t in ('movie', 'anime', 'other')),
			'has_series': any(t in types for t in ('series', 'anime', 'tv', 'channel', 'other')),
			'supports_catalog': supports_catalog,
			'supports_subtitles': supports_subtitles,
			'supports_meta': supports_meta,
			'supports_addon_catalog': supports_addon_catalog,
			'configurable': configurable,
			'configuration_required': configuration_required,
			'is_adult': is_adult,
			'is_p2p': is_p2p,
			'config_url': ''  # Will be set during configuration
		}

		# Store per-resource type filtering if available
		if stream_types:
			addon_info['stream_types'] = stream_types
		if catalog_types:
			addon_info['catalog_types'] = catalog_types
		if meta_types:
			addon_info['meta_types'] = meta_types
		if subtitles_types:
			addon_info['subtitles_types'] = subtitles_types

		# Extract idPrefixes for filtering - manifest-level and per-resource
		id_prefixes = manifest.get('idPrefixes', [])
		if id_prefixes:
			addon_info['id_prefixes'] = id_prefixes
		if stream_id_prefixes:
			addon_info['stream_id_prefixes'] = stream_id_prefixes
		if meta_id_prefixes:
			addon_info['meta_id_prefixes'] = meta_id_prefixes
		if subtitles_id_prefixes:
			addon_info['subtitles_id_prefixes'] = subtitles_id_prefixes

		# If the URL already had configuration, preserve it
		if existing_config:
			addon_info['config_url'] = f"{base_url}/{existing_config}"
			addon_info['existing_config'] = existing_config
			if has_debrid_config:
				addon_info['has_debrid_config'] = True

		if return_config_info:
			return addon_info, None, existing_config, has_debrid_config
		return addon_info, None

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
			'multi_line': 'true',
			'show_buttons': 'true'
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

	# If addon requires configuration and none was provided, warn user
	if addon_info.get('configuration_required') and not existing_config and not addon_info.get('config_url'):
		ok_dialog(heading='Configuration Required',
				  text=f"'{addon_info['name']}' requires configuration before use.\n"
					   f"Please visit the addon's website to configure it, then enter the configuration URL.")
		enter_url = dialog.input('Enter configured addon URL (or cancel to add without config)', type=0)
		if enter_url:
			# Re-validate with the configured URL
			result = validate_stremio_addon(enter_url, return_config_info=True)
			if len(result) == 4:
				new_addon_info, new_error, new_config, new_has_debrid = result
				if new_addon_info and not new_error:
					addon_info = new_addon_info
					existing_config = new_config
					has_debrid_config = new_has_debrid

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
	content_types = ', '.join(addon_info.get('types', []))
	resources_list = []
	if addon_info.get('supports_catalog'): resources_list.append('Catalog')
	if addon_info.get('supports_meta'): resources_list.append('Meta')
	if addon_info.get('supports_subtitles'): resources_list.append('Subtitles')
	resources_str = ', '.join(resources_list) if resources_list else 'Streams only'
	flags = []
	if addon_info.get('is_p2p'): flags.append('P2P')
	if addon_info.get('is_adult'): flags.append('Adult')
	if addon_info.get('configuration_required'): flags.append('Config Required')
	flags_str = ' | '.join(flags) if flags else ''
	info_text = (
		f"[B]Name:[/B] {addon_info['name']}\n"
		f"[B]Version:[/B] {addon_info['version']}\n"
		f"[B]ID:[/B] {addon_info['id']}\n"
		f"[B]Types:[/B] {content_types}\n"
		f"[B]Resources:[/B] {resources_str}\n"
		f"[B]Debrid:[/B] {debrid_status}\n"
	)
	if flags_str:
		info_text += f"[B]Flags:[/B] {flags_str}\n"
	info_text += f"[B]Description:[/B] {addon_info.get('description', 'N/A')[:100]}"

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
				ok_dialog(heading='Error', text='Received HTML instead of JSON - configuration may still work')
				# Still save it since it might work during playback
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
	content_types = ', '.join(addon.get('types', [])) or 'N/A'
	resources = []
	if addon.get('supports_catalog'): resources.append('Catalog')
	if addon.get('supports_meta'): resources.append('Meta')
	if addon.get('supports_subtitles'): resources.append('Subtitles')
	if addon.get('supports_addon_catalog'): resources.append('Addon Catalog')
	resources_str = ', '.join(resources) if resources else 'Streams only'
	flags = []
	if addon.get('is_p2p'): flags.append('P2P')
	if addon.get('is_adult'): flags.append('Adult')
	if addon.get('configuration_required'): flags.append('Config Required')
	flags_str = ' | '.join(flags) if flags else 'None'

	# Show per-resource type filtering info
	id_prefixes = ', '.join(addon.get('id_prefixes', [])) or 'All'
	stream_types = ', '.join(addon.get('stream_types', [])) or 'All'

	text = (
		f"[B]Name:[/B] {addon.get('name', 'Unknown')}\n"
		f"[B]ID:[/B] {addon.get('id', 'N/A')}\n"
		f"[B]Version:[/B] {addon.get('version', 'N/A')}\n"
		f"[B]URL:[/B] {addon.get('url', 'N/A')}\n"
		f"[B]Content Types:[/B] {content_types}\n"
		f"[B]Resources:[/B] {resources_str}\n"
		f"[B]ID Prefixes:[/B] {id_prefixes}\n"
		f"[B]Stream Types:[/B] {stream_types}\n"
		f"[B]Catalogs:[/B] {'Yes' if addon.get('supports_catalog', False) else 'No'}\n"
		f"[B]Subtitles:[/B] {'Yes' if addon.get('supports_subtitles', False) else 'No'}\n"
		f"[B]Flags:[/B] {flags_str}\n"
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


def stremio_debug_loop():
	"""Debug Stremio addons by testing connectivity 10 times in a loop.
	Shows detailed results for each attempt including timing and status."""
	import time
	addons = get_stremio_addons()
	if not addons:
		notification('No addons configured', 2000)
		return

	# Let user select which addon to debug, or all
	items = [{'line1': '[B]All Addons[/B]', 'line2': 'Test all configured addons'}]
	for addon in addons:
		name = addon.get('name', 'Unknown')
		url = addon.get('url', '')
		items.append({'line1': f'[B]{name}[/B]', 'line2': url})

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Debug Stremio - Select Addon',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(items))), **kwargs)
	if selection is None:
		return

	if selection == 0:
		test_addons = addons
	else:
		test_addons = [addons[selection - 1]]

	loop_count = 10
	results = []

	notification(f'Running {loop_count} debug loops...', 3000)

	for addon in test_addons:
		addon_name = addon.get('name', 'Unknown')
		addon_url = addon.get('config_url', '') or addon.get('url', '')
		addon_results = {
			'name': addon_name,
			'url': addon_url,
			'attempts': [],
			'success_count': 0,
			'fail_count': 0,
			'total_time': 0.0
		}

		for i in range(loop_count):
			attempt = {'iteration': i + 1, 'status': 'unknown', 'time': 0.0, 'error': '', 'http_code': 0}
			start_time = time.time()

			try:
				response, error = _fetch_url(addon_url.rstrip('/') + '/manifest.json', timeout=10)
				elapsed = time.time() - start_time
				attempt['time'] = round(elapsed, 3)

				if response is not None and response.status_code == 200:
					content_type = response.headers.get('content-type', '')
					if 'text/html' not in content_type:
						try:
							manifest = response.json()
							if manifest.get('id') and manifest.get('name'):
								attempt['status'] = 'success'
								attempt['http_code'] = 200
								addon_results['success_count'] += 1
							else:
								attempt['status'] = 'fail'
								attempt['http_code'] = 200
								attempt['error'] = 'Invalid manifest'
								addon_results['fail_count'] += 1
						except Exception:
							attempt['status'] = 'fail'
							attempt['http_code'] = 200
							attempt['error'] = 'Invalid JSON'
							addon_results['fail_count'] += 1
					else:
						attempt['status'] = 'fail'
						attempt['http_code'] = response.status_code
						attempt['error'] = 'HTML response (not JSON)'
						addon_results['fail_count'] += 1
				else:
					attempt['status'] = 'fail'
					attempt['http_code'] = response.status_code if response else 0
					attempt['error'] = error or 'No response'
					addon_results['fail_count'] += 1
			except Exception as e:
				elapsed = time.time() - start_time
				attempt['time'] = round(elapsed, 3)
				attempt['status'] = 'fail'
				attempt['error'] = str(e)[:60]
				addon_results['fail_count'] += 1

			addon_results['total_time'] += attempt['time']
			addon_results['attempts'].append(attempt)

		results.append(addon_results)

	# Build summary text
	summary_lines = []
	for r in results:
		avg_time = round(r['total_time'] / loop_count, 3) if loop_count else 0
		summary_lines.append(f"[B]{r['name']}[/B]")
		summary_lines.append(f"  URL: {r['url']}")
		summary_lines.append(f"  Success: {r['success_count']}/{loop_count} | Avg: {avg_time}s | Total: {round(r['total_time'], 3)}s")

		# Show each attempt
		for a in r['attempts']:
			if a['status'] == 'success':
				summary_lines.append(f"  #{a['iteration']}: [COLOR green]OK[/COLOR] ({a['time']}s)")
			else:
				summary_lines.append(f"  #{a['iteration']}: [COLOR red]FAIL[/COLOR] ({a['time']}s) - {a['error']}")

		# Show failure summary if any failures
		if r['fail_count'] > 0:
			errors = {}
			for a in r['attempts']:
				if a['status'] == 'fail' and a['error']:
					errors[a['error']] = errors.get(a['error'], 0) + 1
			if errors:
				summary_lines.append(f"  Errors: {', '.join(f'{e} x{c}' for e, c in errors.items())}")
		summary_lines.append('')

	summary_text = '\n'.join(summary_lines)

	# Log to Kodi log
	try:
		from modules.kodi_utils import logger
		logger('STREMIO DEBUG LOOP', summary_text.replace('[B]', '').replace('[/B]', '').replace('[COLOR green]', '').replace('[/COLOR]', '').replace('[COLOR red]', ''))
	except Exception:
		pass

	# Show results dialog
	ok_dialog(heading=f'Stremio Debug - {loop_count} Loops', text=summary_text)


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
