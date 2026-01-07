# Performance Anti-Pattern Analysis

This document identifies performance issues found in the POV Kodi addon codebase.

## ✅ Issues Fixed in This Commit

| Issue | File | Fix Applied |
|-------|------|-------------|
| N+1 watched status lookups | `watched_cache.py` | Added dict/set-based lookup functions for O(1) access |
| eval() security/performance | `meta_cache.py` | Replaced with `ast.literal_eval()` |
| Regex recompilation | `source_utils.py` | Pre-compiled 60+ regex patterns at module level |
| Thread joining anti-pattern | `watched_cache.py`, `sources.py` | Replaced list comprehensions with proper for loops |
| Race condition bug | `watched_cache.py:382` | Fixed `.append()` returning None bug |
| Multiple filter passes | `sources.py` | Combined exclusion filters into single pass |

---

## 1. N+1 Query Patterns (HIGH IMPACT)

### 1.1 Linear Search in Watched Status Functions

**Files:** `resources/lib/caches/watched_cache.py:243-273`

```python
def get_watched_status_movie(watched_info, tmdb_id):
    watched = [i for i in watched_info if i[0] == tmdb_id]  # O(n) for each call
```

**Problem:** These functions perform linear scans through `watched_info` for every single item when building lists. When building a list of 100 movies, this results in 100 full list iterations.

**Similar issues at:**
- `get_watched_status_tvshow()` line 250-257
- `get_watched_status_season()` line 259-266
- `get_watched_status_episode()` line 268-273
- `detect_bookmark()` line 57-58

**Solution:** Convert `watched_info` to a dictionary/set for O(1) lookups:
```python
# Create lookup dict once
watched_dict = {i[0]: i for i in watched_info}
# Then O(1) lookup
watched = watched_dict.get(tmdb_id)
```

### 1.2 Database Queries in Loops

**File:** `resources/lib/caches/watched_cache.py:571-603`

```python
def batch_mark_episodes_as_watched_unwatched_kodi_library(action, show_info, episode_list):
    for item in episode_list:
        # Individual JSON-RPC call for EACH episode
        r = execJSONRPC(json.dumps({"jsonrpc": "2.0", "method": "VideoLibrary.GetEpisodes"...}))
```

**Problem:** Makes individual Kodi library queries for each episode instead of batch querying.

### 1.3 Repeated Cache Instantiation (NEW)

**Files:** `resources/lib/caches/mdbl_cache.py:60-115`, `resources/lib/caches/trakt_cache.py:60-137`

```python
def cache_trakt_object(function, string, url):
    dbcur = TraktCache().dbcur  # NEW connection opened here
    dbcur.execute(TC_BASE_GET, (string,))
```

**Problem:** Each module-level function creates a new database connection by instantiating the cache class. Functions affected:
- `cache_trakt_object()`, `reset_activity()`, `clear_trakt_*()` (9 instances in trakt_cache.py)
- `cache_mdbl_object()`, `reset_activity()`, `clear_mdbl_*()` (6 instances in mdbl_cache.py)

### 1.4 N+1 DELETE Loop Pattern (NEW)

**File:** `resources/lib/caches/main_cache.py:66-78`

```python
def delete_all_lists(self):
    for item in results:  # Loop starts here
        self.dbcur.execute(DELETE, (str(item[0]),))  # Individual DELETE per item
```

**Problem:** If `results` contains 100+ items, this executes 100+ separate DELETE statements. Should use `executemany()`.

---

## 2. O(n²) Algorithm Anti-Patterns (CRITICAL)

### 2.1 List Membership Checks in Comprehensions (NEW)

**File:** `resources/lib/modules/sources.py`

```python
# Line 206
remainder_list = [i for i in results if not i in priority_list]  # O(n²)

# Line 458
sort_last = [i for i in results if not i in sort_first]  # O(n²)

# Line 490, 505, 683 - Similar patterns
```

**Problem:** Checking `not i in list` performs O(n) lookup inside O(n) loop = O(n²). With 500 sources, this is 250,000 comparisons.

**Solution:** Convert to set for O(1) lookup:
```python
priority_set = set(priority_list)
remainder_list = [i for i in results if i not in priority_set]
```

### 2.2 Multiple Passes Over Same Data (NEW)

**File:** `resources/lib/fenom/source_utils.py:370-530`

