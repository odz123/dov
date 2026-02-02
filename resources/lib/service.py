import time
from threading import Thread
from modules import kodi_utils, settings

# Constants
DATABASE_MAINTENANCE_INTERVAL = 259200  # 3 days in seconds

logger, ls, path_exists, translate_path = kodi_utils.logger, kodi_utils.local_string, kodi_utils.path_exists, kodi_utils.translate_path
monitor, is_playing, get_visibility = kodi_utils.monitor, kodi_utils.player.isPlaying, kodi_utils.get_visibility
get_property, set_property, clear_property = kodi_utils.get_property, kodi_utils.set_property, kodi_utils.clear_property
get_setting, set_setting, make_settings_dict = kodi_utils.get_setting, kodi_utils.set_setting, kodi_utils.make_settings_dict

def initializeDatabases():
	from modules.cache import check_databases
	logger('POV', 'InitializeDatabases Service Starting')
	check_databases()
	return logger('POV', 'InitializeDatabases Service Finished')

def checkSettingsFile():
	logger('POV', 'CheckSettingsFile Service Starting')
	clear_property('pov_settings')
	profile_dir = kodi_utils.get_addoninfo('profile')
	settings_xml = profile_dir + 'settings.xml'
	if not path_exists(profile_dir):
		kodi_utils.make_directorys(profile_dir)
		kodi_utils.addon().setSetting('kodi_menu_cache', 'true')
		kodi_utils.sleep(500)
	make_settings_dict()
	set_property('pov_kodi_menu_cache', get_setting('kodi_menu_cache'))
	set_property('pov_rli_fix', get_setting('rli_fix'))
	return logger('POV', 'CheckSettingsFile Service Finished')

def databaseMaintenance():
	from modules.cache import clean_databases
	current_time = int(time.time())
	next_clean = current_time + DATABASE_MAINTENANCE_INTERVAL
	due_clean = int(get_setting('database.maintenance.due', '0'))
	if current_time < due_clean: return
	logger('POV', 'Database Maintenance Service Starting')
	clean_databases(current_time, database_check=False, silent=True)
	set_setting('database.maintenance.due', str(next_clean))
	return logger('POV', 'Database Maintenance Service Finished')

def viewsSetWindowProperties():
	logger('POV', 'ViewsSetWindowProperties Service Starting')
	kodi_utils.set_view_properties()
	return logger('POV', 'ViewsSetWindowProperties Service Finished')

def reuseLanguageInvokerCheck():
	import xml.etree.ElementTree as ET
	logger('POV', 'ReuseLanguageInvokerCheck Service Starting')
	addon_xml = translate_path('special://home/addons/plugin.video.pov/addon.xml')
	tree = ET.parse(addon_xml)
	root = tree.getroot()
	current_addon_setting = get_setting('reuse_language_invoker', 'true')
	text = '[B]Reuse Language Invoker[/B] SETTING/XML mismatch[CR]POV will reload your profile to refresh the addon.xml'
	item, refresh = next(root.iter('reuselanguageinvoker'), None), False
	if item is None: kodi_utils.notification(text.split('[CR]')[0])
	if item is not None and item.text != current_addon_setting:
		item.text = current_addon_setting
		tree.write(addon_xml)
		refresh = True
	if refresh and kodi_utils.confirm_dialog(text=text):
		kodi_utils.execute_builtin('LoadProfile(%s)' % kodi_utils.get_infolabel('system.profilename'))
	return logger('POV', 'ReuseLanguageInvokerCheck Service Finished')

def autoRun():
	logger('POV', 'AutoRun Service Starting')
	if settings.auto_start_pov(): kodi_utils.execute_builtin('RunAddon(plugin.video.pov)')
	return logger('POV', 'AutoRun Service Finished')

def clearSubs():
	logger('POV', 'Clear Subtitles Service Starting')
	sub_formats = ('.srt', '.ssa', '.smi', '.sub', '.idx')
	subtitle_path = 'special://temp/'
	dir_result = kodi_utils.list_dirs(subtitle_path)
	files = dir_result[1] if len(dir_result) > 1 else []
	for i in files:
		if i.startswith('POVSubs_') or i.endswith(sub_formats):
			kodi_utils.delete_file(subtitle_path + i)
	return logger('POV', 'Clear Subtitles Service Finished')

