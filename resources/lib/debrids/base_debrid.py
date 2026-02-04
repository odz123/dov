"""
Base class for debrid service UI operations.
Provides common functionality for cloud browsing, downloads display, and context menus.
"""
import sys
from modules import kodi_utils
from modules.source_utils import supported_video_extensions
from modules.utils import clean_file_name, normalize

ls, build_url, make_listitem = kodi_utils.local_string, kodi_utils.build_url, kodi_utils.make_listitem
folder_str, file_str, delete_str, down_str = ls(32742).upper(), ls(32743).upper(), ls(32785), ls(32747)
fanart = kodi_utils.get_addoninfo('fanart')
extensions = supported_video_extensions()
KODI_VERSION = kodi_utils.get_kodi_version()

def get_default_art(icon):
	"""Get default art dict for a debrid service icon."""
	return {'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon}

def format_size_gb(size_bytes):
	"""Convert bytes to GB with 2 decimal places."""
	return float(int(size_bytes)) / 1073741824

def create_folder_listitem(count, name, url_params, delete_params, default_art, cm_extra=None):
	"""Create a listitem for a folder entry with common formatting.

	Args:
		count: Item number for display
		name: Folder name to display
		url_params: URL parameters for the folder action
		delete_params: URL parameters for the delete action
		default_art: Art dict for the listitem
		cm_extra: Optional list of extra context menu items as (label, action) tuples

	Returns:
		Tuple of (url, listitem, is_folder)
	"""
	try:
		cm = []
		cm_append = cm.append
		display = '%02d | [B]%s[/B] | [I]%s [/I]' % (count, folder_str, clean_file_name(normalize(name)).upper())
		cm_append(('[B]%s %s[/B]' % (delete_str, folder_str.capitalize()), 'RunPlugin(%s)' % build_url(delete_params)))
		if cm_extra:
			for item in cm_extra:
				cm_append(item)
		url = build_url(url_params)
		listitem = make_listitem()
		listitem.setLabel(display)
		listitem.addContextMenuItems(cm)
		listitem.setArt(default_art)
		return (url, listitem, True)
	except Exception as e:
		kodi_utils.logger('base_debrid.create_folder_listitem', str(e))
		return None

def create_file_listitem(count, name, size_bytes, url_params, default_art, cm_items=None, datetime_str=None):
	"""Create a listitem for a file entry with common formatting.

	Args:
		count: Item number for display
		name: File name to display
		size_bytes: File size in bytes
		url_params: URL parameters for the file action
		default_art: Art dict for the listitem
		cm_items: Optional list of context menu items as (label, action) tuples
		datetime_str: Optional datetime string to include in display

	Returns:
		Tuple of (url, listitem, is_folder)
	"""
	try:
		cm = []
		name = clean_file_name(name).upper()
		size = format_size_gb(size_bytes)
		if datetime_str:
			display = '%02d | %.2f GB | %s | [I]%s [/I]' % (count, size, datetime_str, name)
		else:
			display = '%02d | [B]%s[/B] | %.2f GB | [I]%s [/I]' % (count, file_str, size, name)
		if cm_items:
			for item in cm_items:
				cm.append(item)
		url = build_url(url_params)
		listitem = make_listitem()
		listitem.setLabel(display)
		listitem.addContextMenuItems(cm)
		listitem.setArt(default_art)
		listitem.setInfo('video', {}) if KODI_VERSION < 20 else listitem.getVideoInfoTag()
		return (url, listitem, False)
	except Exception as e:
		kodi_utils.logger('base_debrid.create_file_listitem', str(e))
		return None

def is_video_file(filename):
	"""Check if filename has a supported video extension."""
	return filename.lower().endswith(tuple(extensions))

def finalize_directory(handle):
	"""Finalize directory with common settings for debrid cloud views."""
	kodi_utils.set_content(handle, 'files')
	kodi_utils.end_directory(handle)
	kodi_utils.set_view_mode('view.premium')

def confirm_and_delete(delete_func, *args, **kwargs):
	"""Prompt for confirmation and execute delete operation.

	Args:
		delete_func: Function to call for deletion
		*args, **kwargs: Arguments to pass to delete_func

	Returns:
		Result of delete operation or None if cancelled/failed
	"""
	if not kodi_utils.confirm_dialog():
		return None
	result = delete_func(*args, **kwargs)
	if not result:
		return kodi_utils.notification(32574)
	return result

def post_delete_refresh(clear_cache_func):
	"""Execute common post-delete actions."""
	clear_cache_func()
	kodi_utils.container_refresh()
