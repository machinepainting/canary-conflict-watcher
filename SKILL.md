---
name: canary
description: "🦜 Canary — A conflict-aware logging and notification tool for teams and individuals running multiple trees in Claude Code. Use this skill when working with git worktrees, parallel Claude Code sessions, or multi-developer workflows where separate branches may introduce breaking changes. Monitors file changes in real time, traces import/dependency relationships to catch indirect conflicts, auto-generates impact descriptions, and sends push notifications when breaking changes are detected. Especially useful for API/frontend splits, microservice boundaries, or any situation where one developer's changes can break another's work. Trigger this skill when users mention worktrees, parallel sessions, branch conflicts, API contract changes, breaking changes between branches, coordinating work across multiple developers or Claude Code instances, or setting up conflict detection."
---

# 🦜 Canary

**A conflict-aware logging and notification tool for teams and individuals
running multiple trees in Claude Code.**

Canary watches your branches so you don't have to. It detects conflicts before
they become merge nightmares — tracing dependencies to catch indirect breaks,
scoring conflicts by urgency, and chirping when something needs your attention.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    🦜 Canary                          │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │🦉 Watcher│→ │🦅 Dependency │→ │🐣 Impact       │  │
│  │ (hooks / │  │   Graph      │  │   Generator    │  │
│  │ watchdog)│  │ (imports)    │  │ (what changed) │  │
│  └──────────┘  └──────────────┘  └───────┬────────┘  │
│                                          │            │
│  ┌──────────┐  ┌──────────────┐  ┌───────▼────────┐  │
│  │🔒 Lock   │← │🧪 Merge     │← │📋 Canary Log   │  │
│  │  Manager │  │  Dry-Runs   │  │ .canary-log.md │  │
│  └──────────┘  └──────────────┘  └───────┬────────┘  │
│                                  ┌───────▼────────┐  │
│                                  │🦜 Canary Alerts│  │
│                                  │(ntfy/slack/OS) │  │
│                                  └────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### The Flock

| Bird | Role | Where you'll see it |
|------|------|-------------------|
| 🦜 | **The Canary** — Brand, alerts, HIGH severity | Notification headers, priority ranking |
| 🐦‍🔥 | **The Phoenix** — CRITICAL severity (80+) | On-fire conflicts that block everything |
| 🦉 | **The Owl** — Watcher / monitoring | Watch mode, always scanning |
| 🦅 | **The Eagle** — Dependency graph | Blast radius analysis, sees the whole picture |
| 🐣 | **The Hatchling** — Newly detected | Conflicts that just appeared |
| 🐦 | **The Bluebird** — MEDIUM severity | Regular alerts, pay attention |
| 🪶 | **The Feather** — LOW severity | Light touch, just awareness |
| 🪹 | **The Empty Nest** — INFO / minimal | Barely registering |
| 🐦‍⬛ | **The Blackbird** — Resolved / historical | Past conflicts, no longer active |
| 🪺 | **The Nest** — Timeline / history | Where old snapshots are stored |

Six components:

1. **🦉 Watcher** — The owl. Always watching. Detects changes via git hooks, file watchers, or manual runs
2. **🦅 Dependency Graph** — The eagle. Sees the whole picture. Traces imports/exports to find indirect conflicts
3. **🐣 Impact Generator** — Newly hatched problems. Auto-writes plain-language descriptions of what broke
4. **📋 Canary Log** — Shared markdown + JSON log that all sessions read
5. **🔒 Lock Manager** — Lets developers claim files before making breaking changes
6. **🦜 Canary Alerts** — The canary chirps. Pushes notifications via ntfy.sh, Slack, Discord, or native OS

## Quick Start

```bash
# 1. Copy the skill into your project
cp -r canary/ your-project/.claude/skills/

# 2. Initialize config (creates .canary.json)
python scripts/watch_conflicts.py --init

# 3. Install git hooks (auto-runs on commit, merge, checkout)
python scripts/install_hooks.py --repo .

# 4. Optional: enable continuous file watching
python scripts/watch_conflicts.py --watch

# 5. Optional: enable push notifications
python scripts/watch_conflicts.py --init --ntfy-topic your-team-conflicts
```

