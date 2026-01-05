# Performance Anti-Pattern Analysis

This document identifies performance issues found in the POV Kodi addon codebase.

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

---

## 2. Inefficient Nested Loop Algorithms (MEDIUM-HIGH IMPACT)

### 2.1 O(n*m) Title Matching in source_utils.py

**File:** `resources/lib/fenom/source_utils.py:132-176`

```python
def check_title(title, aliases, release_title, hdlr, year, years=None):
    if all(cleantitle.get(i) != cleantitle.get(t) for i in title_list):  # O(n)
        return False
```

**Problem:** For each source being checked, this iterates through all aliases. With 500 sources and 10 aliases, this is 5000 comparisons.

### 2.2 Expensive Regex Compilation in Loops

**File:** `resources/lib/fenom/source_utils.py:277-515`

The `filter_show_pack()` function creates multiple regex patterns inside loops:

```python
season_regex += tuple([r'complete[.-]%s[.-]season' % x for x in season_ordinal_list])  # 25 items
season_regex += tuple([r'complete[.-]%s[.-]season' % x for x in season_ordinal2_list])  # 25 items
# Then loops through all regex patterns for each release title
```

**Problem:** Regex patterns are created dynamically for every call. They should be pre-compiled once at module load.

### 2.3 Repeated String Operations

**File:** `resources/lib/fenom/source_utils.py:348-502`

Multiple while loops build range lists for every title checked:

```python
while season_count <= int(total_seasons):
    dot_season_ranges.append(all_seasons + '.and.%s' % str(season_count))
    all_seasons += '.%s' % str(season_count)  # String concatenation in loop
```

**Problem:** These range lists are rebuilt for every release title check but depend only on `total_seasons`, not the specific release.

---

## 3. String Concatenation Anti-Patterns (LOW-MEDIUM IMPACT)

### 3.1 String Concatenation with +=

**Multiple files use string building with `+=` instead of join:**

**File:** `resources/lib/fenom/source_utils.py:351-358`
```python
all_seasons += '.%s' % str(season_count)  # Creates new string object each iteration
```

**File:** `resources/lib/indexers/tmdb_api.py`, `resources/lib/modules/dialogs.py`
String concatenation in loops creating unnecessary intermediate objects.

### 3.2 Inefficient URL Building

**File:** `resources/lib/magneto/*.py` files
```python
url = '%s%s' % (self.base_link, self.movieSearch_link % imdb)
```

While not critical, using f-strings or `.format()` would be more readable and slightly faster.

---

## 4. API Calls in Loops (HIGH IMPACT)

### 4.1 Sequential External API Calls

**File:** `resources/lib/modules/debrid.py:282-335`

```python
def mfn_check_cache(imdb, season, episode, collector):
    results = session.get(url, timeout=7.05)  # Individual HTTP request
```

These functions are called sequentially from `external_check_cache()`. While some parallelism exists via threading, the pattern of making multiple HTTP calls per debrid check adds latency.

### 4.2 Thread Joining Pattern

**File:** `resources/lib/caches/watched_cache.py:196-197`

```python
threads = list(make_thread_list(_process, prelim_data, Thread))
[i.join() for i in threads]  # Blocks until ALL threads complete
```

**Problem:** List comprehension creates unnecessary list just to wait for threads. Also blocks on all threads serially instead of waiting for them in parallel.

**Better pattern:**
```python
for t in threads:
    t.join()
```

---

## 5. Caching Inefficiencies (MEDIUM IMPACT)

### 5.1 eval() for Cache Deserialization

**File:** `resources/lib/caches/meta_cache.py:40,80,105`

```python
meta, expiry = eval(cache_data[0]), cache_data[1]  # SECURITY RISK + SLOW
```

**Problem:**
1. `eval()` is significantly slower than `json.loads()`
2. Security vulnerability - arbitrary code execution if cache is corrupted

**Solution:** Use `json.loads()` instead of `eval()`, store data as JSON.

### 5.2 No Connection Pooling for Database

