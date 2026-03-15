import os
import sqlite3
import urllib.request
from urllib.parse import urlparse
import tempfile
import configparser
from concurrent.futures import ThreadPoolExecutor
import shutil

# Reuse logic from history.py
def searchPlaces(firefox_path):
    firefox_path = os.path.expanduser(firefox_path)
    if not firefox_path.endswith("/"):
        firefox_path += "/"
    conf_path = os.path.join(firefox_path, 'profiles.ini')
    profile = configparser.RawConfigParser()
    profile.read(conf_path)
    if not profile.has_section("Profile0"):
        return None
    prof_path = profile.get("Profile0", "Path")
    sql_path = os.path.join(firefox_path, prof_path)
    return os.path.join(sql_path, 'places.sqlite')

def get_all_domains(db_path):
    # Firefox places.sqlite is often locked, so we copy it first
    temp_db_path = tempfile.mktemp()
    shutil.copyfile(db_path, temp_db_path)
    
    conn = sqlite3.connect(temp_db_path, check_same_thread=False)
    query = 'SELECT url FROM moz_bookmarks AS A JOIN moz_places AS B ON(A.fk = B.id)'
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    
    domains = set()
    for row in rows:
        url = row[0]
        if url:
            domain = urlparse(url).netloc
            if domain:
                domains.add(domain)
    conn.close()
    
    try:
        os.remove(temp_db_path)
    except OSError:
        pass
        
    return list(domains)

def test_fetch_duckduckgo(domain):
    favicon_url = f"https://icons.duckduckgo.com/ip3/{domain}.ico"
    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read()
            if len(content) > 0:
                return domain, content
            return domain, None
    except Exception:
        # Try root domain fallback
        parts = domain.split('.')
        if len(parts) > 2:
            root_domain = '.'.join(parts[-2:])
            favicon_url = f"https://icons.duckduckgo.com/ip3/{root_domain}.ico"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read()
                    if len(content) > 0:
                        return domain, content
            except Exception:
                pass
        return domain, None

def test_fetch_google(domain):
    favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read()
            if len(content) > 0:
                return domain, content
            return domain, None
    except Exception:
        # Try root domain fallback
        parts = domain.split('.')
        if len(parts) > 2:
            root_domain = '.'.join(parts[-2:])
            favicon_url = f"https://www.google.com/s2/favicons?domain={root_domain}&sz=64"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read()
                    if len(content) > 0:
                        return domain, content
            except Exception:
                pass
        return domain, None

def test_fetch_google_v2(domain):
    favicon_url = f"https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=64"
    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read()
            if len(content) > 0:
                return domain, content
            return domain, None
    except Exception:
        return domain, None

def test_fetch_iconhorse(domain):
    favicon_url = f"https://icon.horse/icon/{domain}"
    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read()
            if len(content) > 0:
                return domain, content
            return domain, None
    except Exception:
        return domain, None

def test_fetch_direct(domain):
    import urllib.request
    favicon_url = f"https://{domain}/favicon.ico"
    req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.getcode() == 200 and "image" in response.headers.get("content-type", ""):
                return domain, response.read()
            return domain, None
    except Exception:
        # Try root domain fallback
        parts = domain.split('.')
        if len(parts) > 2:
            root_domain = '.'.join(parts[-2:])
            favicon_url = f"https://{root_domain}/favicon.ico"
            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.getcode() == 200 and "image" in response.headers.get("content-type", ""):
                        return domain, response.read()
            except Exception:
                pass
        return domain, None

from html.parser import HTMLParser

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []
        self.manifest = None

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            attrs_dict = dict(attrs)
            rel = attrs_dict.get("rel", "").lower()
            if "icon" in rel:
                if "href" in attrs_dict:
                    self.icons.append(attrs_dict["href"])
            elif "manifest" in rel:
                if "href" in attrs_dict:
                    self.manifest = attrs_dict["href"]

