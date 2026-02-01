# CLAUDE.md - AI Assistant Guide for POV Kodi Addon

This document provides guidance for AI assistants working with the POV codebase.

## Project Overview

**POV** (`plugin.video.pov`) is a Kodi video addon that aggregates and streams media content from multiple sources including torrent providers, debrid services, Stremio addons, and cloud storage. It provides a unified interface for searching and playing movies and TV shows with integrated caching, metadata aggregation, and provider management.

- **Version**: 6.01.02
- **Language**: Python 3
- **Framework**: Kodi (XBMC)
- **License**: GNU GPL v3
- **Dependencies**: `script.module.requests`, `script.module.urllib3` (Kodi addons)

## Directory Structure

```
/home/user/dov/
├── addon.xml                       # Kodi addon manifest
├── changelog.txt                   # Version history
├── CLAUDE.md                       # This file - AI assistant guide
├── PERFORMANCE_ANALYSIS.md         # Performance documentation
├── STREMIO_ADDON_RESEARCH.md       # Stremio integration research
├── API_AUDIT_REPORT.md             # API usage audit
├── IMPROVEMENT_RECOMMENDATIONS.md  # Improvement notes
├── README.md                       # Project readme
├── plugin.video.pov-6.01.02.zip   # Distributable addon package
├── pov.png                         # Addon icon
├── pov_fanart.png                  # Addon fanart
└── resources/
    ├── settings.xml                # User settings schema
    └── lib/
        ├── router.py               # Main entry point - URL routing (314 lines)
        ├── service.py              # Background service (247 lines)
        ├── caches/                 # SQLite caching layer (11 modules)
        ├── debrids/                # Premium debrid service integrations (7 services, 14 modules)
        ├── indexers/               # Content discovery & metadata (23 modules)
        ├── magneto/                # Torrent/magnet scrapers (14 modules)
        ├── modules/                # Core business logic (21 modules)
        ├── scrapers/               # Cloud provider scrapers (7 modules)
        ├── fenom/                  # Metadata extraction framework (9 modules)
        └── windows/                # Custom Kodi window classes (8 modules)
    ├── skins/                      # UI definitions (14 XML templates, 82 PNG assets)
    └── language/                   # Localization (English only)
```

**Total**: 109 Python modules + 8 `__init__.py` package markers.

## Entry Points

### Plugin Entry (`router.py`)
The main plugin entry point. Routes all `plugin://` URLs to appropriate handlers:
- `navigator.*` - Main navigation menus
- `menu_editor.*` - Custom menu editing
- `discover.*` - Content discovery
- `build_*` - List building (movies, tvshows, seasons, episodes, trakt, tmdb, mdblist, simkl)
- `trakt.*`, `tmdb.*`, `mdblist.*`, `simkl.*` - API integrations
- `easynews.*` - Easynews operations
- `*debrid*` - Debrid service operations (alldebrid, premiumize, real_debrid, torbox, offcloud, easydebrid)
- `stremio_*` - Stremio addon management, catalogs, subtitles, and debug
- `play_media` / `media_play` - Media playback initiation
- `*_settings`, `*_cache`, `*_view`, `*_image`, `*_text` - Utility operations
- `watched_unwatched_*` - Watched status management
- `history` / `search_history` - Search history
- `toggle_*` - Feature toggles

### Background Service (`service.py`)
Runs scheduled tasks:
- Database initialization and maintenance (3-day cycle)
- Settings file management and window properties
- Subtitle cache cleanup
- Kodi library sync
- View properties initialization
- Reuse language invoker checks

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
- Thread-local database connections used in metadata to avoid cross-thread SQLite errors

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

### HTTP Client
- `modules/http_client.py` provides centralized session management
- Chrome-like TLS fingerprinting to avoid Cloudflare 403 blocks
- Built-in retry logic and browser-like headers

### Caching
- Memory cache via Kodi window properties (`get_property`, `set_property`)
- Persistent cache via SQLite databases
- Use `ast.literal_eval()` instead of `eval()` for deserialization
- Configurable cache duration multipliers (Short/Standard/Long/Extended)

### Error Handling
- Use `logger(heading, function)` for logging
- Context managers for Router class (`__enter__`, `__exit__`)

## Key Modules