**File:** `resources/lib/caches/watched_cache.py:21-22`

```python
def _database_connect(database_file):
    return kodi_utils.database_connect(database_file, timeout=timeout, isolation_level=None)
```

New database connections are created for each operation instead of using a connection pool.

### 5.3 PRAGMA Settings Per Connection

**File:** `resources/lib/caches/watched_cache.py:24-28`

```python
def set_PRAGMAS(dbcon):
    dbcur = dbcon.cursor()
    dbcur.execute("""PRAGMA synchronous = OFF""")
    dbcur.execute("""PRAGMA journal_mode = OFF""")
```

These PRAGMA settings are reset on every connection. They should be set once at database initialization.

---

## 6. Memory & Object Creation Issues (LOW-MEDIUM IMPACT)

### 6.1 Unnecessary List Creation

**File:** `resources/lib/modules/sources.py:128`
```python
if self.providers: [i.join() for i in self.threads]
```

Creates a list just to iterate - should be a simple for loop or generator.

### 6.2 Property String Building in Loops

**File:** `resources/lib/caches/meta_cache.py:116-117`
```python
def delete_all_seasons_memory_cache(self, media_id):
    for item in range(1, 51): clear_property('pov_meta_season_%s_%s' % (string(media_id), string(item)))
```

String formatting 50 times when a single batch clear could work.

---

## 7. Threading Issues (MEDIUM IMPACT)

### 7.1 Unbounded Thread Creation

**File:** `resources/lib/modules/utils.py:48-52`

```python
def make_thread_list(_target, _list, _thread):
    for item in _list:
        threaded_object = _thread(target=_target, args=(item,))
        threaded_object.start()  # Starts immediately, no limit
```

**Problem:** Creates as many threads as items in the list. With 500 sources, this creates 500 threads.

**Good pattern exists at:** `TaskPool` class (lines 16-43) which limits thread count - but not used consistently.

### 7.2 Race Conditions

**File:** `resources/lib/caches/watched_cache.py:326-328`
```python
episodes = {}
for i in insert_list:
    episodes[i[2]] = episodes.get(i[2], []).append({'number': i[3]})
```

**Bug:** `.append()` returns `None`, so this always sets values to `None`.

---

## 8. Algorithm Complexity Issues (MEDIUM IMPACT)

### 8.1 Sorting After Filtering

**File:** `resources/lib/indexers/movies.py:150`
```python
self.items.sort(key=lambda k: int(k[1].getProperty('pov_sort_order')))
```

Sorting happens after building items. If items are already ordered during insertion, this would be unnecessary.

### 8.2 Multiple Passes Over Same Data

**File:** `resources/lib/modules/sources.py:149-163`
```python
results = self.filter_results(results)
results = self.sort_results(results)
results = self._special_filter(results, hevc_filter_key, self.filter_hevc)
results = self._special_filter(results, hdr_filter_key, self.filter_hdr)
results = self._special_filter(results, dolby_vision_filter_key, self.filter_dv)
results = self._special_filter(results, av1_filter_key, self.filter_av1)
```

**Problem:** 6 separate passes through the results list. These could be combined into a single pass.

---

## Priority Summary

| Issue | Impact | Effort to Fix |
|-------|--------|---------------|
| N+1 watched status lookups | HIGH | LOW |
| eval() in cache | HIGH (security) | LOW |
| Regex recompilation | MEDIUM-HIGH | LOW |
| Unbounded thread creation | MEDIUM-HIGH | LOW |
| Multiple list passes | MEDIUM | MEDIUM |
| String concatenation in loops | LOW-MEDIUM | LOW |
| Database connection pooling | MEDIUM | HIGH |

---

## Quick Wins (Recommended First)

1. **Convert watched_info to dict** - Single change, biggest impact
2. **Replace eval() with json.loads()** - Security + performance
3. **Pre-compile regex patterns** - Move to module level constants
4. **Use TaskPool consistently** - Already exists, just needs wider adoption
5. **Combine filter passes** - Single iteration over results

---

*Analysis generated on 2026-01-05*
