<p align="center">
  <h1 align="center">🦜 Canary</h1>
  <p align="center">
    <strong>Conflict-aware logging and notifications for teams and individuals running parallel git worktrees.</strong>
  </p>
  <p align="center">
    Dependency tracking · Priority scoring · File locking · Push alerts
  </p>
  <p align="center">
    Built for Claude Code. Works with any git workflow.
  </p>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#how-it-works">How It Works</a> ·
  <a href="#the-flock">The Flock</a> ·
  <a href="#integrations">Integrations</a> ·
  <a href="#configuration">Configuration</a>
</p>

---

## The Problem

You're running two Claude Code sessions in parallel — one building an API, another building a frontend. They're in separate worktrees, isolated branches, everything looks clean.

Then you merge.

The API branch renamed `email` to `emailAddress`. The frontend branch built 6 components that destructure `email` from the API response. Git merges it cleanly. Your app crashes at runtime.

**Canary catches this before you merge.** It traces import/export relationships across your codebase, detects that the API change breaks files on the frontend branch, scores the conflict by urgency, and chirps — sending a push notification to your phone, surfacing a warning in your IDE, or telling your Claude Code session to adapt.

---

## Real-World Use Cases

### 🏗️ Parallel Feature Development

You're building a SaaS app. One Claude Code session is implementing user authentication on `feature/auth`, another is building the dashboard on `feature/dashboard`. Both touch the `User` model. The auth branch adds `role` and `permissions` fields, the dashboard branch is reading user data assuming the old schema.

**Without Canary:** You discover the mismatch at merge time. The dashboard components reference fields that don't exist yet or have different shapes. You spend an hour figuring out what changed and rewiring the frontend.

**With Canary:** The moment the auth branch commits the schema change, Canary's dependency graph sees that `UserDashboard.tsx`, `AdminPanel.tsx`, and `usePermissions.ts` on the dashboard branch import from the same types file. You get a 🐦‍🔥 CRITICAL alert with a plain-language description: *"Added `role` and `permissions` to User type. Dashboard branch destructures User without these fields in 3 files."* You fix it in context, not after the fact.

---

### 👥 Team Coordination (Multiple Developers)

Your backend developer is refactoring the payments API while a frontend developer is building the checkout flow against the current API contract. They're on different continents, different timezones.

**Without Canary:** The backend dev pushes a breaking change to the response format at 11pm their time. The frontend dev starts their day building against the old format. 8 hours of work diverge silently.

**With Canary:** The backend dev's commit triggers a post-commit hook. Canary detects the response shape change, traces it to the frontend's `apiClient.ts` and `CheckoutForm.tsx`, and sends a Slack notification to the team channel: *"🦜 CANARY ALERT: payments API response changed. Affects checkout flow on feature/frontend."* The frontend dev sees it when they start their day and adapts immediately.

---

### 🤖 Multi-Agent AI Workflows

You're running 4 Claude Code agents simultaneously via worktrees — one on API routes, one on database migrations, one on the React frontend, one on tests. This is the [parallel Claude Code workflow](https://docs.anthropic.com/en/docs/claude-code/worktrees) that Anthropic recommends for maximum throughput.

**Without Canary:** The agents work in isolation. The database agent renames a column. The API agent is writing queries against the old column name. The test agent is asserting against the old API response. Three branches that all merge cleanly but produce a broken application.

**With Canary (MCP server):** Each Claude Code session has access to Canary via MCP tools. Before editing a shared file, Claude calls `check_file` and sees that it's being modified on other branches. After making a breaking change, Claude calls `log_change` to broadcast it. The other sessions pick it up on their next check. The database agent claims `schema.prisma` with `claim_file` before restructuring, so the API agent knows to wait.

---

### 📱 Solo Developer, Multiple Features

You're a solo dev working on a mobile app. You've got a worktree for a UI redesign and another for a performance refactor. The refactor changes how data flows through the app. The redesign builds new screens that consume that data.

**Without Canary:** You context-switch between branches, lose track of what you changed where, and spend merge day untangling subtle breakages.

**With Canary:** Your phone buzzes when your refactor branch touches something your redesign depends on. The priority score tells you which conflict to resolve first. The timeline shows you exactly what changed since your last merge. Merge day takes 10 minutes instead of 2 hours.

