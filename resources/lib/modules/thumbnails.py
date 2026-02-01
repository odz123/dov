import sqlite3 as database
from pathlib import Path
from datetime import datetime, timedelta
import xbmc, xbmcaddon, xbmcgui, xbmcvfs

_addon_info = xbmcaddon.Addon().getAddonInfo

def notification(line1, time=3000, sound=False):
	xbmcgui.Dialog().notification(_addon_info('name'), line1, _addon_info('icon'), time, sound)

def thumb_cleaner():
	current_date = datetime.utcnow().date()
	thumbs_folder = Path(xbmcvfs.translatePath('special://thumbnails'))
	dbfile = Path(xbmcvfs.translatePath('special://database'), 'Textures13.db')
	if not dbfile.exists(): return notification('Failed')
	item_list = []
	minimum_uses = 30
	days = xbmcgui.Dialog().numeric(0 , 'Remove Thumbs Older Than (Days)...', defaultt=str(minimum_uses))
	if not days: return notification('No Days Set')
	back_date = (current_date - timedelta(days=int(days))).strftime('%Y-%m-%d %H:%M:%S')
	dbcon = database.connect(str(dbfile), isolation_level=None)
	dbcur = dbcon.cursor()
	dbcur.execute('''PRAGMA synchronous = OFF''')
	dbcur.execute('''PRAGMA journal_mode = OFF''')
#	dbcur.execute(
#		"SELECT idtexture FROM sizes WHERE usecount < ? AND lastusetime < ?",
#		(minimum_uses, str(back_date))
#	)
	dbcur.execute("SELECT idtexture FROM sizes WHERE lastusetime < ?", (str(back_date), ))
	result = dbcur.fetchall()
	result_length = len(result)
	if not result_length > 0: return notification('No Thumbnails to Clear')
	progress_dialog = xbmcgui.DialogProgress()
	progress_dialog.create('Thumbnails Remover', '')
	progress_dialog.update(0, 'Gathering Thumbnail Info...')

	# Batch fetch all cachedurls in one query instead of N+1 queries
	_ids = [item[0] for item in result]
	# Process in chunks of 500 to avoid SQL parameter limits
	url_map = {}
	for i in range(0, len(_ids), 500):
		chunk = _ids[i:i+500]
		placeholders = ','.join('?' * len(chunk))
		dbcur.execute("SELECT id, cachedurl FROM texture WHERE id IN (%s)" % placeholders, chunk)
		for row in dbcur.fetchall():
			url_map[row[0]] = row[1]

	for count, item in enumerate(result):
		if progress_dialog.iscanceled(): break
		_id = item[0]
		url = url_map.get(_id)
		if not url: continue
		path = thumbs_folder.joinpath(url)
		path.unlink(missing_ok=True)
		item_list.append((_id,))
		percent = int(count / result_length * 100)
		line = '[B]Total To Remove:[/B] %s[CR][B]Removing:[/B] %02d - %s[CR][B]Path: [/B]%s'
		line = line % (result_length, count, str(path.name), str(path.parent))
		progress_dialog.update(max(1, percent), line)
	line = 'Removing %d Database Entries...[CR]Please Wait...[CR]%s' % (result_length, '%s')
	progress_dialog.update(33, line % 'Removing Sizes IDS...')
	dbcur.executemany("DELETE FROM sizes WHERE idtexture = ?", item_list)
	progress_dialog.update(66, line % 'Removing Texture IDS...')
	dbcur.executemany("DELETE FROM texture WHERE id = ?", item_list)
	progress_dialog.update(99, line % 'Cleaning Database...')
	dbcon.commit()
	dbcur.execute("VACUUM")
	xbmc.sleep(1500)
	try: progress_dialog.close()
	except Exception: pass
	return notification('Success')

