import re
import json
import random
from threading import Thread
from debrids import alldebrid_api, premiumize_api, real_debrid_api, torbox_api, offcloud_api, easydebrid_api
from caches.debrid_cache import DebridCache
from indexers import metadata
from modules.utils import clean_file_name
from modules import kodi_utils, settings
# from modules.kodi_utils import logger

ls, get_setting, notification = kodi_utils.local_string, kodi_utils.get_setting, kodi_utils.notification
show_busy_dialog, hide_busy_dialog = kodi_utils.show_busy_dialog, kodi_utils.hide_busy_dialog
ok_dialog, confirm_dialog, select_dialog = kodi_utils.ok_dialog, kodi_utils.confirm_dialog, kodi_utils.select_dialog
default_internal_scrapers, enabled_debrids_check = settings.default_internal_scrapers, settings.enabled_debrids_check
default_hosters_providers = ('real-debrid', 'premiumize.me', 'alldebrid')
plswait_str, checking_debrid_str, remaining_debrid_str = ls(32577), ls(32578), ls(32579)

debrid_list = (
	('real-debrid', 'rd', real_debrid_api.RealDebridAPI),
	('premiumize.me', 'pm', premiumize_api.PremiumizeAPI),
	('alldebrid', 'ad', alldebrid_api.AllDebridAPI),
	('torbox', 'tb', torbox_api.TorBoxAPI),
	('offcloud', 'oc', offcloud_api.OffcloudAPI),
	('easydebrid', 'ed', easydebrid_api.EasyDebridAPI)
)

def import_debrid(debrid_provider):
	cls = next((i[2] for i in debrid_list if i[0] == debrid_provider), None)
	return cls() if cls else cls

def debrid_enabled():
	return [i[0] for i in debrid_list if enabled_debrids_check(i[1])]

def debrid_type_enabled(debrid_type, enabled_debrids):
	return [i[0] for i in debrid_list if i[0] in enabled_debrids and get_setting('%s.%s.enabled' % (i[1], debrid_type)) == 'true']

def debrid_valid_hosts(enabled_debrids):
	return []