## Configuration

Create `.canary.json` in your project root (or run `--init`):

```json
{
  "base_branch": "main",
  "log_path": ".canary-log.md",
  "json_log_path": ".canary-log.json",

  "high_impact_patterns": {
    "api_contracts": ["src/api/", "routes/", "controllers/"],
    "shared_types": ["src/types/", "src/interfaces/", "src/models/"],
    "database": ["prisma/", "migrations/", "src/db/"],
    "config": [".env", "src/config/", "package.json"],
    "shared_utilities": ["src/utils/", "src/lib/", "src/shared/"]
  },

  "dependency_tracking": {
    "enabled": true,
    "languages": ["typescript", "python"],
    "entry_points": ["src/"]
  },

  "notifications": {
    "enabled": false,
    "ntfy_topic": null,
    "ntfy_server": "https://ntfy.sh",
    "slack_webhook": null,
    "discord_webhook": null,
    "notify_on": ["high"],
    "cooldown_seconds": 300
  },

  "automation": {
    "post_commit_hook": true,
    "file_watcher": false,
    "debounce_seconds": 30,
    "auto_cleanup_merged": true,
    "dry_run_merges": true,
    "dry_run_interval_minutes": 15
  },

  "locks": {
    "enabled": true,
    "lock_file": ".canary-locks.json",
    "auto_expire_minutes": 120
  },

  "scoring": {
    "enabled": true,
    "category_weights": {
      "api_contracts": 30,
      "shared_types": 25,
      "database": 25,
      "config": 15,
      "shared_utilities": 15
    },
    "escalation_hours": [1, 4, 12, 24]
  },

  "snapshots": {
    "enabled": true,
    "keep_resolved": 2,
    "snapshot_file": ".canary-snapshots.json"
  }
}
```

Customize `high_impact_patterns` for your project structure. The defaults cover common
layouts, but your project may have unique critical paths.

## Key Features

### Dependency Graph Tracking

Catches indirect conflicts that file-level comparison misses entirely.

**Example:** Branch A modifies `src/types/user.ts`. Branch B hasn't touched that file
but modified `src/components/UserProfile.tsx`, which imports `User` from that types file.
The system flags this because Branch B's component depends on what Branch A just changed.

The dependency graph is built by scanning import/export statements, cached at
`.canary-deps.json`, and incrementally updated on each run. It supports
TypeScript/JavaScript and Python, extensible to other languages.

### Auto-Generated Impact Descriptions

Reads the actual diff and writes plain-language descriptions:

> "Renamed `email` → `emailAddress` in UserResponse type. This will break
> `UserProfile.tsx` (line 23) and `SignupForm.tsx` (line 45) on the frontend-redesign
> branch, which destructure `email` from the API response."

No developer input needed — the system figures out what changed and who it affects.

### File Locking / Claiming

Prevent conflicts proactively by claiming files before making breaking changes:

```bash
python scripts/lock_manager.py --claim src/types/user.ts \
  --branch feature-api \
  --reason "Restructuring User type, ~2 hours"
```

Others see a warning in the log and get notified. Locks auto-expire after the
configured timeout so forgotten locks don't block the team.

### Debounced File Watching

Continuous mode watches for file saves across worktrees in real time. Changes are
debounced — waits for a quiet period (default 30s) after the last save before
regenerating the log and notifying. No notification spam during active editing.

### Merge Dry-Runs

Every 15 minutes (configurable), attempts `git merge --no-commit --no-ff` between
all active branch pairs in a temporary detached HEAD. Results logged as clean or
conflicting with exact file details. Always aborted — never touches real branches.

### Auto Cleanup

When a branch is merged or deleted, the post-merge hook automatically removes its
log entries, releases its locks, updates the dependency graph, and notes the
resolution in the merge history.

### Merge History & Analytics

Maintains `.canary-history.json` tracking detected conflicts and resolutions.
Over time reveals which files cause the most pain, which branches tend to conflict,
and whether coordination is improving.

### Conflict Priority Scoring

Every detected conflict gets a priority score from 0-100 based on seven weighted factors:

