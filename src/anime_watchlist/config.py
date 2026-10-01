"""Reads the shows from animes.toml (hand-editable; the program only appends to it)."""
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def home() -> Path:
    """Directory holding animes.toml; defaults to the repo root (editable install)."""
    return Path(os.environ.get("ANIME_HOME", Path(__file__).resolve().parents[2]))


def toml_path() -> Path:
    return home() / "animes.toml"


@dataclass
class Show:
    slug: str
    name: str
    query: str
    select: int = 1
    mode: str = "sub"
    quality: str = "best"
    total: int = 0


def load(path: Path | None = None) -> dict[str, Show]:
    path = path or toml_path()
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    defaults = data.pop("defaults", {})
    shows = {}
    for slug, entry in data.items():
        merged = {**defaults, **entry}
        shows[slug] = Show(slug=slug, **merged)
    return shows


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def append(show: Show, path: Path | None = None) -> None:
    path = path or toml_path()
    block = (
        f"\n[{show.slug}]\n"
        f"name    = {_quote(show.name)}\n"
        f"query   = {_quote(show.query)}\n"
        f"select  = {show.select}\n"
        f"mode    = {_quote(show.mode)}\n"
        f"quality = {_quote(show.quality)}\n"
        f"total   = {show.total}\n"
    )
    with path.open("a") as f:
        f.write(block)