class Source:
	def dumps(self, depth=1, width=172):
		from pprint import pformat
		return pformat(vars(self), depth=depth, width=width)

	def __init__(self, source_dict, meta=None):
		self.direct_debrid_link = False
		self.scrape_provider, self.url = '', ''
		for k, v in source_dict.items(): setattr(self, k, v)
		self.meta = meta or {}

	def resolve_sources(self):
		try:
			if self.scrape_provider in ('external',):
				if self.meta['media_type'] == 'episode':
					title = self.meta.get('ep_name') or self.meta.get('title')
					season = self.meta.get('custom_season') or self.meta.get('season')
					episode = self.meta.get('custom_episode') or self.meta.get('episode')
				else: title, season, episode = metadata.get_title(self.meta), None, None
				api = import_debrid(self.debrid)
				api.store_to_cloud = settings.store_resolved_torrent_to_cloud(self.debrid)
				if self.url.startswith('magnet'):
					return self.resolve_external_sources(api, title, season, episode)
				store_to_cloud = settings.store_resolved_usenet_to_cloud(self.debrid)
				return api.resolve_nzb(self.url, self.hash, store_to_cloud, title, season, episode)
			if self.scrape_provider in default_internal_scrapers:
				return self.resolve_internal_sources(self.direct_debrid_link)
			if self.debrid in default_hosters_providers and not self.source.lower() == 'torrent':
				return import_debrid(self.debrid).unrestrict_link(self.url)
			return self.url
		except: pass

	def resolve_external_sources(self, api, title, season, episode):
		from modules.source_utils import supported_video_extensions, seas_ep_filter, extras_filter
		try:
			extensions = supported_video_extensions()
			extras_filtering_list = tuple(i for i in extras_filter() if not i in title.lower())
			if self.debrid in ('real-debrid', 'alldebrid'): args = self.url, self.hash, True
			else: args = self.url, self.hash
			files = api.parse_magnet_pack(*args)
			selected_files = []
			selected_files_append = selected_files.append
			for i in files or selected_files:
				torrent_id, filename = i.get('torrent_id'), i['filename'].lower()
				if filename.endswith('.m2ts'): raise Exception('_m2ts_check failed')
				if not filename.endswith(tuple(extensions)): continue
				if season and not seas_ep_filter(season, episode, filename): continue
				elif any(x in filename for x in extras_filtering_list): continue
				selected_files_append(i)
			if not selected_files: raise Exception('selected_files failed')
			if not season: selected_files.sort(key=lambda k: k['size'], reverse=True)
			file_key = next((i['link'] for i in selected_files), None)
			if self.debrid in ('premiumize.me',): file_url = api.add_headers_to_url(file_key)
			else: file_url = api.unrestrict_link(file_key)
			if self.debrid in ('premiumize.me',):
				if api.store_to_cloud: Thread(target=api.create_transfer, args=(self.url,)).start()
			if self.debrid in ('real-debrid', 'alldebrid', 'torbox'):
				if not api.store_to_cloud: Thread(target=api.delete_torrent, args=(torrent_id,)).start()
			return file_url
		except Exception as e:
			kodi_utils.logger('resolve_external_sources exception', f"{e}\n{self.dumps()}")
			if files and torrent_id: Thread(target=api.delete_torrent, args=(torrent_id,)).start()

	def resolve_internal_sources(self, direct_debrid_link=False):
		try:
			if self.scrape_provider == 'tb_cloud':
				if direct_debrid_link == 'usenet': function = 'unrestrict_usenet'
				elif direct_debrid_link == 'webdl': function = 'unrestrict_webdl'
				else: function = 'unrestrict_link'
				function = getattr(torbox_api.TorBoxAPI(), function)
				url = function(self.id)
			elif self.scrape_provider == 'rd_cloud':
				if direct_debrid_link: url = self.url_dl
				else: url = real_debrid_api.RealDebridAPI().unrestrict_link(self.id)
			elif self.scrape_provider == 'pm_cloud':
				details = premiumize_api.PremiumizeAPI().get_item_details(self.id)
				url = details['link']
				if url.startswith('/'): url = 'https' + url
			elif self.scrape_provider == 'ad_cloud':
				if direct_debrid_link: url = self.url_dl
				else: url = alldebrid_api.AllDebridAPI().unrestrict_link(self.id)
			elif self.scrape_provider == 'easynews':
				from debrids.easynews import resolve_easynews
				url = resolve_easynews({'url_dl': self.url_dl, 'play': 'false'})
				if not direct_debrid_link: url += '|seekable=0'
			elif self.scrape_provider == 'folders':
				if self.url_dl.endswith('.strm'):
					with kodi_utils.open_file(self.url_dl) as f: url = f.read()
				else: url = self.url_dl
			else: url = self.url_dl
			return url
		except Exception as e:
			kodi_utils.logger('resolve_internal_sources exception', f"{e}\n{self.dumps()}")

	def browse_packs(self, highlight=None, download=False):
		show_busy_dialog()
		api = import_debrid(self.debrid)
		pack_choices = api.parse_magnet_pack(self.url, self.hash)
		hide_busy_dialog()
		if not pack_choices: return None if download else notification(32574)
		pack_choices.sort(key=lambda k: k['filename'].lower())
		for item in pack_choices: item.update({
			'icon': self.meta.get('poster') or api.icon,
			'line1': clean_file_name(item['filename']),
			'line2': '%s: %.2f GB' % (ls(32584), float(item['size'])/1073741824)
		})
		if download: return pack_choices
		kwargs = {'enumerate': 'true', 'multi_line': 'true'}
		kwargs.update({'items': json.dumps(pack_choices), 'heading': self.name, 'highlight': highlight})
		chosen_result = select_dialog(pack_choices, **kwargs)
		if chosen_result is None: return 'cancel'
		url_dl = chosen_result['link']
		if self.debrid == 'premiumize.me': return api.add_headers_to_url(url_dl)
		else: return api.unrestrict_link(url_dl)

	def unchecked_magnet_status(self):
		show_busy_dialog()
		api = import_debrid(self.debrid)
		result = api.parse_magnet_pack(self.url, self.hash)
		hide_busy_dialog()
		if not result: return ok_dialog(text='Not Cached at [B]%s[/B]' % self.debrid.upper(), top_space=True)
		torrent_id = next((i['torrent_id'] for i in result if 'torrent_id' in i), None)
		if torrent_id: Thread(target=api.delete_torrent, args=(torrent_id,)).start()
		ok_dialog(text='Cached at [B]%s[/B]' % self.debrid.upper(), top_space=True)

	def nzb_cache_and_play(self):
		line, status_str = '%s[CR]%s[CR]STATUS: %s', '[B]%s[/B] (%2d%%)'
		title, season, episode = self.meta['title'], self.meta['season'], self.meta['episode']
		if season and episode: line1 = '%s (%02dx%02d)' % (title, season, episode)
		else: line1 = '%s (%s)' % (title, self.meta['year'])
		kodi_utils.progressDialog.create('POV', '')
		kodi_utils.progressDialog.update(0, line % (line1, '', '[B]GRAB...[/B]'))
		try:
			store_to_cloud = get_setting('store_usenet.torbox') == 'true'
			api = import_debrid(self.debrid)
			api.clear_cache()
			nzb_id = api.create_transfer(self.url, self.name)
			if not nzb_id: return kodi_utils.notification(32574)
			resolved_link = None
			data = {'files': []}
			while not data['files']:
				if kodi_utils.progressDialog.iscanceled(): return kodi_utils.notification(32736)
				line2 = 'ETA: %s' % data.get('eta', 'NA')
				progress = int(float(data.get('progress', '0')) * 100)
				status = status_str % (data.get('download_state', '...').upper(), progress)
				kodi_utils.progressDialog.update(progress, line % (line1, line2, status))
				kodi_utils.sleep(500)
				result = api.nzb_info(nzb_id)
				if result and 'id' in result: data = result
			else: resolved_link = api.resolve_nzb(
				self.url, self.hash, store_to_cloud, title, season, episode, nzb_info=result
			)
		finally: kodi_utils.progressDialog.close()
		return kodi_utils.notification(32574) if not resolved_link else resolved_link

	def manual_add_nzb_to_cloud(self):
		if self.debrid in ('torbox',) and self.meta:
			args = 'POV', '[CR]%s' % ls(32831) % self.debrid.upper()
			choice = kodi_utils.dialog.yesnocustom(*args, customlabel='Cache/Play')
		else: choice = confirm_dialog(text=ls(32831) % self.debrid.upper(), top_space=True)
		if choice == 2: return self.nzb_cache_and_play()
		if choice in (-1, 0, False): return
		show_busy_dialog()
		api = import_debrid(self.debrid)
		api.clear_cache()
		result = api.create_transfer(self.url, self.name)
		hide_busy_dialog()
		if result: notification(32576)
		else: notification(32575)

	def manual_add_magnet_to_cloud(self):
		if not confirm_dialog(text=ls(32831) % self.debrid.upper(), top_space=True): return
		show_busy_dialog()
		api = import_debrid(self.debrid)
		api.clear_cache()
		result = api.create_transfer(self.url)
		hide_busy_dialog()
		if result: notification(32576)
		else: notification(32575)

