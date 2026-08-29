# E500 Bridge — Self-Contained Green Baseline (pre-014)

Status: `SELF_CONTAINED_BASELINE = PASS`
Date: 2026-08-29
Push state: local only, nothing published to any remote.

## Why this exists

`3a99346` was created as the 011–013 reviewed-behavior anchor with an explicitly
scoped 13-file set. It is a valid attribution anchor, but it is **not** a runnable
baseline: extracting it with `git archive` into a clean directory fails test
collection.

```
bridge/queue.py:15: in <module>
    from .models import (
E   ImportError: cannot import name 'DC_EXECUTION_KIND' from 'bridge.models'
```

`bridge/queue.py` is committed and imports four names that only existed in the
uncommitted `bridge/models.py`:

| Imported by committed `queue.py` | Present in committed `models.py` |
| --- | --- |
| `DC_EXECUTION_KIND` | MISSING |
| `DEFAULT_EXECUTION_KIND` | MISSING |
| `validate_external_publication_enabled` | MISSING |
| `validate_execution_kind` | MISSING |
| `DEFAULT_PROVIDER`, `WORKFLOW_STAGES`, `Job`, `Notification`, `Workflow`, `validate_provider` | present |

Task 014 changes workspace routing, allowlisting, and the API entry point — files
that sit directly on top of this same import graph. Building 014 on a SHA that
cannot collect its own tests would make every later failure ambiguous between
"014 introduced this" and "the baseline was already broken."

## Closure was derived from test evidence, not from relevance

The candidate list (`api.py`, `claude_runner.py`, `observability.py`,
`test_api.py`, `test_api_logging.py`, …) was **not** assumed. Each file was tested
by asking whether the isolated checkout still failed without it.

Overlaying `bridge/models.py` alone was sufficient: collection went from 8 errors
to 0, and both suites passed. Nothing else was required.

Corroboration that `models.py` is genuinely a dependency and not smuggled feature
work — every symbol the 42-line additive diff introduces is consumed by already
committed code:

| New symbol in `models.py` | Committed consumers |
| --- | --- |
| `DC_EXECUTION_KIND`, `DEFAULT_EXECUTION_KIND`, `validate_execution_kind` | `bridge/queue.py` |
| `validate_external_publication_enabled` | `bridge/queue.py` |
| `external_publication_enabled`, `execution_kind`, `idempotency_key`, `attempts` | `bridge/queue.py`, `tests/test_queue.py`, `tests/test_notifications.py` |

So the minimum dependency closure is exactly one file. The remaining 14
tracked-modified and 102 untracked entries were deliberately left alone.

## Commit graph

```
654da77   chore(bridge): manage native API with systemd
   │
   ▼
3a99346   bridge: finalize report and needs-attention semantics
   │      011–013 attribution anchor · 13 files · NOT self-contained
   ▼
17cf2c4   bridge: complete self-contained baseline for routing work
   │      dependency anchor · 1 file · runnable green baseline
   ▼
   (014 durable workspace routing starts here)
```

| SHA | Role | Scope |
| --- | --- | --- |
| `3a9934693d4c3c78bdf6f384f054a2aedab4f0bf` | 011–013 reviewed behavior anchor | 13 files |
| `17cf2c4b2aaf76a30748d93560858a0a2c741d8e` | dependency-complete runnable baseline | 1 file |
| `17cf2c4..<014>` | durable workspace routing only | future |

### Exact path list — `3a99346` (`git diff 654da77..3a99346 --name-status`)

```
M  gpt-codex-bridge/bridge/agy_runner.py
M  gpt-codex-bridge/bridge/codex_runner.py
M  gpt-codex-bridge/bridge/queue.py
M  gpt-codex-bridge/bridge/worker.py
M  gpt-codex-bridge/tests/test_codex_runner.py
M  gpt-codex-bridge/tests/test_queue.py
M  gpt-codex-bridge/tests/test_worker.py
A  reports/E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013-AGY.md
A  reports/E500-BRIDGE-NEEDS-ATTENTION-SEMANTICS-013-CLAUDE-REVIEW.md
A  reports/E500-CODEX-REPORT-CONTRACT-HARDEN-012-AGY.md
A  reports/E500-CODEX-REPORT-CONTRACT-HARDEN-012-CLAUDE-REVIEW.md
A  reports/E500-V19-PRODUCTION-SMOKE-FIX-011-AGY.md
A  reports/sites-version-state.json
```

