# E500-GPT-CODEX-API-SYSTEMD-003 — AGY

CODEX EXECUTION REPORT

WORKSPACE:
- `/home/b827262/project/e500-codex-smoke`
- Bridge source: `/home/b827262/project/e500-codex-smoke/gpt-codex-bridge`
- User Systemd Bus: `/run/user/1000/bus` (`DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"`, `XDG_RUNTIME_DIR=/run/user/1000`)

ROOT_CAUSE / BACKGROUND:
- Terra was previously blocked due to running inside a sandbox lacking access to the user D-Bus session, socket inspection (`ss`), and write access to `~/.config/gpt-codex-bridge`.
- In this execution session, the native user bus `/run/user/1000/bus` is accessible and fully operational.
- The pre-existing bridge API was running as an unmanaged manual process (`python3 -m bridge.api`, PID 2146436) under a desktop cgroup (`.../app-org.chromium.Chromium-2050168.scope`), binding `127.0.0.1:4300`.

ACTIONS PERFORMED:
1. Reviewed prior reports `reports/E500-GPT-CODEX-API-SYSTEMD-003-TERRA.md` and `reports/E500-GPT-LOOP-POSTFIX-002-OPUS5-YOYO-REVIEW.md`.
2. Verified queue database state (`~/.local/state/gpt-codex-bridge/jobs.sqlite3`): 0 queued, 0 running jobs/workflows; safe for API handoff.
3. Mechanically transferred allowlisted runtime/API variables from mode-0600 `.env` (`CODEX_ALLOWED_WORKSPACES`, `CODEX_DEFAULT_WORKSPACE`, `CODEX_QUEUE_DB`, `CODEX_BRIDGE_API_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `TELEGRAM_BOT_TOKEN`) to `~/.config/gpt-codex-bridge/api.env` with mode 0600 (`~/.config/gpt-codex-bridge` directory mode 0700) without printing secrets.
4. Installed exact repository unit `gpt-codex-bridge/systemd/gpt-codex-api.service` to `~/.config/systemd/user/gpt-codex-api.service` (`cmp` confirmed byte-identical).
5. Executed `systemctl --user daemon-reload`.
6. Verified PID 2146436 was indeed `python3 -m bridge.api` on `127.0.0.1:4300`, safely terminated it (`kill -TERM`), and immediately enabled and started `gpt-codex-api.service` (`systemctl --user enable --now gpt-codex-api.service`), achieving sub-second handoff.
7. Verified service status is `enabled` and `active (running)`.
8. Verified MainPID cgroup ownership: `/user.slice/user-1000.slice/user@1000.service/app.slice/gpt-codex-api.service`.
9. Verified port `127.0.0.1:4300` listener ownership belongs to the new systemd service MainPID.
10. Tested HTTP `/health`:
    - Unauthenticated request: HTTP 401 `{"ok": false, "code": "BRIDGE_UNAUTHORIZED"}`
    - Invalid bearer token request: HTTP 401 `{"ok": false, "code": "BRIDGE_UNAUTHORIZED"}`
    - Valid authenticated request: HTTP 200 `{"ok": true, "service": "codex-bridge", "api": "v1", "telegramConfigured": true, "workspaceConfigured": true, "queue": {"queued": 0, "running": 0, "succeeded": 68, "failed": 7}}`
11. Tested restart resilience: restarted ONLY `gpt-codex-api.service` (`systemctl --user restart gpt-codex-api.service`), verified new MainPID ownership of port 4300, and re-verified authenticated HTTP 200 / unauthenticated HTTP 401 `/health` contract.
12. Confirmed worker process (PID 2717732), Telegram adapter process (PID 2717737), and protected process (PID 2432453) remained completely untouched and running.

CHANGED_FILES:
- `~/.config/gpt-codex-bridge/api.env` (created, mode 0600, allowlisted variables only)
- `~/.config/systemd/user/gpt-codex-api.service` (installed exact repo unit, enabled)
- `reports/E500-GPT-CODEX-API-SYSTEMD-003-AGY.md` (this report)

TESTS & VERIFICATION:
- PASS: `systemd-analyze --user verify ~/.config/systemd/user/gpt-codex-api.service` (clean, exit code 0)
- PASS: `bash -n gpt-codex-bridge/scripts/run-api.sh` (clean, exit code 0)
- PASS: `python3 -m pytest -v tests/test_api.py tests/test_api_logging.py` (26 passed in 2.20s)
- PASS: `python3 -m pytest -q` (125 passed, 17 subtests passed in 4.72s)
- PASS: `git diff --check` (clean)
- PASS: Service enabled & active verification (`systemctl --user is-enabled` -> `enabled`, `systemctl --user is-active` -> `active`)
- PASS: Process / cgroup ownership verification
- PASS: Port 4300 listener verification
- PASS: Authenticated `/health` 200 OK contract & unauthenticated 401 rejection
- PASS: Service restart test (`systemctl --user restart gpt-codex-api.service`) with subsequent health proof
- PASS: Worker PID (2717732) and protected process PID (2432453) unchanged

GIT_DIFF:
- `git diff --check` passed cleanly across both repository root and subprojects.
- No source files were modified. Pre-existing dirty/untracked files preserved untouched.
- No commit, push, deploy, remote write, workflow submission, publication, reset, clean, rebase, merge, or secret rotation occurred.

SAFETY & INTEGRITY:
- Secrets were never printed to stdout or logs.
- PID 2432453 was untouched.
- Worker PID 2717732 and Telegram PID 2717737 were untouched.
- Only the verified manual API process (PID 2146436) was stopped during the migration handoff.

FINAL VERDICT:
PASS