def test_fetch_html(domain):
    import urllib.request
    from urllib.parse import urljoin
    try:
        url = f"https://{domain}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
        parser = LinkParser()
        parser.feed(html)
        
        for href in parser.icons:
            icon_url = urljoin(url, href)
            # Remove method='HEAD' to actually download it
            ireq = urllib.request.Request(icon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'})
            try:
                with urllib.request.urlopen(ireq, timeout=5) as ir:
                    if ir.getcode() == 200:
                        return domain, ir.read()
            except Exception:
                pass
        return domain, None
    except Exception:
        # Try root domain fallback
        parts = domain.split('.')
        if len(parts) > 2:
            root_domain = '.'.join(parts[-2:])
            res = test_fetch_html(root_domain)
            if res[1] is not None:
                return domain, res[1]
        return domain, None

def test_fetch_manifest(domain):
    import urllib.request
    from urllib.parse import urljoin
    import json
    try:
        url = f"https://{domain}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
        parser = LinkParser()
        parser.feed(html)
        
        if parser.manifest:
            manifest_url = urljoin(url, parser.manifest)
            mreq = urllib.request.Request(manifest_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with urllib.request.urlopen(mreq, timeout=5) as mr:
                data = json.loads(mr.read().decode('utf-8'))
                if "icons" in data and len(data["icons"]) > 0:
                    icon_url = urljoin(manifest_url, data["icons"][0].get("src", ""))
                    ireq = urllib.request.Request(icon_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                    try:
                        with urllib.request.urlopen(ireq, timeout=5) as ir:
                            if ir.getcode() == 200:
                                return domain, ir.read()
                    except Exception:
                        pass
        return domain, None
    except Exception:
        # Try root domain fallback
        parts = domain.split('.')
        if len(parts) > 2:
            root_domain = '.'.join(parts[-2:])
            res = test_fetch_manifest(root_domain)
            if res[1] is not None:
                return domain, res[1]
        return domain, None

if __name__ == '__main__':
    import time
    firefox_path = "~/.mozilla/firefox/"
    db_path = searchPlaces(firefox_path)
    
    if not db_path or not os.path.exists(db_path):
        print(f"Could not find places.sqlite using path {firefox_path}")
        exit(1)

    print("Fetching domains from Firefox history...")
    domains = get_all_domains(db_path)
    total = len(domains)
    print(f"Found {total} unique domains in bookmarks.")
    
    final_icons = {}

    def run_tests(name, func):
        print(f"\nTesting {name}...")
        start_time = time.time()
        success_domains = set()
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = executor.map(func, domains)
            for domain, content in results:
                if content is not None:
                    success_domains.add(domain)
                    if domain not in final_icons:
                        final_icons[domain] = content
        elapsed_time = time.time() - start_time
        print(f"[{name}] took {elapsed_time:.2f} seconds")
        return success_domains, elapsed_time
        
    ddg_success_domains, ddg_time = run_tests("DuckDuckGo favicon API", test_fetch_duckduckgo)
    google_success_domains, google_time = run_tests("Google favicon API", test_fetch_google)
    google_v2_success_domains, google_v2_time = run_tests("Google Favicon V2 API", test_fetch_google_v2)
    iconhorse_success_domains, iconhorse_time = run_tests("IconHorse API", test_fetch_iconhorse)
    direct_success_domains, direct_time = run_tests("Direct /favicon.ico fetch", test_fetch_direct)
    html_success_domains, html_time = run_tests("HTML <link rel='icon'> parsing", test_fetch_html)
    manifest_success_domains, manifest_time = run_tests("Manifest icons parsing", test_fetch_manifest)
    
    baseline_success_domains = ddg_success_domains | google_success_domains
    
    combined_success_domains = (baseline_success_domains | 
                              google_v2_success_domains | iconhorse_success_domains |
                              direct_success_domains | html_success_domains | 
                              manifest_success_domains)
    
    print(f"\nFinal Results (Found / Time):")
    print(f"DuckDuckGo: {len(ddg_success_domains)}/{total} found ({ddg_time:.2f}s)")
    print(f"Google: {len(google_success_domains)}/{total} found ({google_time:.2f}s)")
    print(f"Google V2: {len(google_v2_success_domains)}/{total} found ({google_v2_time:.2f}s)")
    print(f"IconHorse: {len(iconhorse_success_domains)}/{total} found ({iconhorse_time:.2f}s)")
    print(f"Direct /favicon.ico: {len(direct_success_domains)}/{total} found ({direct_time:.2f}s)")
    print(f"HTML <link rel='icon'>: {len(html_success_domains)}/{total} found ({html_time:.2f}s)")
    print(f"Manifest icons: {len(manifest_success_domains)}/{total} found ({manifest_time:.2f}s)")
    
    print(f"\n--- Incremental Value Over Baseline (DDG + Google S2 = {len(baseline_success_domains)} domains) ---")
    print(f"Google V2 adds: {len(google_v2_success_domains - baseline_success_domains)} unique new domains")
    print(f"IconHorse adds: {len(iconhorse_success_domains - baseline_success_domains)} unique new domains")
    print(f"Direct /favicon.ico adds: {len(direct_success_domains - baseline_success_domains)} unique new domains")
    print(f"HTML parsing adds: {len(html_success_domains - baseline_success_domains)} unique new domains")
    print(f"Manifest icons add: {len(manifest_success_domains - baseline_success_domains)} unique new domains")

    print(f"\nCombined (All methods): {len(combined_success_domains)}/{total} found.")

    print("\n--- Final Sequential Algorithm Simulation ---")
    step1_domains = baseline_success_domains
    print(f"Step 1 (Baseline): DDG + Google S2 found {len(step1_domains)}/{total} domains.")
    
    missing_after_step1 = set(domains) - step1_domains
    html_found_in_missing = html_success_domains.intersection(missing_after_step1)
    step2_domains = step1_domains | html_found_in_missing
    print(f"Step 2: HTML parsing on missing domains found {len(html_found_in_missing)} additional domains. Total: {len(step2_domains)}/{total}.")
    
    missing_after_step2 = set(domains) - step2_domains
    direct_found_in_missing = direct_success_domains.intersection(missing_after_step2)
    step3_domains = step2_domains | direct_found_in_missing
    print(f"Step 3: Direct /favicon.ico on still missing domains found {len(direct_found_in_missing)} additional domains. Total: {len(step3_domains)}/{total}.")
    
    if total > 0:
        print(f"Final Algorithm Coverage: {len(step3_domains)/total*100:.1f}%")

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_favicons_output")
    os.makedirs(output_dir, exist_ok=True)
    print(f"\nSaving {len(final_icons)} distinct favicons to {output_dir} ...")
    for domain, content in final_icons.items():
        with open(os.path.join(output_dir, f"{domain}.ico"), "wb") as f:
            f.write(content)
    print("Done!")
