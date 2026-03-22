"""
	Fenomscrapers Module
"""

from json import dumps as jsdumps, loads as jsloads
import os.path
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import xml.etree.ElementTree as ET

addon = xbmcaddon.Addon
addonObject = addon('plugin.video.pov') # addonObject = addon('script.module.fenomscrapers')
addonInfo = addonObject.getAddonInfo
getLangString = addonObject.getLocalizedString
condVisibility = xbmc.getCondVisibility
execute = xbmc.executebuiltin
jsonrpc = xbmc.executeJSONRPC
monitor_class = xbmc.Monitor
monitor = xbmc.Monitor()

dialog = xbmcgui.Dialog()
homeWindow = xbmcgui.Window(10000)

existsPath = xbmcvfs.exists
openFile = xbmcvfs.File
makeFile = xbmcvfs.mkdir
makeDirs = xbmcvfs.mkdirs
transPath = xbmcvfs.translatePath
joinPath = os.path.join

SETTINGS_PATH = transPath(joinPath(addonInfo('path'), 'resources', 'settings.xml'))
try: dataPath = transPath(addonInfo('profile')).decode('utf-8')
except Exception: dataPath = transPath(addonInfo('profile'))
#cacheFile = joinPath(dataPath, 'cache.db')
#undesirablescacheFile = joinPath(dataPath, 'undesirables.db')
cacheFile = joinPath(dataPath, 'fenomcache.db')
undesirablescacheFile = joinPath(dataPath, 'fenomundesirables.db')
settingsFile = joinPath(dataPath, 'settings.xml')


def setting(id, fallback=None):
#	try: settings_dict = jsloads(homeWindow.getProperty('fenomscrapers_settings'))
	try: settings_dict = jsloads(homeWindow.getProperty('pov_settings'))
	except Exception: settings_dict = make_settings_dict()
	if settings_dict is None: settings_dict = settings_fallback(id)
	value = settings_dict.get(id, '')
	if fallback is None: return value
	if value == '': return fallback
	return value

def settings_fallback(id):
	return {id: addonObject.getSetting(id)}

def setSetting(id, value):
	return addonObject.setSetting(id, value)

def make_settings_dict(): # service runs upon a setting change
	try:
		root = ET.parse(settingsFile).getroot()
		settings_dict = {}
		for item in root:
			dict_item = {}
			setting_id = item.get('id')
			setting_value = item.text
			if setting_value is None: setting_value = ''
			dict_item = {setting_id: setting_value}
			settings_dict.update(dict_item)
#		homeWindow.setProperty('fenomscrapers_settings', jsdumps(settings_dict))
		homeWindow.setProperty('pov_settings', jsdumps(settings_dict))
		return settings_dict
	except Exception:
		return None

def refresh_debugReversed(): # called from service "onSettingsChanged" to clear fenomscrapers.log if setting to reverse has been changed
	if homeWindow.getProperty('fenomscrapers.debug.reversed') != setting('debug.reversed'):
		homeWindow.setProperty('fenomscrapers.debug.reversed', setting('debug.reversed'))
		execute('RunPlugin(plugin://script.module.fenomscrapers/?action=tools_clearLogFile)')

def lang(language_id):
	return getLangString(language_id)

def addonId():
	return addonInfo('id')

def addonName():
	return addonInfo('name')

def addonVersion():
	return addonInfo('version')

def addonIcon():
	return addonInfo('icon')

def addonPath():
	try: return transPath(addonInfo('path').decode('utf-8'))
	except Exception: return transPath(addonInfo('path'))

def yesnoDialog(line, heading=addonInfo('name'), nolabel='', yeslabel=''):
	return dialog.yesno(heading, line, nolabel, yeslabel)

def selectDialog(list, heading=addonInfo('name')):
	return dialog.select(heading, list)

def multiselectDialog(list, preselect=None, heading=addonInfo('name')):
	if preselect is None: preselect = []
	return dialog.multiselect(heading, list, preselect=preselect)

def notification(title=None, message=None, icon=None, time=3000, sound=False):
	if title == 'default' or title is None: title = addonName()
	if isinstance(title, int): heading = lang(title)
	else: heading = str(title)
	if isinstance(message, int): body = lang(message)
	else: body = str(message)
	if not icon or icon == 'default': icon = addonIcon()
	elif icon == 'INFO': icon = xbmcgui.NOTIFICATION_INFO
	elif icon == 'WARNING': icon = xbmcgui.NOTIFICATION_WARNING
	elif icon == 'ERROR': icon = xbmcgui.NOTIFICATION_ERROR
	dialog.notification(heading, body, icon, time, sound=sound)