---

### 🔄 Migration & Modernization

Your team is migrating from REST to GraphQL. One branch is building the new GraphQL resolvers while another branch continues shipping features on the REST API. Every REST endpoint change needs a corresponding resolver update, but there's no automated way to know when they drift.

**Without Canary:** The migration branch falls behind. When you finally try to reconcile, dozens of endpoints have changed on the feature branch and the resolvers are stale.

**With Canary:** Every change to `src/api/routes/` triggers a dependency conflict check against `src/graphql/resolvers/`. The rolling snapshot timeline shows you the drift accumulating day by day. You keep the branches in sync incrementally instead of facing a massive reconciliation.

---

## How It Works

```
You commit → 🦉 Owl watches → 🦅 Eagle traces dependencies
                                        ↓
              🐣 Hatchlings detected → 📋 Canary Log updated
                                        ↓
                    🦜 Canary chirps → Push notification / MCP alert / IDE warning
```

1. **You commit normally.** Git hooks trigger Canary in the background.
2. **The 🦉 Owl detects changes** across all active worktrees and branches.
3. **The 🦅 Eagle traces dependencies** — not just "did two branches edit the same file?" but "did one branch change a file that another branch's code *imports from*?"
4. **Conflicts are scored 0-100** based on category, dependency fan-out, merge test results, age, and cascade risk.
5. **The 🦜 Canary chirps** — push notifications, Slack messages, IDE warnings, or MCP tool responses, depending on what you've configured.
6. **The 🪺 Nest keeps history** — rolling snapshots show current conflicts, the last 2 resolved states, and a commit timeline between merges.

### What Makes Canary Different

Most conflict tools only detect **textual** conflicts — two branches edited the same line. Canary also catches **semantic** conflicts — one branch changed a type definition, another branch uses that type, and the merge will succeed but the app will break.

| | Textual conflicts | Dependency tracking | Impact descriptions | Priority scoring | Rolling history |
|---|---|---|---|---|---|
| `git merge` | At merge time | ❌ | ❌ | ❌ | ❌ |
| Clash | ✅ Real-time | ❌ | ❌ | ❌ | ❌ |
| GitLive | ✅ Real-time | ❌ | ❌ | ❌ | ❌ |
| GitKraken | ✅ Pre-PR | ❌ | ❌ | ❌ | ❌ |
| **🦜 Canary** | ✅ Real-time | ✅ Import tracing | ✅ Auto-generated | ✅ 0-100 weighted | ✅ Last 2 snapshots |

---

## The Flock

Every bird in the system means something.

| Bird | Name | Meaning |
|------|------|---------|
| 🦜 | **The Canary** | Brand icon. Alert headers. HIGH severity (60-79). |
| 🐦‍🔥 | **The Phoenix** | CRITICAL severity (80-100). Your branches are on fire. |
| 🦉 | **The Owl** | Watcher mode. Always scanning. Sees in the dark. |
| 🦅 | **The Eagle** | Dependency graph. Sees the whole picture from above. |
| 🐣 | **The Hatchling** | Newly detected conflict. Catch it while it's small. |
| 🐦 | **The Bluebird** | MEDIUM severity (40-59). Regular alert. |
| 🪶 | **The Feather** | LOW severity (20-39). Light touch, just awareness. |
| 🪹 | **The Empty Nest** | INFO (0-19). Barely registering. |
| 🐦‍⬛ | **The Blackbird** | Resolved. Historical. No longer active. |
| 🪺 | **The Nest** | Timeline and snapshot history. |

A notification looks like:

```
🦜 CANARY ALERT

🐦‍🔥 CRITICAL: src/types/user.ts
   Branches: feature-api, feature-frontend
   Renamed email → emailAddress (breaks UserProfile.tsx, SignupForm.tsx)

🐦 MEDIUM: src/config/endpoints.ts
   Branches: feature-api, feature-frontend
   Added VERIFY_PHONE endpoint path

→ Check .canary-log.md for details
```

---

## Quick Start

