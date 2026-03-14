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

class FirefoxHistoryExtension(Extension):
    def __init__(self):
        super(FirefoxHistoryExtension, self).__init__()
        #   Firefox History Getter
        #   Delayed initialisation, need to get path from preferences
        self.fh = None
        #   Ulauncher Events
        self.subscribe(KeywordQueryEvent,KeywordQueryEventListener())
        self.subscribe(SystemExitEvent,SystemExitEventListener())
        self.subscribe(PreferencesEvent,PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent,PreferencesUpdateEventListener())

    def init_fh(self, firefox_path: str):
        #   Initialise Firefox History Getter with path from preferences
        if self.fh is None:
            self.fh = FirefoxHistory(firefox_path)

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
            icon_path = 'images/icon.png'

            if url:
                domain = urlparse(url).netloc
                if domain:
                    domain_icon_path = os.path.join(cache_dir, f"{domain}.png")
                    
                    if not os.path.exists(domain_icon_path):
                        try:
                            favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
                            req = urllib.request.Request(favicon_url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(req, timeout=1) as response, open(domain_icon_path, 'wb') as out_file:
                                out_file.write(response.read())
                        except Exception:
                            pass
                    
                    if os.path.exists(domain_icon_path):
                        icon_path = domain_icon_path

            items.append(ExtensionResultItem(icon=icon_path,
                                            name=title,
                                            description=url,
                                            on_enter=RunScriptAction(f"xdg-open {url}")))

        return RenderResultListAction(items)

if __name__ == '__main__':
    FirefoxHistoryExtension().run()