### Core (`modules/`)
| Module | Purpose |
|--------|---------|
| `sources.py` | Source aggregation, filtering, selection (772 lines) |
| `kodi_utils.py` | Kodi framework bindings (496 lines) |
| `debrid.py` | Debrid service orchestration |
| `dialogs.py` | User interface dialogs |
| `player.py` | Media playback control |
| `source_utils.py` | Source validation and metadata matching |
| `source_objects.py` | Source metadata, filename matching, and Stremio-aware metadata resolution |
| `stremio_manager.py` | Stremio addon configuration UI, debug loop |
| `stremio_subtitles.py` | Stremio subtitle handling |
| `http_client.py` | Centralized HTTP client with TLS fingerprinting and retry logic |
| `settings.py` | Settings helper functions |
| `utils.py` | General utilities, TaskPool, string cleaning, date handling |
| `cache.py` | Cache management and database cleanup |
| `downloader.py` | File download management |
| `menu_editor.py` | Custom menu editing logic |
| `menu_lists.py` | Menu list definitions |
| `meta_lists.py` | Metadata list building |
| `myservices.py` | Service authorization and referral links |
| `episode_tools.py` | Episode-related utilities |
| `thumbnails.py` | Kodi thumbnail cache cleaning |
| `dom_parser.py` | HTML DOM parsing utilities |

### Indexers (`indexers/`)
| Module | Purpose |
|--------|---------|
| `navigator.py` | Main menu navigation structure |
| `discover.py` | Recommendation/discovery engine |
| `metadata.py` | Unified metadata aggregation with Stremio support |
| `movies.py` | Movie list indexing |
| `tvshows.py` | TV show list indexing |
| `seasons.py` | Season/episode list building |
| `episodes.py` | Episode list indexing (next episodes, calendars) |
| `tmdb_api.py` | TMDB API integration |
| `tmdb.py` | TMDB list building (custom lists, watchlist, favorites) |
| `trakt_api.py` | Trakt API integration |
| `trakt.py` | Trakt list building and account info |
| `mdblist_api.py` | MDBList API integration |
| `mdblist.py` | MDBList list building and account info |
| `simkl.py` | Simkl account info |
| `simkl_api.py` | Simkl API integration |
| `imdb_api.py` | IMDb user lists and keyword results |
| `fanarttv_api.py` | FanartTV artwork API |
| `stremio_catalog.py` | Stremio addon catalog browsing |
| `stremio_meta.py` | Stremio metadata provider (converts Stremio meta to POV format) |
| `people.py` | Person search and data dialogs |
| `images.py` | Image browsing |
| `history.py` | Search history management |
| `subtitles.py` | Subtitle handling |

### Caches (`caches/`)
| Module | Purpose |
|--------|---------|
| `watched_cache.py` | Watched status tracking (769 lines) |
| `meta_cache.py` | Metadata caching with TTL |
| `trakt_cache.py` | Trakt API response caching |
| `debrid_cache.py` | Debrid availability caching |
| `mdbl_cache.py` | MDBList response caching |
| `main_cache.py` | General-purpose main cache |
| `favourites_cache.py` | User favorites storage |
| `navigator_cache.py` | Menu/navigation caching |
| `providers_cache.py` | Provider configuration cache |
| `undesirables_cache.py` | User-defined filter terms |
| `simkl_cache.py` | Simkl API response caching |

### Debrids (`debrids/`)
Each service has two files:
- `*_api.py` - Low-level API wrapper
- `*.py` - Higher-level logic and UI

Supported services:
| Service | Files |
|---------|-------|
| RealDebrid | `real_debrid.py`, `real_debrid_api.py` |
| Premiumize | `premiumize.py`, `premiumize_api.py` |
| AllDebrid | `alldebrid.py`, `alldebrid_api.py` |
| TorBox | `torbox.py`, `torbox_api.py` |
| Offcloud | `offcloud.py`, `offcloud_api.py` |
| EasyDebrid | `easydebrid.py`, `easydebrid_api.py` |
| Easynews | `easynews.py`, `easynews_api.py` |

### Scrapers

#### Magneto (Torrent Scrapers - `magneto/`)
| Module | Description |
|--------|-------------|
| `aiostreams.py` | AIOStreams (Comet/MediaFusion aggregator) |
| `animetosho.py` | AnimeTosho anime tracker |
| `bitmagnet.py` | Bitmagnet DHT indexer |
| `dmm.py` | DMM scraper |
| `nyaa.py` | Nyaa anime torrents |
| `piratebay.py` | The Pirate Bay |
| `prowlarr.py` | Prowlarr indexer manager |
| `stremio.py` | Generic Stremio addon scraper |
| `torboxnews.py` | TorBox usenet search |
| `torrentdownload.py` | TorrentDownload scraper |
| `torrentio.py` | Torrentio dedicated scraper |
| `torrentsdb.py` | TorrentsDB aggregator |
| `torz.py` | Torz/StremThru scraper |
| `zilean.py` | Zilean DMM scraper |

