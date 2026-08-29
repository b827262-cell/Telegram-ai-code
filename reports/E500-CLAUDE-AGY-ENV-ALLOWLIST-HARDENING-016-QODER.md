# E500-CLAUDE-AGY-ENV-ALLOWLIST-HARDENING-016 — Qoder execution report

Role: Qoder CLI implementer (GLM-5.3-Flash xhigh)
Workspace: /home/b827262/project/e500-codex-smoke
Bridge: gpt-codex-bridge
Baseline HEAD: fd28223bd64447a44ec0e4ee0b3406b3bfd803fb (verified before any edit)
Bridge tracked tree at start: clean. All unrelated dirty/untracked files preserved untouched.

## Objective

Close 015 MEDIUM-2: replace broad provider environment inheritance for sandboxed
Claude/AGY execution with a proven minimal, explicit environment policy
(`bwrap --clearenv` + `--setenv` allowlist), preserving real account
authentication and 013/014/015 semantics.

## Baseline verification

- `git rev-parse HEAD` = fd28223bd64447a44ec0e4ee0b3406b3bfd803fb (exact match).
- `git status --porcelain -- gpt-codex-bridge`: empty at start; at end contains
  only the 016 change set listed below.

## Real provider auth canaries

Method (identical before/after): harmless prompt of the form "Return exactly
this token and nothing else: <fixed-unique-token>", launched through the
production `bridge.sandbox.sandbox_launch_plan` prefix in `read-only` mode
(filesystem read-only + no external writes; private /tmp; credentials re-bound
read-only). Only success/failure, provider, model, exit status, elapsed time,
and environment KEY NAMES were recorded. No credential values exist in this
session's host env (host env carries 23 keys, none matching token/key/secret
naming patterns); auth is filesystem-backed OAuth under HOME.

| Stage | Provider | Model | Sandbox | Exit | Token returned | Elapsed |
|---|---|---|---|---|---|---|
| Before (015 broad inheritance) | claude | claude-opus-5 | read-only | 0 | yes | 4.6s |
| Before (015 broad inheritance) | agy | gemini-3.7-flash-high | read-only | 0 | yes | 4.8s |
| After (016 clearenv allowlist, main tree) | claude | claude-opus-5 | read-only | 0 | yes | 4.6s |
| After (016 clearenv allowlist, main tree) | agy | gemini-3.7-flash-high | read-only | 0 | yes | 4.6s |
| After (016, isolated candidate tree) | claude | claude-opus-5 | read-only | 0 | yes | 4.5s |
| After (016, isolated candidate tree) | agy | gemini-3.7-flash-high | read-only | 0 | yes | 6.9s |

Both providers authenticate with only the allowlisted variables; no weakening
to broad inheritance was needed at any point.

## Environment policy (key names only — no values)

Allowlist (exact, in `SANDBOX_ENV_ALLOWLIST`): `HOME`, `PATH`.

- `HOME` — required for file-backed OAuth state (`~/.claude`, `~/.gemini`)
  carved out by the 015 binds; always set to the same path the wrapper used
  for state-dir binds.
- `PATH` — required for exec/module resolution of the provider CLIs and their
  runtime; value is taken from the runner-provided environment only when
  present.
- Everything else — including `TELEGRAM_BOT_TOKEN`, `MCP_BEARER_TOKEN`,
  `MEETING_API_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
  `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `GITHUB_TOKEN`,
  `HTTPS_PROXY`/proxy credentials, arbitrary sentinels, and any future host
  variable — is dropped by `--clearenv` and never forwarded.
- `PWD` observed in the child is set by bwrap itself to the job workspace cwd;
  it is not inherited from the host.

Semantics: sandboxed `read-only`/`workspace-write` claude/agy children start
from `bwrap --clearenv` plus exactly the allowlisted `--setenv` entries, with
values sourced from the caller-provided runner environment (never a wholesale
`os.environ` copy into the child). `danger-full-access` remains unrestricted
by definition (documented deliberate compatibility, prefix stays empty). Codex
is untouched: native CLI `--sandbox` mechanism, argv prefix still `()`.

## Changed paths

- `gpt-codex-bridge/bridge/sandbox.py` — `SANDBOX_ENV_ALLOWLIST`,
  `_sandbox_env_argv`, `_bwrap_argv` env block, module docstring boundary
  statement (+46/-? lines).
- `gpt-codex-bridge/tests/test_sandbox_enforcement.py` — exact-prefix
  expectations updated; new `SandboxEnvironmentPolicyTests` (5 unit tests);
  new real-bwrap `RealChildEnvironmentPolicyTests` (2 tests).
- `gpt-codex-bridge/tests/test_agy_runner.py` — exact argv expectation updated
  for the env block.
- `gpt-codex-bridge/tests/fixtures/sandbox_env_probe_tool.sh` — new env-probe
  fixture reporting child environment VARIABLE NAMES only (never values).

Diff stat: 3 files modified (254 insertions, 10 deletions) + 1 new fixture.
No git add/commit/push or any other git mutation performed.

## Tests (exact counts, no skips)

- Focused 016/015: `pytest tests/test_sandbox_enforcement.py
  tests/test_agy_runner.py tests/test_claude_router.py` → 50 passed,
  9 subtests passed, 0 skipped.
- Full unittest (main tree): `python3 -m unittest discover -s tests` →
  213 tests, OK, 0 skipped (real-bwrap classes ran: bwrap functional here).
- Full pytest (main tree): 236 passed + 78 subtests, 0 skipped
  (baseline 229 + 74; +7 new 016 tests).
- `git diff --check`: clean.
- Isolated candidate: `git archive HEAD` extracted fresh, overlaid with only
  the four 016 paths, then full unittest (213 OK) and full pytest
  (236 + 78) — all green. Candidate-tree real auth canaries: both providers
  exit 0, tokens returned.

Note: a first candidate build under /tmp failed 6 real-bwrap tests because
bwrap's `--tmpfs /tmp` shadows a /tmp-located tree (fixture scripts vanish
inside the sandbox). This is a /tmp artifact, not a code defect; rebuilding
the identical candidate under /var/tmp passes everything.

## Preserved semantics (verified by suites)

- 015 read-only/workspace-write bwrap enforcement, fail-closed probe, and
  real kernel-denial tests: all pass unchanged.
- 013 failure/needs_attention report semantics: full suite green.
- 014 workspace routing/publication ceilings: test_workspaces,
  test_api_workspace_routing, test_telegram_workspace_routing green.
- Codex argv and native sandbox mechanism unchanged.

## Residual risks

1. Minimal allowlist is deliberately fail-closed: a future CLI version needing
   another variable (locale, XDG override) would fail sandboxed jobs with an
   honest error; add keys only with fresh canary proof, never broad
   inheritance.
2. PATH value is forwarded as-is; it shapes which binaries the child resolves.
   This is a runtime necessity, not a secret, and remains host-controlled.
3. `danger-full-access` children keep the full runner environment by
   definition; the hardening applies to sandboxed modes only.
4. Network egress for wrapped children remains unrestricted (015 boundary);
   environment hardening does not change the network story.
5. The runner-level env helpers still copy os.environ into the popen env for
   bwrap itself; the child-facing policy is enforced by `--clearenv` inside
   the wrapper, so no host secret reaches the sandboxed child.

## Recommended commit subject

bridge: fail sandboxed claude/agy child env to clearenv allowlist

## Verdict

READY_TO_COMMIT: yes

QODER_GLM53_FLASH_XHIGH_ENV_ALLOWLIST_016_COMPLETE
