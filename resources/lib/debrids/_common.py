"""Common utilities for debrid cloud browser modules.
Reduces code duplication across debrid Indexer classes."""

import sys
from modules import kodi_utils

build_url, make_listitem = kodi_utils.build_url, kodi_utils.make_listitem
ls = kodi_utils.local_string
KODI_VERSION = kodi_utils.get_kodi_version()

# Shared localized strings
folder_str = ls(32742).upper()
file_str = ls(32743).upper()
delete_str = ls(32785)
down_str = ls(32747)


def get_default_art(icon_path):
	"""Build standard art dict for debrid listitems."""
	fanart = kodi_utils.get_addoninfo('fanart')
	icon = kodi_utils.media_path(icon_path)
	return icon, {'icon': icon, 'poster': icon, 'thumb': icon, 'fanart': fanart, 'banner': icon}


def make_folder_listitem(count, name, url_params, delete_params, default_art):
	"""Create a standard folder listitem for cloud browsers."""
	display = '%02d | [B]%s[/B] | [I]%s [/I]' % (count, folder_str, name)
	cm = [('[B]%s %s[/B]' % (delete_str, folder_str.capitalize()), 'RunPlugin(%s)' % build_url(delete_params))]
	url = build_url(url_params)
	listitem = make_listitem()
	listitem.setLabel(display)
	listitem.addContextMenuItems(cm)
	listitem.setArt(default_art)
	return url, listitem, True


def make_file_listitem(count, name, size_gb, url_params, down_params, default_art, extra_cm=None, set_video_info=True):
	"""Create a standard file listitem for cloud browsers."""
	display = '%02d | [B]%s[/B] | %.2f GB | [I]%s [/I]' % (count, file_str, size_gb, name)
	cm = []
	if extra_cm:
		cm.extend(extra_cm)
	cm.append((down_str, 'RunPlugin(%s)' % build_url(down_params)))
	url = build_url(url_params)
	listitem = make_listitem()
	listitem.setLabel(display)
	listitem.addContextMenuItems(cm)
	listitem.setArt(default_art)
	if set_video_info:
		listitem.setInfo('video', {}) if KODI_VERSION < 20 else listitem.getVideoInfoTag()
	return url, listitem, False


def finalize_directory(builder, items):
	"""Common directory finalization for debrid cloud browsers."""
	__handle__ = int(sys.argv[1])
	kodi_utils.add_items(__handle__, list(builder(items)))
	kodi_utils.set_content(__handle__, 'files')
	kodi_utils.end_directory(__handle__)
	kodi_utils.set_view_mode('view.premium')