| Factor | Weight | What it measures |
|--------|--------|------------------|
| Category | 25 | File type (API contracts score highest, misc files lowest) |
| Dependency Fan-out | 25 | How many downstream files break if this file changes |
| Merge Failure | 20 | Whether a dry-run merge actually produces a conflict |
| Branch Count | 10 | How many branches are involved (more = harder to coordinate) |
| Age | 10 | How long the conflict has been unresolved (escalates over time) |
| Cascade Risk | 5 | Whether other conflicts depend on resolving this one first |
| Lock Active | 5 | Whether someone is actively modifying the file |

The score determines the priority label:
- 🐦‍🔥 **CRITICAL** (80-100) — Resolve immediately, likely blocking other work
- 🦜 **HIGH** (60-79) — Resolve soon, significant breakage risk
- 🐦 **MEDIUM** (40-59) — Plan to resolve, moderate impact
- 🪶 **LOW** (20-39) — Low urgency, minor overlap
- 🪹 **INFO** (0-19) — Awareness only, unlikely to cause issues

The log renders a priority ranking table with visual score bars:

```
## 📊 Conflict Priority Ranking

| Priority | Score | File | Key Factors |
|----------|-------|------|-------------|
| 🐦‍🔥 CRITICAL | **85** | `src/types/user.ts` | Category: 25/25 | Dep Fanout: 20/25 | Merge Fail: 20/20 |
| 🦜 HIGH | **62** | `src/api/routes/users.ts` | Category: 25/25 | Dep Fanout: 15/25 | Merge Fail: 20/20 |
| 🐦 MEDIUM | **40** | `src/config/endpoints.ts` | Category: 10/25 | Branch Count: 5/10 |

### Resolution Order
1. `src/types/user.ts` — [████████████████░░░░] 85/100
2. `src/api/routes/users.ts` — [████████████░░░░░░░░] 62/100
```

The resolution order matters — higher-scored conflicts often unblock lower ones
(the cascade risk factor captures this).

### Rolling Conflict Snapshots

Instead of deleting resolved conflict entries, the system keeps a rolling window
of the current state plus the last 2 resolved states. This gives you context on
what was recently fixed and what's still active.

Each snapshot captures the full conflict state at a point in time, tagged with
what triggered it (commit, merge, scheduled scan). When conflicts change:

1. The previous "current" snapshot moves to "resolved" (slot 1)
2. The old "resolved" slot 1 moves to slot 2
3. The oldest resolved snapshot is dropped
4. The new state becomes "current"

The log renders this as a visual timeline:

```
## 🪺 Canary Timeline

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 🦜 CURRENT — Active Conflicts
Since: 2026-02-22T14:30:00Z
Trigger: 💾 commit: a3f21c8 refactor: rename email to emailAddress
3 conflict(s) — 2 high severity

  🔴 **85** `src/types/user.ts` — CRITICAL
  🟠 **62** `src/api/routes/users.ts` — HIGH
  🟡 **40** `src/config/endpoints.ts` — MEDIUM

🐣 New since last snapshot: `src/config/endpoints.ts`
✅ Resolved since last snapshot: `src/utils/helpers.ts`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 🐦‍⬛ PREVIOUS — Resolved
Period: 2026-02-22T10:00:00Z → 2026-02-22T14:30:00Z
Trigger: 💾 commit: b7e09d4 feat: add phone verification

Resolved (1):
  ✅ `src/utils/helpers.ts`

Still active at that time (2):
  ↳ `src/types/user.ts` (score: 72)
  ↳ `src/api/routes/users.ts` (score: 55)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

This makes it immediately clear what's current vs. what's already been dealt with.

### Commit Timeline (Between Merges)

Below the conflict timeline, the log shows a per-branch commit history since
the last merge, with high-impact files flagged:

```
## 📝 Recent Changes (Since Last Merge)

### `feature-api-v2` — 4 commit(s)

  a3f21c8 [2026-02-22] refactor: rename email to emailAddress
  b7e09d4 [2026-02-22] feat: add phone verification endpoint
  c1a88f2 [2026-02-21] fix: update JWT payload to include user role
  d9e33a1 [2026-02-21] chore: migrate users table

  ⚡ High-impact files in this range:
  - `src/types/user.ts` — Modified
  - `src/api/routes/users.ts` — Modified
  - `prisma/schema.prisma` — Modified
