from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, SystemExitEvent,PreferencesUpdateEvent, PreferencesEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.RunScriptAction import RunScriptAction
from history import FirefoxHistory
import os
import urllib.request
from urllib.parse import urlparse
import struct
import logging

logger = logging.getLogger(__name__)

# Prefer SVG > PNG > ICO. Also used for cache filenames.
_MIME_TO_EXT = {
    'image/svg+xml': '.svg',
    'image/svg': '.svg',
    'image/png': '.png',
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
    'image/icon': '.ico',
    'image/ico': '.ico',
}


def _guess_ext_from_response_headers(headers) -> str:
    try:
        ctype = (headers.get('content-type') or '').split(';', 1)[0].strip().lower()
    except Exception:
        ctype = ''
    return _MIME_TO_EXT.get(ctype, '')


def _guess_ext_from_url(url: str) -> str:
    u = (url or '').lower().split('?', 1)[0].split('#', 1)[0]
    for ext in ('.svg', '.png', '.ico'):
        if u.endswith(ext):
            return ext
    return ''


def _cache_path_for_domain(cache_dir: str, domain: str, ext: str) -> str:
    ext = ext if ext in ('.svg', '.png', '.ico') else '.ico'
    return os.path.join(cache_dir, f"{domain}{ext}")


def _find_cached_icon_for_domain(cache_dir: str, domain: str) -> str | None:
    # Prefer vector then bigger raster by conventionless ordering.
    for ext in ('.svg', '.png', '.ico'):
        p = _cache_path_for_domain(cache_dir, domain, ext)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def extract_largest_icon_bytes(data: bytes) -> bytes:
    try:
        # Check if it's an ICO file
        if len(data) < 6:
            return data

        reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)

        # Only process if valid ICO format and has multiple images
        if icon_type != 1 or count <= 1:
            return data

        entries = []
        offset = 6

        # Parse ICONDIR entries safely
        for i in range(count):
            if offset + 16 > len(data):
                break

            width, height, colors, reserved, planes, bpp, size, img_offset = struct.unpack_from(
                "<BBBBHHII", data, offset
            )

            # Width and height can be 0, which means 256
            width = 256 if width == 0 else width
            height = 256 if height == 0 else height

            # Basic validation of offset and size
            if img_offset + size <= len(data):
                entries.append({
                    "width": width,
                    "height": height,
                    "size": size,
                    "offset": img_offset
                })

            offset += 16

        if not entries:
            return data

        # check if it contains any valid PNG payload
        # if not, return the original data. DO NOT extract raw DIBs.
        valid_png_entries = []
        for entry in entries:
            img_data = data[entry["offset"]:entry["offset"] + entry["size"]]
            if img_data.startswith(b"\x89PNG\r\n\x1a\n"):
                valid_png_entries.append(entry)

        if not valid_png_entries:
            return data

        # Prefer largest resolution, tie-breaker: bigger payload
        best = max(valid_png_entries, key=lambda e: (e["width"] * e["height"], e["size"]))
        return data[best["offset"]:best["offset"] + best["size"]]

    except Exception:
        # If parsing fails, fall back to returning original data
        return data


def _is_probably_html(data: bytes) -> bool:
    if not data:
        return False
    head = data[:512].lstrip().lower()
    return head.startswith(b'<!doctype html') or head.startswith(b'<html') or b'<html' in head or b'<head' in head


def _is_png(data: bytes) -> bool:
    return bool(data) and data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_svg(data: bytes) -> bool:
    if not data:
        return False
    head = data[:1024].lstrip().lower()
    # allow XML preamble and svg tag
    return head.startswith(b'<?xml') or head.startswith(b'<svg') or b'<svg' in head


def _is_ico(data: bytes) -> bool:
    # ICO header: reserved=0, type=1, count>=1
    if not data or len(data) < 6:
        return False
    try:
        reserved, icon_type, count = struct.unpack_from('<HHH', data, 0)
        return reserved == 0 and icon_type == 1 and count >= 1
    except Exception:
        return False


