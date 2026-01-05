# CLAUDE.md - AI Assistant Guide for POV Kodi Addon

This document provides guidance for AI assistants working with the POV codebase.

## Project Overview

**POV** (`plugin.video.pov`) is a Kodi video addon that aggregates and streams media content from multiple sources including torrent providers, debrid services, and cloud storage. It provides a unified interface for searching and playing movies and TV shows with integrated caching, metadata aggregation, and provider management.

- **Version**: 6.01.02
- **Language**: Python 3
- **Framework**: Kodi (XBMC)
- **License**: GNU GPL v3
- **Dependencies**: `script.module.requests` (Kodi addon)

## Directory Structure

```
/home/user/dov/
├── addon.xml              # Kodi addon manifest
├── settings.xml           # User settings schema
├── changelog.txt          # Version history
├── PERFORMANCE_ANALYSIS.md # Performance documentation
└── resources/
    └── lib/
        ├── router.py      # Main entry point - URL routing
        ├── service.py     # Background service (scheduled tasks)
        ├── caches/        # SQLite caching layer (10 modules)
        ├── debrids/       # Premium debrid service integrations (7 services)
        ├── indexers/      # Content discovery & metadata (18 modules)
        ├── magneto/       # Torrent/magnet scrapers (13 modules)
        ├── modules/       # Core business logic (19 modules)
        ├── scrapers/      # Cloud provider scrapers (8 modules)
        ├── fenom/         # Metadata extraction framework
        └── windows/       # Custom Kodi window classes
    ├── skins/             # UI definitions (XML templates)
    └── language/          # Localization strings
```

## Entry Points

### Plugin Entry (`router.py`)
The main plugin entry point. Routes all `plugin://` URLs to appropriate handlers:
- `navigator.*` - Main navigation menus
- `menu_editor.*` - Custom menu editing
- `discover.*` - Content discovery
- `build_*` - List building (movies, tvshows, episodes)
- `trakt.*`, `tmdb.*`, `mdblist.*` - API integrations
- `*debrid*` - Debrid service operations

### Background Service (`service.py`)
Runs scheduled tasks:
- Database initialization and maintenance
- Settings file management
- Subtitle cache cleanup
- Kodi library sync

## Coding Conventions

### Import Style
- Lazy imports inside functions/methods for performance
- Module-level imports only for frequently used utilities
```python
# Good - lazy import for routes
elif 'build_movie_list' in mode:
    from indexers.movies import Indexer
    Indexer(params).run()

# Module-level for utilities
from modules.kodi_utils import parse_qsl, logger
```

### Class Patterns
- Indexers use `Indexer` class with `.run()` method
- Debrids use `Indexer` class with `.run(params)` method
- Navigation uses class methods via `runmode()` helper:
```python
def runmode(cls, params, mode):
    call = getattr(cls(params), mode, None)
    return call() if callable(call) else None
```

### Parameter Handling
- Parameters passed as dictionaries from URL query strings
- Use `params_get = params.get` pattern for repeated access
```python
params_get = params.get
mode = params_get('mode', 'navigator.main')
```

### Database Operations
- All databases are SQLite, stored in `special://profile/addon_data/plugin.video.pov/`
- Key databases: `watched.db`, `metacache.db`, `traktcache4.db`, `maincache.db`
- Use `database_connect()` from `kodi_utils.py`
- Set PRAGMA optimizations for write-heavy operations

### Threading
- Use `TaskPool` class from `modules/utils.py` for bounded threading
- Avoid unbounded thread creation with `make_thread_list`
- Join threads with proper for loops (not list comprehensions)
```python
# Good
for t in threads:
    t.join()

# Bad - creates unnecessary list
[t.join() for t in threads]
```

### Caching
- Memory cache via Kodi window properties (`get_property`, `set_property`)
- Persistent cache via SQLite databases
- Use `ast.literal_eval()` instead of `eval()` for deserialization

### Error Handling
- Use `logger(heading, function)` for logging
- Context managers for Router class (`__enter__`, `__exit__`)

## Key Modules

### Core (`modules/`)
| Module | Purpose |
|--------|---------|
| `sources.py` | Source aggregation, filtering, selection |
| `dialogs.py` | User interface dialogs |
| `debrid.py` | Debrid service orchestration |
| `source_utils.py` | Source validation and metadata matching |
| `player.py` | Media playback control |
| `kodi_utils.py` | Kodi framework bindings |

