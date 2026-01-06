# Stremio Addon Manager for POV
"""
	Manager for adding, removing, and configuring Stremio addons
"""

import json
import requests
from modules.kodi_utils import (
	notification, ok_dialog, confirm_dialog, select_dialog,
	get_setting, set_setting, dialog, local_string
)


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
		for res in resources:
			if isinstance(res, str) and res == 'stream':
				supports_stream = True
				break
			elif isinstance(res, dict) and res.get('name') == 'stream':
				supports_stream = True
				break

		if not supports_stream:
			return None, "Addon does not provide stream resources"

		# Check supported types
		types = manifest.get('types', [])
		has_movie_or_series = 'movie' in types or 'series' in types
		if not has_movie_or_series:
			return None, "Addon does not support movies or series"

		return {
			'url': base_url,
			'name': manifest.get('name', 'Unknown'),
			'id': manifest.get('id', ''),
			'version': manifest.get('version', '1.0.0'),
			'description': manifest.get('description', ''),
			'types': types,
			'has_movies': 'movie' in types,
			'has_series': 'series' in types
		}, None

	except requests.exceptions.Timeout:
		return None, "Connection timed out"
	except requests.exceptions.ConnectionError:
		return None, "Could not connect to server"
	except json.JSONDecodeError:
		return None, "Invalid JSON response"
	except Exception as e:
		return None, str(e)


def stremio_addon_manager():
	"""Main Stremio addon manager dialog"""
	addons = get_stremio_addons()

	while True:
		# Build menu items
		items = []
		items.append({'line1': '[B]+ Add New Stremio Addon[/B]', 'line2': 'Add an addon by URL'})

		for addon in addons:
			name = addon.get('name', 'Unknown')
			url = addon.get('url', '')
			types = []
			if addon.get('has_movies', True):
				types.append('Movies')
			if addon.get('has_series', True):
				types.append('Series')
			type_str = ', '.join(types) if types else 'Unknown'
			items.append({
				'line1': f'[B]{name}[/B]',
				'line2': f'{type_str} | {url}'
			})

		if addons:
			items.append({'line1': '[B]- Remove All Addons[/B]', 'line2': 'Clear all configured Stremio addons'})

		# Create selection list
		labels = ['+ Add New Stremio Addon'] + [a.get('name', 'Unknown') for a in addons]
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
		elif addons and selection == len(addons) + 1:
			# Remove all
			if confirm_dialog(text='Remove all Stremio addons?'):
				save_stremio_addons([])
				notification('All Stremio addons removed', 2000)
				addons = []
		elif selection > 0 and selection <= len(addons):
			# Edit/remove specific addon
			addon_idx = selection - 1
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

	# Show addon info and confirm
	info_text = (
		f"[B]Name:[/B] {addon_info['name']}\n"
		f"[B]Version:[/B] {addon_info['version']}\n"
		f"[B]ID:[/B] {addon_info['id']}\n"
		f"[B]Supports:[/B] {'Movies' if addon_info['has_movies'] else ''}"
		f"{', ' if addon_info['has_movies'] and addon_info['has_series'] else ''}"
		f"{'Series' if addon_info['has_series'] else ''}\n"
		f"[B]Description:[/B] {addon_info.get('description', 'N/A')[:100]}"
	)

	if not confirm_dialog(heading='Add Stremio Addon?', text=info_text):
		return

	# Add to list
	addons.append(addon_info)
	save_stremio_addons(addons)
	notification(f"Added: {addon_info['name']}", 2000)


def manage_single_addon(addon_idx):
	"""Manage a single Stremio addon (edit/remove)"""
	addons = get_stremio_addons()
	if addon_idx >= len(addons):
		return

	addon = addons[addon_idx]

	items = [
		{'line1': '[B]Test Connection[/B]', 'line2': 'Verify addon is working'},
		{'line1': '[B]View Details[/B]', 'line2': 'Show addon information'},
		{'line1': '[B]Remove Addon[/B]', 'line2': 'Delete this addon'}
	]

	kwargs = {
		'items': json.dumps(items),
		'heading': addon.get('name', 'Unknown'),
		'multi_line': 'true'
	}

	selection = select_dialog([0, 1, 2], **kwargs)

	if selection == 0:
		# Test connection
		test_stremio_addon(addon)
	elif selection == 1:
		# View details
		view_addon_details(addon)
	elif selection == 2:
		# Remove addon
		if confirm_dialog(text=f"Remove '{addon.get('name', 'Unknown')}'?"):
			addons.pop(addon_idx)
			save_stremio_addons(addons)
			notification('Addon removed', 2000)


def test_stremio_addon(addon):
	"""Test a Stremio addon connection"""
	notification('Testing addon...', 2000)

	addon_info, error = validate_stremio_addon(addon.get('url', ''))

	if error:
		ok_dialog(heading='Connection Failed', text=error)
	else:
		ok_dialog(heading='Connection Successful', text=f"'{addon_info['name']}' is working correctly")


def view_addon_details(addon):
	"""View detailed information about an addon"""
	text = (
		f"[B]Name:[/B] {addon.get('name', 'Unknown')}\n"
		f"[B]ID:[/B] {addon.get('id', 'N/A')}\n"
		f"[B]Version:[/B] {addon.get('version', 'N/A')}\n"
		f"[B]URL:[/B] {addon.get('url', 'N/A')}\n"
		f"[B]Movies:[/B] {'Yes' if addon.get('has_movies', True) else 'No'}\n"
		f"[B]Series:[/B] {'Yes' if addon.get('has_series', True) else 'No'}\n"
		f"[B]Description:[/B] {addon.get('description', 'N/A')}"
	)
	ok_dialog(heading='Addon Details', text=text)


def get_popular_stremio_addons():
	"""Return a list of popular/known Stremio addons"""
	return [
		{
			'name': 'Torrentio',
			'url': 'https://torrentio.strem.fun',
			'description': 'Search torrent indexers for movies and shows'
		},
		{
			'name': 'The Movie Database Addon',
			'url': 'https://94c8cb9f702d-tmdb-addon.baby-beamup.club',
			'description': 'Movie and TV show info from TMDB'
		},
		{
			'name': 'Cinemeta',
			'url': 'https://v3-cinemeta.strem.io',
			'description': 'Official Stremio metadata addon'
		}
	]


def add_popular_addon():
	"""Show dialog to add a popular/known addon"""
	popular = get_popular_stremio_addons()

	items = [
		{'line1': f"[B]{a['name']}[/B]", 'line2': a['description']}
		for a in popular
	]

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Popular Stremio Addons',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(popular))), **kwargs)

	if selection is not None:
		addon = popular[selection]
		notification('Validating addon...', 2000)

		addon_info, error = validate_stremio_addon(addon['url'])

		if error:
			ok_dialog(heading='Error', text=f'Failed to add addon:\n{error}')
			return

		addons = get_stremio_addons()
		for existing in addons:
			if existing.get('id') == addon_info.get('id'):
				ok_dialog(heading='Error', text='This addon is already configured')
				return

		addons.append(addon_info)
		save_stremio_addons(addons)
		notification(f"Added: {addon_info['name']}", 2000)
