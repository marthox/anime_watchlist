#!/usr/bin/env bash
# Usage: ./add.sh [search terms]
# Searches with the same source as ani-cli, lets you pick a result and creates
# animes/<slug>/anime.conf (EPISODE=0) without starting to watch it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="https://hianime.at"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

for dep in curl fzf; do command -v "$dep" >/dev/null || { echo "Missing dependency: $dep" >&2; exit 1; }; done

fetch() { curl -sL -A "$UA" --max-time 15 "$1"; }

query="${*:-}"
while [[ -z $query ]]; do read -rp "Search anime: " query; done

# same parsing as ani-cli's hianime_search -> "id<TAB>title"
results=$(fetch "$BASE/search?keyword=${query// /+}" | sed '/id="main-sidebar"/,$d' | tr '\n' ' ' |
  sed 's|<div class="film-detail">|\n<div class="film-detail">|g' |
  sed -nE 's|.*<h3 class="film-name">[[:space:]]*<a href="[^"]*/([^"/]*)"[[:space:]]*title="([^"]*)".*|\1\t\2|p' |
  sed -e "s|&#039;|'|g" -e 's|&quot;|"|g' -e 's|&amp;|\&|g')
[[ -n $results ]] || { echo "No results for '$query'" >&2; exit 1; }

# the position in this list is what ani-cli's -S flag expects
pick=$(printf '%s\n' "$results" | nl -w2 -s$'\t' | fzf --delimiter=$'\t' --with-nth=1,3 --prompt="Add anime > ") || exit 0
select_idx=$(( $(cut -f1 <<<"$pick") ))
id=$(cut -f2 <<<"$pick")
name=$(cut -f3 <<<"$pick")

total=$(fetch "$BASE/api/theme/episode/list/${id##*-}" | sed 's|\\||g; s|ep-item|\nep-item|g' |
  grep -cE "data-number=.*/watch/${id}[?]ep=" || true)

default_slug=$(tr '[:upper:]' '[:lower:]' <<<"$name" | sed -E "s/[^a-z0-9]+/_/g; s/^_+|_+$//g")
read -rp "Folder name [$default_slug]: " slug; slug=${slug:-$default_slug}
read -rp "Mode sub/dub [sub]: " mode; mode=${mode:-sub}

dir="$ROOT/animes/$slug"
[[ -e $dir ]] && { echo "$dir already exists" >&2; exit 1; }
mkdir -p "$dir"
cat > "$dir/anime.conf" <<CONF
# $name
NAME="$name"
QUERY="$query"
SELECT=$select_idx
MODE="$mode"
QUALITY="best"
TOTAL=$total
EPISODE=0
CONF

echo "Created $dir/anime.conf ($total episodes). Start with: ./watch.sh $slug"
