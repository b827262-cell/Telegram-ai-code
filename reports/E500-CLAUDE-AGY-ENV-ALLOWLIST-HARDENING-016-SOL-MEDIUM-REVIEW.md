# E500-CLAUDE-AGY-ENV-ALLOWLIST-HARDENING-016 — Sol medium review

## Verdict

PASS. READY_TO_PUSH: yes.

## Reviewed baseline and scope

- Baseline before 016: `fd28223bd64447a44ec0e4ee0b3406b3bfd803fb`.
- 016 code commit: `72c9a19bb095efc8ad50018f34e100488cf78d19`.
- Parent: `fd28223bd64447a44ec0e4ee0b3406b3bfd803fb`.
- Exact code/test scope: 4 paths only.
- `bridge/sandbox.py`: sandboxed Claude/AGY use `bwrap --clearenv` and explicit `HOME`,`PATH`.
- `danger-full-access` remains unrestricted by definition; Codex argv/sandbox path is unchanged.

## Independent Sol gates

- `git diff --check`: PASS.
- Focused 016/015: 50 passed + 9 subtests.
- Full unittest: 213 tests, OK.
- Full pytest: 236 passed + 78 subtests.
- Skips: 0.
- Controlled proc-bypass probe: direct child sentinel absent; no readable `/proc/*/environ` contained the sentinel.
- Pre-commit real account canary: Claude RC=0 exact token match; AGY RC=0 exact token match.
## Post-commit runtime verification

- `gpt-codex-worker.service` restarted through the user systemd bus.
- Old PID: `3298365`; new PID: `3314526`.
- New worker start: 2026-08-29 21:27:08 CST.
- API health: HTTP 200, `ok=true`.
- Durable Claude canary: `job-7e0ae26e0ad24840`, read-only, succeeded, exact token match.
- Durable AGY canary: `job-d0fa68055c614831`, read-only, succeeded, exact token match.
- Both jobs therefore exercised API → queue → restarted worker → 016 bwrap environment policy → real provider authentication.

## Security conclusion

The 015 environment-inheritance finding is closed for sandboxed Claude/AGY children: arbitrary host variables are not inherited by the child, while file-backed OAuth continues to authenticate with the explicit environment policy. No sandbox downgrade or broad-environment fallback was required.

Residual boundary is unchanged: 015/016 are filesystem/mount and child-environment hardening, not network/PID/IPC/seccomp isolation. `PATH` remains an explicitly forwarded runtime input and should remain host-controlled.

SOL_MEDIUM_ENV_ALLOWLIST_016_REVIEW_COMPLETE
