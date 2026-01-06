# Stremio Subtitles Integration for POV
"""
	Fetches and integrates subtitles from Stremio addons
	Features:
	- Fetch subtitles from /subtitles endpoint
	- Language filtering and selection
	- Download and cache subtitles
	- Integration with POV player
"""

import os
import re
import requests
from modules.kodi_utils import (
	get_setting, set_property, get_property, clear_property,
	translate_path, notification, select_dialog
)
import json


# Language code mapping (ISO 639-1 to full name)
LANGUAGE_CODES = {
	'en': 'English', 'eng': 'English',
	'es': 'Spanish', 'spa': 'Spanish',
	'fr': 'French', 'fra': 'French', 'fre': 'French',
	'de': 'German', 'deu': 'German', 'ger': 'German',
	'it': 'Italian', 'ita': 'Italian',
	'pt': 'Portuguese', 'por': 'Portuguese',
	'ru': 'Russian', 'rus': 'Russian',
	'ja': 'Japanese', 'jpn': 'Japanese',
	'ko': 'Korean', 'kor': 'Korean',
	'zh': 'Chinese', 'chi': 'Chinese', 'zho': 'Chinese',
	'ar': 'Arabic', 'ara': 'Arabic',
	'hi': 'Hindi', 'hin': 'Hindi',
	'pl': 'Polish', 'pol': 'Polish',
	'nl': 'Dutch', 'nld': 'Dutch', 'dut': 'Dutch',
	'tr': 'Turkish', 'tur': 'Turkish',
	'sv': 'Swedish', 'swe': 'Swedish',
	'da': 'Danish', 'dan': 'Danish',
	'no': 'Norwegian', 'nor': 'Norwegian',
	'fi': 'Finnish', 'fin': 'Finnish',
	'cs': 'Czech', 'ces': 'Czech', 'cze': 'Czech',
	'el': 'Greek', 'ell': 'Greek', 'gre': 'Greek',
	'he': 'Hebrew', 'heb': 'Hebrew',
	'hu': 'Hungarian', 'hun': 'Hungarian',
	'id': 'Indonesian', 'ind': 'Indonesian',
	'ro': 'Romanian', 'ron': 'Romanian', 'rum': 'Romanian',
	'th': 'Thai', 'tha': 'Thai',
	'vi': 'Vietnamese', 'vie': 'Vietnamese',
	'bg': 'Bulgarian', 'bul': 'Bulgarian',
	'hr': 'Croatian', 'hrv': 'Croatian',
	'sk': 'Slovak', 'slk': 'Slovak', 'slo': 'Slovak',
	'sl': 'Slovenian', 'slv': 'Slovenian',
	'uk': 'Ukrainian', 'ukr': 'Ukrainian',
	'ms': 'Malay', 'msa': 'Malay', 'may': 'Malay',
	'ta': 'Tamil', 'tam': 'Tamil',
	'te': 'Telugu', 'tel': 'Telugu'
}


def get_language_name(code):
	"""Convert language code to full name"""
	if not code:
		return 'Unknown'
	code_lower = code.lower()
	return LANGUAGE_CODES.get(code_lower, code.capitalize())


def get_stremio_addons_with_subtitles():
	"""Get list of configured Stremio addons that support subtitles"""
	try:
		import ast
		addons_str = get_setting('stremio.addons', '')
		if addons_str:
			addons = ast.literal_eval(addons_str)
			return [a for a in addons if a.get('supports_subtitles', False)]
	except:
		pass
	return []


def fetch_subtitles_from_addon(addon_url, media_type, media_id, video_hash=None, video_size=None):
	"""
	Fetch subtitles from a Stremio addon

	Args:
		addon_url: Base URL of the addon
		media_type: 'movie' or 'series'
		media_id: IMDB ID (or imdb:season:episode for series)
		video_hash: OpenSubtitles video hash (optional)
		video_size: Video file size in bytes (optional)

	Returns:
		List of subtitle objects
	"""
	subtitles = []
	try:
		base_url = addon_url.rstrip('/')
		if base_url.endswith('/manifest.json'):
			base_url = base_url[:-14]

		# Build subtitle endpoint with optional extra args
		if video_hash and video_size:
			endpoint = f"{base_url}/subtitles/{media_type}/{media_id}/{video_hash}:{video_size}.json"
		else:
			endpoint = f"{base_url}/subtitles/{media_type}/{media_id}.json"

		response = requests.get(
			endpoint,
			timeout=8,
			headers={'User-Agent': 'POV-Kodi/1.0'}
		)

		if response.status_code == 200:
			data = response.json()
			subtitles = data.get('subtitles', [])
	except:
		pass

	return subtitles