The `filter_show_pack()` function performs ~14 separate while loops building range lists with identical patterns:
```python
while season_count <= int(total_seasons):
    to_season_ranges.append(...)
    season_count += 1
if any(i in dot_release_title for i in to_season_ranges):  # Check
    keys = [i for i in to_season_ranges if i in dot_release_title]  # Redundant filter
    # ... then rebuild with .replace() and check again - 4+ more times
```

**Problem:** ~7 separate iterations for nearly identical operations.

### 2.3 Double Hash Iteration (NEW)

**File:** `resources/lib/modules/sources.py:604-607`

```python
for name, hashes in ...:
    self.final_sources.extend({**i} for i in torrent_sources if i['hash'] in hashes)     # First pass
    self.final_sources.extend({**i} for i in torrent_sources if not i['hash'] in hashes) # Second pass
```

**Problem:** Iterates torrent_sources twice with opposite conditions. Should be a single pass.

---

## 3. Inefficient Nested Loop Algorithms (MEDIUM-HIGH IMPACT)

### 3.1 O(n*m) Title Matching in source_utils.py

**File:** `resources/lib/fenom/source_utils.py:132-176`

```python
def check_title(title, aliases, release_title, hdlr, year, years=None):
    if all(cleantitle.get(i) != cleantitle.get(t) for i in title_list):  # O(n)
        return False
```

**Problem:** For each source being checked, this iterates through all aliases. With 500 sources and 10 aliases, this is 5000 comparisons.

### 3.2 Repeated String Operations in Loops (NEW)

**File:** `resources/lib/fenom/source_utils.py`

```python
# Lines 210, 223, 285, 345
for i in years: alias = alias.replace(i, '')
for i in split_list: t = t.split(i)[0]

# Lines 553, 556
for i in str1_replace: name_info = name_info.replace(i, '')
for i in str2_replace: name_info = name_info.replace(i, '')
```

**Problem:** Multiple sequential `.replace()` or `.split()` calls. Should use a single regex pattern.

### 3.3 Hoster Nested Loop Lookup (NEW)

**File:** `resources/lib/modules/sources.py:613-614`

```python
for item in self.debrid_hosters:
    for k, v in item.items():
        valid_hosters = [i for i in result_hosters if i in v]  # O(m*n)
        self.final_sources.extend([{**i} for i in hoster_sources if i['source'] in valid_hosters])
```

**Problem:** Nested loops with list membership checks = O(n³) in worst case.

---

## 4. String Concatenation Anti-Patterns (LOW-MEDIUM IMPACT)

### 4.1 String Concatenation with +=

**File:** `resources/lib/fenom/source_utils.py:351-358`
```python
all_seasons += '.%s' % str(season_count)  # Creates new string object each iteration
```

### 4.2 Inefficient URL Building

**File:** `resources/lib/magneto/*.py` files
```python
url = '%s%s' % (self.base_link, self.movieSearch_link % imdb)
```

---

## 5. Threading Anti-Patterns (HIGH IMPACT)

### 5.1 Fire-and-Forget Threads (NEW - CRITICAL)

**Locations:**
- `resources/lib/modules/player.py:187, 209, 216, 225, 233`
- `resources/lib/modules/debrid.py:96, 98, 102, 163, 248`
- `resources/lib/windows/people.py:32-35` (4 threads)
- `resources/lib/windows/extras.py:43-49` (12 threads)
- `resources/lib/debrids/torbox_api.py:178, 182`
- `resources/lib/indexers/trakt_api.py:624`
- `resources/lib/indexers/tmdb_api.py:594, 615`

```python
Thread(target=self.run_media_watched, args=(...)).start()  # No join, no tracking
Thread(target=execute_nextep, args=(...)).start()
```

**Problem:** 25+ locations create threads without:
- Wait mechanism
- Exception handling
- Lifecycle tracking
- Graceful shutdown

### 5.2 Thread Join in List Comprehension (NEW)

**File:** `resources/lib/indexers/tmdb_api.py:572`

```python
[i.join(3/4) for i in threads]  # Creates unnecessary list
```

### 5.3 Unbounded Thread Creation

**File:** `resources/lib/modules/utils.py:48-52`

