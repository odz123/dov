# Stremio Catalog Indexer for POV
"""
	Browse content catalogs from Stremio addons
	Features:
	- List available catalogs from addons
	- Browse catalog contents (movies, series)
	- Integration with POV metadata system
"""

import json
import requests
from threading import Thread
from modules.kodi_utils import (
	get_setting, notification, make_listitem, add_items,
	set_content, end_directory, set_view_mode, build_url
)


class StremioIndexer:
	"""Indexer for browsing Stremio addon catalogs"""

	def __init__(self, params=None):
		self.params = params or {}
		self.params_get = self.params.get
		self.items = []

	def run(self):
		"""Main entry point - routes to appropriate handler"""
		mode = self.params_get('stremio_mode', 'list_addons')

		if mode == 'list_addons':
			self.list_addons_with_catalogs()
		elif mode == 'list_catalogs':
			self.list_addon_catalogs()
		elif mode == 'browse_catalog':
			self.browse_catalog()
		elif mode == 'view_meta':
			self.view_meta()

	def get_stremio_addons(self):
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

	def fetch_manifest(self, addon_url):
		"""Fetch addon manifest"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				manifest_url = base_url
			else:
				manifest_url = f"{base_url}/manifest.json"

			response = requests.get(
				manifest_url,
				timeout=10,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)

			if response.status_code == 200:
				return response.json()
		except:
			pass
		return None

	def list_addons_with_catalogs(self):
		"""List all addons that have catalog support"""
		addons = self.get_stremio_addons()
		items = []

		for addon in addons:
			if addon.get('supports_catalog', False):
				addon_url = addon.get('config_url', '') or addon.get('url', '')
				items.append({
					'name': addon.get('name', 'Unknown'),
					'url': addon_url,
					'description': addon.get('description', ''),
					'mode': 'stremio_catalog',
					'stremio_mode': 'list_catalogs',
					'addon_url': addon_url
				})

		if not items:
			notification('No addons with catalog support found', 2000)
			return

		self._build_addon_list(items)

	def _build_addon_list(self, items):
		"""Build Kodi list of addons"""
		from modules.kodi_utils import build_url, make_listitem, add_items

		listitems = []
		for item in items:
			listitem = make_listitem()
			listitem.setLabel(item['name'])
			listitem.setInfo('video', {'title': item['name'], 'plot': item.get('description', '')})

			url = build_url({
				'mode': item['mode'],
				'stremio_mode': item['stremio_mode'],
				'addon_url': item['addon_url']
			})

			listitems.append((url, listitem, True))

		add_items(None, listitems)
		set_content(None, 'files')
		end_directory(None)

	def list_addon_catalogs(self):
		"""List available catalogs from a specific addon"""
		addon_url = self.params_get('addon_url', '')
		if not addon_url:
			notification('No addon URL provided', 2000)
			return

		manifest = self.fetch_manifest(addon_url)
		if not manifest:
			notification('Failed to fetch addon manifest', 2000)
			return

		catalogs = manifest.get('catalogs', [])
		if not catalogs:
			notification('No catalogs available', 2000)
			return

		items = []
		for catalog in catalogs:
			catalog_type = catalog.get('type', '')
			catalog_id = catalog.get('id', '')
			catalog_name = catalog.get('name', catalog_id)

			# Filter to movie and series only
			if catalog_type not in ('movie', 'series'):
				continue

			items.append({
				'name': f"{catalog_name} ({catalog_type.capitalize()})",
				'catalog_type': catalog_type,
				'catalog_id': catalog_id,
				'addon_url': addon_url,
				'mode': 'stremio_catalog',
				'stremio_mode': 'browse_catalog'
			})

		if not items:
			notification('No movie/series catalogs found', 2000)
			return

		self._build_catalog_list(items)

	def _build_catalog_list(self, items):
		"""Build Kodi list of catalogs"""
		from modules.kodi_utils import build_url, make_listitem, add_items

		listitems = []
		for item in items:
			listitem = make_listitem()
			listitem.setLabel(item['name'])
			listitem.setInfo('video', {'title': item['name']})

			url = build_url({
				'mode': item['mode'],
				'stremio_mode': item['stremio_mode'],
				'addon_url': item['addon_url'],
				'catalog_type': item['catalog_type'],
				'catalog_id': item['catalog_id']
			})

			listitems.append((url, listitem, True))

		add_items(None, listitems)
		set_content(None, 'files')
		end_directory(None)

	def browse_catalog(self):
		"""Browse contents of a catalog"""
		addon_url = self.params_get('addon_url', '')
		catalog_type = self.params_get('catalog_type', 'movie')
		catalog_id = self.params_get('catalog_id', '')
		skip = int(self.params_get('skip', '0'))

		if not addon_url or not catalog_id:
			notification('Missing catalog parameters', 2000)
			return

		# Fetch catalog contents
		metas = self.fetch_catalog(addon_url, catalog_type, catalog_id, skip)

		if not metas:
			notification('No items found', 2000)
			return

		self._build_meta_list(metas, addon_url, catalog_type, catalog_id, skip)

	def fetch_catalog(self, addon_url, catalog_type, catalog_id, skip=0):
		"""Fetch catalog contents from addon"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			# Build catalog endpoint with skip for pagination
			if skip > 0:
				endpoint = f"{base_url}/catalog/{catalog_type}/{catalog_id}/skip={skip}.json"
			else:
				endpoint = f"{base_url}/catalog/{catalog_type}/{catalog_id}.json"

			response = requests.get(
				endpoint,
				timeout=15,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)

			if response.status_code == 200:
				data = response.json()
				return data.get('metas', [])
		except:
			pass
		return []

	def _build_meta_list(self, metas, addon_url, catalog_type, catalog_id, current_skip):
		"""Build Kodi list of meta items"""
		from modules.kodi_utils import build_url, make_listitem, add_items

		listitems = []

		for meta in metas:
			listitem = make_listitem()

			name = meta.get('name', 'Unknown')
			year = meta.get('year', '')
			imdb_id = meta.get('imdb_id', '') or meta.get('id', '')

			# Extract IMDb ID from id if needed
			if imdb_id.startswith('tt'):
				pass
			elif ':' in imdb_id:
				imdb_id = imdb_id.split(':')[0]

			# Set label
			if year:
				listitem.setLabel(f"{name} ({year})")
			else:
				listitem.setLabel(name)

			# Set info
			info_dict = {
				'title': name,
				'year': int(year) if year and year.isdigit() else 0,
				'plot': meta.get('description', ''),
				'genre': ', '.join(meta.get('genres', [])) if meta.get('genres') else '',
				'imdbnumber': imdb_id
			}
			listitem.setInfo('video', info_dict)

			# Set art
			poster = meta.get('poster', '')
			background = meta.get('background', '') or meta.get('fanart', '')
			logo = meta.get('logo', '')

			art_dict = {}
			if poster:
				art_dict['poster'] = poster
				art_dict['thumb'] = poster
			if background:
				art_dict['fanart'] = background
			if logo:
				art_dict['clearlogo'] = logo

			if art_dict:
				listitem.setArt(art_dict)

			# Determine action based on type
			if catalog_type == 'movie':
				# Link to POV's movie play/info
				if imdb_id.startswith('tt'):
					url = build_url({
						'mode': 'extras_menu_choice',
						'media_type': 'movie',
						'imdb_id': imdb_id,
						'name': name
					})
				else:
					url = build_url({
						'mode': 'stremio_catalog',
						'stremio_mode': 'view_meta',
						'addon_url': addon_url,
						'meta_type': catalog_type,
						'meta_id': meta.get('id', '')
					})
			else:
				# Series - link to POV's show info
				if imdb_id.startswith('tt'):
					url = build_url({
						'mode': 'extras_menu_choice',
						'media_type': 'tvshow',
						'imdb_id': imdb_id,
						'name': name
					})
				else:
					url = build_url({
						'mode': 'stremio_catalog',
						'stremio_mode': 'view_meta',
						'addon_url': addon_url,
						'meta_type': catalog_type,
						'meta_id': meta.get('id', '')
					})

			listitems.append((url, listitem, catalog_type == 'series'))

		# Add "Next Page" item if we got a full page
		if len(metas) >= 20:  # Assuming 20 items per page
			next_skip = current_skip + len(metas)
			listitem = make_listitem()
			listitem.setLabel('[B]Next Page >>>[/B]')
			listitem.setInfo('video', {'title': 'Next Page'})

			url = build_url({
				'mode': 'stremio_catalog',
				'stremio_mode': 'browse_catalog',
				'addon_url': addon_url,
				'catalog_type': catalog_type,
				'catalog_id': catalog_id,
				'skip': str(next_skip)
			})

			listitems.append((url, listitem, True))

		add_items(None, listitems)
		set_content(None, 'movies' if catalog_type == 'movie' else 'tvshows')
		end_directory(None)
		set_view_mode('view.movies' if catalog_type == 'movie' else 'view.tvshows', 'movies' if catalog_type == 'movie' else 'tvshows')

	def view_meta(self):
		"""View detailed metadata for an item"""
		addon_url = self.params_get('addon_url', '')
		meta_type = self.params_get('meta_type', 'movie')
		meta_id = self.params_get('meta_id', '')

		if not addon_url or not meta_id:
			notification('Missing meta parameters', 2000)
			return

		# Fetch meta details
		meta = self.fetch_meta(addon_url, meta_type, meta_id)

		if not meta:
			notification('Failed to fetch metadata', 2000)
			return

		# Display meta info
		self._show_meta_dialog(meta, meta_type)

	def fetch_meta(self, addon_url, meta_type, meta_id):
		"""Fetch detailed metadata for an item"""
		try:
			base_url = addon_url.rstrip('/')
			if base_url.endswith('/manifest.json'):
				base_url = base_url[:-14]

			endpoint = f"{base_url}/meta/{meta_type}/{meta_id}.json"

			response = requests.get(
				endpoint,
				timeout=10,
				headers={'User-Agent': 'POV-Kodi/1.0'}
			)

			if response.status_code == 200:
				data = response.json()
				return data.get('meta', {})
		except:
			pass
		return None

	def _show_meta_dialog(self, meta, meta_type):
		"""Show metadata in a dialog"""
		from modules.kodi_utils import ok_dialog

		name = meta.get('name', 'Unknown')
		year = meta.get('year', 'N/A')
		description = meta.get('description', 'No description available')
		genres = ', '.join(meta.get('genres', [])) if meta.get('genres') else 'N/A'
		runtime = meta.get('runtime', 'N/A')
		imdb_id = meta.get('imdb_id', '') or meta.get('id', '')

		text = (
			f"[B]Title:[/B] {name}\n"
			f"[B]Year:[/B] {year}\n"
			f"[B]Genres:[/B] {genres}\n"
			f"[B]Runtime:[/B] {runtime}\n"
			f"[B]ID:[/B] {imdb_id}\n\n"
			f"[B]Description:[/B]\n{description[:500]}"
		)

		ok_dialog(heading=name, text=text)


def stremio_catalog_menu():
	"""Entry point for Stremio catalog navigation menu"""
	from modules.kodi_utils import build_url, make_listitem, add_items, set_content, end_directory

	items = [
		('Browse Stremio Catalogs', 'stremio_catalog', {'stremio_mode': 'list_addons'}),
	]

	listitems = []
	for label, mode, extra_params in items:
		listitem = make_listitem()
		listitem.setLabel(label)
		listitem.setInfo('video', {'title': label})

		params = {'mode': mode}
		params.update(extra_params)
		url = build_url(params)

		listitems.append((url, listitem, True))

	add_items(None, listitems)
	set_content(None, 'files')
	end_directory(None)
