---
name: merge-and-verify
description: Merge remote main into the current branch, resolving each conflict by asking the user a yes/no per hunk, then restart the stack and drive a Playwright end-to-end check that a new goal builds a DAG, runs it, and updates the hypothesis status, then push the branch and open a PR. Use when asked to sync with main, bring a branch up to date, or smoke-test the UI after merging.
---

# Merge main, verify end to end, open a PR

Three phases, in order. Do not start phase 2 until phase 1 leaves a clean tree,
and do not start phase 3 until phase 2 reaches a terminal status. Do not report
success for any phase from assumption — quote the command output.

## Phase 1: merge origin/main

1. Record the starting point so the merge can be undone:

   ```sh
   pwd && git status --short --branch && git worktree list
   git rev-parse HEAD
   ```

   If the tree is dirty, stop and show the user the dirty paths. Ask whether to
   stash, commit, or abort. Never discard someone else's uncommitted work, and
   never merge on top of a dirty tree so the conflict hunks stay attributable.

2. Fetch and merge. `origin/main` is only current evidence after a fetch.

   ```sh
   git fetch origin main
   git merge origin/main
   ```

   If it reports `Already up to date.`, say so and go straight to phase 2.

### Resolving conflicts

3. List the conflicted files and read each one's conflict hunks in full,
   including enough surrounding code to know what the hunk does:

   ```sh
   git diff --name-only --diff-filter=U
   ```

4. **Ask the user about every conflict hunk — one question per hunk.** Use
   `AskUserQuestion`, batching up to 4 hunks per call. For each hunk give:

   - a `header` naming the file and rough location, e.g. `agent.py:214`
   - a question stating what the two sides actually do, in one sentence each
   - options: `Keep ours (branch)`, `Take theirs (main)`, and where a genuine
     combination exists, `Combine both` with a description of the result

   Put a recommendation first and mark it `(Recommended)` when one side is
   clearly right — for example when main changed a shared contract in
   `src/node_dag/types.py` or `factory.py` that this branch merely moved.

   Do not resolve a hunk silently, including ones that look trivial. Whitespace
   and import-order hunks still get a question; they are cheap to answer. The
   only hunks you may resolve without asking are ones where one side is
   byte-identical to the merge base (git already handled those, so they will not
   appear as conflicts anyway).

5. Apply each answer by editing the file, removing every `<<<<<<<`, `=======`
   and `>>>>>>>` marker. Then confirm none survive:

   ```sh
   git grep -nE '^(<{7}|={7}|>{7})' -- . || echo "no markers left"
   ```

6. Run the checks from [AGENTS.md](../../../AGENTS.md) before committing the
   merge, because a merge touches shared contracts by definition:

   ```sh
   uv run pytest -q
   uv run ruff check .
   uv run ruff format --check .
   uv run ty check
   ```

   Report pre-existing failures separately from ones the merge caused — compare
   against the recorded starting commit if unsure. If the merge caused failures,
   tell the user and ask before proceeding rather than patching around them.

7. Stage only the conflicted files and commit the merge. If the user wants out,
   `git merge --abort` restores the recorded starting point.

## Phase 2: Playwright end-to-end check

This drives the real app with the `playwright` MCP server from
[.mcp.json](../../../.mcp.json). It makes real model calls, so it costs money
and takes minutes. Check `ANTHROPIC_API_KEY` is set before starting; if it is
missing, report the gap and stop — do not invent a key or skip to a stub.

### i. Restart the stack

```sh
make restart
```

That stops and restarts the Temporal dev server, the worker and the UI. Wait for
the UI to answer rather than sleeping a fixed time:

```sh
until curl -sf http://127.0.0.1:8000/api/nodes > /dev/null; do sleep 1; done
```

If it never comes up, read `results/logs/ui.log` and `results/logs/worker.log`
and report what they say. A UI started without `--model` returns 503 from
`/api/hypotheses`; `make restart` passes `MODEL`, so a 503 means the Makefile
variable or the environment is wrong.

### ii. Load the frontend and go to the new page

`browser_navigate` to `http://127.0.0.1:8000/`, then to `/new`.
`browser_snapshot` after each. Check `browser_console_messages` for errors — a
page that renders but logs a failed `fetch("/api/goals")` is a failure, not a
pass.

### iii. Create a goal and hypothesis

