#!/bin/sh
# Deterministic sandbox probe for enforcement tests. Ignores provider flags,
# performs filesystem and loopback-socket probes, and reports KEY=VALUE
# results on stdout as plain lines, or as JSON for agy-style --output-format.
set -u

tmp_entries=$(ls -A /tmp 2>/dev/null | wc -l)
host_leaks=$(ls -A /tmp/.sbx-host-canary-* 2>/dev/null | wc -l)

if touch /tmp/.sbx-tmp-canary 2>/dev/null; then
  tmp_write=allowed
  rm -f /tmp/.sbx-tmp-canary
else
  tmp_write=denied
fi

if touch "$PWD/.sbx-workspace-violation" 2>/dev/null; then
  workspace_write=allowed
else
  workspace_write=denied
fi

if touch "$HOME/.sbx-home-violation" 2>/dev/null; then
  home_write=allowed
  rm -f "$HOME/.sbx-home-violation"
else
  home_write=denied
fi

loopback=$(python3 -c "
import socket
try:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    print('ok')
except PermissionError:
    print('permission_denied')
except OSError:
    print('error')
" 2>/dev/null)

results="TMP_FRESH=${tmp_entries} TMP_HOST_LEAK=${host_leaks} TMP_WRITE=${tmp_write} WORKSPACE_WRITE=${workspace_write} HOME_WRITE=${home_write} LOOPBACK_BIND=${loopback}"

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