def fetch_all_stremio_subtitles(imdb_id, media_type='movie', season=None, episode=None, video_hash=None, video_size=None):
	"""
	Fetch subtitles from all configured Stremio addons that support subtitles

	Args:
		imdb_id: IMDB ID (e.g., 'tt1234567')
		media_type: 'movie' or 'episode'
		season: Season number (for episodes)
		episode: Episode number (for episodes)
		video_hash: OpenSubtitles video hash (optional)
		video_size: Video file size in bytes (optional)

	Returns:
		List of subtitle objects with addon source info
	"""
	all_subtitles = []
	addons = get_stremio_addons_with_subtitles()

	# Also check for hardcoded OpenSubtitles addon
	opensubtitles_url = 'https://opensubtitles-v3.strem.io'
	if not any(a.get('url', '').startswith('https://opensubtitles') for a in addons):
		addons.append({'url': opensubtitles_url, 'name': 'OpenSubtitles'})

	# Build media ID
	if media_type == 'episode' and season and episode:
		stremio_type = 'series'
		media_id = f"{imdb_id}:{season}:{episode}"
	else:
		stremio_type = 'movie'
		media_id = imdb_id

	for addon in addons:
		try:
			addon_url = addon.get('config_url', '') or addon.get('url', '')
			addon_name = addon.get('name', 'Unknown')

			subtitles = fetch_subtitles_from_addon(
				addon_url, stremio_type, media_id, video_hash, video_size
			)

			for sub in subtitles:
				sub['addon'] = addon_name
				all_subtitles.append(sub)
		except:
			continue

	return all_subtitles


def filter_subtitles_by_language(subtitles, preferred_language=None):
	"""
	Filter and sort subtitles by language preference

	Args:
		subtitles: List of subtitle objects
		preferred_language: Preferred language (e.g., 'English')

	Returns:
		Filtered and sorted list of subtitles
	"""
	if not preferred_language:
		preferred_language = get_setting('subtitles.language', 'English')

	preferred_lower = preferred_language.lower()
	preferred_subs = []
	other_subs = []

	for sub in subtitles:
		lang = sub.get('lang', '') or sub.get('SubLanguageID', '') or ''
		lang_name = get_language_name(lang)

		# Add language name to subtitle object
		sub['language_name'] = lang_name

		if lang_name.lower() == preferred_lower or lang.lower() == preferred_lower[:2]:
			preferred_subs.append(sub)
		else:
			other_subs.append(sub)

	# Sort by rating if available
	preferred_subs.sort(key=lambda x: float(x.get('SubRating', 0) or 0), reverse=True)
	other_subs.sort(key=lambda x: float(x.get('SubRating', 0) or 0), reverse=True)

	return preferred_subs + other_subs


def download_subtitle(subtitle_url, filename=None):
	"""
	Download a subtitle file and save to cache

	Args:
		subtitle_url: URL of the subtitle file
		filename: Optional filename to save as

	Returns:
		Path to downloaded subtitle file, or None on failure
	"""
	try:
		# Create subtitle cache directory
		cache_dir = translate_path('special://profile/addon_data/plugin.video.pov/subtitles/')
		if not os.path.exists(cache_dir):
			os.makedirs(cache_dir)

		# Determine filename
		if not filename:
			# Extract filename from URL or generate one
			url_parts = subtitle_url.split('/')
			filename = url_parts[-1] if url_parts else 'subtitle.srt'
			# Ensure it has an extension
			if not any(filename.endswith(ext) for ext in ['.srt', '.sub', '.ass', '.ssa', '.vtt']):
				filename += '.srt'

		# Clean filename
		filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
		filepath = os.path.join(cache_dir, filename)

		# Download subtitle
		response = requests.get(
			subtitle_url,
			timeout=15,
			headers={'User-Agent': 'POV-Kodi/1.0'}
		)

		if response.status_code == 200:
			# Handle gzip encoding if present
			content = response.content

			# Check if content is gzipped
			if content[:2] == b'\x1f\x8b':
				import gzip
				content = gzip.decompress(content)

			# Write to file
			with open(filepath, 'wb') as f:
				f.write(content)

			return filepath
	except Exception as e:
		pass

	return None