```bash
# Clone into your project's skills directory
git clone https://github.com/YOUR_USERNAME/canary-conflict-watcher.git \
  .claude/skills/canary

# Initialize config
python .claude/skills/canary/scripts/watch_conflicts.py --init

# Install git hooks (auto-runs on every commit, merge, checkout)
python .claude/skills/canary/scripts/install_hooks.py --repo .

# Optional: enable push notifications
python .claude/skills/canary/scripts/watch_conflicts.py --init \
  --ntfy-topic my-team-canary

# Optional: continuous file watching
python .claude/skills/canary/scripts/watch_conflicts.py --watch
```

That's it. Canary runs silently on every commit. You'll hear it when something's wrong.

### MCP Server (Claude Code)

For the best experience, add the MCP server so Claude Code can query conflicts programmatically:

```json
// .claude/mcp.json
{
  "mcpServers": {
    "canary": {
      "command": "python3",
      "args": [".claude/skills/canary/scripts/mcp_server.py"]
    }
  }
}
```

Now Claude Code can call `check_file` before editing shared code, `claim_file` to lock files during breaking changes, and `log_change` to broadcast changes to other sessions.

---

## Priority Scoring

Every conflict gets a score from 0 to 100 based on seven weighted factors:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| **Category** | 25 | API contracts score highest, misc files lowest |
| **Dependency fan-out** | 25 | How many downstream files break |
| **Merge failure** | 20 | Does a `git merge-tree` dry-run actually fail |
| **Branch count** | 10 | More branches involved = harder coordination |
| **Age** | 10 | Unresolved conflicts escalate over time |
| **Cascade risk** | 5 | Do other conflicts depend on resolving this first |
| **Lock active** | 5 | Is someone actively modifying this file right now |

The score determines what you see:

```
🦜 Canary Priority Ranking

| Priority      | Score | File                        |
|---------------|-------|-----------------------------|
| 🐦‍🔥 CRITICAL  |  85   | src/types/user.ts           |
| 🦜 HIGH       |  62   | src/api/routes/users.ts     |
| 🐦 MEDIUM     |  40   | src/config/endpoints.ts     |

Resolution Order:
1. src/types/user.ts  — [████████████████░░░░] 85/100
2. src/api/routes/users.ts — [████████████░░░░░░░░] 62/100
```

Resolve from the top — higher-scored conflicts often unblock lower ones.

---

## Rolling Snapshot Timeline

Instead of deleting resolved conflicts, Canary keeps a sliding window: **current + 2 previous states**. You always know what's active, what was just resolved, and what the conflict landscape looked like before.

