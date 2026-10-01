# Anime Watchlist

The objective of this small personal project is to keep
track of all the animes I'm watching through the ani-cli
tool, for anime watching using the command line interface

## How it works

`anime` is a small Python CLI on top of [ani-cli](https://github.com/pystardust/ani-cli).
ani-cli does the hard part (searching the provider and resolving the stream); this
project adds the watchlist around it:

- **Shows** live in `animes.toml` (hand-editable, committed).
- **History** lives in a SQLite database in `data/` (git-ignored), one row per watching
  session with start, end, position and time actually spent playing, so it can
  resume where you stopped and later feed per-day / per-week stats.
- **Playback** goes through a local server (`player.py` / `stream.py`) that downloads
  the stream's segments in parallel (the host caps each connection below playback
  speed) and plays them in Windows VLC, polling VLC's web interface for the position.

## Requirements

| Tool | Notes |
| --- | --- |
| [uv](https://docs.astral.sh/uv/) | project and environment manager (`brew install uv`) |
| [ani-cli](https://github.com/pystardust/ani-cli) | on your `PATH` |
| [fzf](https://github.com/junegunn/fzf) | every picker in the CLI |
| VLC for Windows | the player (WSL2); other players work but are not tracked |

Python 3.11+ and nothing else: the standard library covers the rest.

## Usage

```bash
uv run anime add [search terms]    # search, pick a result, append it to animes.toml
uv run anime watch [slug]          # pick a show (fzf) and an episode, then play
uv run anime watch --no-auto       # don't auto-start the next episode
uv run anime mark <slug> <ep>      # count an episode as watched without playing it
uv run anime list                  # shows in animes.toml
```

`uv tool install --editable .` puts `anime` on your `PATH` so the `uv run` prefix isn't needed.

After an episode:
- finishing it (95% or more, or VLC reaching the end) starts the next one after a
  5-second countdown; Ctrl+C there opens the menu instead,
- otherwise an fzf menu offers Resume / Next / Replay / Previous / Select episode / Quit.

Sessions under a minute of actual playback are discarded.

## animes.toml

```toml
[hunter_x_hunter]            # the slug, used as `anime watch hunter_x_hunter`
name    = "Hunter x Hunter (2011)"
query   = "hunter x hunter 2011"   # search string passed to ani-cli
select  = 1                        # -S: Nth search result
mode    = "sub"                    # sub | dub
quality = "best"
total   = 148                      # 0 = unknown
```

`mode` and `quality` can be omitted, or set once in a `[defaults]` table.
`select` stores the result's position, which matters when several results share a
title (e.g. the two "Hunter x Hunter" series).

## Data

| Variable | Default | |
| --- | --- | --- |
| `ANIME_DB` | `data/anime.db` in the project | SQLite history (git-ignored) |
| `ANIME_HOME` | the repo root | where `animes.toml` is read from |
| `ANI_PLAYER_BIN` | Windows VLC | player executable |
| `ANI_PLAYER_ARGS` | – | extra player arguments |
| `ANI_WORKERS` | `12` | parallel segment downloads |

The `sessions` table, for the stats to come:

```sql
-- minutes watched per day
SELECT date(started_at) AS day, SUM(watched_sec) / 60 AS minutes
FROM sessions WHERE source = 'play' GROUP BY day ORDER BY day;
```