def select_subtitle_dialog(subtitles):
	"""
	Show a dialog for the user to select a subtitle

	Args:
		subtitles: List of subtitle objects

	Returns:
		Selected subtitle object, or None if cancelled
	"""
	if not subtitles:
		notification('No subtitles found', 2000)
		return None

	items = []
	for sub in subtitles:
		lang = sub.get('language_name', get_language_name(sub.get('lang', '')))
		addon = sub.get('addon', 'Unknown')
		rating = sub.get('SubRating', '')
		rating_str = f" ⭐{rating}" if rating else ''

		# Get subtitle label
		label = sub.get('SubFileName', '') or sub.get('id', '') or 'Unknown'
		if len(label) > 50:
			label = label[:47] + '...'

		items.append({
			'line1': f"[B]{lang}[/B]{rating_str}",
			'line2': f"{label} ({addon})"
		})

	kwargs = {
		'items': json.dumps(items),
		'heading': 'Select Subtitle',
		'multi_line': 'true'
	}

	selection = select_dialog(list(range(len(items))), **kwargs)

	if selection is not None:
		return subtitles[selection]
	return None


def get_subtitle_for_source(source_item, imdb_id, media_type='movie', season=None, episode=None):
	"""
	Get subtitles for a source item (integrates with source selection)

	Args:
		source_item: Source dictionary from scraper
		imdb_id: IMDB ID
		media_type: 'movie' or 'episode'
		season: Season number
		episode: Episode number

	Returns:
		Path to downloaded subtitle, or None
	"""
	# Check if source has embedded Stremio subtitles
	stremio_subs = source_item.get('stremio_subtitles', [])

	if stremio_subs:
		# Use embedded subtitles from stream
		preferred_language = get_setting('subtitles.language', 'English')
		filtered_subs = filter_subtitles_by_language(stremio_subs, preferred_language)

		if filtered_subs:
			sub_action = get_setting('subtitles.subs_action', '2')
			if sub_action == '0':  # Auto
				sub = filtered_subs[0]  # Use first (best match)
			elif sub_action == '1':  # Select
				sub = select_subtitle_dialog(filtered_subs)
				if not sub:
					return None
			else:  # Off
				return None

			# Download the subtitle
			sub_url = sub.get('url', '')
			if sub_url:
				return download_subtitle(sub_url)

	return None


def fetch_and_set_subtitle(imdb_id, media_type='movie', season=None, episode=None, auto_select=True):
	"""
	Fetch subtitles and optionally set for playback

	Args:
		imdb_id: IMDB ID
		media_type: 'movie' or 'episode'
		season: Season number
		episode: Episode number
		auto_select: If True, auto-select best match; otherwise show dialog

	Returns:
		Path to downloaded subtitle, or None
	"""
	# Fetch from all subtitle-supporting addons
	subtitles = fetch_all_stremio_subtitles(
		imdb_id, media_type, season, episode
	)

	if not subtitles:
		return None

	# Filter by preferred language
	preferred_language = get_setting('subtitles.language', 'English')
	filtered_subs = filter_subtitles_by_language(subtitles, preferred_language)

	if not filtered_subs:
		return None

	if auto_select:
		# Use first (best match)
		selected = filtered_subs[0]
	else:
		# Show selection dialog
		selected = select_subtitle_dialog(filtered_subs)
		if not selected:
			return None

	# Download the subtitle
	sub_url = selected.get('url', '')
	if sub_url:
		filepath = download_subtitle(sub_url)
		if filepath:
			notification(f"Subtitle: {selected.get('language_name', 'Unknown')}", 2000)
			return filepath

	return None


def clear_subtitle_cache():
	"""Clear the subtitle cache directory"""
	try:
		cache_dir = translate_path('special://profile/addon_data/plugin.video.pov/subtitles/')
		if os.path.exists(cache_dir):
			import shutil
			for filename in os.listdir(cache_dir):
				filepath = os.path.join(cache_dir, filename)
				try:
					if os.path.isfile(filepath):
						os.unlink(filepath)
				except:
					pass
		notification('Subtitle cache cleared', 2000)
	except Exception as e:
		notification('Failed to clear subtitle cache', 2000)


def get_stremio_subtitle_for_player(meta):
	"""
	Called from player to get Stremio subtitle

	Args:
		meta: Playback metadata dictionary

	Returns:
		Path to subtitle file, or None
	"""
	try:
		# Check if Stremio subtitles are enabled
		if get_setting('stremio.subtitles', 'true') != 'true':
			return None

		# Get media info
		imdb_id = meta.get('imdb_id', '')
		if not imdb_id:
			return None

		media_type = meta.get('media_type', 'movie')
		season = meta.get('season')
		episode = meta.get('episode')

		# Determine auto-select based on settings
		sub_action = get_setting('subtitles.subs_action', '2')
		auto_select = sub_action == '0'  # Auto mode

		if sub_action == '2':  # Off
			return None

		return fetch_and_set_subtitle(
			imdb_id,
			media_type,
			season,
			episode,
			auto_select
		)
	except:
		return None