#### Cloud Scrapers (`scrapers/`)
| Module | Description |
|--------|-------------|
| `ad_cloud.py` | AllDebrid cloud storage |
| `easynews.py` | Easynews usenet |
| `folders.py` | Local folder scraper |
| `oc_cloud.py` | Offcloud cloud storage |
| `pm_cloud.py` | Premiumize cloud storage |
| `rd_cloud.py` | RealDebrid cloud storage |
| `tb_cloud.py` | TorBox cloud storage |

### Fenom (`fenom/`)
Metadata extraction and title matching framework:
| Module | Purpose |
|--------|---------|
| `source_utils.py` | Title matching, validation, quality detection (719 lines) |
| `cleantitle.py` | Title normalization utilities |
| `undesirables.py` | Content filtering definitions |
| `client.py` | HTTP client utilities |
| `cache.py` | Caching utilities |
| `speedtest.py` | Provider speed testing |
| `control.py` | Fenom control/configuration |
| `dom_parser.py` | DOM parsing utilities |
| `log_utils.py` | Logging utilities |

### Windows (`windows/`)
Custom Kodi window classes:
| Module | Purpose |
|--------|---------|
| `sources.py` | Source selection dialog |
| `extras.py` | Movie/show extras window |
| `next_episode.py` | Autoplay next episode dialog |
| `people.py` | Person information dialog |
| `imageviewer.py` | Image gallery viewer |
| `textviewer.py` | Text display window |
| `select_ok.py` | Selection dialogs |
| `videoplayer.py` | Custom video player window |

## Common Tasks

### Adding a New Scraper
1. Create module in `resources/lib/magneto/` or `resources/lib/scrapers/`
2. Implement required interface (see existing scrapers for pattern)
3. Register in `resources/settings.xml` for user configuration
4. Add to provider list in relevant modules

### Adding a New Debrid Service
1. Create `*_api.py` for API wrapper in `resources/lib/debrids/`
2. Create `*.py` for logic/UI in `resources/lib/debrids/`
3. Add settings in `resources/settings.xml`
4. Add routing in `router.py`
5. Register in `modules/debrid.py`

### Modifying Navigation
- Edit `resources/lib/indexers/navigator.py`
- Use `Navigator` class methods
- Register new routes in `router.py` if needed

### Working with Metadata
- Primary source: TMDB (`indexers/tmdb_api.py`)
- Additional: Trakt, IMDb, MDBList, Simkl, FanartTV
- Stremio metadata: `indexers/stremio_meta.py` converts Stremio addon metadata to POV format
- Caching in `caches/meta_cache.py` with thread-local connections
- Cache duration is configurable (Short/Standard/Long/Extended multipliers)

### Working with Stremio Addons
- Manager: `modules/stremio_manager.py` - Add/remove/configure addons, debug loop
- Scraper: `magneto/stremio.py` - Fetch streams from addons
- Catalogs: `indexers/stremio_catalog.py` - Browse addon catalogs
- Metadata: `indexers/stremio_meta.py` - Fetch/convert addon metadata
- Subtitles: `modules/stremio_subtitles.py` - Fetch/manage subtitles
- Source objects: `modules/source_objects.py` - Stremio-aware source metadata resolution
- See `STREMIO_ADDON_RESEARCH.md` for protocol details

## Performance Guidelines

See `PERFORMANCE_ANALYSIS.md` for detailed analysis. Key optimizations applied:

1. **Dict-based lookups** - Converted O(n) list searches to O(1) dict lookups
2. **Pre-compiled regex** - 60+ patterns compiled at module level (see `modules/utils.py`)
3. **Safe deserialization** - `ast.literal_eval()` instead of `eval()`
4. **Proper thread joining** - For loops instead of list comprehensions
5. **Thread-local DB connections** - Avoids cross-thread SQLite ProgrammingError in `indexers/metadata.py`
6. **Centralized HTTP client** - Session reuse and TLS fingerprinting in `modules/http_client.py`

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
| `router.py` | ~314 | All URL routing - understand this first |
| `modules/sources.py` | ~772 | Core source aggregation logic |
| `caches/watched_cache.py` | ~769 | Watched status management |
| `fenom/source_utils.py` | ~719 | Title matching and validation |
| `modules/kodi_utils.py` | ~496 | Kodi API bindings |
| `service.py` | ~247 | Background service tasks |

