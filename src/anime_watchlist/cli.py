import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import re

from . import config, db, search


def fmt_time(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def need(tool: str) -> None:
    if not shutil.which(tool):
        sys.exit(f"anime: '{tool}' is required but was not found on PATH")


def pick(rows: list[tuple[str, object]], prompt: str):
    """fzf over (label, value) rows; None if cancelled."""
    labels = [label for label, _ in rows]
    out = subprocess.run(
        ["fzf", "--prompt", prompt, "--height", "40%", "--reverse", "--no-sort"],
        input="\n".join(labels), capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    chosen = out.stdout.rstrip("\n")
    return next(value for label, value in rows if label == chosen)


def pick_show(shows: dict[str, config.Show], con) -> config.Show | None:
    def recency(show):
        last = db.last_played(con, show.slug)
        return last["ended_at"] if last else ""

    rows = []
    for show in sorted(shows.values(), key=recency, reverse=True):
        last = db.last_played(con, show.slug)
        if last is None:
            note = "not started"
        elif last["finished"]:
            note = f"last: ep {last['episode']}"
        else:
            note = f"ep {last['episode']} at {fmt_time(last['end_pos_sec'])}"
        rows.append((f"{show.name}  ({note})", show))
    return pick(rows, "anime> ")


def build_menu(show: config.Show, con) -> list[tuple[str, tuple[int, int]]]:
    """-> [(label, (episode, start_pos_sec))] for what makes sense given the history."""
    last = db.last_played(con, show.slug)
    highest = db.highest_finished(con, show.slug)

    def valid(ep: int) -> bool:
        return ep >= 1 and (not show.total or ep <= show.total)

    options: list[tuple[str, tuple[int, int]]] = []

    def add(label: str, ep: int, pos: int = 0) -> None:
        if valid(ep) and all(target != (ep, pos) for _, target in options):
            options.append((label, (ep, pos)))

    if last is None:
        add("Start → ep 1", 1)
    else:
        ep = last["episode"]
        pos = db.resume_position(con, show.slug, ep)
        if pos > 0:
            add(f"Resume ep {ep} at {fmt_time(pos)}", ep, pos)
        else:
            add(f"Continue → ep {ep + 1}", ep + 1)
        if highest and highest != ep:
            add(f"Next after highest watched → ep {highest + 1}", highest + 1)
        add(f"Repeat ep {ep} from the start", ep)
        add(f"Previous → ep {ep - 1}", ep - 1)
    options.append(("Jump to episode…", (-1, 0)))
    return options


def select_episode(show: config.Show, con) -> tuple[int, int] | None:
    """fzf list of all episodes with a mark next to the ones already watched."""
    if not show.total:
        raw = input("Episode number: ").strip()
        return (int(raw), db.resume_position(con, show.slug, int(raw))) if raw.isdigit() and int(raw) >= 1 else None
    status = db.episode_status(con, show.slug)
    rows = []
    for ep in range(1, show.total + 1):
        done, pos = status.get(ep, (False, 0))
        mark = f"▶ {fmt_time(pos)}" if pos else ("✓" if done else "")
        rows.append((f"Episode {ep:<4} {mark}".rstrip(), (ep, pos)))
    return pick(rows, "Select episode> ")


def post_menu(show: config.Show, episode: int, con) -> list[tuple[str, tuple[int, int]]]:
    """What to do after an episode: same idea as ani-cli's next/replay/previous/select/quit."""
    pos = db.resume_position(con, show.slug, episode)
    options: list[tuple[str, tuple[int, int]]] = []
    if pos > 0:
        options.append((f"Resume ep {episode} at {fmt_time(pos)}", (episode, pos)))
    if not show.total or episode < show.total:
        options.append((f"Next → ep {episode + 1}", (episode + 1, 0)))
    options.append((f"Replay ep {episode}", (episode, 0)))
    if episode > 1:
        options.append((f"Previous → ep {episode - 1}", (episode - 1, 0)))
    options.append(("Select episode…", (-1, 0)))
    options.append(("Quit", (-2, 0)))
    return options


AUTO_DELAY_SEC = 5


def auto_continue(show: config.Show, next_ep: int) -> bool:
    """Countdown before playing the next episode on its own; Ctrl+C cancels (returns False)."""
    try:
        for left in range(AUTO_DELAY_SEC, 0, -1):
            print(f"\rNext: ep {next_ep} of {show.name} in {left}s (Ctrl+C for the menu) ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        return False
    print()
    return True


def run_ani_cli(show: config.Show, episode: int, start_pos: int) -> int:
    need("ani-cli")
    with tempfile.TemporaryDirectory(prefix="anime_shim_") as shim_dir:
        # ani-cli chooses the player's argument style from its *name*; 'mpv' gives the layout player.py parses
        shim = os.path.join(shim_dir, "mpv-anime")
        with open(shim, "w") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -m anime_watchlist.player "$@"\n')
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
        env = {
            **os.environ,
            "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
            "ANI_CLI_PLAYER": "mpv-anime",
            "ANIME_SLUG": show.slug,
            "ANIME_EPISODE": str(episode),
            "ANIME_START_POS": str(start_pos),
        }
        cmd = ["ani-cli", "--exit-after-play", "-e", str(episode), "-S", str(show.select), "-q", show.quality]
        if show.mode == "dub":
            cmd.append("--dub")
        cmd.append(show.query)
        return subprocess.run(cmd, env=env).returncode


def watch(slug: str | None, auto: bool = True) -> None:
    need("fzf")
    shows = config.load()
    if not shows:
        sys.exit("anime: no shows yet; run 'anime migrate' or 'anime add'")
    with db.connect() as con:
        if slug:
            show = shows.get(slug) or sys.exit(f"anime: unknown show '{slug}' (see 'anime list')")
        else:
            show = pick_show(shows, con)
            if show is None:
                return
        menu = build_menu(show, con)
        prompt = f"{show.name}> "
        continue_auto = False
        while True:  # our menus replace ani-cli's own next/replay/previous prompt
            choice = menu[0][1] if continue_auto else pick(menu, prompt)
            if choice is None or choice == (-2, 0):
                return
            if choice[0] == -1:
                choice = select_episode(show, con)
                if choice is None:
                    continue
            episode, start_pos = choice
            print(f"{show.name} — ep {episode}" + (f" (from {fmt_time(start_pos)})" if start_pos else ""))
            before = db.last_played(con, show.slug)
            before_id = before["id"] if before else 0
            if run_ani_cli(show, episode, start_pos) != 0:
                print("anime: ani-cli exited with an error (pick again or Esc to quit)", file=sys.stderr)
            last = db.last_played(con, show.slug)
            if last and last["id"] > before_id and last["episode"] == episode:  # a session saved by this run
                state = "finished" if last["finished"] else f"stopped at {fmt_time(last['end_pos_sec'])}"
                print(f"Saved: ep {episode} {state}, watched {fmt_time(last['watched_sec'])}")
            else:
                last = None
                print("Session too short to save (under a minute watched).")
            next_ep = episode + 1
            if (auto and last and last["finished"]
                    and (not show.total or next_ep <= show.total) and auto_continue(show, next_ep)):
                menu, prompt = [("", (next_ep, 0))], ""  # skip the menu: play straight on
                continue_auto = True
            else:
                menu, prompt = post_menu(show, episode, con), f"Played ep {episode} of {show.name}> "
                continue_auto = False


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "show"


def add(query: str | None) -> None:
    need("fzf")
    query = query or input("Search anime: ").strip()
    if not query:
        return
    try:
        results = search.search(query)
    except OSError as e:
        sys.exit(f"anime: search failed ({e})")
    if not results:
        sys.exit(f"anime: no results for '{query}'")
    rows = [(title, (i, sid, title)) for i, (sid, title) in enumerate(results, start=1)]
    picked = pick(rows, "Add anime> ")
    if picked is None:
        return
    select, show_id, name = picked
    shows = config.load()
    slug = slugify(name)
    if slug in shows:
        sys.exit(f"anime: '{slug}' is already in animes.toml")
    mode = pick([("sub", "sub"), ("dub", "dub")], "Mode> ") or "sub"
    try:
        total = search.episode_count(show_id)
    except OSError:
        total = 0
    config.append(config.Show(slug=slug, name=name, query=query, select=select, mode=mode, total=total))
    print(f"Added {name} ({total or '?'} episodes). Watch it with: anime watch {slug}")


def mark(slug: str, episode: int) -> None:
    with db.connect() as con:
        db.import_progress(con, slug, episode)
    print(f"Marked {slug} ep {episode} as watched")


def main() -> None:
    parser = argparse.ArgumentParser(prog="anime", description="Personal anime watchlist on top of ani-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="pick an episode and play it")
    w.add_argument("slug", nargs="?", help="show to watch (default: pick with fzf)")
    w.add_argument("--no-auto", action="store_true", help="don't start the next episode automatically")
    sub.add_parser("list", help="list the shows in animes.toml")
    m = sub.add_parser("mark", help="mark an episode as watched without playing it")
    m.add_argument("slug")
    m.add_argument("episode", type=int)
    a = sub.add_parser("add", help="search for a show and add it to animes.toml")
    a.add_argument("query", nargs="*", help="search terms (asked for if omitted)")
    args = parser.parse_args()

    if args.command == "watch":
        watch(args.slug, auto=not args.no_auto)
    elif args.command == "mark":
        mark(args.slug, args.episode)
    elif args.command == "add":
        add(" ".join(args.query))
    elif args.command == "list":
        for show in config.load().values():
            print(f"{show.slug:36} {show.name} ({show.total or '?'} eps)")