```
🪺 Canary Timeline

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🦜 CURRENT — Active Conflicts
Since: 2026-02-22T14:30:00Z
Trigger: 💾 commit: a3f21c8 refactor: rename email to emailAddress
3 conflict(s) — 2 high severity

  🐦‍🔥 85 src/types/user.ts — CRITICAL
  🦜  62 src/api/routes/users.ts — HIGH
  🐦  40 src/config/endpoints.ts — MEDIUM

🐣 New since last snapshot: src/config/endpoints.ts
✅ Resolved since last snapshot: src/utils/helpers.ts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🐦‍⬛ PREVIOUS — Resolved
Period: 2026-02-22T10:00:00Z → 2026-02-22T14:30:00Z

Resolved (1):
  ✅ src/utils/helpers.ts

Still active at that time (2):
  ↳ src/types/user.ts (score: 72)
  ↳ src/api/routes/users.ts (score: 55)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Integrations

### Where Canary Chirps

| Channel | Setup | Best for |
|---------|-------|----------|
| **Claude Code (MCP)** | Add to `.claude/mcp.json` | AI sessions that auto-check before editing |
| **ntfy.sh** | Set `ntfy_topic` in config | Phone/desktop push — zero infrastructure |
| **Slack** | Add webhook URL | Team channel alerts |
| **Discord** | Add webhook URL | Team channel alerts |
| **VS Code** | Run with `--vscode` flag | Problems panel integration |
| **JetBrains** | File Watcher config | External tool integration |
| **Vim/Neovim** | ALE or native diagnostics | Inline warnings |
| **GitHub Actions** | Add CI step | PR gate checks |
| **macOS/Win/Linux** | Automatic | Native OS toast notifications |

### MCP Server Tools

| Tool | What it does |
|------|-------------|
| `check_conflicts` | Get all active conflicts with priority scores |
| `check_file` | Is this specific file safe to edit right now? |
| `get_dependents` | What's the blast radius if I change this file? |
| `get_locks` | Who has claimed what? |
| `claim_file` | Lock a file before making breaking changes |
| `release_file` | Release a lock |
| `merge_compatibility` | Can these two branches merge cleanly? |
| `log_change` | Broadcast a change to other sessions |
| `get_timeline` | Get the rolling snapshot history |

---

## Configuration

Initialize with defaults:

```bash
python scripts/watch_conflicts.py --init
```

This creates `.canary.json` in your project root. Key settings:

```jsonc
{
  // Which branch everything is compared against
  "base_branch": "main",

  // Where the log files go
  "log_path": ".canary-log.md",
  "json_log_path": ".canary-log.json",

  // What counts as high-impact (customize for your project)
  "high_impact_patterns": {
    "api_contracts": ["src/api/", "routes/", "controllers/"],
    "shared_types": ["src/types/", "src/interfaces/", "src/models/"],
    "database": ["prisma/", "migrations/", "src/db/"],
    "config": [".env", "src/config/", "package.json"],
    "shared_utilities": ["src/utils/", "src/lib/", "src/shared/"]
  },

  // Dependency tracing (TypeScript + Python supported)
  "dependency_tracking": {
    "enabled": true,
    "languages": ["typescript", "python"],
    "entry_points": ["src/"]
  },

  // Push notifications
  "notifications": {
    "enabled": true,
    "ntfy_topic": "my-team-canary",
    "notify_on": ["high"],       // Only alert on high+ severity
    "cooldown_seconds": 300       // Don't re-alert for 5 minutes
  },

  // File locking
  "locks": {
    "enabled": true,
    "auto_expire_minutes": 120    // Forgotten locks release after 2 hours
  }
}
```

---

## File Structure

```
canary/
├── SKILL.md                              # Claude Code skill reference
├── scripts/
│   ├── watch_conflicts.py                # Main watcher (hooks, watch, CI, VS Code)
│   ├── mcp_server.py                     # MCP server for Claude Code
│   ├── scoring.py                        # Priority scoring engine (0-100)
│   ├── snapshots.py                      # Rolling history & timeline
│   ├── dependency_graph.py               # Import/export tracer
│   ├── lock_manager.py                   # File claiming & locks
│   └── install_hooks.py                  # Git hook installer
└── references/
    ├── claude_md_template.md             # CLAUDE.md instructions
    ├── claude_hook_template.md           # Claude Code hook config
    ├── ide_integration.md                # VS Code, JetBrains, Vim setup
    └── example-conflict-log.md           # Example log output
```

---

## Requirements

- **Python 3.8+** (no external dependencies for core features)
- **Git 2.38+** (for `git merge-tree` fast merge checks; falls back gracefully on older versions)
- **watchdog** (optional, for continuous file monitoring: `pip install watchdog`)

---

## How It Compares

| Tool | What it does | What it doesn't do |
|------|-------------|-------------------|
| **Clash** | Fast Rust CLI for textual conflict detection across worktrees | No dependency tracing, no impact descriptions, no scoring |
| **parallel-cc** | Full orchestration platform with session management | Heavy — requires Node.js, SQLite, full system adoption |
| **GitLive** | Real-time IDE gutter indicators for team changes | IDE-only, no CLI, no dependency awareness, paid |
| **GitKraken** | Pre-PR conflict scanning with team view | Desktop GUI only, no worktree focus, no semantic analysis |
| **🦜 Canary** | Dependency-aware conflict detection with scoring and history | Not a full orchestration platform — it watches and warns |

Canary is deliberately focused: **detect, score, notify.** It doesn't manage sessions, orchestrate agents, or resolve conflicts for you. It tells you what's wrong, how bad it is, and what to fix first.

---

## Contributing

Contributions welcome. Some areas that could use help:

- **Language support** — The dependency graph currently traces TypeScript/JavaScript and Python imports. Adding Go, Rust, Java, C# would expand coverage.
- **AST-level analysis** — The impact description generator uses diff heuristics. Full AST parsing would catch more rename and restructure patterns.
- **Performance** — The watcher is Python. A Rust core (like Clash uses) would make continuous monitoring faster.
- **Testing** — Integration tests with real multi-worktree repos.

---

## License

MIT

---

<p align="center">
  <strong>🦜 Install it, forget about it, get warned when it matters.</strong>
</p>
