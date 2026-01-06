# Stremio Addon Manager for POV
"""
	Enhanced manager for adding, removing, and configuring Stremio addons
	Features:
	- Debrid service configuration
	- Addon configuration URLs (for addons like Torrentio)
	- Popular addon presets
	- Connection testing
"""

import json
import requests
from modules.kodi_utils import (
	notification, ok_dialog, confirm_dialog, select_dialog,
	get_setting, set_setting, dialog, local_string
)


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


def validate_stremio_addon(url):
	"""Validate a Stremio addon URL by fetching its manifest"""
	try:
		# Clean up URL
		base_url = url.rstrip('/')
		if not base_url.startswith(('http://', 'https://')):
			base_url = 'https://' + base_url

		# Check if URL ends with manifest.json, if so use as-is
		if base_url.endswith('/manifest.json'):
			manifest_url = base_url
			base_url = base_url[:-14]
		else:
			manifest_url = f"{base_url}/manifest.json"

		response = requests.get(
			manifest_url,
			timeout=10,
			headers={'User-Agent': 'POV-Kodi/1.0'}
		)

		if response.status_code != 200:
			return None, "Failed to fetch manifest (HTTP %d)" % response.status_code

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
		config_url = behavior_hints.get('configurationRequired', '')

		return {
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
		}, None

	except requests.exceptions.Timeout:
		return None, "Connection timed out"
	except requests.exceptions.ConnectionError:
		return None, "Could not connect to server"
	except json.JSONDecodeError:
		return None, "Invalid JSON response"
	except Exception as e:
		return None, str(e)


def build_addon_config_url(base_url, debrid_service=None, custom_config=None):
	"""Build a configuration URL for an addon with debrid settings"""
	config_parts = []

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

	# Validate the addon
	addon_info, error = validate_stremio_addon(url)

	if error:
		ok_dialog(heading='Error', text=f'Failed to add addon:\n{error}')
		return

	# Check if addon already exists
	addons = get_stremio_addons()
	for existing in addons:
		if existing.get('id') == addon_info.get('id') or existing.get('url') == addon_info.get('url'):
			ok_dialog(heading='Error', text='This addon is already configured')
			return

	# Ask if user wants to configure debrid
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

	# Build config URL
	config_url = build_addon_config_url(addon_info['url'], selected_debrid)
	addon_info['config_url'] = config_url
	addon_info['debrid_service'] = selected_debrid['id']

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
		# Try to fetch manifest from config URL
		base_url = url.rstrip('/')
		if not base_url.startswith(('http://', 'https://')):
			base_url = 'https://' + base_url

		if not base_url.endswith('/manifest.json'):
			manifest_url = f"{base_url}/manifest.json"
		else:
			manifest_url = base_url
			base_url = base_url[:-14]

		response = requests.get(
			manifest_url,
			timeout=10,
			headers={'User-Agent': 'POV-Kodi/1.0'}
		)

		if response.status_code == 200:
			addon['config_url'] = base_url
			addons[addon_idx] = addon
			save_stremio_addons(addons)
			notification('Configuration URL saved', 2000)
		else:
			ok_dialog(heading='Error', text=f'Failed to validate URL (HTTP {response.status_code})')
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

	# Check if already exists
	addons = get_stremio_addons()
	for existing in addons:
		if existing.get('id') == addon_info.get('id'):
			ok_dialog(heading='Error', text='This addon is already configured')
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
			config_url = build_addon_config_url(addon['url'], selected_debrid)
			addon['config_url'] = config_url
			addon['debrid_service'] = selected_debrid['id']
			updated_count += 1

	save_stremio_addons(addons)
	notification(f"Updated {updated_count} addons with {selected_debrid['name']}", 2000)