def traktMonitor():
	from caches.trakt_cache import clear_trakt_list_contents_data
	from indexers.trakt_api import trakt_sync_activities
	from indexers.mdblist_api import mdbl_sync_activities, clear_mdbl_cache
	from indexers.simkl_api import simkl_sync_activities
	from caches.simkl_cache import clear_simkl_list_data
	from indexers.tmdb_api import tmdb_clean_watchlist, clear_tmdbl_cache
	logger('POV', 'TraktMonitor Service Starting')
	trakt_service_string = 'TraktMonitor Service Update %s - %s'
	update_string = 'Next Update in %s minutes...'
	if not kodi_utils.get_property('pov_traktmonitor_first_run') == 'true':
		for i in ('user_lists', 'liked_lists', 'my_lists'): clear_trakt_list_contents_data(i)
		clear_mdbl_cache()
		clear_tmdbl_cache()
		clear_simkl_list_data()
		kodi_utils.set_property('pov_traktmonitor_first_run', 'true')
	def _run_sync(sync_func, service_name, account_name, next_update_string):
		try: status = sync_func()
		except Exception as e:
			logger('POV', 'TraktMonitor %s error: %s' % (sync_func.__name__, str(e)))
			status = 'failed'
		if status == 'success':
			logger('POV', trakt_service_string % ('POV %s - Success' % service_name, '%s Update Performed' % account_name))
			if settings.trakt_sync_refresh_widgets():
				kodi_utils.widget_refresh()
				logger('POV', trakt_service_string % ('POV %s - Widgets Refresh' % service_name, 'Setting Activated. Widget Refresh Performed'))
			else: logger('POV', trakt_service_string % ('POV %s - Widgets Refresh' % service_name, 'Setting Disabled. Skipping Widget Refresh'))
		elif status == 'no account':
			logger('POV', trakt_service_string % ('POV %s - Aborted. No %s Account Active' % (service_name, account_name), next_update_string))
		elif status == 'failed':
			logger('POV', trakt_service_string % ('POV %s - Failed. Error from %s' % (service_name, account_name), next_update_string))
		else:
			logger('POV', trakt_service_string % ('POV %s - Success. No Changes Needed' % service_name, next_update_string))
	while not monitor.abortRequested():
		while is_playing() or get_visibility('Container().isUpdating') or get_property('pov_pause_services') == 'true':
			monitor.waitForAbort(10)
		if not kodi_utils.get_property('pov_traktmonitor_first_run') == 'true':
			monitor.waitForAbort(5)
		value, interval = settings.trakt_sync_interval()
		next_update_string = update_string % value
		_run_sync(trakt_sync_activities, 'TraktMonitor', 'Trakt', next_update_string)
		_run_sync(mdbl_sync_activities, 'MDBListMonitor', 'MDBList', next_update_string)
		_run_sync(simkl_sync_activities, 'SimklMonitor', 'Simkl', next_update_string)
		try:
			if get_setting('tmdb.token') and get_setting('tmdblist.watchlist_sync') == 'true':
				status = tmdb_clean_watchlist(silent=True)
				if status: logger('POV', 'TMDB Lists Service Update - Success. %s' % status)
		except Exception as e:
			logger('POV', 'TraktMonitor tmdb_clean_watchlist error: %s' % str(e))
		monitor.waitForAbort(interval)
	return logger('POV', 'TraktMonitor Service Finished')

def premAccntNotification():
	logger('POV', 'Debrid Account Expiry Notification Service Starting')
	from importlib import import_module
	for user, expires, module, cls in (
		('ad.account_id', 'ad.expires', 'alldebrid_api', 'AllDebridAPI'),
		('pm.account_id', 'pm.expires', 'premiumize_api', 'PremiumizeAPI'),
		('rd.username', 'rd.expires', 'real_debrid_api', 'RealDebridAPI'),
		('tb.account_id', 'tb.expires', 'torbox_api', 'TorBoxAPI'),
		('ed.account_id', 'ed.expires', 'easydebrid_api', 'EasyDebridAPI')
	):
		try:
			if not get_setting(user): continue
			if limit := int(get_setting(expires, '7')):
				module = 'debrids.%s' % module
				cls = getattr(import_module(module), cls)
				days_remaining = cls().days_remaining()
				if days_remaining is not None and days_remaining <= limit:
					kodi_utils.notification('%s expires in %s days' % (cls.__name__, days_remaining))
		except Exception as e:
			logger('POV', 'premAccntNotification error for %s: %s' % (user, str(e)))
	return logger('POV', 'Debrid Account Expiry Notification Service Finished')

def checkUndesirablesDatabase():
	from fenom.undesirables import Undesirables, add_new_default_keywords
	logger('POV', 'CheckUndesirablesDatabase Service Starting')
	old_database = Undesirables().check_database()
	if old_database: add_new_default_keywords()
	return logger('POV', 'CheckUndesirablesDatabase Service Finished')

class POVMonitor(kodi_utils.xbmc_monitor):
	def __enter__(self):
		self.threads = (Thread(target=traktMonitor), Thread(target=premAccntNotification))
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		for i in self.threads: i.join()

	def startUpServices(self):
		try: initializeDatabases()
		except Exception as e: logger('POV', 'initializeDatabases error: %s' % str(e))
		try: checkSettingsFile()
		except Exception as e: logger('POV', 'checkSettingsFile error: %s' % str(e))
		try: databaseMaintenance()
		except Exception as e: logger('POV', 'databaseMaintenance error: %s' % str(e))
		try: viewsSetWindowProperties()
		except Exception as e: logger('POV', 'viewsSetWindowProperties error: %s' % str(e))
		try: reuseLanguageInvokerCheck()
		except Exception as e: logger('POV', 'reuseLanguageInvokerCheck error: %s' % str(e))
		for i in self.threads: i.start()
		try: autoRun()
		except Exception as e: logger('POV', 'autoRun error: %s' % str(e))
		try: clearSubs()
		except Exception as e: logger('POV', 'clearSubs error: %s' % str(e))
		try: checkUndesirablesDatabase()
		except Exception as e: logger('POV', 'checkUndesirablesDatabase error: %s' % str(e))

	def onScreensaverActivated(self):
		set_property('pov_pause_services', 'true')

	def onScreensaverDeactivated(self):
		clear_property('pov_pause_services')

	def onSettingsChanged(self):
		clear_property('pov_settings')
		kodi_utils.clear_settings_cache()
		kodi_utils.sleep(50)
		make_settings_dict()
		set_property('pov_kodi_menu_cache', get_setting('kodi_menu_cache'))
		set_property('pov_rli_fix', get_setting('rli_fix'))

	def onNotification(self, sender, method, data):
		if method == 'System.OnSleep': set_property('pov_pause_services', 'true')
		elif method == 'System.OnWake': clear_property('pov_pause_services')


logger('POV', 'Main Monitor Service Starting')
logger('POV', 'Settings Monitor Service Starting')

with POVMonitor() as pov:
	pov.startUpServices()
	pov.waitForAbort()

logger('POV', 'Settings Monitor Service Finished')
logger('POV', 'Main Monitor Service Finished')

