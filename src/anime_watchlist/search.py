"""Searches the same source ani-cli uses, to add new shows."""
import html
import re
import urllib.parse
import urllib.request

BASE = "https://hianime.at"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
RESULT = re.compile(r'<h3 class="film-name">\s*<a href="[^"]*/([^"/]*)"\s*title="([^"]*)"')


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def search(query: str) -> list[tuple[str, str]]:
    """-> [(id, title)]; the position in this list is what ani-cli's -S flag expects (1-based)."""
    page = _get(f"{BASE}/search?keyword={urllib.parse.quote_plus(query)}")
    page = page.split('id="main-sidebar"')[0]  # the sidebar repeats the markup with a top-10 list
    chunks = page.replace("\n", " ").split('<div class="film-detail">')[1:]
    results = []
    for chunk in chunks:
        m = RESULT.search(chunk)
        if m:
            results.append((m.group(1), html.unescape(m.group(2))))
    return results


def episode_count(show_id: str) -> int:
    page = _get(f"{BASE}/api/theme/episode/list/{show_id.rsplit('-', 1)[-1]}")
    page = page.replace("\\", "").replace("ep-item", "\nep-item")
    pattern = re.compile(rf"data-number=.*/watch/{re.escape(show_id)}[?]ep=")
    return sum(1 for line in page.split("\n") if pattern.search(line))
