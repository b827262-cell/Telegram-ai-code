# E500-GPT-CODEX-API-SYSTEMD-003 — SOL Independent Review

CODEX EXECUTION REPORT

WORKSPACE:
- `/home/b827262/project/e500-codex-smoke`
- Review subject: `reports/E500-GPT-CODEX-API-SYSTEMD-003-AGY.md` and the current E500 runtime after the native API systemd handoff.

ROOT_CAUSE:
- The prior unmanaged native API has been successfully handed off to the user unit `gpt-codex-api.service`.
- No blocker or regression was reproduced. AGY's operational and test claims are supported by the current repository and runtime state.

ACTIONS:
- Read the AGY and Terra systemd reports, the prior Opus YOYO review, the repository unit, launcher, bridge API/config implementation, repository instructions, central tooling standard, current Git state, and relevant diff/commit evidence.
- Performed read-only inspection of the installed unit, private environment file metadata and variable-name allowlist, user-service state, MainPID/cgroup, listener ownership, protected processes, HTTP health behavior, and durable queue counts.
- Re-ran the claimed targeted and full Python test suites, the structured-report regression suite, shell syntax validation, user-unit verification, and `git diff --check`.
- Did not modify source, runtime configuration, service state, credentials, Git index, refs, or remotes. Did not restart, commit, push, deploy, or publish anything.

CURRENT-STATE VERIFICATION:
- PASS — Installed unit `/home/b827262/.config/systemd/user/gpt-codex-api.service` is byte-identical to `gpt-codex-bridge/systemd/gpt-codex-api.service` (`cmp -s`).
- PASS — `/home/b827262/.config/gpt-codex-bridge/api.env` exists, is owned by UID/GID `1000:1000`, and has mode `0600`; its parent directory has mode `0700`. No value was displayed.
- PASS — The environment file contains exactly six assignments and no malformed entries. The names are the intended allowlist: `CODEX_ALLOWED_WORKSPACES`, `CODEX_DEFAULT_WORKSPACE`, `CODEX_QUEUE_DB`, `CODEX_BRIDGE_API_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, and `TELEGRAM_BOT_TOKEN`.
- PASS — With the documented native user-bus address, `systemctl --user is-enabled` reports `enabled` and `is-active` reports `active`/`running`; the unit result is `success` and automatic restart count is zero.
- PASS — MainPID `2740518` is in `/user.slice/user-1000.slice/user@1000.service/app.slice/gpt-codex-api.service`, and `ss` identifies that same Python PID as the sole listener on `127.0.0.1:4300`.
- PASS — Native unauthenticated `GET /health` returns HTTP 401 with exactly `{"ok":false,"code":"BRIDGE_UNAUTHORIZED"}`.
- PASS — Native authenticated `GET /health`, using the already-loaded service credential entirely in process memory and never printing it, returns HTTP 200 with `ok=true`, `service=codex-bridge`, `api=v1`, truthful configuration booleans, and integer queue counts.
- PASS — Health queue counts exactly match a separate read-only SQLite aggregation: queued 0, running 0, succeeded 68, failed 7.
- PASS — Worker PID `2717732` remains alive in `gpt-codex-worker.service`; Telegram PID `2717737` remains alive in `gpt-codex-telegram.service`; protected unrelated PID `2432453` remains alive in its original Chromium application scope.

TESTS:
- PASS — `python3 -m pytest -q tests/test_api.py tests/test_api_logging.py`: **26 passed in 2.20s**. AGY's targeted-test claim is independently reproduced.
- PASS — `python3 -m pytest -q`: **125 passed, 17 subtests passed in 4.63s**. AGY's full-suite claim is independently reproduced.
- PASS — `python3 -m pytest -q tests/test_codex_runner.py`: **12 passed, 11 subtests passed**.
- PASS — `bash -n gpt-codex-bridge/scripts/run-api.sh`.
- PASS — `systemd-analyze --user verify /home/b827262/.config/systemd/user/gpt-codex-api.service` using the native user bus.
- PASS — `git diff --check`.

GIT_DIFF:
- `HEAD` remains `49ce405` (`fix(bridge): preserve Codex structured reports`).
- `git diff --name-only HEAD -- gpt-codex-bridge/bridge/codex_runner.py gpt-codex-bridge/tests/test_codex_runner.py` is empty, and the dedicated regression suite passes. The structured-report fix remains intact.
- Current tracked modifications and untracked files are consistent with the pre-handoff state recorded by the Terra and Opus reports. In particular, `bridge/api.py` was already modified and the `systemd/` directory was already untracked before AGY's migration; their filesystem timestamps also predate the AGY report. No evidence attributes an unintended repository source change to the systemd handoff.
- Pre-existing dirty/untracked files are not treated as AGY changes. This review adds only this report.

SAFETY REVIEW:
- PASS — The EnvironmentFile boundary is appropriately narrow for the current implementation: exact allowlisted names, private directory/file permissions, no host/port override, and therefore the API retains its code default of loopback-only `127.0.0.1:4300`. The API enforces a minimum 32-character bearer credential and constant-time header comparison.
- PASS — `Restart=on-failure` with `RestartSec=3` is appropriate: unexpected failures recover, while an intentional stop is not immediately undone. Default control-group process handling keeps ownership within the unit.
- PASS — `NoNewPrivileges=yes` and `PrivateTmp=yes` provide a valid baseline for this unprivileged user service. The launcher uses `set -euo pipefail` and `exec`, so systemd directly tracks the Python server.
- Non-blocking defense-in-depth observation — hardening remains minimal. `systemd-analyze --user security` reports a raw exposure score of 9.2 because protections such as `ProtectSystem`, `ProtectHome`, `PrivateDevices`, address-family restrictions, `RestrictSUIDSGID`, `LockPersonality`, and `UMask` are not set. Several capability/root findings are not directly applicable to an already-unprivileged user unit, and the API legitimately needs its project and queue paths, so any tightening must be compatibility-tested. This is not a handoff regression or acceptance blocker, but a future hardening change should consider at least `PrivateDevices=yes`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`, `RestrictSUIDSGID=yes`, `LockPersonality=yes`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, and `UMask=0077`, then re-run unit, API, and queue tests.
- Non-blocking least-privilege observation — `TELEGRAM_BOT_TOKEN` is currently supplied to the API only so `/health` can truthfully compute `telegramConfigured`. A future design could pass a non-secret configuration flag instead, reducing credential exposure without changing the health contract. No token was returned by the API or exposed during review.

ERRORS:
- An initial `systemctl --user` query without the native session environment returned `Failed to connect to bus: No medium found`. Repeating the same read-only query with `XDG_RUNTIME_DIR=/run/user/1000` and `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`, exactly as documented by AGY, succeeded. This is a reviewer-shell environment issue, not a service defect.

REMAINING_ISSUES:
- None required for acceptance.
- The two defense-in-depth observations above are optional follow-up work and do not require Git finalize to stop.

RESULT:
- PASS
- Git finalize may proceed; no systemd handoff fix is required.

FINAL VERDICT: PASS