```python
def make_thread_list(_target, _list, _thread):
    for item in _list:
        threaded_object = _thread(target=_target, args=(item,))
        threaded_object.start()  # Creates threads for every item!
```

**Problem:** Creates as many threads as items in the list. With 500 sources, this creates 500 threads.

**Good pattern exists at:** `TaskPool` class (lines 16-43) which limits thread count - but not used consistently.

### 5.4 Excessive UI Initialization Threads (NEW)

**File:** `resources/lib/windows/extras.py:42-49`

```python
for i in (
    Thread(target=self.set_poster), Thread(target=self.make_cast),
    Thread(target=self.make_recommended), Thread(target=self.make_reviews),
    # ... 12-13 threads total
): i.start()
```

**Problem:** Creates 12-13 threads on dialog initialization with no synchronization.

### 5.5 Race Condition in CloudFlare Cookie Fetch (NEW)

**File:** `resources/lib/fenom/client.py:352-361`

```python
def get(self, netloc, ua, timeout):
    for i in list(range(0, 15)):  # Creates 15 threads!
        threads.append(Thread(target=self.get_cookie, args=(...)))
    for i in threads: i.start()
    for i in list(range(0, 30)):  # Sleep loop instead of join!
        if self.cookie is not None: return self.cookie
        sleep(1)
```

**Issues:**
- Creates 15 threads for a single cookie
- Uses `sleep()` loop instead of `.join()`
- Reads `self.cookie` without synchronization

---

## 6. Caching & Memory Issues (MEDIUM-HIGH IMPACT)

### 6.1 Database Connection Leaks (NEW - CRITICAL)

**Files:** `resources/lib/caches/trakt_cache.py`, `resources/lib/caches/mdbl_cache.py`

Each function creates new `TraktCache()` or `MDBLCache()` instance without closing connections.

### 6.2 Unbounded Window Property Accumulation (NEW)

**File:** `resources/lib/modules/episode_tools.py:25-36`

```python
episode_history = json.loads(kodi_utils.get_property('pov_random_episode_history'))
episode_list.append(chosen_episode)
episode_history[tmdb_key] = episode_list  # GROWS INDEFINITELY
kodi_utils.set_property('pov_random_episode_history', json.dumps(episode_history))
```

**Problem:** `pov_random_episode_history` accumulates all episodes played across session with no cleanup.

### 6.3 No TTL Cleanup for Window Properties (NEW)

**Files:** `resources/lib/caches/meta_cache.py:85-103`, `resources/lib/caches/main_cache.py:40-54`

SQLite caches have TTL tracking, but window property caches:
- Never auto-expire
- No background cleanup task
- Persist indefinitely if not accessed

### 6.4 Hardcoded Season Cleanup Limit (NEW)

**File:** `resources/lib/caches/meta_cache.py:128-129`

```python
def delete_all_seasons_memory_cache(self, media_id):
    for item in range(1, 51): clear_property('pov_meta_season_%s_%s' % (...))
```

**Problem:** Only clears seasons 1-50. Shows with 50+ seasons leak memory.

### 6.5 No Connection Pooling

**File:** `resources/lib/caches/watched_cache.py:21-22`

New database connections are created for each operation instead of using a connection pool.

### 6.6 Unbounded Bookmarks Dict (NEW)

**File:** `resources/lib/caches/watched_cache.py:66-72`

```python
return {(i[0], i[3], i[4]): (...) for i in result.fetchall()}  # Loads ALL bookmarks
```

**Problem:** All bookmarks loaded into memory at once. For large libraries (10K+ episodes), significant memory footprint.

### 6.7 Repeated VACUUM Operations (NEW)

**File:** `resources/lib/caches/mdbl_cache.py:49`

```python
def _delete(self, command, args):
    self.dbcur.execute(command, args)
    self.dbcur.execute("""VACUUM""")  # After EVERY delete operation
```

**Problem:** `VACUUM` rewrites entire database. Should batch operations and VACUUM once.

---

## 7. Redundant Operations (MEDIUM IMPACT)

### 7.1 Multiple Sorts on Same Data (NEW)

**File:** `resources/lib/modules/sources.py:463-481`

```python
results.sort(key=lambda k: 'Unchecked' in k.get('cache_provider', ''), reverse=False)
# ... conditional code ...
results.sort(key=lambda k: 'Uncached' in k.get('cache_provider', ''), reverse=False)
# ... filter operations ...
results.sort(key=lambda k: 'Uncached' in k.get('cache_provider', ''), reverse=False)
```

