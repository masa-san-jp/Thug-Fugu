# Codex implementation guide

This file is the repository-level operating contract for Codex and other
implementation agents. User instructions take precedence over this file;
Issue-specific instructions take precedence over general defaults when they do
not conflict with the user or this repository contract.

## Mission

The active product objective is high-performance parallel local-LLM execution.
For the performance roadmap, [GitHub #125](https://github.com/masa-san-jp/Thug-Fugu/issues/125)
is the SSOT. Quality strategy and Go/Pivot/No-Go decisions remain in #69/#106.

## Select work

1. Inspect the worktree before doing anything: `git status --short --branch`.
2. Read the parent SSOT and the selected Issue completely.
3. Select an Issue labelled `performance`, `work-package`, and `ready` with no
   unresolved `Blocked by:` dependency.
4. Claim it on GitHub before implementation:
   - add the `in-progress` label;
   - remove `ready`;
   - comment `Claimed by Codex: <branch>; started <date>`.
5. Create a branch named `codex/issue-<number>-<short-slug>` from the current
   target branch. Never commit directly to `main`.

If an Issue is not labelled `ready`, is labelled `blocked`, or has a dependency
that is not merged, do not implement it. Do not silently change the dependency.

## Worktree safety

- Preserve pre-existing edits and untracked files. In particular, do not add,
  delete, move, or reformat `docs/operations/20260809-issue-specification-loop-report-1103.md.gdoc`.
- If a pre-existing change overlaps an allowed path, stop and report the exact
  conflict before editing it.
- Use `apply_patch` for file edits. Do not use destructive git commands.
- Never place API keys, machine-local paths, raw prompts, raw completions, or
  hostnames in committed artifacts.

## Scope control

- One branch and one PR implement one work package.
- Change only the Issue's `主担当path` unless a small wiring change is listed
  explicitly in that Issue.
- Do not perform unrelated refactors, dependency upgrades, or formatting sweeps.
- Preserve Python 3.9 compatibility, standard-library-only core dependencies,
  loopback-default security, bounded queues/threads, explicit unsupported
  behavior, and backward-compatible config loading.
- Do not treat `max_parallel_workers` as proof of physical parallelism. Use the
  capacity contract and #125 metrics.

## Implementation loop

1. Read relevant source, tests, design docs, and the Issue's non-goals.
2. Add or update focused tests before broad verification.
3. Run the smallest relevant test command from the Issue's Codex execution
   block.
4. Run `make verify-fast` during iteration and `make verify` before PR handoff.
5. Update applicable documentation, `CHANGELOG.md`, and
   `docs/audit/feature-inventory.md` when behavior, scope, evidence, or status
   changes.
6. Record the commands, results, changed paths, limitations, and artifact paths
   in the Issue and PR.

## Stop conditions

Stop and report instead of guessing when:

- the requested behavior conflicts with #125, a dependency, or an existing API;
- a change would require modifying another WP's owned path without coordination;
- a real Ollama/llama.cpp/vLLM/SGLang runtime, model download, hardware, power
  measurement, external network, or public deployment is unavailable;
- a HUMAN GATE, locked benchmark approval, security decision, or Go/Pivot/No-Go
  decision is reached;
- tests need network/socket permissions unavailable in the current environment;
- acceptance thresholds cannot be measured honestly.

For a stop, leave the worktree recoverable, explain the evidence, and add a
concise blocker comment to the Issue. Do not mark the Issue complete.

## Verification

The canonical local checks are:

```bash
python3 -m pip install -e '.[dev]'
make verify
```

`make verify-fast` runs lint, format, and the test suite without coverage/build
for quick iteration. `make verify` additionally runs coverage and package build.
The CI workflows remain authoritative for the Python-version matrix.

## Handoff

Before opening a PR:

- add `review` and remove `in-progress` on the Issue;
- use `Implements #<number>` in the PR body;
- include the relevant test commands and exact results;
- include performance artifacts and rerun commands when applicable;
- list any acceptance criteria that require a human or real hardware gate;
- do not close the parent SSOT or mark an Issue done until the acceptance
  criteria and documentation/status updates are complete.

If GitHub write access, push access, or a required external service is missing,
keep the implementation and tests local and report the exact handoff needed.