## Code Style Notes

- **Indentation**: Tabs (not spaces)
- **Quotes**: Single quotes preferred for strings
- **Line length**: No strict limit, but reasonable
- **Comments**: Minimal inline comments
- **Docstrings**: Not commonly used (some newer modules like `http_client.py` and `stremio_meta.py` include module-level docstrings)
- **Type hints**: Not used

## Git Workflow

- Main development happens on feature branches
- Recent work focused on:
  - Stremio addon integration (SDK compliance, parallel scraping, metadata provider)
  - Performance optimization (thread-local DB connections, HTTP client centralization)
  - MDBList integration
  - Simkl integration (anime calendar, account info)
  - TMDB custom lists, watchlist, favorites
  - New scraper additions (aiostreams, bitmagnet, prowlarr, torrentsdb)
  - HTTP client with TLS fingerprinting for Cloudflare avoidance
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

## Router Modes Reference

Key routing modes in `router.py`:
```
navigator.*              - Main navigation menus
menu_editor.*            - Custom menu editing
discover.*               - Content discovery
build_movie_list         - Movie list building
build_tvshow_list        - TV show list building
build_season_list        - Season list building
build_episode_list       - Episode list building
build_in_progress_episode - In-progress episode list
build_next_episode       - Next episode list
build_my_calendar        - Calendar episode list
build_my_anime_calendar  - Anime calendar list
build_anime_calendar     - Anime calendar list
build_navigate_to_page   - Pagination navigation
build_popular_people     - Popular people list
build_trakt_list         - Trakt list building
build_mdb_list           - MDBList list building
build_simkl_list         - Simkl list building
build_tmdb_list          - TMDB list building
imdb_build_user_lists    - IMDb user lists
imdb_build_keyword_results - IMDb keyword results
play_media               - Media playback (source select)
media_play               - Direct media URL playback
trakt.*                  - Trakt API operations
tmdb.*                   - TMDB API operations
mdblist.*                - MDBList API operations
simkl.*                  - Simkl API operations
easynews.*               - Easynews operations
alldebrid*               - AllDebrid operations
premiumize*              - Premiumize operations
real_debrid*             - RealDebrid operations
torbox*                  - TorBox operations
offcloud*                - Offcloud operations
easydebrid*              - EasyDebrid operations
*_settings               - Settings operations
*_cache                  - Cache operations
*_view                   - View operations
*_image                  - Image operations
*_text                   - Text display
*choice*                 - Dialog choices (scraper color, quality, sorting, etc.)
watched_unwatched_*      - Watched/unwatched status toggling
toggle_*                 - Feature toggles
*history*                - Search history
stremio_addon_manager    - Stremio addon management
stremio_catalog          - Stremio catalog browsing
stremio_clear_subtitles  - Clear subtitle cache
stremio_reconfigure_debrid - Reconfigure addon debrid settings
stremio_debug_loop       - Stremio addon debug loop
manual_add_nzb_to_cloud  - Manual NZB upload to cloud
refer_link               - Service referral links
myservices               - Service authorization
downloader               - File download
clean_databases          - Database cleanup
clean_thumbnails         - Thumbnail cache cleanup
upload_logfile           - Upload Kodi log
speedTest                - Provider speed test
undesirablesInput        - Add user-defined filter terms
undesirablesUserRemove   - Remove user-defined filter terms
```

## Security Considerations

- Avoid `eval()` - use `ast.literal_eval()` for safe deserialization
- API keys stored in Kodi settings (user-provided)
- No sensitive data in repository
- External API calls should use proper timeouts
- HTTP client uses Chrome-like TLS fingerprinting (not for evasion, for compatibility)

## Recent Changelog Highlights (v6.01)

- Changed aiostreams to include only comet/mediafusion
- Removed elfhosted (disabled direct options)
- Updated zilean scraper
- Fixed AllDebrid integration
- Added shuffle option for lists
- Added Sort to Top settings for HEVC, HDR, Dolby Vision, AV1
- MDBList Collection list and caching
- MDBList watched status and resume progress
- Added bitmagnet, prowlarr scrapers
- Stremio SDK addon support with parallel scraping and SDK compliance
- Centralized HTTP client with TLS fingerprinting
