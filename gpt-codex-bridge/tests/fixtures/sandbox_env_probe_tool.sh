#!/bin/sh
# Environment-policy probe for sandboxed children. Reports only the
# environment VARIABLE NAMES the child actually received plus presence
# markers — never values — as plain KEY=VALUE lines, or as JSON for
# agy-style --output-format. Ignores provider flags.
set -u

names=$(env | cut -d= -f1 | sort | tr '\n' ',' | sed 's/,$//')

home_set=0
[ -n "${HOME-}" ] && home_set=1
path_set=0
[ -n "${PATH-}" ] && path_set=1
sentinel_present=0
[ -n "${E500_016_SENTINEL-}" ] && sentinel_present=1

results="CHILD_ENV_NAMES=${names} HOME_SET=${home_set} PATH_SET=${path_set} SENTINEL_PRESENT=${sentinel_present}"

is_json=0
prev=""
for arg in "$@"; do
  if [ "$prev" = "--output-format" ]; then
    is_json=1
  fi
  prev="$arg"
done

if [ "$is_json" = "1" ]; then
  printf '{"response": "%s"}\n' "$results"
else
  printf '%s\n' "$results" | tr ' ' '\n'
fi