def _validate_icon_bytes(data: bytes, ext: str) -> bool:
    if not data:
        return False
    if _is_probably_html(data):
        return False
    if ext == '.png':
        return _is_png(data)
    if ext == '.svg':
        return _is_svg(data)
    if ext == '.ico':
        return _is_ico(data)
    # unknown: reject
    return False


class FirefoxHistoryExtension(Extension):
    def __init__(self):
        super(FirefoxHistoryExtension, self).__init__()
        #   Firefox History Getter
        #   Delayed initialisation, need to get path from preferences
        self.fh = None
        self.downloading_favicons = set()
        #   Ulauncher Events
        self.subscribe(KeywordQueryEvent,KeywordQueryEventListener())
        self.subscribe(SystemExitEvent,SystemExitEventListener())
        self.subscribe(PreferencesEvent,PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent,PreferencesUpdateEventListener())

    def init_fh(self, firefox_path: str):
        #   Initialise Firefox History Getter with path from preferences
        if self.fh is None:
            self.fh = FirefoxHistory(firefox_path)
            self.cache_all_favicons()

    def cache_all_favicons(self):
        import threading
        def worker():
            logger.info("Starting background favicon caching process.")
            cache_dir = os.path.expanduser('~/.cache/ulauncher-firefox-bookmarks-favicons')
            os.makedirs(cache_dir, exist_ok=True)
            domains = self.fh.get_all_domains()
            logger.info(f"Found {len(domains)} unique domains in Firefox bookmarks.")

            cached_count = 0
            download_count = 0

            for domain in domains:
                if _find_cached_icon_for_domain(cache_dir, domain):
                    cached_count += 1
                else:
                    # choose final ext after download based on content type / URL
                    self.download_favicon_async(domain, cache_dir)
                    download_count += 1

            logger.info(f"Favicon cache status: {cached_count} already cached, queued {download_count} for download.")

        threading.Thread(target=worker, daemon=True).start()

    def download_favicon_async(self, domain, cache_dir_or_icon_path):
        # Backwards compatible: allow old call style (domain, domain_icon_path)
        if isinstance(cache_dir_or_icon_path, str) and cache_dir_or_icon_path.endswith(('.ico', '.png', '.svg')):
            cache_dir = os.path.dirname(cache_dir_or_icon_path)
        else:
            cache_dir = cache_dir_or_icon_path

        if domain in self.downloading_favicons:
            return
        self.downloading_favicons.add(domain)
        import threading

        def _fetch_url(url: str, timeout: int = 7):
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
                    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                # If server says it's not an image, don't cache it.
                ctype = (response.headers.get('content-type') or '').lower()
                if ctype and (not ctype.startswith('image/') and 'svg' not in ctype):
                    return None

                content = response.read()
                if not content:
                    return None

                ext = _guess_ext_from_response_headers(response.headers) or _guess_ext_from_url(url)
                ext = ext if ext in ('.svg', '.png', '.ico') else ''

                # If we still can't decide, try sniffing.
                if not ext:
                    if _is_png(content):
                        ext = '.png'
                    elif _is_svg(content):
                        ext = '.svg'
                    elif _is_ico(content):
                        ext = '.ico'
                    else:
                        return None

                if not _validate_icon_bytes(content, ext):
                    return None

                return content, ext

        def test_fetch_duckduckgo(domain_str):
            # DDG usually returns an ICO with multiple embedded sizes -> we later extract best PNG layer.
            for d in (domain_str, '.'.join(domain_str.split('.')[-2:]) if len(domain_str.split('.')) > 2 else None):
                if not d:
                    continue
                try:
                    res = _fetch_url(f"https://icons.duckduckgo.com/ip3/{d}.ico")
                    if res:
                        return res
                except Exception:
                    pass
            return None

        def test_fetch_google(domain_str):
            # Ask for a larger size.
            for d in (domain_str, '.'.join(domain_str.split('.')[-2:]) if len(domain_str.split('.')) > 2 else None):
                if not d:
                    continue
                try:
                    res = _fetch_url(f"https://www.google.com/s2/favicons?domain={d}&sz=256")
                    if res:
                        return res
                except Exception:
                    pass
            return None

        def test_fetch_html(domain_str):
            from html.parser import HTMLParser
            from urllib.parse import urljoin

            class LinkParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.icons = []  # list of dicts

                def handle_starttag(self, tag, attrs):
                    if tag != 'link':
                        return
                    attrs_dict = {k.lower(): v for k, v in attrs}
                    rel = (attrs_dict.get('rel') or '').lower()
                    if 'icon' not in rel:
                        return
                    href = attrs_dict.get('href')
                    if not href:
                        return

                    # Only keep likely icon assets
                    href_l = href.lower().split('?', 1)[0].split('#', 1)[0]
                    if not (
                        href_l.endswith(('.svg', '.png', '.ico'))
                        or 'favicon' in href_l
                        or 'apple-touch-icon' in href_l
                    ):
                        return

                    sizes = (attrs_dict.get('sizes') or '').lower()
                    typ = (attrs_dict.get('type') or '').lower()
                    self.icons.append({'href': href, 'rel': rel, 'sizes': sizes, 'type': typ})

            def _parse_best_area(sizes: str) -> int:
                if not sizes:
                    return 0
                if 'any' in sizes:
                    # Usually used for SVG. Large, but don't let it dominate everything.
                    return 10**9
                best = 0
                for token in sizes.replace(',', ' ').split():
                    if 'x' in token:
                        try:
                            w, h = token.split('x', 1)
                            best = max(best, int(w) * int(h))
                        except Exception:
                            pass
                return best

            def _rel_rank(rel: str) -> int:
                # Lower is better: try to match what browsers use as the tab favicon.
                r = (rel or '').lower()
                if 'shortcut icon' in r or r.strip() == 'icon' or r.startswith('icon ') or ' icon' in r:
                    return 0
                if 'mask-icon' in r:
                    return 1
                if 'apple-touch-icon' in r:
                    return 2
                return 3

            def _score_icon(entry: dict) -> tuple:
                rel = entry.get('rel', '')
                sizes = entry.get('sizes', '')
                typ = entry.get('type', '')
                href = entry.get('href', '')

                is_svg = ('svg' in typ) or href.lower().endswith('.svg')
                is_png = ('png' in typ) or href.lower().endswith('.png')
                area = _parse_best_area(sizes)

                # Cap the size impact so we don't accidentally prefer huge PWA/touch icons over the actual favicon.
                # 128x128 is already plenty for launchers.
                capped_area = min(area, 128 * 128)

                # Prefer: correct rel > svg/png > size
                return (
                    -_rel_rank(rel),
                    1 if is_svg else 0,
                    1 if is_png else 0,
                    capped_area,
                )

            def _try_base(base_url: str):
                req = urllib.request.Request(
                    base_url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
                    },
                )
                with urllib.request.urlopen(req, timeout=7) as response:
                    html = response.read()

                # If we got HTML, parse it; otherwise there's nothing to parse here.
                try:
                    html_text = html.decode('utf-8', errors='ignore')
                except Exception:
                    return None

                parser = LinkParser()
                parser.feed(html_text)

                # Try traditional favicon paths first, then touch icons.
                common_paths = [
                    '/favicon.svg',
                    '/favicon.png',
                    '/favicon.ico',
                    '/apple-touch-icon.png',
                    '/apple-touch-icon-precomposed.png',
                ]

                candidates = []
                if parser.icons:
                    candidates.extend(sorted(parser.icons, key=_score_icon, reverse=True))

                # Append commons as lowest priority fallbacks
                for p in common_paths:
                    candidates.append({'href': p, 'rel': 'common', 'sizes': '', 'type': ''})

                seen = set()
                for icon in candidates:
                    icon_url = urljoin(base_url, icon['href'])
                    if icon_url in seen:
                        continue
                    seen.add(icon_url)
                    try:
                        res = _fetch_url(icon_url)
                        if res:
                            return res
                    except Exception:
                        pass
                return None

            # Try https then http for the *same* domain.
            for scheme in ('https', 'http'):
                base = f"{scheme}://{domain_str}"
                try:
                    res = _try_base(base)
                    if res:
                        return res
                except Exception:
                    pass

            # IMPORTANT: do NOT fall back to root-domain for HTML scraping.
            # It often returns a different brand/favicon than the subdomain.
            return None

        def test_fetch_direct(domain_str):
            for d in (domain_str, '.'.join(domain_str.split('.')[-2:]) if len(domain_str.split('.')) > 2 else None):
                if not d:
                    continue
                try:
                    res = _fetch_url(f"https://{d}/favicon.ico")
                    if res:
                        return res
                except Exception:
                    pass
            return None

        def worker():
            try:
                result = test_fetch_html(domain)
                if not result:
                    result = test_fetch_duckduckgo(domain)
                if not result:
                    result = test_fetch_google(domain)
                if not result:
                    result = test_fetch_direct(domain)

                if result:
                    content, ext = result
                    ext = ext if ext in ('.svg', '.png', '.ico') else '.ico'

                    if not _validate_icon_bytes(content, ext):
                        logger.debug(f"Rejected invalid favicon payload for {domain} (ext {ext})")
                        return

                    # If it's ICO, try to extract the largest embedded PNG payload (best resolution, best compatibility)
                    if ext == '.ico':
                        extracted = extract_largest_icon_bytes(content)
                        # If we extracted a PNG payload, store as PNG
                        if _is_png(extracted):
                            content = extracted
                            ext = '.png'
                        else:
                            content = extracted

                    out_path = _cache_path_for_domain(cache_dir, domain, ext)
                    with open(out_path, 'wb') as out_file:
                        out_file.write(content)
                    logger.debug(f"Successfully downloaded and cached favicon for {domain} as {ext}")
                else:
                    logger.debug(f"Failed to find favicon for {domain}")
            except Exception as e:
                logger.debug(f"Error downloading favicon for {domain}: {str(e)}")
            finally:
                self.downloading_favicons.discard(domain)

        threading.Thread(target=worker, daemon=True).start()