**Problem:** Up to 3 sorts on same list. Can be combined into one.

### 7.2 Duplicate Dict Initialization (NEW)

**File:** `resources/lib/modules/sources.py:575-576`

```python
self.internal_resolutions = dict.fromkeys(resolutions.split(), 0)
self.resolutions = dict.fromkeys(resolutions.split(), 0)
```

**Problem:** `resolutions.split()` called twice.

### 7.3 Set-to-List Conversions (NEW)

**File:** `resources/lib/modules/sources.py:156, 596, 610, 690`

```python
self.all_scrapers = list(set(...))
result_hashes = list({i['hash'] for i in torrent_sources})
```

**Problem:** Creates set then converts to list, losing O(1) lookup benefit.

### 7.4 Inconsistent Dict Lookup Usage (NEW)

**File:** `resources/lib/caches/watched_cache.py`

Efficient dict lookups exist (`get_bookmarks_dict`, `make_watched_info_*_dict`) but inefficient list versions are used in most places.

---

## 8. Runtime Regex Compilation (NEW)

**File:** `resources/lib/modules/source_utils.py:250`

```python
reg_pattern = re.compile(final_string)  # Compiled inside function, called repeatedly
```

**Problem:** Regex compiled on every function call instead of at module level.

---

## Priority Summary

| Issue | Impact | Effort to Fix |
|-------|--------|---------------|
| O(n²) list membership checks | CRITICAL | LOW |
| Fire-and-forget threads (25+ locations) | HIGH | MEDIUM |
| Database connection leaks | HIGH | MEDIUM |
| N+1 watched status lookups | HIGH | LOW |
| Unbounded window property growth | HIGH | LOW |
| Unbounded thread creation | MEDIUM-HIGH | LOW |
| Multiple list passes / redundant iterations | MEDIUM | MEDIUM |
| Missing TTL cleanup for memory cache | MEDIUM | MEDIUM |
| Repeated VACUUM operations | MEDIUM | LOW |
| String concatenation in loops | LOW-MEDIUM | LOW |
| Database connection pooling | MEDIUM | HIGH |

---

## Quick Wins (Recommended First)

1. **Convert list membership to set** - `priority_set = set(priority_list)` - Biggest impact
2. **Use TaskPool consistently** - Already exists, just needs wider adoption
3. **Add cleanup for episode history** - Clear on session end
4. **Use `get_bookmarks_dict()` everywhere** - Already implemented, just use it
5. **Combine filter passes** - Single iteration over results
6. **Fix repeated cache instantiation** - Use module-level cache instance
7. **Remove VACUUM from individual operations** - Batch at end only

---

## Files Most Needing Attention

| File | Issue Count | Priority |
|------|-------------|----------|
| `modules/sources.py` | 12+ | CRITICAL |
| `caches/watched_cache.py` | 8+ | HIGH |
| `fenom/source_utils.py` | 7+ | HIGH |
| `caches/trakt_cache.py` | 6+ | HIGH |
| `caches/mdbl_cache.py` | 6+ | HIGH |
| `windows/extras.py` | 3+ | MEDIUM |
| `modules/player.py` | 5+ | MEDIUM |

---

*Analysis generated on 2026-01-07*

---

## 9. Additional N+1 Query Patterns (NEW)

### 9.1 Thumbnail Cache N+1 Query

**File:** `resources/lib/modules/thumbnails.py:35-39`

```python
for count, item in enumerate(result):
    if progress_dialog.iscanceled(): break
    _id = item[0]
    dbcur.execute("SELECT cachedurl FROM texture WHERE id = ?", (_id, ))  # Query per item!
    url = dbcur.fetchall()[0][0]
```

**Problem:** If `result` has 100 thumbnails, this makes 100 separate database queries instead of 1 batch operation.

**Fix:** Use batch query with `IN` clause:
```python
_ids = [item[0] for item in result]
dbcur.execute("SELECT id, cachedurl FROM texture WHERE id IN (%s)" % ','.join('?' * len(_ids)), _ids)
url_map = {row[0]: row[1] for row in dbcur.fetchall()}
```

### 9.2 View Clear N+1 DELETE