### Exact path list — `17cf2c4` (`git diff 3a99346..17cf2c4 --name-status`)

```
M  gpt-codex-bridge/bridge/models.py
```

1 file changed, 42 insertions(+), 0 deletions. Purely additive.

## Gates

### Pre-commit, in the working tree

| Gate | Result |
| --- | --- |
| `git diff --cached --check` | `CACHED_CHECK_CLEAN` |
| `python3 -m unittest discover -s gpt-codex-bridge/tests -t gpt-codex-bridge` | `Ran 126 tests … OK` |
| `python3 -m pytest -q tests` (cwd `gpt-codex-bridge`) | `149 passed, 26 subtests passed` |
| Staged file count | 1 (`bridge/models.py`) |

Note on the literal command from the plan: `python3 -m unittest discover -s
gpt-codex-bridge/tests` run from the repository root reports 12 errors. Those are
`unittest.loader._FailedTest` import-path artifacts — without `-t
gpt-codex-bridge`, `bridge` is not on `sys.path`. This is an invocation issue, not
a code failure, and it predates this work. The equivalent command with the correct
top-level dir is green, as shown above.

### Post-commit, isolated checkout

```
git archive HEAD | tar -x -C /tmp/baseline-verify
```

The target was confirmed to be outside any git repository (`git rev-parse HEAD`
→ `fatal: 不是一個 git 版本庫`), so nothing from the dirty working tree can leak
in. The archive also carries no `.env`, so no live secrets are present.

| Gate | Result |
| --- | --- |
| `python3 -m unittest discover -s tests -t .` | `Ran 116 tests … OK` |
| `python3 -m pytest -q tests` | `116 passed, 26 subtests passed` |
| Collection errors | `0` |
| Import sweep `bridge/`, `adapters/`, `tests/` | `25/25 ok, failures=0` |
| Committed `models.py` vs working-tree `models.py` | byte-identical → runtime matches baseline |

`116` here vs `149` in the working tree is the expected delta: the 33 additional
tests come from `tests/test_api.py` and `tests/test_api_logging.py`, which are
still untracked and were correctly excluded from the closure.

```
COLLECTION_ERRORS = 0
UNITTEST          = PASS
PYTEST            = PASS
SELF_CONTAINED_BASELINE = PASS
```

### Non-regression on the dirty tree

`git status --short` after commit: 14 tracked-modified + 102 untracked entries,
all preserved untouched. `models.py` correctly moved out of the modified list.
Bridge files still intentionally outside both anchors: `bridge/api.py`,
`bridge/claude_runner.py`, `bridge/github_reporter.py`,
`bridge/observability.py`, `adapters/telegram.py`, `.env.example`, `README.md`,
`tests/test_api.py`, `tests/test_api_logging.py`,
`tests/test_claude_router.py`, `tests/test_github_reporter.py`,
`tests/test_telegram_adapter.py`.

## Verification method note

The archive was extracted with `git archive` rather than `git worktree add` / `git
checkout` specifically so that the uncommitted tree was never mutated. Temp
directories `/tmp/dep-check`, `/tmp/dep-iter`, `/tmp/baseline-verify` were used
only as scratch and are not part of the repository.

## Consequence for 014

`17cf2c4` is now the correct base. Any 014 failure is attributable to 014 unless
it first reproduces at `17cf2c4` in an isolated checkout.

014 scope stays as agreed: request carries a workspace **alias**; a server-side
resolver maps alias → absolute path constrained to `CODEX_ALLOWED_WORKSPACES`;
per-workspace policy selects allowed providers, maximum sandbox mode, and
publication policy; caller-supplied filesystem paths are rejected outright.
Acceptance must cover unknown alias (fail closed), absolute-path input (rejected),
`bridge` alias (exact path), `meeting-room` alias (existing behavior unchanged),
`workspace-write` on `bridge` editing a controlled specimen, path escape and
symlink (blocked), `read-only` (still cannot write), `noExternalWrite`
(containment unchanged), and identical Telegram/API semantics. Route: direct
implementation → durable chain verification → Opus 5 review.
