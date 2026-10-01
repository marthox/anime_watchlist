"""Local HLS server: serves a full VOD playlist while downloading segments in parallel.

The stream host caps each connection (~80 KB/s), slower than playback, so playing
the URL directly stalls on every seek. The player gets a normal full playlist
(full duration, free seeking), segments are downloaded in the background, and a
segment the player asks for before it is ready is fetched on demand and the
background download jumps to that point.
"""
import os
import re
import sys
import threading
import time
import urllib.request
from urllib.parse import urljoin
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
VERBOSE = os.environ.get("ANI_VERBOSE") == "1"


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError)):
            super().handle_error(request, client_address)  # the player closing sockets is routine


def fetch(url, referer, retries=5):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:  # network hiccups and the host's 5xx are routine
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"giving up on {url}: {last}")


def parse_playlist(text, base):
    """-> (segments [(duration, absolute_url)], target_duration). Raises on encryption / master lists."""
    segs, dur, target = [], None, 10
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            raise RuntimeError("encrypted stream")
        if line.startswith("#EXT-X-STREAM-INF"):
            raise RuntimeError("master playlist")
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(float(line.split(":")[1]))
        elif line.startswith("#EXTINF:"):
            dur = float(line.split(":")[1].split(",")[0])
        elif line and not line.startswith("#") and dur is not None:
            segs.append((dur, urljoin(base, line)))
            dur = None
    if not segs:
        raise RuntimeError("no segments")
    return segs, target


class Stream:
    def __init__(self, workdir, segs, target, referer):
        self.workdir, self.segs, self.target, self.referer = workdir, segs, target, referer
        self.lock = threading.Lock()
        self.done = set()
        self.events = {}  # segment -> Event, present while downloading or done
        self.cursor = 0   # where the background download should work next
        self.fails = {}
        self.stop = threading.Event()

    def path(self, i):
        return os.path.join(self.workdir, f"seg_{i:05d}.ts")

    def playlist(self):
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{self.target}",
                 "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
        for i, (dur, _) in enumerate(self.segs):
            lines += [f"#EXTINF:{dur:.3f},", f"seg_{i:05d}.ts"]
        lines.append("#EXT-X-ENDLIST")
        return ("\n".join(lines) + "\n").encode()

    def ensure(self, i):
        """Block until segment i is on disk, downloading it ourselves if nobody is."""
        with self.lock:
            if i in self.done:
                return
            ev = self.events.get(i)
            mine = ev is None
            if mine:
                ev = self.events[i] = threading.Event()
        if not mine:
            ev.wait(180)
            if i not in self.done:
                raise RuntimeError(f"segment {i} unavailable")
            return
        try:
            data = fetch(self.segs[i][1], self.referer, retries=8)
            part = self.path(i) + ".part"
            with open(part, "wb") as f:
                f.write(data)
            os.replace(part, self.path(i))
            with self.lock:
                self.done.add(i)
        finally:
            with self.lock:
                if i not in self.done:
                    del self.events[i]  # let someone retry it
            ev.set()

    def request(self, i):
        """Called when the player wants segment i: move the background download there."""
        with self.lock:
            if i not in self.done:
                self.cursor = i
        self.ensure(i)

    def next_index(self):
        with self.lock:
            n = len(self.segs)
            for k in range(n):
                i = (self.cursor + k) % n
                if i not in self.done and i not in self.events and self.fails.get(i, 0) < 5:
                    return i
        return None

    def background(self):
        while not self.stop.is_set():
            i = self.next_index()
            if i is None:
                return
            try:
                self.ensure(i)
            except Exception:
                with self.lock:
                    self.fails[i] = self.fails.get(i, 0) + 1
                time.sleep(2)


def make_handler(stream, sub_bytes):
    seg_re = re.compile(r"^/seg_(\d{5})\.ts$")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            if VERBOSE:
                sys.stderr.write("[serve] " + fmt % a + "\n")

        def send_body(self, body, ctype, head_only=False):
            total, status, content_range = len(body), 200, None
            rng = self.headers.get("Range")
            if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)) and (m.group(1) or m.group(2)):
                a = int(m.group(1)) if m.group(1) else max(total - int(m.group(2)), 0)
                b = min(int(m.group(2)), total - 1) if m.group(1) and m.group(2) else total - 1
                body, status = body[a:b + 1], 206
                content_range = f"bytes {a}-{b}/{total}"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if content_range:
                self.send_header("Content-Range", content_range)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def serve(self, head_only):
            try:
                if self.path.split("?")[0] == "/index.m3u8":
                    return self.send_body(stream.playlist(), "application/vnd.apple.mpegurl", head_only)
                if self.path.split("?")[0] == "/subs.vtt" and sub_bytes:
                    return self.send_body(sub_bytes, "text/vtt; charset=utf-8", head_only)
                m = seg_re.match(self.path.split("?")[0])
                if m and int(m.group(1)) < len(stream.segs):
                    i = int(m.group(1))
                    stream.request(i)
                    with open(stream.path(i), "rb") as f:
                        return self.send_body(f.read(), "video/mp2t", head_only)
                self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                sys.stderr.write(f"anime: {e}\n")
                try:
                    self.send_error(502)
                except Exception:
                    pass

        def do_GET(self):
            self.serve(False)

        def do_HEAD(self):
            self.serve(True)

    return H