**File:** `resources/lib/modules/kodi_utils.py:296-299`

```python
dbcur.execute("""SELECT view_type FROM views""")
for item in dbcur.fetchall():
    dbcur.execute("""DELETE FROM views WHERE view_type = ?""", (item[0],))  # DELETE in loop!
```

**Problem:** Executes one DELETE query per view instead of a single `DELETE FROM views`.

---

## 10. Missing Database Indexes (NEW)

**File:** `resources/lib/modules/cache.py`

The following tables would benefit from additional indexes:

| Table | Suggested Index | Query Pattern |
|-------|-----------------|---------------|
| `watched_status` | `(db_type, media_id)` | SELECT by db_type filter |
| `progress` | `(db_type, media_id)` | SELECT by db_type filter |
| `metadata` | `(expires)` | DELETE expired entries |
| `results_data` | `(provider, db_type, tmdb_id)` | Complex WHERE clause |

**Impact:** Missing indexes cause full table scans on filtered queries.

---

## 11. TaskPool Misuse Patterns (NEW)

### 11.1 List Comprehension for Side Effects

**File:** `resources/lib/modules/utils.py:35`

```python
[self._queue.put(tag) for tag in _list]  # List comprehension for side effects
```

**Problem:** Creates unused list. Should use for-loop:
```python
for tag in _list:
    self._queue.put(tag)
```

### 11.2 Generator-to-List Conversion

**File:** `resources/lib/modules/utils.py:37`

```python
return list(self.process(threads))  # Forces generator to list
```

**Problem:** `self.process()` is a generator but immediately materialized, losing lazy evaluation.

---

## 12. Race Conditions in Shared State (NEW)

### 12.1 Sources List Modified by Multiple Threads

**File:** `resources/lib/modules/sources.py:37-39, 233-234`

```python
class SourceSelect:
    def __init__(self):
        self.sources = []  # Modified by multiple threads without locks
        self.prescrape_sources = []

    def activate_providers(self, ...):
        # Multiple threads call this concurrently:
        if prescrape: self.prescrape_sources.extend(sources)  # NOT thread-safe!
        else: self.sources.extend(sources)
```

**Problem:** `list.extend()` is not atomic. Concurrent threads can corrupt the list.

**Fix:** Use threading.Lock:
```python
from threading import Lock
self.sources_lock = Lock()

with self.sources_lock:
    self.sources.extend(sources)
```

### 12.2 Dictionary Updated from ThreadPoolExecutor

**File:** `resources/lib/modules/sources.py:606-614`

```python
# Multiple threads from TPE modify self.final_sources
self.final_sources.extend({**i, 'cache_provider': name} for i in torrent_sources if i['hash'] in hashes)
```

**Problem:** Same race condition risk with concurrent `.extend()` calls.

---

## 13. Inefficient String Operations (NEW)

### 13.1 Chained += Concatenation

**File:** `resources/lib/indexers/discover.py:527-565`

```python
name += '| %s %s' % (ls(32672), values['similar'])
name += '| %s %s' % (ls(32673), values['recommended'])
name += '| %s' % values['year_start']
# ... 20+ more += operations
```

**Problem:** 20+ `+=` operations create 20+ intermediate string objects (O(n²) memory allocation).

**Fix:** Use `''.join()` or single format string.

### 13.2 Repeated Translation Function Calls

**File:** `resources/lib/indexers/discover.py:552-562`

```python
name += '(%s %s) ' % (ls(32189).lower(), values['exclude_genres'])
# Later:
name += '| %s %s ' % (ls(32189).lower(), values['exclude_genres'])
```

**Problem:** Same `ls(32189).lower()` called multiple times.

**Fix:** Cache result: `exclude_str = ls(32189).lower()`

---

## 14. Inefficient Collection Usage (NEW)

### 14.1 List Comprehension for Single Item

**File:** `resources/lib/windows/extras.py:396-397, 480`

```python
network_id = [i['id'] for i in results if i['name'] == network][0]  # Creates full list for one item
info = [i for i in ep_list if i['media_ids']['tmdb'] == self.tmdb_id][0]
```

**Problem:** Creates entire filtered list just to get first match.

**Fix:** Use `next()`:
```python
network_id = next((i['id'] for i in results if i['name'] == network), None)
```

### 14.2 Sort Using .index() as Key

