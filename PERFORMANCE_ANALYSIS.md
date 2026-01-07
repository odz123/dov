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
