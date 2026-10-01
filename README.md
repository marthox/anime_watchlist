# Anime Watchlist

The objective of this small personal project is to keep
track of all the animes I'm watching through the ani-cli
tool, for anime watching using the command line interface

## How it works

By using a .sh file and a related .conf file, I'll be ni-cli
to make easier the process to continue an anime where it was
left, the sh script will read the .conf file in order to create
the execution command for the ani-cli tool to be able to start
the new episode.

For example if I'm watching hunter x hunter, or fullmetal alchemist
brotherhood, I should be able to get inside the project and run the
.sh file related to the anime I want to watch, like ./hunterxhunter.sh,
and it will instantly asked me if I want to watch the last episode, i.e
the episode 7 or I want to go the next or the previous one.

## Requirements

| Tool | Needed by | Notes |
| --- | --- | --- |
| bash 4+ | `watch.sh`, `add.sh` | uses `mapfile`; any modern Linux/WSL has it |
| [ani-cli](https://github.com/pystardust/ani-cli) | `watch.sh` | does the actual streaming; must be on your `PATH` (it brings its own player, e.g. mpv, and other dependencies, see its README) |
| [fzf](https://github.com/junegunn/fzf) | `add.sh` (required), `watch.sh` (optional) | fuzzy picker; without it `watch.sh` falls back to a numbered menu |
| curl | `add.sh` | fetches search results and episode counts |
| coreutils, sed, grep, find | both scripts | standard on Linux/WSL |

Make the scripts executable once: `chmod +x add.sh watch.sh`.

## Structure

```bash
add.sh                                # search + add a new anime to the watchlist
watch.sh                              # generic launcher
animes/<slug>/anime.conf              # one config per anime
```

Run `./watch.sh` to pick an anime from a list (fzf, or a numbered menu if
fzf isn't installed), or pass a slug directly: `./watch.sh hunter_x_hunter`.
The script prints the exact command
(e.g. `ani-cli -e 8 -S 1 -q best 'hunter x hunter 2011'`)
and saves `EPISODE` back to the .conf after you confirm.

## Config fields

| Field | Meaning | ani-cli flag |
| --- | --- | --- |
| NAME | display name | – |
| QUERY | search string | positional |
| SELECT | Nth search result | `-S` |
| MODE | `sub` / `dub` | `--dub` |
| QUALITY | `best`, `720`, … | `-q` |
| TOTAL | total episodes (0 = unknown) | – |
| EPISODE | last watched episode | `-e` (next = EPISODE+1) |

## Adding a new anime

`./add.sh [search terms]` searches (same source as ani-cli), lets you pick a
result with fzf, and creates `animes/<slug>/anime.conf` with `EPISODE=0` and
the total episode count. It does not start playback. `SELECT` stores the
result's position, which matters when several results share a title (e.g.
the two "Hunter x Hunter" series).