class DebridCheck:
	def __init__(self, meta, name):
		self.cached_list = []
		self.name, self.debrid, self.function = self._debrid_dict[name]
		self.imdb, self.season, self.episode = meta.get('imdb_id'), meta.get('season'), meta.get('episode')

	def cache_check(self):
		try:
			self.cached_list.extend(i[0] for i in self.cached_hashes if i[1] == self.debrid and i[2] == 'True')
			unchecked_filter = {h[0] for h in self.cached_hashes if h[1] == self.debrid}
			unchecked_hashes = [i for i in self.hash_list if not i in unchecked_filter]
			if not unchecked_hashes: return
			if self.debrid in ('rd', 'ad'): checked_hashes = self.external_check_cache(unchecked_hashes)
			else: checked_hashes = self.function().check_cache(unchecked_hashes)
			if not checked_hashes: return
			hashes_to_cache = []
			process_append = hashes_to_cache.append
			cached_append = self.cached_list.append
			try:
				for h in unchecked_hashes:
					if h in checked_hashes:
						cached_append(h)
						cached = 'True'
					else: cached = 'False'
					process_append((h, cached))
			except:
				for i in unchecked_hashes: process_append((i, 'False'))
			if hashes_to_cache: Thread(target=self.cache_write, args=(hashes_to_cache,)).start()
		finally: return self.cached_list

	def external_check_cache(self, unchecked_hashes):
		checked_hashes = []
		if self.debrid == 'ad': threads = (
			Thread(target=mfn_check_cache, args=(self.imdb, self.season, self.episode, checked_hashes)),
			Thread(target=trz_check_cache, args=(self.imdb, self.season, self.episode, checked_hashes))
		)
		else: threads = (
			Thread(target=tio_check_cache, args=(self.imdb, self.season, self.episode, checked_hashes)),
			Thread(target=dmm_check_cache, args=(unchecked_hashes, self.imdb, checked_hashes))
		)
		for i in threads: i.start()
		for i in threads: i.join()
		return list(set(checked_hashes))

	def cache_write(self, hashes):
		DebridCache().set_many(hashes, self.debrid)

	@classmethod
	def set_cached_hashes(cls, hash_list):
		cls.hash_list = hash_list
		cls.cached_hashes = DebridCache().get_many(hash_list) or []

	_debrid_dict = {i[0]: i for i in debrid_list}
	hash_list, cached_hashes = [], []

