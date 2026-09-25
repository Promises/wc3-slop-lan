"""The web UI server (harness/webui/server.py) and the page it answers to, which sits in each
game's webui folder and is how a game's menus are driven: going offline, finding and joining a
LAN game, and the provider switch activation needs."""
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parents[1] / 'webui'
PAGE = HERE / 'index.html'
SERVER = HERE / 'server.py'
# How our page is recognised; wcmaul is what it was called before it moved here
MARKS = ('window.slop', 'window.wcmaul')
BACKUP = 'index.html.before-slop'


class Server:
    def __init__(self, url):
        self.url = url.rstrip('/')
        self.process = None

    def _open(self, path, body=None, timeout=5):
        data = None if body is None else json.dumps(body).encode()
        return urllib.request.urlopen(urllib.request.Request(self.url + path, data=data), timeout=timeout).read()

    def up(self):
        try:
            self._open('/instances', timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            return False

    def ensure(self, log_file):
        """Starts the server unless one already answers; returns whether this started it."""
        if self.up():
            return False
        port = self.url.rsplit(':', 1)[-1]
        self.process = subprocess.Popen([sys.executable, str(SERVER), str(log_file), port],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(20):
            if self.up():
                return True
            time.sleep(0.25)
        raise RuntimeError(f'the web UI server did not come up at {self.url}')

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process = None

    def reset(self):
        self._open('/reset', {})

    def instances(self):
        return json.loads(self._open('/instances'))

    def command(self, to, verb, **fields):
        return json.loads(self._open('/command', dict(to=str(to), verb=verb, **fields)))

    def checked_in(self):
        """Instances whose page has heard from the game (any screen)."""
        return sum(1 for v in self.instances().values() if v['state'].get('screen'))

    def port_of(self, number):
        return next((int(k) for k, v in self.instances().items() if v['number'] == number), None)


DEFAULT_SERVER = "server: 'http://127.0.0.1:8777'"


def install_page(webui_dir, server):
    """Puts our page in the game's webui folder, keeping the page that was there, with the
    address of the server it reports to written in."""
    webui_dir = pathlib.Path(webui_dir)
    if not webui_dir.is_dir():
        raise RuntimeError(f'no webui folder at {webui_dir}: is the game installed there? (game.webui in slop.toml)')
    target = webui_dir / 'index.html'
    ours = target.exists() and any(mark in target.read_text(errors='replace') for mark in MARKS)
    kept = (webui_dir / BACKUP).exists() or (webui_dir / 'index.html.before-wcmaul').exists()
    if target.exists() and not ours and not kept:
        target.rename(webui_dir / BACKUP)
    page = PAGE.read_text()
    if DEFAULT_SERVER not in page:
        raise RuntimeError(f'{PAGE} no longer has the server address where the harness writes it')
    target.write_text(page.replace(DEFAULT_SERVER, f"server: '{server}'"))


def restore_page(webui_dir):
    """Puts the page that was there before back, if one was kept (under either name the harness
    has used)."""
    webui_dir = pathlib.Path(webui_dir)
    for name in (BACKUP, 'index.html.before-wcmaul'):
        if (webui_dir / name).exists():
            (webui_dir / name).replace(webui_dir / 'index.html')
            return