### Indexers (`indexers/`)
| Module | Purpose |
|--------|---------|
| `navigator.py` | Main menu navigation structure |
| `discover.py` | Recommendation/discovery engine |
| `tmdb_api.py` | TMDB API integration |
| `trakt_api.py` | Trakt API integration |
| `metadata.py` | Unified metadata aggregation |
| `movies.py`, `tvshows.py`, `episodes.py` | Content indexing |

### Caches (`caches/`)
| Module | Purpose |
|--------|---------|
| `watched_cache.py` | Watched status tracking |
| `meta_cache.py` | Metadata caching with TTL |
| `trakt_cache.py` | Trakt API response caching |
| `debrid_cache.py` | Debrid availability caching |

### Debrids (`debrids/`)
Each service has two files:
- `*_api.py` - Low-level API wrapper
- `*.py` - Higher-level logic and UI

Supported: RealDebrid, Premiumize, AllDebrid, TorBox, Offcloud, EasyDebrid, Easynews

### Scrapers
- `magneto/` - Torrent scrapers (PirateBay, Torrentio, Zilean, Prowlarr, etc.)
- `scrapers/` - Cloud search scrapers (debrid cloud libraries)

## Common Tasks

### Adding a New Scraper
1. Create module in `resources/lib/magneto/` or `resources/lib/scrapers/`
2. Implement required interface (see existing scrapers for pattern)
3. Register in `settings.xml` for user configuration
4. Add to provider list in relevant modules

### Adding a New Debrid Service
1. Create `*_api.py` for API wrapper in `resources/lib/debrids/`
2. Create `*.py` for logic/UI in `resources/lib/debrids/`
3. Add settings in `settings.xml`
4. Add routing in `router.py`
5. Register in `modules/debrid.py`

### Modifying Navigation
- Edit `resources/lib/indexers/navigator.py`
- Use `Navigator` class methods
- Register new routes in `router.py` if needed

### Working with Metadata
- Primary source: TMDB (`indexers/tmdb_api.py`)
- Additional: Trakt, IMDb, MDBList, FanartTV
- Caching in `caches/meta_cache.py`

## Performance Guidelines

See `PERFORMANCE_ANALYSIS.md` for detailed analysis. Key optimizations applied:

1. **Dict-based lookups** - Converted O(n) list searches to O(1) dict lookups
2. **Pre-compiled regex** - 60+ patterns compiled at module level
3. **Safe deserialization** - `ast.literal_eval()` instead of `eval()`
4. **Proper thread joining** - For loops instead of list comprehensions

### Known Remaining Issues
- Database connection pooling not implemented
- Multiple filter passes in `sources.py` could be combined
- Unbounded thread creation in some areas

## Testing

No automated test suite exists. Testing is done through Kodi instance:
1. Install addon in Kodi
2. Test functionality through UI
3. Check Kodi log for errors (`upload_logfile` mode available)

## Important Files to Know

| File | Lines | Description |
|------|-------|-------------|
| `router.py` | 294 | All URL routing - understand this first |
| `modules/sources.py` | ~1000 | Core source aggregation logic |
| `caches/watched_cache.py` | ~600 | Watched status management |
| `fenom/source_utils.py` | ~800 | Title matching and validation |
| `modules/kodi_utils.py` | ~500 | Kodi API bindings |

## Code Style Notes

- **Indentation**: Tabs (not spaces)
- **Quotes**: Single quotes preferred for strings
- **Line length**: No strict limit, but reasonable
- **Comments**: Minimal inline comments
- **Docstrings**: Not commonly used
- **Type hints**: Not used

## Git Workflow

- Main development happens on feature branches
- Recent work focused on performance optimization and bug fixes
- Clean commits with descriptive messages

## Kodi-Specific Concepts

### Special Paths
```python
'special://profile/addon_data/plugin.video.pov/'  # User data
'special://home/addons/plugin.video.pov/'          # Addon install
```

### Window Properties
Used for inter-addon communication and memory caching:
```python
window.setProperty('pov_key', 'value')
window.getProperty('pov_key')
window.clearProperty('pov_key')
```

### Plugin URLs
Format: `plugin://plugin.video.pov/?mode=action&param=value`
Parsed via `parse_qsl(sys.argv[2][1:])`

### ListItems
Kodi UI elements built with `xbmcgui.ListItem`:
- Set properties with `.setProperty()`
- Set info with `.setInfo()`
- Set art with `.setArt()`

## Security Considerations

- Avoid `eval()` - use `ast.literal_eval()` for safe deserialization
- API keys stored in Kodi settings (user-provided)
- No sensitive data in repository
- External API calls should use proper timeouts
