#!/usr/bin/env bash
# Usage: ./watch.sh <anime_slug>   (e.g. ./watch.sh hunter_x_hunter)
#        ./watch.sh                (pick an anime from a list)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
  # No slug given: pick one from the list (fzf if available, else a numbered menu)
  mapfile -t slugs < <(find "$ROOT/animes" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
  ((${#slugs[@]})) || {
    echo "No animes in $ROOT/animes" >&2
    exit 1
  }
  if command -v fzf >/dev/null; then
    slug=$(printf '%s\n' "${slugs[@]}" | fzf --prompt="anime> " --height=40% --reverse) || exit 0
  else
    PS3="Pick an anime: "
    select slug in "${slugs[@]}"; do [[ -n $slug ]] && break; done
  fi
  set -- "$slug"
fi

CONF="$ROOT/animes/$1/anime.conf"
[[ -f $CONF ]] || {
  echo "No config at $CONF" >&2
  exit 1
}
# shellcheck source=/dev/null
source "$CONF"

next=$((EPISODE + 1))
prev=$((EPISODE > 1 ? EPISODE - 1 : 1))

echo "$NAME — last watched: ep $EPISODE${TOTAL:+/$TOTAL}"
echo "  [n] next (ep $next)   [r] repeat (ep $EPISODE)   [p] previous (ep $prev)   [number] jump   [q] quit"
read -rp "> " choice

case "$choice" in
n | "") ep=$next ;;
r) ep=$EPISODE ;;
p) ep=$prev ;;
q) exit 0 ;;
*[!0-9]*)
  echo "Invalid choice" >&2
  exit 1
  ;;
*) ep=$choice ;;
esac
((ep >= 1)) || {
  echo "No episode watched yet; use n or a number." >&2
  exit 1
}
((TOTAL == 0 || ep <= TOTAL)) || {
  echo "Episode $ep > total $TOTAL" >&2
  exit 1
}

cmd=(ani-cli -e "$ep" -S "$SELECT" -q "$QUALITY")
[[ $MODE == dub ]] && cmd+=(--dub)
cmd+=("$QUERY")

echo "Running: $(printf '%q ' "${cmd[@]}")"
"${cmd[@]}"

read -rp "Mark episode $ep as watched? [Y/n] " ok
if [[ ${ok:-y} =~ ^[Yy]$ ]]; then
  sed -i "s/^EPISODE=.*/EPISODE=$ep/" "$CONF"
  echo "Saved: EPISODE=$ep"
fi
