# Stremio Addon Integration Research for POV

## Executive Summary

This document details research on integrating Stremio addons as a playback service for the POV Kodi addon. The POV codebase already has basic Stremio integration (`stremio.py`, `torrentio.py`, `stremio_manager.py`), but there are opportunities to enhance this into a more comprehensive streaming service.

---

## 1. Stremio Addon Protocol Overview

### Protocol Structure
Stremio addons follow a REST-like API pattern:

```
Base URL: https://addon-domain.com
Manifest:  /manifest.json
Streams:   /stream/{type}/{id}.json
Catalog:   /catalog/{type}/{id}.json
Meta:      /meta/{type}/{id}.json
Subtitles: /subtitles/{type}/{id}.json
```

### Supported Content Types
- `movie` - Movies (ID format: IMDB ID e.g., `tt1234567`)
- `series` - TV Shows (ID format: `{imdb_id}:{season}:{episode}`)
- `channel` - Live TV channels
- `tv` - Traditional TV

### Stream Object Properties

| Property | Description |
|----------|-------------|
| `url` | Direct HTTP(S) stream URL |
| `infoHash` | Torrent info hash |
| `fileIdx` | File index in torrent |
| `ytId` | YouTube video ID |
| `nzbUrl` | Usenet NZB file URL |
| `externalUrl` | External link (Netflix, etc.) |
| `name` | Stream quality/source identifier |
| `description` | Stream description |
| `subtitles` | Array of subtitle objects |
| `behaviorHints` | Playback behavior hints |

### behaviorHints Object

```json
{
  "notWebReady": true,
  "bingeGroup": "addon-720p",
  "proxyHeaders": {
    "request": {"Authorization": "Bearer xxx"},
    "response": {"Content-Type": "video/mp4"}
  },
  "filename": "Movie.2024.1080p.WEB-DL.mkv",
  "videoSize": 2147483648
}
```

---

## 2. Current POV Implementation

### Existing Files

| File | Purpose |
|------|---------|
| `resources/lib/magneto/stremio.py` | Generic Stremio addon scraper |
| `resources/lib/magneto/torrentio.py` | Dedicated Torrentio scraper |
| `resources/lib/modules/stremio_manager.py` | UI for managing addons |

### Current Capabilities

1. **Addon Management** (`stremio_manager.py`):
   - Add/remove Stremio addons by URL
   - Validate addon manifests
   - Test addon connections
   - Store addon configs in Kodi settings

2. **Stream Scraping** (`stremio.py`):
   - Fetch streams from configured addons
   - Extract torrent hashes (`infoHash`)
   - Extract direct URLs (`url`)
   - Parse metadata (seeders, size, quality)
   - Title validation and filtering

3. **Torrentio Support** (`torrentio.py`):
   - Dedicated scraper for Torrentio
   - Hardcoded base URL: `https://torrentio.strem.fun`
   - Torrent-only (hash extraction)

### Current Limitations

1. **Limited Direct URL Handling**: Focus is primarily on torrent hashes
2. **No proxyHeaders Support**: Can't handle authenticated streams
3. **No Subtitle Integration**: Ignores subtitles resource
4. **No Catalog Support**: Only uses stream resource
5. **No Debrid-Integrated Addons**: Doesn't leverage addons that return pre-resolved debrid links
6. **No bingeGroup Support**: Missing autoplay optimization

---

## 3. Popular Stremio Addons (2025)

### Torrent-Based Addons

| Addon | Description | Debrid Support |
|-------|-------------|----------------|
| **Torrentio** | Most popular, scrapes major torrent sites | RD, PM, AD, TB, OC |
| **Torrentio Lite** | Lighter version, faster loading | Same as Torrentio |
| **Comet** | New favorite, debrid-focused | RD, AD |
| **MediaFusion** | All-in-one, includes non-torrent links | RD, PM, AD |
| **Annatar** | Fast search (< 3s), fanout queries | RD, PM |
| **TorrentsDB** | Supports many debrid services | All major services |

### Debrid-Integrated Addons

| Addon | Description |
|-------|-------------|
| **Deflix** | Automatically converts torrents to cached HTTP streams via debrid |
| **AIOStreams** | Consolidates multiple addons with debrid, supports proxy |
| **AutoStream** | Debrid integration with episode preloading |
| **Stremio Real-Debrid Addon** | Stream RD cloud files directly |

### Addon URLs