class PreferencesEventListener(EventListener):
    def on_event(self,event,extension):
        extension.init_fh(event.preferences['path'])
        #   Aggregate Results
        #extension.fh.aggregate = event.preferences['aggregate']
        #   Results Order
        #extension.fh.order = event.preferences['order']
        #   Results Number
        try:
            n = int(event.preferences['limit'])
        except:
            n = 10
        extension.fh.limit = n
        
class PreferencesUpdateEventListener(EventListener):
    def on_event(self,event,extension):
        extension.init_fh(event.preferences['path'])
        #   Results Order
        #if event.id == 'order':
        #    extension.fh.order = event.new_value
        #   Results Number
        if event.id == 'limit':
            try:
                n = int(event.new_value)
                extension.fh.limit = n
            except:
                pass
        #elif event.id == 'aggregate':
        #    extension.fh.aggregate = event.new_value

class SystemExitEventListener(EventListener):
    def on_event(self,event,extension):
        if extension.fh is not None:
            extension.fh.close()

class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
        extension.init_fh(extension.preferences['path'])
        query  = event.get_argument()
        #   Blank Query
        if query == None:
            query = ''
        items = []

        cache_dir = os.path.expanduser('~/.cache/ulauncher-firefox-bookmarks-favicons')
        os.makedirs(cache_dir, exist_ok=True)

        #   Search into Firefox History
        results = extension.fh.search(query)
        for link in results:
            url = link[1]
            title = link[0] if link[0] else url
            icon_path = 'images/icon.svg'

            if url:
                domain = urlparse(url).netloc
                if domain:
                    cached = _find_cached_icon_for_domain(cache_dir, domain)
                    if cached:
                        icon_path = cached
                    else:
                        extension.download_favicon_async(domain, cache_dir)

            items.append(ExtensionResultItem(icon=icon_path,
                                            name=title,
                                            description=url,
                                            on_enter=RunScriptAction(f"xdg-open {url}")))

        return RenderResultListAction(items)

if __name__ == '__main__':
    FirefoxHistoryExtension().run()