**File:** `resources/lib/windows/sources.py:239`

```python
provider_choices.sort(key=choice_sorter.index)  # O(n²log n)!
```

**Problem:** `.index()` is O(n), called O(n log n) times during sort = O(n² log n).

**Fix:** Create position dict:
```python
positions = {v: i for i, v in enumerate(choice_sorter)}
provider_choices.sort(key=lambda x: positions.get(x, len(positions)))
```

### 14.3 .index() in List Comprehensions

**File:** `resources/lib/modules/dialogs.py:247, 265, 281`

```python
preselect = [fl.index(i) for i in get_setting(...).split(', ')]  # O(n) per item
```

**Fix:** Pre-build index mapping:
```python
fl_idx = {item: idx for idx, item in enumerate(fl)}
preselect = [fl_idx[i] for i in get_setting(...).split(', ') if i in fl_idx]
```

---

## 15. Additional Threading Issues (NEW)

### 15.1 No Thread Pool Limit in Manager

**File:** `resources/lib/modules/sources.py:581`

```python
tpe = TPE(max(1, len(self.source_dict), len(self.debrid_torrents)))
```

**Problem:** Pool size unbounded - could create 100+ threads.

**Fix:** Cap pool size:
```python
tpe = TPE(min(max(1, len(self.source_dict)), 10))
```

### 15.2 Missing Thread Timeouts

**Files:** Multiple locations with `t.join()` without timeout:
- `resources/lib/modules/sources.py:131, 149`
- `resources/lib/magneto/torrentdownload.py:52`
- `resources/lib/service.py:168`

**Problem:** Hung threads block indefinitely.

**Fix:** Always specify timeout:
```python
for t in threads:
    t.join(timeout=self.timeout or 10)
```

---

## 16. Cache Serialization Overhead (NEW)

### 16.1 repr() vs JSON for Window Properties

**File:** `resources/lib/caches/meta_cache.py:102`

```python
set_property(prop_string, repr(cachedata))  # repr() less efficient than JSON
# Later retrieval requires:
cachedata = literal_eval(cachedata)  # Expensive parsing
```

**Problem:** `repr()` creates larger strings than JSON. Every property access requires full `literal_eval()` parse.

**Fix:** Use JSON serialization:
```python
import json
set_property(prop_string, json.dumps(cachedata))
# Faster retrieval:
cachedata = json.loads(get_property(prop_string))
```

### 16.2 Season Cache Key Mismatch

**File:** `resources/lib/caches/meta_cache.py:101, 129`

```python
# Storage uses:
prop_string = 'pov_meta_season_%s' % media_id

# But deletion looks for:
clear_property('pov_meta_season_%s_%s' % (media_id, item))  # Different format!
```

**Problem:** Season metadata cached with one key format, deletion uses different format = cache never invalidated.

---

## 17. Connection Cleanup Issues (NEW)

### 17.1 No Context Manager Support

**File:** `resources/lib/caches/__init__.py:23-24`

```python
def __init__(self):
    self.dbcon = database_connect(self.db_file, isolation_level=None)
    self.dbcur = self.dbcon.cursor()
    # NO __enter__, __exit__, or __del__ methods
```

**Problem:** Database connections never closed automatically.

**Fix:** Add context manager:
```python
def __enter__(self):
    return self

def __exit__(self, *args):
    self.dbcon.close()
```

### 17.2 Unclosed Connection in watched_cache

**File:** `resources/lib/caches/watched_cache.py:501-509`

```python
def clear_local_bookmarks():
    try:
        dbcon = _database_connect(...)
        dbcur = set_PRAGMAS(dbcon)
        # ... operations ...
    except: pass
    # Missing: dbcon.close()
```

---

## 18. Comprehensive Issue Count Summary

| Category | Issue Count | Top Priority |
|----------|-------------|--------------|
| O(n²) Algorithm Patterns | 7+ | CRITICAL |
| Database N+1 Queries | 6+ | CRITICAL |
| Connection/Resource Leaks | 30+ | HIGH |
| Threading Issues | 35+ | HIGH |
| Race Conditions | 4+ | HIGH |
| Caching Inefficiencies | 12+ | MEDIUM |
| String Operations | 8+ | MEDIUM |
| Collection Misuse | 10+ | MEDIUM |
| Missing Indexes | 4 | MEDIUM |
| **TOTAL** | **~120+** | - |