```
Torrentio:           https://torrentio.strem.fun
Torrentio (config):  https://torrentio.strem.fun/configure
MediaFusion:         https://mediafusion.elfhosted.com
Comet:               https://comet.elfhosted.com
AIOStreams:          https://aiostreams.stremio.ru
```

---

## 4. Enhanced Integration Opportunities

### 4.1 Direct URL Playback Enhancement

Current `stremio.py` has basic support but could be improved:

```python
# Current handling
if stream_info['hash']:
    url = 'magnet:?xt=urn:btih:%s&dn=%s' % (hash, name)
    is_direct = False
else:
    url = stream_info['url']
    is_direct = True
```

**Enhancements needed:**
- Better handling of direct HTTP streams
- Support for streams with authentication headers
- Quality detection from direct URLs
- Proper Content-Type handling

### 4.2 proxyHeaders Support

For addons requiring authenticated streams:

```python
def _build_stream_url(self, stream):
    """Handle streams with proxy headers"""
    behavior_hints = stream.get('behaviorHints', {})
    proxy_headers = behavior_hints.get('proxyHeaders', {})

    if proxy_headers.get('request'):
        # Store headers for playback
        return {
            'url': stream['url'],
            'headers': proxy_headers['request']
        }
```

### 4.3 Debrid-Integrated Addon Support

Many addons now return pre-resolved debrid links when configured with user's debrid API key:

```python
class source:
    def _fetch_streams(self, addon_url, media_type, media_id):
        """Fetch streams - detect if addon returns resolved links"""
        # Check if addon URL contains debrid config (common pattern)
        # e.g., https://torrentio.strem.fun/realdebrid=xxx/stream/...

        for stream in streams:
            if 'url' in stream and 'debrid' in stream.get('name', '').lower():
                # This is a pre-resolved debrid stream
                yield {
                    'source': 'debrid_direct',
                    'url': stream['url'],
                    'direct': True,
                    'debridonly': False
                }
```

### 4.4 Subtitle Resource Integration

Add subtitle fetching from Stremio addons:

```python
def _fetch_subtitles(self, addon_url, media_type, media_id):
    """Fetch subtitles from Stremio addon"""
    endpoint = f"{addon_url}/subtitles/{media_type}/{media_id}.json"
    response = requests.get(endpoint, timeout=5)

    if response.ok:
        data = response.json()
        return data.get('subtitles', [])
    return []
```

Subtitle object format:
```json
{
  "id": "sub_id",
  "url": "https://example.com/subtitle.srt",
  "lang": "eng",
  "SubEncoding": "UTF-8"
}
```

### 4.5 Catalog Integration

Use Stremio addons for content discovery:

```python
def get_catalog(self, addon_url, catalog_type, catalog_id, skip=0):
    """Fetch catalog from Stremio addon"""
    endpoint = f"{addon_url}/catalog/{catalog_type}/{catalog_id}/skip={skip}.json"
    response = requests.get(endpoint, timeout=10)

    if response.ok:
        data = response.json()
        return data.get('metas', [])
    return []
```

This could power:
- Trending/Popular lists from addon catalogs
- Search functionality via catalog endpoints
- Discovery recommendations

### 4.6 bingeGroup for Autoplay Optimization

Use `bingeGroup` to optimize autoplay/next episode:

```python
def _parse_stream_info(self, stream):
    info = {...}

    behavior_hints = stream.get('behaviorHints', {})
    if 'bingeGroup' in behavior_hints:
        # Same bingeGroup = same source for episode continuity
        info['binge_group'] = behavior_hints['bingeGroup']

    return info
```

---

## 5. Implementation Approach

### Phase 1: Enhance Current stremio.py

1. **Improve direct URL handling**
   - Better quality detection for HTTP streams
   - Support for additional stream types (ytId, nzbUrl)
   - Proper size extraction from behaviorHints.videoSize

2. **Add proxyHeaders support**
   - Store authentication headers with source
   - Pass headers to player/resolver

3. **Better addon configuration**
   - Support addon-specific settings (debrid keys, filters)
   - Remember addon configuration URLs with options

### Phase 2: Add Debrid-Integrated Addon Support

1. **Create debrid-aware scraping mode**
   - Detect addons with debrid configuration
   - Handle pre-resolved URLs differently
   - Skip cache checking for already-resolved links

2. **Support popular debrid addons**
   - Torrentio with RealDebrid/Premiumize config
   - Comet with debrid integration
   - MediaFusion with debrid support

### Phase 3: Subtitle Integration

1. **Add subtitle fetching to stremio.py**
   - Fetch from /subtitles endpoint
   - Parse subtitle objects
   - Store with source for player

