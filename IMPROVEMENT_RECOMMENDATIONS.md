# POV Kodi Addon - Improvement Recommendations

Comprehensive analysis of the codebase identifying areas for improvement, organized by priority and category.

---

## Priority 1: High Impact

### 1.1 Replace Bare `except: pass` Clauses (250 occurrences across 58 files)

**Problem:** The codebase has 250 `except: pass` blocks that silently swallow all exceptions, including `SystemExit`, `KeyboardInterrupt`, and programming errors like `NameError` or `TypeError`. This makes debugging extremely difficult and can hide real failures.

**Worst offenders:**
| File | Count | Risk |
|------|-------|------|
| `windows/extras.py` | 24 | UI state corruption |
| `indexers/metadata.py` | 22 | Silent metadata fetch failures |
| `indexers/imdb_api.py` | 17 | Silent API failures |
| `windows/people.py` | 7 | UI state corruption |
| `caches/main_cache.py` | 7 | Data loss |
| `caches/trakt_cache.py` | 6 | Data loss |
| `modules/source_utils.py` | 7 | Source matching failures hidden |
| `modules/source_objects.py` | 6 | Source processing failures hidden |
| `fenom/client.py` | 6 | Network failures hidden |

Additionally, 35 `except Exception: pass` blocks in critical modules like `sources.py` (10), `player.py` (16), and `watched_cache.py` (6) hide failures in core playback and data persistence logic.

**Recommendation:**
- Catch specific exceptions: `(ValueError, TypeError, KeyError)` for data parsing, `(requests.ConnectionError, requests.Timeout)` for network, `sqlite3.Error` for database
- At minimum, replace bare `except` with `except Exception` to avoid catching `SystemExit`/`KeyboardInterrupt`
- Add `logger()` calls in critical paths (watched status, playback, cache writes)
- `except: pass` is acceptable only in cleanup/teardown code (e.g., `close()` methods, dialog dismissal)

**Example fix for `player.py:196`:**
```python
# Before
except Exception: pass

# After
except Exception as e:
    logger('POVPlayer.onPlayBackStopped', str(e))
```

---

### 1.2 Fire-and-Forget Threads Without Lifecycle Management

**Problem:** `modules/player.py` creates 7+ threads via `Thread(target=...).start()` with no tracking, no exception handling, and no shutdown coordination. If any thread raises, the exception is lost silently.

**Locations:**
- `player.py:191` - `run_media_watched` thread
- `player.py:213` - `execute_nextep` thread
- `player.py:220` - `execute_nextep` thread (random continual)
- `player.py:229` - `Subtitles().get` thread
- `player.py:237` - `getStingers` thread
- `player.py:245` - `mdbl_scrobble` start thread
- `player.py:253` - `mdbl_scrobble` stop thread
- `sources.py:755` - Progress dialog thread

**Recommendation:** Apply the same pattern already used in `windows/extras.py:38-77` which tracks threads as daemon threads with exception handling:

```python
def _safe_thread(self, target, *args):
    def wrapper():
        try:
            target(*args)
        except Exception as e:
            logger('POVPlayer thread', str(e))
    t = Thread(target=wrapper, daemon=True)
    t.start()
    return t
```

---

### 1.3 Input Validation at System Boundaries

**Problem:** URL parameters from `plugin://` URLs are used with minimal validation. While this is a local addon (not internet-facing), malformed parameters can cause confusing failures deep in the call stack.

**Locations:**
- `sources.py:58-63` - `int()` conversion without bounds checking:
  ```python
  self.season = int(params_get('season')) if 'season' in self.params else ''
  self.episode = int(params_get('episode')) if 'episode' in self.params else ''
  ```
- `router.py:18-19` - Parameters parsed but not validated before dispatch
- Various debrid API modules - Settings values used directly in API URLs

**Recommendation:** Add lightweight validation at the router entry point:
```python
def _safe_int(value, default=0, min_val=0, max_val=9999):
    try:
        v = int(value)
        return v if min_val <= v <= max_val else default
    except (ValueError, TypeError):
        return default
```

---

## Priority 2: Medium Impact

### 2.1 Inconsistent Database Connection Management

**Problem:** A `ConnectionPool` class exists in `caches/__init__.py:21-84` and `BaseCache` uses it, but several modules still create connections directly:

- `modules/cache.py:28-104` - Multiple direct `database_connect()` calls
- `modules/kodi_utils.py:245-312` - Direct connections for view management
- `modules/thumbnails.py` - Direct connections