import requests
from fenom.client import randomagent

session = requests.session()
session.headers.update({'User-Agent': randomagent(), 'Accept': 'application/json'})

def mfn_check_cache(imdb, season, episode, collector):
	if str(season).isdigit(): url = 'series/%s:%s:%s.json' % (imdb, season, episode)
	else: url = 'movie/%s.json' % (imdb)
	params = (
		'D-T2iZoymNCCD1T5c2sX5u8tIZVcgcFWlCsCJ72rCmrU2mDdmvgieM-lvX-bp4h_ExG1IpHLObtgmLCC'
		'k_QbhNTZz32wbhNmYO1HLaefzqGoYjcIhiUH-MWgL-dMxyrTPR2fo2--HtvH0V5KpEi6vPfjKKGBmpe3'
		'wRD0c_QsSxlcQ'
	)
	url = 'https://mediafusion.elfhosted.com/%s/stream/%s' % (params, url)
	pattern = re.compile(r'\b\w{40}\b')
	try:
		results = session.get(url, timeout=7.05)
		files = results.json()['streams']
		collector.extend(pattern.findall(file['url'])[-1] for file in files if '⚡' in file['name'] and 'url' in file)
	except Exception as e: kodi_utils.logger('mfn error', str(e))

def trz_check_cache(imdb, season, episode, collector):
	if str(season).isdigit(): url = 'series/%s:%s:%s.json' % (imdb, season, episode)
	else: url = 'movie/%s.json' % (imdb)
	params = 'eyJzdG9yZXMiOlt7ImMiOiJhZCIsInQiOiJzdGF0aWNEZW1vQXBpa2V5UHJlbSJ9XSwiY2FjaGVkIjp0cnVlfQ=='
	url = 'https://stremthru.elfhosted.com/stremio/torz/%s/stream/%s' % (params, url)
	pattern = re.compile(r'\b\w{40}\b')
	try:
		results = session.get(url, timeout=7.05)
		files = results.json()['streams']
		collector.extend(pattern.findall(file['url'])[-1] for file in files if '⚡' in file['name'] and 'url' in file)
	except Exception as e: kodi_utils.logger('trz error', str(e))

def tio_check_cache(imdb, season, episode, collector):
	if str(season).isdigit(): url = 'series/%s:%s:%s.json' % (imdb, season, episode)
	else: url = 'movie/%s.json' % (imdb)
	params = 'debridoptions=nodownloadlinks,nocatalog|realdebrid=T2iZoymNCCD1T5c2sX5u8tIZVcgcFWlCsCJ72rCmrU2mDdmvgieM'
	url = 'https://torrentio.strem.fun/%s/stream/%s' % (params, url)
	pattern = re.compile(r'\b\w{40}\b')
	try:
		results = session.get(url, timeout=7.05)
		files = results.json()['streams']
		collector.extend(pattern.findall(file['url'])[-1] for file in files if '+' in file['name'] and 'url' in file)
	except Exception as e: kodi_utils.logger('tio error', str(e))

def dmm_check_cache(unchecked_hashes_chunk, imdb, collector): # DMM API Allows max 100 hashes per request.
	""" do not thread multiple calls, abusing the api will get it turned off
		100 sample size should be enough """
	from magneto.dmm import get_secret
	unchecked_hashes_chunk = [i for i in unchecked_hashes_chunk if len(i) == 40]
	if len(unchecked_hashes_chunk) > 100: unchecked_hashes_chunk = random.sample(unchecked_hashes_chunk, 100)
	url = 'https://debridmediamanager.com/api/availability/check'
	dmmProblemKey, solution = get_secret()
	data = {'dmmProblemKey': dmmProblemKey, 'solution': solution, 'imdbId': imdb, 'hashes': unchecked_hashes_chunk}
	try:
		results = session.post(url, json=data, timeout=7.05)
		files = results.json()['available']
		collector.extend(file['hash'] for file in files if 'hash' in file)
	except Exception as e: kodi_utils.logger('dmm error', str(e))

