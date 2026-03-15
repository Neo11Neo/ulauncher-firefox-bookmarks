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
            
            # If the entry says it's 48x48 but the size implies something else,
            # this is a malformed entry (common in some web icons)
            # The issue in ulauncher is "Format error decoding Ico: Entry(48, 48) and BMP(16, 16) dimensions do not match"
            
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
        has_png = False
        for entry in entries:
            img_data = data[entry["offset"]:entry["offset"] + entry["size"]]
            if img_data.startswith(b"\x89PNG\r\n\x1a\n"):
                has_png = True
                break
        
        if not has_png:
            # If there's no PNG layer, the parsing tool we pass it to (Ulauncher/glycin) might fail 
            # if the DIB sizes don't match entry headers, so we just return the original file.
            return data

        # pick largest resolution that is a PNG
        valid_png_entries = []
        for entry in entries:
            img_data = data[entry["offset"]:entry["offset"] + entry["size"]]
            if img_data.startswith(b"\x89PNG\r\n\x1a\n"):
                valid_png_entries.append(entry)

        if not valid_png_entries:
            return data

        best = max(valid_png_entries, key=lambda e: e["width"] * e["height"])
        return data[best["offset"]:best["offset"] + best["size"]]
        
    except Exception:
        # If parsing fails, fall back to returning original data
        return data

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
                domain_icon_path = os.path.join(cache_dir, f"{domain}.ico")
                if os.path.exists(domain_icon_path) and os.path.getsize(domain_icon_path) > 0:
                    cached_count += 1
                else:
                    self.download_favicon_async(domain, domain_icon_path)
                    download_count += 1
                    
            logger.info(f"Favicon cache status: {cached_count} already cached, queued {download_count} for download.")
            
        threading.Thread(target=worker, daemon=True).start()

    def download_favicon_async(self, domain, domain_icon_path):
        if domain in self.downloading_favicons:
            return
        self.downloading_favicons.add(domain)
        import threading
        
        def test_fetch_duckduckgo(domain_str):
            favicon_url = f"https://icons.duckduckgo.com/ip3/{domain_str}.ico"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read()
                    if len(content) > 0:
                        return content
            except Exception:
                parts = domain_str.split('.')
                if len(parts) > 2:
                    root_domain = '.'.join(parts[-2:])
                    favicon_url = f"https://icons.duckduckgo.com/ip3/{root_domain}.ico"
                    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                    try:
                        with urllib.request.urlopen(req, timeout=5) as response:
                            content = response.read()
                            if len(content) > 0:
                                return content
                    except Exception:
                        pass
            return None

        def test_fetch_google(domain_str):
            favicon_url = f"https://www.google.com/s2/favicons?domain={domain_str}&sz=64"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read()
                    if len(content) > 0:
                        return content
            except Exception:
                parts = domain_str.split('.')
                if len(parts) > 2:
                    root_domain = '.'.join(parts[-2:])
                    favicon_url = f"https://www.google.com/s2/favicons?domain={root_domain}&sz=64"
                    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                    try:
                        with urllib.request.urlopen(req, timeout=5) as response:
                            content = response.read()
                            if len(content) > 0:
                                return content
                    except Exception:
                        pass
            return None

        def test_fetch_html(domain_str):
            from html.parser import HTMLParser
            from urllib.parse import urljoin
            
            class LinkParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.icons = []

                def handle_starttag(self, tag, attrs):
                    if tag == "link":
                        attrs_dict = dict(attrs)
                        rel = attrs_dict.get("rel", "").lower()
                        if "icon" in rel:
                            if "href" in attrs_dict:
                                self.icons.append(attrs_dict["href"])

            try:
                url = f"https://{domain_str}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    html = response.read().decode('utf-8', errors='ignore')
                    
                parser = LinkParser()
                parser.feed(html)
                
                for href in parser.icons:
                    icon_url = urljoin(url, href)
                    ireq = urllib.request.Request(icon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
                    try:
                        with urllib.request.urlopen(ireq, timeout=5) as ir:
                            if ir.getcode() == 200:
                                content = ir.read()
                                if len(content) > 0:
                                    return content
                    except Exception:
                        pass
            except Exception:
                parts = domain_str.split('.')
                if len(parts) > 2:
                    root_domain = '.'.join(parts[-2:])
                    try:
                        url = f"https://{root_domain}"
                        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'})
                        with urllib.request.urlopen(req, timeout=5) as response:
                            html = response.read().decode('utf-8', errors='ignore')
                            
                        parser = LinkParser()
                        parser.feed(html)
                        
                        for href in parser.icons:
                            icon_url = urljoin(url, href)
                            ireq = urllib.request.Request(icon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
                            try:
                                with urllib.request.urlopen(ireq, timeout=5) as ir:
                                    if ir.getcode() == 200:
                                        content = ir.read()
                                        if len(content) > 0:
                                            return content
                            except Exception:
                                pass
                    except Exception:
                        pass
            return None

        def test_fetch_direct(domain_str):
            favicon_url = f"https://{domain_str}/favicon.ico"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.getcode() == 200 and "image" in response.headers.get("content-type", ""):
                        content = response.read()
                        if len(content) > 0:
                            return content
            except Exception:
                parts = domain_str.split('.')
                if len(parts) > 2:
                    root_domain = '.'.join(parts[-2:])
                    favicon_url = f"https://{root_domain}/favicon.ico"
                    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
                    try:
                        with urllib.request.urlopen(req, timeout=5) as response:
                            if response.getcode() == 200 and "image" in response.headers.get("content-type", ""):
                                content = response.read()
                                if len(content) > 0:
                                    return content
                    except Exception:
                        pass
            return None

        def worker():
            try:
                content = test_fetch_duckduckgo(domain)
                if not content:
                    content = test_fetch_google(domain)
                if not content:
                    content = test_fetch_html(domain)
                if not content:
                    content = test_fetch_direct(domain)
                
                if content:
                    content = extract_largest_icon_bytes(content)
                    with open(domain_icon_path, 'wb') as out_file:
                        out_file.write(content)
                    logger.debug(f"Successfully downloaded and cached favicon for {domain}")
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
                    domain_icon_path = os.path.join(cache_dir, f"{domain}.ico")
                    
                    if os.path.exists(domain_icon_path):
                        # Some icons might be invalid (e.g., 0 bytes), so check size
                        if os.path.getsize(domain_icon_path) > 0:
                            icon_path = domain_icon_path
                        else:
                            extension.download_favicon_async(domain, domain_icon_path)
                    else:
                        extension.download_favicon_async(domain, domain_icon_path)

            items.append(ExtensionResultItem(icon=icon_path,
                                            name=title,
                                            description=url,
                                            on_enter=RunScriptAction(f"xdg-open {url}")))

        return RenderResultListAction(items)

if __name__ == '__main__':
    FirefoxHistoryExtension().run()