```

This gives you a clear view of everything that happened between merge boundaries.

## Notifications

### ntfy.sh (Recommended — zero infrastructure)

Free, open-source push notifications. Install the ntfy app on phone/desktop,
subscribe to your topic, done.

```
🦜 CANARY ALERT
src/types/user.ts modified by both feature-api and feature-frontend

feature-api: Renamed email → emailAddress in User type
Impact: UserProfile.tsx and SignupForm.tsx on feature-frontend will break

→ Check .canary-log.md for details
```

### Slack / Discord

Posts structured messages to a channel via webhook.

### Claude Code Hooks

Integrates directly into Claude Code sessions. When a conflict is detected, Claude
pauses and alerts the developer in-session. See `references/claude_hook_template.md`.

### Native OS

macOS (notification center), Windows (toast), Linux (notify-send).

## CI/CD Integration

```yaml
# GitHub Actions
- name: Conflict check
  run: |
    python scripts/watch_conflicts.py --ci --base main --target ${{ github.head_ref }}
    if [ -f .canary-alert ]; then
      echo "::warning::Cross-branch conflicts detected"
      cat .canary-log.md >> $GITHUB_STEP_SUMMARY
    fi
```

## Integrations

### MCP Server (Claude Code — recommended)

The MCP server lets Claude Code sessions query conflict state programmatically
instead of reading a markdown file. Claude can ask "is this file safe to edit?"
and get a structured answer with scores, locks, and dependency risks.

Add to `.claude/mcp.json`:

```json
{
  "mcpServers": {
    "canary": {
      "command": "python3",
      "args": [".claude/skills/canary/scripts/mcp_server.py"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

**Available tools:**

| Tool | What it does |
|------|-------------|
| `check_conflicts` | Get all active conflicts with priority scores |
| `check_file` | Check a specific file for conflicts, locks, dependency risks |
| `get_dependents` | Find all files that import from a given file |
| `get_locks` | List active file locks |
| `claim_file` | Lock a file before making breaking changes |
| `release_file` | Release a file lock |
| `merge_compatibility` | Check if two branches merge cleanly (uses `git merge-tree`) |
| `log_change` | Record a breaking change for other sessions to see |
| `get_timeline` | Get the rolling snapshot timeline |

With MCP, Claude Code sessions can automatically claim files before editing,
check for conflicts before modifying shared code, and broadcast breaking changes
to other sessions — all without the developer asking.

### VS Code (Problem Matcher)

Run with `--vscode` to output warnings in VS Code's problem matcher format.
Conflicts show up directly in the Problems panel — CRITICAL/HIGH as errors,
MEDIUM as warnings. See `references/ide_integration.md` for full setup
including `tasks.json` templates and auto-run on folder open.

### JetBrains / Vim / Neovim

External tool and linter configurations for all major IDEs are documented
in `references/ide_integration.md`.

### `git merge-tree` (Git 2.38+)

The MCP server and merge compatibility checks use `git merge-tree --write-tree`
for filesystem-safe merge testing. Unlike `git merge --no-commit`, this runs
entirely in memory — no temporary merge state, no abort needed, safe to run
continuously at high frequency. Falls back to the traditional dry-run method
on older git versions.

## File Reference

- `scripts/watch_conflicts.py` — Main watcher (hooks, file watch, CI, VS Code modes)
- `scripts/mcp_server.py` — MCP server for Claude Code integration
- `scripts/scoring.py` — Conflict priority scoring engine (0-100 weighted score)
- `scripts/snapshots.py` — Rolling snapshot manager and visual timeline renderer
- `scripts/dependency_graph.py` — Import/export tracer for indirect conflicts
- `scripts/lock_manager.py` — File claiming and lock management
- `scripts/install_hooks.py` — Git hook installer
- `references/claude_md_template.md` — CLAUDE.md instructions template
- `references/claude_hook_template.md` — Claude Code hook config
- `references/ide_integration.md` — VS Code, JetBrains, Vim/Neovim setup guides
- `references/example-conflict-log.md` — Realistic example log