**Recommendation:** Migrate all database access to use the `ConnectionPool` through `BaseCache`, or at minimum through the `get_connection()`/`return_connection()` wrapper functions. This ensures connection reuse and prevents resource leaks from unclosed connections.

---

### 2.2 Debrid API Code Duplication

**Problem:** All 7 debrid API modules (`real_debrid_api.py`, `premiumize_api.py`, `alldebrid_api.py`, `torbox_api.py`, `offcloud_api.py`, `easydebrid_api.py`, `easynews_api.py`) duplicate the same patterns:

- Session setup with `HTTPAdapter(max_retries=1)`
- Request/response handling with timeout
- Token refresh logic
- Error notification pattern

**Example of repeated pattern (in every debrid API):**
```python
session = requests.Session()
session.mount('https://...', requests.adapters.HTTPAdapter(max_retries=1))
timeout = 10.0

def _request(self, method, path, ...):
    try: response = session.request(...)
    except (ConnectionError, Timeout): return notification(...)
```

**Recommendation:** Extract a `BaseDebridAPI` class:
```python
class BaseDebridAPI:
    base_url = ''
    timeout = 10.0
    max_retries = 1

    def __init__(self):
        self.session = requests.Session()
        self.session.mount(self.base_url, HTTPAdapter(max_retries=self.max_retries))

    def _request(self, method, path, data=None, params=None):
        # Shared request logic, token refresh, error handling
```

This would reduce ~50 lines of duplicated code per module and make retry/timeout changes a single-point update.

---

### 2.3 SQL String Formatting

**Problem:** Several modules use Python string formatting for SQL query construction instead of parameterized queries:

- `modules/cache.py:163-165` - `%d` formatting for LIMIT/OFFSET
- `modules/thumbnails.py:43` - `%s` formatting for IN clause placeholders
- `caches/main_cache.py:81,85` - LIKE queries with string formatting
- `caches/watched_cache.py:580` - Dynamic table name in DELETE

While these are mostly integer-only or internally-generated values (low injection risk), inconsistency makes the codebase harder to audit.