On `/new`:

- type the goal into `#goal`
- type a hypothesis into `#hypothesis`
- add at least one criterion with `#add-criterion` and fill its text input, so
  the verifier has something to judge in `Verdict.criteria`

Use a goal the installed nodes can actually serve, so a failure means a broken
pipeline rather than an impossible request. A recoding or expression goal over a
single fetched CDS is the safe default; keep it small, since the builder fetches
sequences from NCBI.

Skip `#suggest` and `#search-lit` unless the user asked to exercise them — each
is an extra agent call, and the drafts they return are not needed for a smoke
test.

Click `#go`. The page POSTs `/api/hypotheses` and redirects to
`/hypotheses?id=<id>`. Record that id from the URL; it keys every later check.

### iv. Check the DAG is built and runs

Status moves `building → running → verifying → achieved | not achieved`, with
`failed` or `interrupted` as terminal failures. Poll the API rather than
re-snapshotting the browser in a loop:

```sh
curl -s http://127.0.0.1:8000/api/hypotheses \
  | jq '.[] | select(.hypothesis.id == "<id>") | {status, wf: .hypothesis.workflow_id}'
```

Loop until the status leaves `building`/`running`/`verifying`, with a cap of
roughly 15 minutes. Then assert:

- it reached `building` → `running`, meaning a `Dag` was built and validated
- `hypothesis.workflow_id` is set, and `/api/runs/<workflow_id>` reports
  `COMPLETED`
- the run's output is non-empty

A `failed` status is a real result to report, not something to retry past.
Read `results/logs/worker.log` and `hypothesis.error` and say which layer broke:
builder validation, DAG execution, or the verifier.

### v. Check the hypothesis status updated in the UI

Navigate back to `/hypotheses?id=<id>` and `browser_snapshot`. Confirm the page
shows the same terminal status the API reported, and that the per-criterion
met/not met/unclear calls from `Verdict.criteria` are rendered. An API that
says `achieved` while the page still says `building` is a UI bug worth
reporting.

Take a `browser_take_screenshot` of the final page for the summary.

## Phase 3: push and open a PR

Only after phase 2 reaches a terminal status. Pushing and opening a PR are
outward-facing, so **show the user the final diff and the branch you are about
to push to, and get an explicit yes before the first `git push`.** One approval
covers that push; a later force-push or a change of target branch needs a fresh
one.

1. Check what would go out, and that nothing stray is staged:

   ```sh
   git status --short --branch
   git diff origin/main...HEAD --stat
   ```

   Stage only task-owned files. Never commit `.env*`, `results/` (it holds
   `temporal.db`, `logs/`, run output and the phase 2 screenshots in
   `results/playwright/`), caches or local scratch. If any of those are
   untracked and unignored, say so rather than quietly adding them.

2. If the branch is `main`, branch first — use a `codex/` name describing the
   work, not the merge. Otherwise push the current branch:

   ```sh
   git push -u origin HEAD
   ```

3. Open the PR with `gh`:

   ```sh
   gh pr create --base main --title "<title>" --body "<body>"
   ```

   If a PR for this branch already exists, do not open a second one — report the
   existing URL and, if the merge or the e2e result changes what it claims,
   offer to update its description.

   Tie the body to the live diff and the evidence actually gathered:

   - what changed, from `git diff origin/main...HEAD`, not from intent
   - how each conflict was resolved and that the user chose each resolution
   - the phase 1 check results, with pre-existing failures named as such
   - the phase 2 outcome: hypothesis id, workflow id, final status, and the
     five steps marked pass or fail
   - limitations — the e2e run is plumbing evidence only

   Do not describe the e2e run as passing if it was skipped for a missing
   `ANTHROPIC_API_KEY`, or if a step failed. Say which steps ran.

   End the body with:

   ```
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

4. Report the PR URL. Merging stays a separate human decision — do not merge,
   and do not enable auto-merge.

## Reporting

Finish with:

- which conflicts there were and how the user chose to resolve each
- the check results from phase 1, with pre-existing failures separated out
- each of the five e2e steps marked pass or fail, with the hypothesis id,
  workflow id and final status
- the PR URL, and that merging is left to the user
- remaining limitations — in particular, this exercises plumbing only. A
  completed run and an `achieved` verdict say the pipeline works end to end;
  they are not evidence about recoding quality.