2. **Integrate with POV's subtitle system**
   - Map Stremio subtitles to Kodi format
   - Auto-select based on language preferences

### Phase 4: Catalog Integration (Optional)

1. **Create stremio_catalog.py indexer**
   - Fetch catalogs from configured addons
   - Convert to POV metadata format
   - Integrate with navigator

---

## 6. Technical Considerations

### Thread Safety
- Use `requests.Session` for connection pooling
- Implement proper timeout handling
- Use thread-safe addon configuration access

### Caching
- Cache addon manifests (1 hour TTL)
- Cache stream results (same as external providers)
- Don't cache direct debrid URLs (they expire)

### Error Handling
- Graceful fallback when addons fail
- Retry with exponential backoff
- User notification for persistent failures

### Configuration Storage

```python
# Addon config structure in settings
{
    'url': 'https://torrentio.strem.fun',
    'config_url': 'https://torrentio.strem.fun/realdebrid=xxx|...',
    'name': 'Torrentio',
    'id': 'com.stremio.torrentio.addon',
    'has_movies': True,
    'has_series': True,
    'debrid_enabled': True,
    'debrid_service': 'realdebrid'
}
```

---

## 7. Settings.xml Additions

```xml
<!-- Stremio Addons Enhanced Settings -->
<setting label="Stremio Addons" type="lsep" />
<setting id="provider.stremio" label="Enable Stremio Addons" type="bool" default="false" />
<setting id="stremio.manage" label="Manage Stremio Addons" type="action"
         default="[B]Configure...[/B]" visible="eq(-1,true)"
         action="RunPlugin(plugin://plugin.video.pov/?mode=stremio_addon_manager)" />
<setting id="stremio.addons" label="stremio.addons" type="text" default="" visible="false" />
<setting id="stremio.timeout" label="Stremio Timeout (seconds)" type="slider"
         default="8" range="3,1,15" visible="eq(-3,true)" />
<setting id="stremio.subtitles" label="Fetch Stremio Subtitles" type="bool"
         default="true" visible="eq(-4,true)" />
<setting id="stremio.debrid_direct" label="Prefer Debrid-Integrated Addons" type="bool"
         default="true" visible="eq(-5,true)" />
```

---

## 8. Router.py Additions

```python
# Additional routes for enhanced Stremio support
elif mode == 'stremio_addon_manager':
    from modules.stremio_manager import stremio_addon_manager
    stremio_addon_manager()
elif mode == 'stremio_addon_configure':
    from modules.stremio_manager import configure_addon
    configure_addon(params)
elif mode == 'stremio_catalog':
    from indexers.stremio_catalog import StremioIndexer
    StremioIndexer(params).run()
```

---

## 9. Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `indexers/stremio_catalog.py` | Catalog browsing from Stremio addons |
| `modules/stremio_subtitles.py` | Subtitle fetching and integration |

### Files to Modify
| File | Changes |
|------|---------|
| `magneto/stremio.py` | Add proxyHeaders, better direct URL, debrid detection |
| `modules/stremio_manager.py` | Add addon configuration with debrid options |
| `modules/player.py` | Handle streams with custom headers |
| `settings.xml` | Add enhanced Stremio settings |
| `router.py` | Add new routes |

---

## 10. Sources

- [Stremio Addon SDK Protocol](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/protocol.md)
- [Stremio Manifest Format](https://stremio.github.io/stremio-addon-sdk/api/responses/manifest.html)
- [Stream Response Format](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/api/responses/stream.md)
- [Stremio Addons Community List](https://stremio-addons.com/)
- [Best Stremio Addons 2025](https://troypoint.com/best-stremio-addons/)
- [Top Torrentio Alternatives](https://thetorrentio.com/torrentio-alternatives/)
- [Awesome Stremio - GitHub](https://github.com/doingodswork/awesome-stremio)
- [Stremio Addons with Debrid Support](https://stremio-addons.net/addons?categories=debrid+support&sort=popular)

---

## 11. Conclusion

The POV Kodi addon already has a foundation for Stremio addon integration. The key enhancements to make it a full "playback service" are:

1. **Direct HTTP stream support** with proper header handling
2. **Debrid-integrated addon support** for pre-resolved streams
3. **Subtitle integration** from Stremio's subtitle resource
4. **Enhanced addon configuration** supporting debrid credentials

These enhancements would allow users to leverage the rich Stremio addon ecosystem while benefiting from POV's superior source aggregation, filtering, and playback features.