**Recommendation:** Use parameterized queries (`?` placeholders) consistently for all dynamic values. For dynamic table/column names (which can't use `?`), use a whitelist:
```python
VALID_TABLES = {'watched', 'progress', 'hidden'}
if table not in VALID_TABLES:
    raise ValueError(f'Invalid table: {table}')
```

---

### 2.4 Memory Accumulation in Long Sessions

**Problem:** Several patterns can accumulate memory over long Kodi sessions:

1. **Bookmark loading** (`watched_cache.py:83`) - Loads ALL bookmarks into memory at once:
   ```python
   return {(i[0], i[3], i[4]): (...) for i in result.fetchall()}
   ```
   For large libraries (10K+ episodes), this is significant.

2. **Episode history** (`modules/episode_tools.py:25-36`) - Window property `episode_history` grows indefinitely without cleanup or size bounds.

3. **Season metadata cache** (`caches/meta_cache.py:128`) - Only clears seasons 1-50, leaking entries for shows with 50+ seasons.

**Recommendation:**
- Paginate or filter bookmark loading by recently-accessed media
- Cap episode history at a reasonable size (e.g., 100 entries) with FIFO eviction
- Use dynamic season range based on actual data rather than hardcoded limit

---

### 2.5 HTTP Retry Configuration

**Problem:** All debrid APIs use `max_retries=1` which may be insufficient for unreliable networks (mobile, VPN, etc.). There is no exponential backoff configured.

**Locations:** Every debrid API module's session setup (e.g., `real_debrid_api.py:10`).

**Recommendation:** Increase to 2-3 retries with backoff:
```python
from urllib3.util.retry import Retry
retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retry))
```

---

## Priority 3: Low Impact / Code Quality

### 3.1 Magic Numbers and Hardcoded Values

**Problem:** Window IDs, database version mappings, language string IDs, and other constants are scattered as literals throughout the code:

- `windows/imageviewer.py:16,110,156` - Window control IDs: 2000, 2020, 5000
- `modules/kodi_utils.py:40` - `myvideos_db_paths = {19: '119', 20: '121', 21: '131', 22: '139'}`
- `modules/dialogs.py:296` - Language string range 2050-2062
- `modules/dialogs.py:614` - `range(97,123)` for lowercase ASCII
- `service.py:6` - `DATABASE_MAINTENANCE_INTERVAL = 259200`

**Recommendation:** Group related constants at module level with descriptive names. A dedicated `constants.py` is optional but would centralize commonly-referenced values.

---

### 3.2 Large Functions

**Problem:** Several functions exceed 100 lines, making them harder to test, debug, and modify:

| Function | File | Approx Lines |
|----------|------|-------------|
| `results()` | `sources.py:629-676` | ~50 (acceptable) |
| `wait()` | `sources.py:678-711` | ~35 (acceptable) |
| `get_sources()` | `sources.py:89-200+` | ~110+ (should split) |
| Title matching | `fenom/source_utils.py:250-400+` | ~150+ (should split) |
| Playback control | `player.py:150-300+` | ~150+ (should split) |

**Recommendation:** Extract logical subsections into named helper methods. For example, `get_sources()` could separate prescrape, external scrape, and internal scrape phases into distinct methods.

---

### 3.3 Redundant Operations

**Minor inefficiencies:**

1. `sources.py:625-626` - `resolutions.split()` called twice:
   ```python
   self.internal_resolutions = dict.fromkeys(resolutions.split(), 0)
   self.resolutions = dict.fromkeys(resolutions.split(), 0)
   ```
   Should call `.split()` once and reuse.

2. `sources.py:750` - `list(set(pr_list))` creates a set then immediately converts back to list. If the result is only used for `in` checks, keep as set.

3. Inconsistent HTTP session usage in magneto scrapers - some create module-level sessions (prowlarr, torrentsdb), others create per-request (bitmagnet, dmm).

---

### 3.4 Lazy Import Inconsistency

**Problem:** `router.py` correctly uses lazy imports for performance, but `sources.py` loads heavy modules at import time (lines 1-13), including scrapers, debrid modules, and window classes. Since `sources.py` is imported for every scrape operation, this is acceptable but not optimal.

**Recommendation:** Consider deferring imports of `scrapers.folders`, `windows`, and `modules.player` to method level in `sources.py` since not all code paths use all imports.

---

### 3.5 ReDoS Risk in Regex Patterns

**Problem:** `fenom/source_utils.py` has 60+ pre-compiled regex patterns (good for performance). Some season-range patterns (`lines 51-68`) operate on user-provided torrent titles that could theoretically be crafted to cause catastrophic backtracking.

**Recommendation:** Add a string length check before regex matching:
```python
if len(title) > 500:
    return None  # Skip unreasonably long titles
```

---

## Summary Matrix

| # | Issue | Priority | Effort | Files Affected |
|---|-------|----------|--------|----------------|
| 1.1 | Bare except clauses | HIGH | Medium | 58 files, 250 occurrences |
| 1.2 | Fire-and-forget threads | HIGH | Low | player.py, sources.py |
| 1.3 | Input validation | HIGH | Low | router.py, sources.py |
| 2.1 | DB connection consistency | MEDIUM | Medium | cache.py, kodi_utils.py, thumbnails.py |
| 2.2 | Debrid API duplication | MEDIUM | Medium | 7 debrid API modules |
| 2.3 | SQL string formatting | MEDIUM | Low | cache.py, thumbnails.py, main_cache.py |
| 2.4 | Memory accumulation | MEDIUM | Low | watched_cache.py, episode_tools.py, meta_cache.py |
| 2.5 | HTTP retry config | MEDIUM | Low | 7 debrid API modules |
| 3.1 | Magic numbers | LOW | Low | Scattered |
| 3.2 | Large functions | LOW | Medium | sources.py, source_utils.py, player.py |
| 3.3 | Redundant operations | LOW | Low | sources.py |
| 3.4 | Lazy import gaps | LOW | Low | sources.py |
| 3.5 | ReDoS risk | LOW | Low | fenom/source_utils.py |

## What's Already Done Well

The codebase has several strong patterns that should be maintained:

- **Pre-compiled regex** - 60+ patterns at module level in `fenom/source_utils.py`
- **Dict/set-based O(1) lookups** - Applied throughout `sources.py` and `watched_cache.py`
- **Connection pooling** - `ConnectionPool` class in `caches/__init__.py` with thread-safe reuse
- **Lazy imports in router** - `router.py` defers all heavy imports to point of use
- **Context managers** - `BaseCache`, `Router`, and `POVMonitor` use `__enter__`/`__exit__`
- **Safe deserialization** - Consistent `ast.literal_eval()` instead of `eval()`
- **Bounded threading** - `TaskPool` and `make_thread_list()` in `modules/utils.py`
- **Batch DB operations** - `executemany()` used in cache modules
- **Session reuse** - Module-level `requests.Session()` in debrid APIs and some scrapers
