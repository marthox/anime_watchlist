"""The player ani-cli runs in place of mpv (see cli.watch: the shim's name must contain 'mpv').

ani-cli hands over --referrer / --sub-file / --force-media-title and the stream URL.
We serve the stream locally (stream.py), launch VLC pointed at it, and, if the CLI
told us which show and episode this is, record the session by polling VLC's web
interface.

Env:
  ANIME_SLUG, ANIME_EPISODE, ANIME_START_POS  what is being watched (set by `anime watch`)
  ANI_PLAYER_BIN   player executable (default: Windows VLC; only VLC is tracked)
  ANI_PLAYER_ARGS  extra player args, shell-style
  ANI_WORKERS      parallel downloads (default 12)
"""
import base64
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import db, stream

DEFAULT_PLAYER = "/mnt/c/Program Files/VideoLAN/VLC/vlc.exe"
WORKERS = int(os.environ.get("ANI_WORKERS", "12"))
POLL_SEC = 5


def parse_args(argv):
    opts = {"referrer": "", "sub": "", "title": "", "url": ""}
    for a in argv:
        if a.startswith("--referrer="):
            opts["referrer"] = a.split("=", 1)[1]
        elif a.startswith("--sub-file="):
            opts["sub"] = a.split("=", 1)[1]
        elif a.startswith("--force-media-title="):
            opts["title"] = a.split("=", 1)[1]
        elif not a.startswith("-"):
            opts["url"] = a
    return opts


def wsl_host_ip() -> str:
    """The Windows side of WSL's NAT network: reachable from WSL, not from the LAN."""
    out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.split()
    return out[out.index("via") + 1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tracker(threading.Thread):
    """Polls VLC's web interface and keeps one sessions row up to date."""

    def __init__(self, host, port, password, slug, episode, start_pos):
        super().__init__(daemon=True)
        self.url = f"http://{host}:{port}/requests/status.json"
        token = base64.b64encode(f":{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.slug, self.episode, self.start_pos = slug, episode, start_pos
        self.stop = threading.Event()
        self.kept = False

    def status(self):
        try:
            req = urllib.request.Request(self.url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.load(r)
        except Exception:
            return None  # VLC not up yet, or already gone

    def run(self):
        with db.connect() as con:
            session = db.start_session(con, self.slug, self.episode, self.start_pos)
            last_t, last_pos, carry = time.monotonic(), None, 0.0
            while not self.stop.wait(POLL_SEC):
                st = self.status()
                t = time.monotonic()
                if not st:
                    last_t = t
                    continue
                pos = max(int(st.get("time", 0)), 0)
                if st.get("state") == "playing" and last_pos is not None:
                    # credit forward progress only: buffering adds nothing and a seek adds at most the elapsed time
                    carry += min(max(pos - last_pos, 0), t - last_t)
                last_t, last_pos = t, pos
                add, carry = int(carry), carry - int(carry)
                db.update_session(con, session, pos, st.get("length") or None, add)
            self.kept = db.end_session(con, session)


def main(argv=None) -> int:
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    if not opts["url"]:
        sys.exit("anime: no stream URL given")
    player = os.environ.get("ANI_PLAYER_BIN", DEFAULT_PLAYER)
    referer = opts["referrer"]
    is_vlc = "vlc" in os.path.basename(player).lower()

    try:
        segs, target = stream.parse_playlist(stream.fetch(opts["url"], referer).decode(), opts["url"])
    except RuntimeError as e:
        print(f"anime: {e}; playing the URL directly", file=sys.stderr)
        arg = [f"--http-referrer={referer}"] if is_vlc else [f"--referrer={referer}"]
        os.execvp(player, [player, *arg, opts["url"]])

    sub_bytes = b""
    if opts["sub"]:
        try:
            sub_bytes = stream.fetch(opts["sub"], referer)
        except RuntimeError as e:
            print(f"anime: subtitles unavailable ({e})", file=sys.stderr)

    workdir = tempfile.mkdtemp(prefix="anime_watchlist_")
    st = stream.Stream(workdir, segs, target, referer)
    server = stream.QuietServer(("127.0.0.1", 0), stream.make_handler(st, sub_bytes))
    server.daemon_threads = True
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    for _ in range(WORKERS):
        pool.submit(st.background)

    slug = os.environ.get("ANIME_SLUG")
    episode = int(os.environ.get("ANIME_EPISODE", "0") or 0)
    start_pos = int(os.environ.get("ANIME_START_POS", "0") or 0)
    extra = shlex.split(os.environ.get("ANI_PLAYER_ARGS", ""))
    tracker = None
    if is_vlc:
        cmd = [player, "--play-and-exit", f"--meta-title={opts['title']}"]
        if start_pos > 0:
            cmd.append(f"--start-time={start_pos}")
        if slug and episode:
            host, port, password = wsl_host_ip(), free_port(), secrets.token_urlsafe(12)
            cmd += ["--extraintf=http", f"--http-host={host}", f"--http-port={port}", f"--http-password={password}"]
            tracker = Tracker(host, port, password, slug, episode, start_pos)
        cmd += [*extra, f"{base}/index.m3u8"]
        if sub_bytes:
            cmd.append(f":input-slave={base}/subs.vtt")
    else:
        cmd = [player, *([f"--force-media-title={opts['title']}"] if opts["title"] else []),
               *([f"--sub-file={base}/subs.vtt"] if sub_bytes else []),
               *([f"--start={start_pos}"] if start_pos > 0 else []), *extra, f"{base}/index.m3u8"]

    code = 1
    try:
        if tracker:
            tracker.start()
        print(f"Serving {len(segs)} segments on {base}", file=sys.stderr)
        code = subprocess.run(cmd).returncode
    finally:
        if tracker:
            tracker.stop.set()
            tracker.join(timeout=10)
        st.stop.set()
        server.shutdown()
        pool.shutdown(wait=False, cancel_futures=True)
        shutil.rmtree(workdir, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