---

## 19. Files Requiring Most Attention (Updated)

| File | Issue Count | Critical Issues |
|------|-------------|-----------------|
| `modules/sources.py` | 18+ | O(n²), race conditions, threading |
| `caches/watched_cache.py` | 10+ | N+1, connection leaks |
| `fenom/source_utils.py` | 9+ | O(n²) loops, string ops |
| `caches/trakt_cache.py` | 8+ | Connection leaks, VACUUM |
| `caches/mdbl_cache.py` | 7+ | Connection leaks, VACUUM |
| `caches/meta_cache.py` | 6+ | Key mismatch, serialization |
| `modules/debrid.py` | 6+ | Fire-and-forget threads |
| `windows/extras.py` | 5+ | Threading, collection misuse |
| `indexers/tmdb_api.py` | 4+ | Thread join pattern |
| `modules/thumbnails.py` | 2 | N+1 query |

---

## 20. Recommended Fix Priority Order

### Phase 1: Critical (Immediate Impact)
1. Convert list membership checks to sets in `sources.py`
2. Fix N+1 queries in `thumbnails.py` and `kodi_utils.py`
3. Add threading locks for shared state in `sources.py`

### Phase 2: High Priority (Significant Impact)
4. Implement connection cleanup with context managers
5. Remove individual VACUUM calls, batch at end
6. Add missing database indexes
7. Fix thread pool limits and timeouts
8. Use dict-based lookups consistently in `watched_cache.py`

### Phase 3: Medium Priority (Moderate Impact)
9. Fix season cache key mismatch in `meta_cache.py`
10. Replace `next()` for single-item lookups
11. Pre-compute sort keys to avoid lambda calls
12. Fix string concatenation patterns
13. Replace fire-and-forget threads with tracked execution

### Phase 4: Low Priority (Polish)
14. Switch to JSON serialization for window properties
15. Add TTL cleanup for memory caches
16. Optimize TaskPool implementation
17. Cache repeated function calls like `ls()`

---

*Updated analysis generated on 2026-01-07*

---

## 21. Additional Discovered Issues (NEW)

### 21.1 Repeated List Comprehensions for Single Items in extras.py

**File:** `resources/lib/windows/extras.py:480, 485, 488`

```python
# Line 480: Finding next episode info
info = [i for i in ep_list if i['media_ids']['tmdb'] == self.tmdb_id][0]

# Line 485: Finding season data
curr_season_data = [i for i in season_data if i['season_number'] == current_season][0]

# Line 488: Another season lookup
info = [i for i in season_data if i['season_number'] == season][0]
```

**Problem:** Creates complete filtered lists just to extract the first matching item. With large episode/season lists, this is wasteful.

**Fix:** Use `next()` with generator:
```python
info = next((i for i in ep_list if i['media_ids']['tmdb'] == self.tmdb_id), None)
curr_season_data = next((i for i in season_data if i['season_number'] == current_season), None)
```

### 21.2 Redundant Translation Calls in discover.py

**File:** `resources/lib/indexers/discover.py`

```python
# Multiple calls to ls() for the same string IDs in menu building
ls(32451), ls(32592)  # Called repeatedly in movie() and tvshow() methods
```

**Problem:** Translation function called multiple times for same strings during menu construction.

**Fix:** Cache translation results at method start:
```python
str_discover = ls(32451)
str_similar = ls(32592)
# Then use cached values
```

### 21.3 Repeated filter_show_pack Range Building

**File:** `resources/lib/fenom/source_utils.py:370-530`

The `filter_show_pack()` function builds 14+ similar range lists in sequence:
```python
# Pattern repeated ~14 times:
to_season_ranges = []
season_count = 2
while season_count <= int(total_seasons):
    to_season_ranges.append(...)
    season_count += 1
if any(i in dot_release_title for i in to_season_ranges):
    keys = [i for i in to_season_ranges if i in dot_release_title]
```

**Problem:** Each range type (to, thru, dash, tilde, etc.) builds its own list and does its own iteration. These could be consolidated into a single pass with combined patterns.

### 21.4 Multiple JSON Dumps in extras.py

**File:** `resources/lib/windows/extras.py:354, 361`

```python
# Line 354: JSON dumps inside loop
listitem.setProperty('tikiskins.extras.all_images', json_all_images)

# Line 361: Computing same JSON multiple times
json_all_images = json.dumps([(tmdb_image_base % ('original', i['file_path']), '%sx%s' % (i['height'], i['width'])) for i in data])
```

**Fix:** The `json_all_images` is correctly computed once before the loop - this is already optimized.

### 21.5 Service Thread Join Pattern

**File:** `resources/lib/service.py:168`

```python
def __exit__(self, exc_type, exc_value, traceback):
    for i in self.threads: i.join()
```

**Status:** GOOD - Uses proper for loop instead of list comprehension for joining threads. This is the correct pattern.

### 21.6 Potential Infinite Loop in set_view_mode

**File:** `resources/lib/modules/kodi_utils.py:282-285`

```python
while not container_content() == content:
    hold += 1
    if hold < 5000: sleep(1)
    else: return
```

**Problem:** The loop sleeps for 1ms per iteration up to 5000 times (5 seconds). With 1ms sleep granularity, this could cause high CPU usage during the wait period.

**Fix:** Use larger sleep intervals:
```python
while not container_content() == content:
    hold += 1
    if hold < 50: sleep(100)  # 100ms intervals, 5 second timeout
    else: return
```

### 21.7 Unbounded Session History in episode_tools.py

**File:** `resources/lib/modules/episode_tools.py:25-36` (referenced in existing docs)

**Status:** Already documented in Section 6.2 - confirmed as unbounded window property accumulation.

---

## 22. Positive Patterns Found (Keep These)

The following good patterns were observed and should be maintained:

| Pattern | Location | Description |
|---------|----------|-------------|
| Pre-compiled regex | `fenom/source_utils.py:12-76` | 60+ patterns compiled at module level |
| Whitelist validation | `caches/meta_cache.py:13-14` | SQL injection prevention via frozenset |
| Context manager | `service.py:163-168` | Proper `__enter__`/`__exit__` for cleanup |
| Dict-based lookups | `watched_cache.py:66-73, 305-327` | O(1) lookup functions exist |
| TaskPool | `modules/utils.py:16-43` | Bounded thread pool implementation |
| Lazy imports | `router.py` | Modules imported only when needed |
| safe_eval | `caches/meta_cache.py:7` | Uses `literal_eval` instead of `eval` |

---

## 23. Summary Metrics

### Issues by Severity

| Severity | Count | Examples |
|----------|-------|----------|
| CRITICAL | 10+ | O(n²) algorithms, race conditions |
| HIGH | 30+ | N+1 queries, connection leaks, unbounded threads |
| MEDIUM | 40+ | Redundant operations, missing indexes |
| LOW | 20+ | String concatenation, minor optimizations |
| **TOTAL** | **100+** | - |

### Files by Issue Density

| File | Lines | Issues | Density |
|------|-------|--------|---------|
| `modules/sources.py` | 703 | 18+ | 1 per 39 lines |
| `fenom/source_utils.py` | 715 | 12+ | 1 per 60 lines |
| `caches/watched_cache.py` | 665 | 10+ | 1 per 67 lines |
| `windows/extras.py` | 771 | 8+ | 1 per 96 lines |
| `caches/trakt_cache.py` | 205 | 8+ | 1 per 26 lines |

---

## 24. Quick Reference: Top 10 Fixes by Impact

1. **Convert list membership to set** in `sources.py` - O(n²) → O(n)
2. **Use dict-based lookups** in `watched_cache.py` - Already implemented, needs wider adoption
3. **Add threading locks** for shared state in `sources.py` - Prevents data corruption
4. **Cap ThreadPoolExecutor size** in `sources.py:581` - Prevents thread explosion
5. **Remove VACUUM after each delete** in cache files - Batch at end only
6. **Fix N+1 queries** in `thumbnails.py`, `kodi_utils.py` - Use batch operations
7. **Use `next()` for single items** in `extras.py` - Avoids full list creation
8. **Add database connection cleanup** via context managers - Prevents leaks
9. **Pre-compute sort keys** to avoid `.index()` in sorts - O(n²) → O(n log n)
10. **Fix season cache key mismatch** in `meta_cache.py:101, 129` - Cache invalidation bug

---

*Final analysis completed on 2026-01-07*
