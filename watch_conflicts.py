#!/usr/bin/env python3
"""
Canary Watcher

Monitors changes across git branches/worktrees, detects direct and indirect
conflicts via dependency tracking, auto-generates impact descriptions, and
dispatches notifications.

Modes:
    Default:    One-shot scan — compare branches, generate log, exit
    --watch:    Continuous file monitoring with debounced updates
    --ci:       CI/CD mode — output for GitHub Actions / GitLab CI
    --init:     Generate default .canary.json config

Usage:
    python watch_conflicts.py --repo .
    python watch_conflicts.py --watch
    python watch_conflicts.py --ci --base main --target feature-branch
    python watch_conflicts.py --init --ntfy-topic my-team
"""

import subprocess
import argparse
import json
import os
import sys
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# Local imports (scoring and snapshot modules)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scoring import score_all_conflicts, format_scored_log_section
    from snapshots import (
        create_snapshot, update_snapshots, render_timeline,
        render_commit_timeline
    )
    HAS_EXTENSIONS = True
except ImportError:
    HAS_EXTENSIONS = False

# ── Constants ──

DEFAULT_CONFIG = {
    "base_branch": "main",
    "log_path": ".canary-log.md",
    "json_log_path": ".canary-log.json",
    "high_impact_patterns": {
        "api_contracts": ["src/api/", "routes/", "controllers/", "endpoints/"],
        "shared_types": ["src/types/", "src/interfaces/", "src/models/", "types.ts", "types.py"],
        "database": ["prisma/", "migrations/", "alembic/", "schema.prisma"],
        "config": [".env", "src/config/", "config/", "package.json", "pyproject.toml"],
        "shared_utilities": ["src/utils/", "src/lib/", "src/shared/", "src/common/"]
    },
    "dependency_tracking": {
        "enabled": True,
        "languages": ["typescript", "python"],
        "entry_points": ["src/"]
    },
    "notifications": {
        "enabled": False,
        "ntfy_topic": None,
        "ntfy_server": "https://ntfy.sh",
        "slack_webhook": None,
        "discord_webhook": None,
        "notify_on": ["high"],
        "cooldown_seconds": 300
    },
    "automation": {
        "post_commit_hook": True,
        "file_watcher": False,
        "debounce_seconds": 30,
        "auto_cleanup_merged": True,
        "dry_run_merges": True,
        "dry_run_interval_minutes": 15
    },
    "locks": {
        "enabled": True,
        "lock_file": ".canary-locks.json",
        "auto_expire_minutes": 120
    }
}

# ── Git Helpers ──

def run_git(repo_path, *args):
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + list(args),
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_active_worktrees(repo_path):
    """Return list of (path, branch) for all active worktrees."""
    output = run_git(repo_path, "worktree", "list", "--porcelain")
    if not output:
        return []

    worktrees = []
    current = {}
    for line in output.split("\n"):
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line == "":
            if "path" in current and "branch" in current:
                worktrees.append((current["path"], current["branch"]))
            current = {}
    if "path" in current and "branch" in current:
        worktrees.append((current["path"], current["branch"]))
    return worktrees


def get_active_branches(repo_path, base_branch):
    """Get branches from worktrees, falling back to local branches."""
    worktrees = get_active_worktrees(repo_path)
    if worktrees:
        return [(p, b) for p, b in worktrees if b != base_branch]

    branches_output = run_git(repo_path, "branch", "--format=%(refname:short)")
    if branches_output:
        branches = [b.strip() for b in branches_output.split("\n")
                     if b.strip() and b.strip() != base_branch]
        return [(repo_path, b) for b in branches]
    return []


def get_changed_files(repo_path, base_branch, target_branch):
    """Get files changed between base and target with status."""
    merge_base = run_git(repo_path, "merge-base", base_branch, target_branch)
    if not merge_base:
        merge_base = base_branch

    output = run_git(repo_path, "diff", "--name-status", merge_base, target_branch)
    if not output:
        return []

    changes = []
    for line in output.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0][0]
            filepath = parts[-1]
            changes.append({"status": status, "file": filepath})
    return changes


def get_diff_details(repo_path, base_branch, target_branch, filepath):
    """Get the actual diff content for a file (for impact analysis)."""
    merge_base = run_git(repo_path, "merge-base", base_branch, target_branch)
    if not merge_base:
        merge_base = base_branch

    output = run_git(repo_path, "diff", "-U3", merge_base, target_branch, "--", filepath)
    return output or ""


def get_recent_commits(repo_path, base_branch, target_branch, limit=5):
    """Get recent commit messages on target since base."""
    merge_base = run_git(repo_path, "merge-base", base_branch, target_branch)
    if not merge_base:
        merge_base = base_branch

    output = run_git(repo_path, "log", "--oneline", "--no-merges",
                     f"--max-count={limit}", f"{merge_base}..{target_branch}")
    if not output:
        return []
    return [line.strip() for line in output.split("\n") if line.strip()]


def try_merge_dry_run(repo_path, branch_a, branch_b):
    """Attempt a merge dry-run between two branches. Returns (success, conflict_files)."""
    # Create temporary merge in detached HEAD
    current = run_git(repo_path, "rev-parse", "HEAD")
    if not current:
        return None, []

    result = subprocess.run(
        ["git", "-C", str(repo_path), "merge", "--no-commit", "--no-ff", branch_b],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "GIT_MERGE_AUTOEDIT": "no"}
    )

    conflict_files = []
    if result.returncode != 0:
        # Parse conflict files
        status_output = run_git(repo_path, "diff", "--name-only", "--diff-filter=U")
        if status_output:
            conflict_files = [f.strip() for f in status_output.split("\n") if f.strip()]

    # Always abort
    run_git(repo_path, "merge", "--abort")
    run_git(repo_path, "checkout", current)

    return result.returncode == 0, conflict_files


# ── Classification ──

def load_config(repo_path):
    """Load config from .canary.json or return defaults."""
    config_path = Path(repo_path) / ".canary.json"
    if config_path.exists():
        with open(config_path) as f:
            user_config = json.load(f)
        # Merge with defaults
        config = DEFAULT_CONFIG.copy()
        for key, value in user_config.items():
            if isinstance(value, dict) and key in config:
                config[key] = {**config[key], **value}
            else:
                config[key] = value
        return config
    return DEFAULT_CONFIG.copy()


def categorize_file(filepath, config):
    """Determine impact category of a changed file."""
    filepath_lower = filepath.lower()
    for category, patterns in config.get("high_impact_patterns", {}).items():
        for pattern in patterns:
            if pattern.lower() in filepath_lower:
                return category
    return "other"


def assess_severity(filepath, branches_info, config):
    """Rate severity: high, medium, or low."""
    category = categorize_file(filepath, config)
    if category in ("api_contracts", "shared_types", "database"):
        return "high"
    elif category in ("config", "shared_utilities"):
        return "medium"
    return "low"


# ── Dependency Graph ──

def build_dependency_graph(repo_path, config):
    """Build import/export dependency graph by scanning source files."""
    dep_config = config.get("dependency_tracking", {})
    if not dep_config.get("enabled", False):
        return {}

    cache_path = Path(repo_path) / ".canary-deps.json"
    graph = {}  # file -> [files it imports from]

    entry_points = dep_config.get("entry_points", ["src/"])
    languages = dep_config.get("languages", ["typescript", "python"])

    extensions = []
    if "typescript" in languages:
        extensions.extend([".ts", ".tsx", ".js", ".jsx"])
    if "python" in languages:
        extensions.extend([".py"])

    for entry in entry_points:
        entry_path = Path(repo_path) / entry
        if not entry_path.exists():
            continue

        for fpath in entry_path.rglob("*"):
            if not fpath.is_file() or fpath.suffix not in extensions:
                continue

            rel_path = str(fpath.relative_to(repo_path))
            imports = extract_imports(fpath, repo_path)
            if imports:
                graph[rel_path] = imports

    # Cache the graph
    with open(cache_path, "w") as f:
        json.dump(graph, f, indent=2)

    return graph


def extract_imports(filepath, repo_path):
    """Extract import paths from a source file."""
    imports = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return imports

    suffix = filepath.suffix

    if suffix in (".ts", ".tsx", ".js", ".jsx"):
        imports = extract_ts_imports(content, filepath, repo_path)
    elif suffix == ".py":
        imports = extract_py_imports(content, filepath, repo_path)

    return imports


def extract_ts_imports(content, filepath, repo_path):
    """Extract TypeScript/JavaScript import paths."""
    import re
    imports = []

    # Match: import ... from '...' or import ... from "..."
    pattern = r"""(?:import|export)\s+.*?from\s+['"]([^'"]+)['"]"""
    # Also match: require('...')
    require_pattern = r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)"""

    for match in re.finditer(pattern, content):
        imp = match.group(1)
        resolved = resolve_ts_import(imp, filepath, repo_path)
        if resolved:
            imports.append(resolved)

    for match in re.finditer(require_pattern, content):
        imp = match.group(1)
        resolved = resolve_ts_import(imp, filepath, repo_path)
        if resolved:
            imports.append(resolved)

    return imports


def resolve_ts_import(import_path, source_file, repo_path):
    """Resolve a TS/JS import path to a relative file path."""
    if not import_path.startswith("."):
        return None  # Skip node_modules

    source_dir = source_file.parent
    candidates = [
        import_path,
        import_path + ".ts",
        import_path + ".tsx",
        import_path + ".js",
        import_path + ".jsx",
        import_path + "/index.ts",
        import_path + "/index.tsx",
        import_path + "/index.js",
    ]

    for candidate in candidates:
        resolved = (source_dir / candidate).resolve()
        try:
            rel = str(resolved.relative_to(Path(repo_path).resolve()))
            if Path(resolved).exists():
                return rel
        except ValueError:
            continue

    # Return best guess even if file doesn't exist yet
    best_guess = (source_dir / (import_path + ".ts")).resolve()
    try:
        return str(best_guess.relative_to(Path(repo_path).resolve()))
    except ValueError:
        return None


def extract_py_imports(content, filepath, repo_path):
    """Extract Python import paths."""
    import re
    imports = []

    # from X import Y
    from_pattern = r"from\s+([\w.]+)\s+import"
    # import X
    import_pattern = r"^import\s+([\w.]+)"

    for match in re.finditer(from_pattern, content):
        module = match.group(1)
        resolved = resolve_py_import(module, filepath, repo_path)
        if resolved:
            imports.append(resolved)

    for match in re.finditer(import_pattern, content, re.MULTILINE):
        module = match.group(1)
        resolved = resolve_py_import(module, filepath, repo_path)
        if resolved:
            imports.append(resolved)

    return imports


def resolve_py_import(module_path, source_file, repo_path):
    """Resolve a Python module path to a file path."""
    parts = module_path.split(".")
    candidates = [
        os.path.join(*parts) + ".py",
        os.path.join(*parts, "__init__.py"),
    ]
    for candidate in candidates:
        full = Path(repo_path) / candidate
        if full.exists():
            return candidate
    return None


def find_dependents(filepath, dep_graph):
    """Find all files that directly or transitively depend on filepath."""
    dependents = set()
    queue = [filepath]
    visited = set()

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        for source_file, imports in dep_graph.items():
            if current in imports and source_file not in dependents:
                dependents.add(source_file)
                queue.append(source_file)

    return dependents


def find_dependency_conflicts(branch_changes, dep_graph, config):
    """Find conflicts where one branch changes a file that another branch's files depend on."""
    dep_conflicts = []
    branches = list(branch_changes.keys())

    for i, branch_a in enumerate(branches):
        for branch_b in branches[i + 1:]:
            changes_a = {c["file"] for c in branch_changes[branch_a]}
            changes_b = {c["file"] for c in branch_changes[branch_b]}

            # For each file changed in A, find files in B that depend on it
            for changed_file in changes_a:
                dependents = find_dependents(changed_file, dep_graph)
                affected_in_b = dependents & changes_b
                if affected_in_b:
                    dep_conflicts.append({
                        "source_file": changed_file,
                        "source_branch": branch_a,
                        "affected_files": list(affected_in_b),
                        "affected_branch": branch_b,
                        "category": categorize_file(changed_file, config)
                    })

            # Reverse: files changed in B affecting files in A
            for changed_file in changes_b:
                dependents = find_dependents(changed_file, dep_graph)
                affected_in_a = dependents & changes_a
                if affected_in_a:
                    dep_conflicts.append({
                        "source_file": changed_file,
                        "source_branch": branch_b,
                        "affected_files": list(affected_in_a),
                        "affected_branch": branch_a,
                        "category": categorize_file(changed_file, config)
                    })

    return dep_conflicts


# ── Impact Description Generator ──

def generate_impact_description(repo_path, base_branch, branch, filepath, dep_graph, all_branch_changes, config):
    """Auto-generate a plain-language impact description from a diff."""
    diff = get_diff_details(repo_path, base_branch, branch, filepath)
    if not diff:
        return None

    description = {"what_changed": "", "impact": [], "affected_interfaces": []}
    category = categorize_file(filepath, config)

    # Analyze diff for common patterns
    added_lines = [l[1:] for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++")]
    removed_lines = [l[1:] for l in diff.split("\n") if l.startswith("-") and not l.startswith("---")]

    # Detect renames (removed line with a name, added line with different name)
    renames = detect_renames_in_diff(added_lines, removed_lines)
    additions = detect_additions_in_diff(added_lines, removed_lines)
    deletions = detect_deletions_in_diff(added_lines, removed_lines)

    # Build description
    parts = []
    if renames:
        parts.append(f"Renamed: {', '.join(f'`{old}` → `{new}`' for old, new in renames)}")
    if additions:
        parts.append(f"Added: {', '.join(f'`{a}`' for a in additions[:5])}")
    if deletions:
        parts.append(f"Removed: {', '.join(f'`d}`' for d in deletions[:5])}")

    description["what_changed"] = "; ".join(parts) if parts else f"Modified `{filepath}`"

    # Find affected files on other branches
    dependents = find_dependents(filepath, dep_graph)
    for other_branch, changes in all_branch_changes.items():
        if other_branch == branch:
            continue
        other_files = {c["file"] for c in changes}
        affected = dependents & other_files
        if affected:
            description["impact"].append({
                "branch": other_branch,
                "files": list(affected),
                "note": f"These files import from `{filepath}` and may break"
            })

    if renames:
        description["affected_interfaces"] = [
            f"`{old}` → `{new}`" for old, new in renames
        ]

    return description


def detect_renames_in_diff(added, removed):
    """Heuristic: detect field/variable renames from diff lines."""
    import re
    renames = []

    # Simple pattern: look for lines that are very similar but with a name changed
    # This catches things like: email: string → emailAddress: string
    field_pattern = re.compile(r'(\w+)\s*[=:?]')

    removed_fields = {}
    for line in removed:
        match = field_pattern.search(line.strip())
        if match:
            removed_fields[line.strip()] = match.group(1)

    for line in added:
        match = field_pattern.search(line.strip())
        if match:
            added_field = match.group(1)
            # Find a removed line that's very similar
            for rem_line, rem_field in removed_fields.items():
                if rem_field != added_field and _lines_similar(rem_line, line.strip(), rem_field, added_field):
                    renames.append((rem_field, added_field))
                    break

    return renames[:10]  # Limit


def detect_additions_in_diff(added, removed):
    """Detect newly added fields/functions."""
    import re
    additions = []
    field_pattern = re.compile(r'(\w+)\s*[=:?(]')

    added_names = set()
    for line in added:
        match = field_pattern.search(line.strip())
        if match:
            added_names.add(match.group(1))

    removed_names = set()
    for line in removed:
        match = field_pattern.search(line.strip())
        if match:
            removed_names.add(match.group(1))

    return list(added_names - removed_names)[:10]


def detect_deletions_in_diff(added, removed):
    """Detect removed fields/functions."""
    import re
    field_pattern = re.compile(r'(\w+)\s*[=:?(]')

    added_names = set()
    for line in added:
        match = field_pattern.search(line.strip())
        if match:
            added_names.add(match.group(1))

    removed_names = set()
    for line in removed:
        match = field_pattern.search(line.strip())
        if match:
            removed_names.add(match.group(1))

    return list(removed_names - added_names)[:10]


def _lines_similar(line_a, line_b, field_a, field_b):
    """Check if two lines are similar except for a field name change."""
    normalized_a = line_a.replace(field_a, "__FIELD__")
    normalized_b = line_b.replace(field_b, "__FIELD__")
    # Allow some variance (whitespace, punctuation)
    return normalized_a.strip().rstrip(",;") == normalized_b.strip().rstrip(",;")


# ── Lock Manager ──

def load_locks(repo_path, config):
    """Load active file locks."""
    lock_file = Path(repo_path) / config.get("locks", {}).get("lock_file", ".canary-locks.json")
    if not lock_file.exists():
        return []

    with open(lock_file) as f:
        locks = json.load(f)

    # Filter expired locks
    now = datetime.now(timezone.utc)
    expire_minutes = config.get("locks", {}).get("auto_expire_minutes", 120)
    active = []
    for lock in locks:
        claimed_at = datetime.fromisoformat(lock["claimed_at"])
        if (now - claimed_at).total_seconds() < expire_minutes * 60:
            active.append(lock)

    # Write back cleaned locks
    if len(active) != len(locks):
        with open(lock_file, "w") as f:
            json.dump(active, f, indent=2)

    return active


# ── Overlap Detection ──

def find_direct_overlaps(branch_changes):
    """Find files modified by multiple branches."""
    file_to_branches = defaultdict(list)
    for branch, changes in branch_changes.items():
        for change in changes:
            file_to_branches[change["file"]].append({
                "branch": branch,
                "status": change["status"]
            })

    return {f: b for f, b in file_to_branches.items() if len(b) > 1}


# ── Notification Dispatch ──

def send_notifications(conflicts, config, repo_path):
    """Send notifications for new high-severity conflicts."""
    notif_config = config.get("notifications", {})
    if not notif_config.get("enabled", False):
        return

    notify_on = notif_config.get("notify_on", ["high"])

    # Filter to relevant severity
    relevant = [c for c in conflicts if c.get("severity", "low") in notify_on]
    if not relevant:
        return

    # Check cooldown
    cooldown = notif_config.get("cooldown_seconds", 300)
    cooldown_file = Path(repo_path) / ".canary-notified.json"
    now = datetime.now(timezone.utc)

    notified = {}
    if cooldown_file.exists():
        with open(cooldown_file) as f:
            notified = json.load(f)

    new_conflicts = []
    for conflict in relevant:
        key = hashlib.md5(json.dumps(conflict, sort_keys=True).encode()).hexdigest()
        last_notified = notified.get(key)
        if last_notified:
            elapsed = (now - datetime.fromisoformat(last_notified)).total_seconds()
            if elapsed < cooldown:
                continue
        notified[key] = now.isoformat()
        new_conflicts.append(conflict)

    if not new_conflicts:
        return

    # Save cooldown state
    with open(cooldown_file, "w") as f:
        json.dump(notified, f, indent=2)

    # Build message
    message = format_notification(new_conflicts)

    # Dispatch to configured channels
    if notif_config.get("ntfy_topic"):
        send_ntfy(message, notif_config)
    if notif_config.get("slack_webhook"):
        send_slack(message, notif_config)
    if notif_config.get("discord_webhook"):
        send_discord(message, notif_config)

    # Write alert flag for Claude Code hooks
    alert_path = Path(repo_path) / ".canary-alert"
    with open(alert_path, "w") as f:
        json.dump({"timestamp": now.isoformat(), "conflicts": new_conflicts}, f, indent=2)

    # Native OS notification
    send_os_notification(message)


def format_notification(conflicts):
    """Format conflicts into a Canary alert."""
    lines = ["🦜 CANARY ALERT", ""]
    for c in conflicts:
        icon = {"high": "🐦‍🔥", "medium": "🐦", "low": "🪶", "critical": "🐦‍🔥"}.get(
            c.get("severity", "low"), "🪹"
        )
        lines.append(f"{icon} {c.get('severity', 'unknown').upper()}: {c.get('file', 'unknown')}")
        if c.get("branches"):
            branch_names = ", ".join(c["branches"])
            lines.append(f"   Branches: {branch_names}")
        if c.get("description"):
            lines.append(f"   {c['description']}")
    lines.append("")
    lines.append("→ Check .canary-log.md for details")
    return "\n".join(lines)


def send_ntfy(message, config):
    """Send notification via ntfy.sh."""
    import urllib.request
    topic = config["ntfy_topic"]
    server = config.get("ntfy_server", "https://ntfy.sh")
    url = f"{server}/{topic}"

    try:
        req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
        req.add_header("Title", "🦜 Canary Alert")
        req.add_header("Priority", "high")
        req.add_header("Tags", "warning")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"ntfy notification failed: {e}", file=sys.stderr)


def send_slack(message, config):
    """Send notification via Slack webhook."""
    import urllib.request
    url = config["slack_webhook"]
    payload = json.dumps({"text": f"🦜 *Canary Alert*\n```\n{message}\n```"})

    try:
        req = urllib.request.Request(url, data=payload.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Slack notification failed: {e}", file=sys.stderr)


def send_discord(message, config):
    """Send notification via Discord webhook."""
    import urllib.request
    url = config["discord_webhook"]
    payload = json.dumps({"content": f"*🦜 *Canary Alert**\n```\n{message}\n```"})

    try:
        req = urllib.request.Request(url, data=payload.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Discord notification failed: {e}", file=sys.stderr)


def send_os_notification(message):
    """Send native OS desktop notification."""
    title = "🦜 Canary Alert"
    short_msg = message.split("\n")[0]

    if sys.platform == "darwin":
        os.system(f"""osascript -e 'display notification "{short_msg}" with title "{title}"'""")
    elif sys.platform == "win32":
        os.system(f"""powershell -Command "New-BurntToastNotification -Text '{title}', '{short_msg}'" 2>nul""")
    elif sys.platform.startswith("linux"):
        os.system(f"""notify-send "{title}" "{short_msg}" 2>/dev/null""")


# ── Log Generation ──

def generate_log(repo_path, base_branch, branch_changes, direct_overlaps,
                 dep_conflicts, locks, merge_results, impact_descs, config,
                 scored_conflicts=None):
    """Generate the full conflict awareness log in markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []

    lines.append("# 🦜 Canary Log")
    lines.append(f"**Last updated:** {now}")
    lines.append(f"**Base branch:** `{base_branch}`")
    lines.append(f"**Active branches:** {len(branch_changes)}")
    lines.append("")
    lines.append("> **For Claude Code sessions:** Check this log before modifying any")
    lines.append("> shared file. If your planned change conflicts with an entry below,")
    lines.append("> coordinate before proceeding.")
    lines.append("")

    # ── Priority ranking (scored) ──
    if scored_conflicts and HAS_EXTENSIONS:
        lines.append(format_scored_log_section(scored_conflicts))
        lines.append("")

    # ── Active locks ──
    if locks:
        lines.append("---")
        lines.append("")
        lines.append("## 🔒 Active Locks")
        lines.append("")
        for lock in locks:
            expire_mins = config.get("locks", {}).get("auto_expire_minutes", 120)
            claimed = datetime.fromisoformat(lock["claimed_at"])
            expires = claimed + timedelta(minutes=expire_mins)
            lines.append(f"### `{lock['file']}`")
            lines.append(f"**Claimed by:** `{lock['branch']}` ({lock.get('author', 'unknown')})")
            lines.append(f"**Reason:** {lock.get('reason', 'No reason given')}")
            lines.append(f"**Expires:** {expires.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            lines.append(f"→ Wait before building against this file, or coordinate with the owner.")
            lines.append("")

    # ── Direct file overlaps ──
    if direct_overlaps:
        lines.append("---")
        lines.append("")
        lines.append("## 🐦‍🔥 Direct File Conflicts")
        lines.append("")
        lines.append("These files are modified by multiple branches simultaneously.")
        lines.append("")

        sorted_overlaps = sorted(
            direct_overlaps.items(),
            key=lambda x: {"high": 0, "medium": 1, "low": 2}[
                assess_severity(x[0], x[1], config)
            ]
        )

        for filepath, branches in sorted_overlaps:
            severity = assess_severity(filepath, branches, config)
            icon = {"high": "🐦‍🔥", "medium": "🐦", "low": "🪶"}[severity]
            branch_names = ", ".join(f"`{b['branch']}`" for b in branches)
            category = categorize_file(filepath, config).replace("_", " ").title()

            lines.append(f"### {icon} `{filepath}`")
            lines.append(f"**Severity:** {severity} | **Category:** {category}")
            lines.append(f"**Branches:** {branch_names}")
            lines.append("")

            # Add auto-generated impact description if available
            key = filepath
            if key in impact_descs:
                desc = impact_descs[key]
                if desc.get("what_changed"):
                    lines.append(f"**What changed:** {desc['what_changed']}")
                for imp in desc.get("impact", []):
                    lines.append(f"- **{imp['branch']}:** {imp['note']}")
                    for af in imp.get("files", []):
                        lines.append(f"  - `{af}`")
                if desc.get("affected_interfaces"):
                    lines.append(f"**Interfaces:** {', '.join(desc['affected_interfaces'])}")
                lines.append("")

    # ── Dependency-based conflicts ──
    if dep_conflicts:
        lines.append("---")
        lines.append("")
        lines.append("## 🦅 Dependency Conflicts")
        lines.append("")
        lines.append("These files aren't directly overlapping, but are connected via imports.")
        lines.append("")

        for dc in dep_conflicts:
            category = dc["category"].replace("_", " ").title()
            lines.append(f"### `{dc['source_file']}` ({category})")
            lines.append(f"**Changed on:** `{dc['source_branch']}`")
            lines.append(f"**Affects on `{dc['affected_branch']}`:**")
            for af in dc["affected_files"]:
                lines.append(f"- `{af}` (imports from `{dc['source_file']}`)")
            lines.append("")

    # ── Merge dry-run results ──
    if merge_results:
        lines.append("---")
        lines.append("")
        lines.append("## 🧪 Merge Dry-Run Results")
        lines.append("")
        for mr in merge_results:
            if mr["clean"]:
                lines.append(f"- ✅ `{mr['branch_a']}` ↔ `{mr['branch_b']}`: Clean merge")
            else:
                lines.append(f"- ❌ `{mr['branch_a']}` ↔ `{mr['branch_b']}`: **Conflicts** in:")
                for cf in mr["conflict_files"]:
                    lines.append(f"  - `{cf}`")
        lines.append("")

    # ── Per-branch summaries ──
    lines.append("---")
    lines.append("")
    lines.append("## Branch Change Summaries")
    lines.append("")

    for branch, changes in branch_changes.items():
        commits = get_recent_commits(repo_path, base_branch, branch)
        lines.append(f"### `{branch}`")
        lines.append(f"**Files changed:** {len(changes)}")
        lines.append("")

        if commits:
            lines.append("**Recent commits:**")
            for commit in commits:
                lines.append(f"- {commit}")
            lines.append("")

        # High-impact changes
        by_category = defaultdict(list)
        for change in changes:
            cat = categorize_file(change["file"], config)
            by_category[cat].append(change)

        high_cats = ["api_contracts", "shared_types", "database", "config", "shared_utilities"]
        has_high = any(cat in by_category for cat in high_cats)

        if has_high:
            lines.append("**⚡ High-impact changes:**")
            for cat in high_cats:
                if cat in by_category:
                    cat_label = cat.replace("_", " ").title()
                    for change in by_category[cat]:
                        status_map = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed"}
                        status_label = status_map.get(change["status"], change["status"])
                        lines.append(f"- [{cat_label}] `{change['file']}` — {status_label}")
            lines.append("")

        other = by_category.get("other", [])
        if other:
            lines.append(f"**Other changes ({len(other)} files):**")
            for change in other[:10]:
                status_map = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed"}
                lines.append(f"- `{change['file']}` — {status_map.get(change['status'], change['status'])}")
            if len(other) > 10:
                lines.append(f"- ... and {len(other) - 10} more")
            lines.append("")

        lines.append("---")
        lines.append("")

    # ── Recommendations ──
    has_conflicts = direct_overlaps or dep_conflicts
    if has_conflicts:
        lines.append("## Recommended Actions")
        lines.append("")

        high_items = [f for f, b in direct_overlaps.items()
                      if assess_severity(f, b, config) == "high"]
        if high_items:
            lines.append("**Immediate:**")
            for f in high_items:
                branch_names = " and ".join(f"`{b['branch']}`" for b in direct_overlaps[f])
                lines.append(f"- Coordinate changes to `{f}` between {branch_names}")
            lines.append("")

        lines.append("**General:**")
        lines.append("- Merge the branch with fewer changes first, then rebase the other")
        lines.append("- Agree on the final shape of shared interfaces before continuing")
        lines.append("- Use file locks to claim critical files before making breaking changes")
        lines.append("")

    return "\n".join(lines)


def generate_json_log(branch_changes, direct_overlaps, dep_conflicts,
                      locks, merge_results, impact_descs, base_branch, config,
                      scored_conflicts=None):
    """Generate machine-readable JSON log."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    overlap_entries = []
    for filepath, branches in direct_overlaps.items():
        entry = {
            "file": filepath,
            "severity": assess_severity(filepath, branches, config),
            "category": categorize_file(filepath, config),
            "branches": [{"branch": b["branch"], "status": b["status"]} for b in branches]
        }
        if filepath in impact_descs:
            entry["impact"] = impact_descs[filepath]
        # Add score if available
        if scored_conflicts:
            for sc in scored_conflicts:
                if sc.get("file") == filepath:
                    entry["score"] = sc.get("score", {})
                    break
        overlap_entries.append(entry)

    return {
        "last_updated": now,
        "base_branch": base_branch,
        "active_branches": list(branch_changes.keys()),
        "priority_ranking": [
            {"file": sc["file"], "score": sc["score"]["total"], "label": sc["score"]["label"]}
            for sc in (scored_conflicts or [])
        ],
        "direct_conflicts": sorted(
            overlap_entries,
            key=lambda x: x.get("score", {}).get("total", 0),
            reverse=True
        ),
        "dependency_conflicts": dep_conflicts,
        "locks": locks,
        "merge_dry_runs": merge_results,
        "branch_summaries": {
            branch: {
                "files_changed": len(changes),
                "high_impact_files": [
                    c["file"] for c in changes
                    if categorize_file(c["file"], config) != "other"
                ]
            }
            for branch, changes in branch_changes.items()
        }
    }


def update_history(repo_path, direct_overlaps, dep_conflicts, config):
    """Append to conflict history for analytics."""
    history_path = Path(repo_path) / ".canary-history.json"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history = []
    if history_path.exists():
        with open(history_path) as f:
            history = json.load(f)

    entry = {
        "timestamp": now,
        "direct_conflicts": len(direct_overlaps),
        "dependency_conflicts": len(dep_conflicts),
        "high_severity": sum(
            1 for f, b in direct_overlaps.items()
            if assess_severity(f, b, config) == "high"
        ),
        "files": list(direct_overlaps.keys())
    }

    history.append(entry)

    # Keep last 500 entries
    history = history[-500:]

    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)


# ── File Watcher (Continuous Mode) ──

def run_file_watcher(repo_path, config):
    """Run continuous file monitoring with debounced updates."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("Install watchdog for file watching: pip install watchdog", file=sys.stderr)
        sys.exit(1)

    debounce = config.get("automation", {}).get("debounce_seconds", 30)
    last_change = [0.0]
    pending = [False]

    class ChangeHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            # Ignore our own log files
            if any(event.src_path.endswith(ext) for ext in
                   [".canary-log.md", ".canary-log.json", ".canary-locks.json",
                    ".canary-deps.json", ".canary-alert", ".canary-notified.json",
                    ".canary-history.json"]):
                return
            last_change[0] = time.time()
            pending[0] = True

    observer = Observer()
    handler = ChangeHandler()

    # Watch all worktree paths
    worktrees = get_active_worktrees(repo_path)
    watched_paths = set()
    for wt_path, _ in worktrees:
        if wt_path not in watched_paths:
            observer.schedule(handler, wt_path, recursive=True)
            watched_paths.add(wt_path)

    if not watched_paths:
        observer.schedule(handler, repo_path, recursive=True)

    observer.start()
    print(f"🦉 Watching for changes (debounce: {debounce}s)...")

    try:
        while True:
            time.sleep(5)
            if pending[0] and (time.time() - last_change[0]) > debounce:
                pending[0] = False
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Changes detected, regenerating log...")
                run_scan(repo_path, config, vscode_output=False)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# ── Main Scan ──

def run_scan(repo_path, config, vscode_output=False):
    """Run a full scan and generate the conflict log."""
    base_branch = config.get("base_branch", "main")
    log_path = Path(repo_path) / config.get("log_path", ".canary-log.md")
    json_path = Path(repo_path) / config.get("json_log_path", ".canary-log.json")

    if vscode_output:
        print("[canary] Scanning...")

    # Collect changes per branch
    branches = get_active_branches(repo_path, base_branch)
    branch_changes = {}
    for _, branch in branches:
        changes = get_changed_files(repo_path, base_branch, branch)
        if changes:
            branch_changes[branch] = changes

    if not branch_changes:
        print("No changes detected on any branch.")
        with open(log_path, "w") as f:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            f.write(f"# 🦜 Canary Log\n**Last updated:** {now}\n\nNo active changes detected.\n")
        return

    # Build dependency graph
    dep_graph = build_dependency_graph(repo_path, config)

    # Find direct overlaps
    direct_overlaps = find_direct_overlaps(branch_changes)

    # Find dependency-based conflicts
    dep_conflicts = find_dependency_conflicts(branch_changes, dep_graph, config)

    # Generate impact descriptions for overlapping files
    impact_descs = {}
    for filepath in direct_overlaps:
        for branch_info in direct_overlaps[filepath]:
            desc = generate_impact_description(
                repo_path, base_branch, branch_info["branch"],
                filepath, dep_graph, branch_changes, config
            )
            if desc:
                impact_descs[filepath] = desc
                break

    # Load locks
    locks = load_locks(repo_path, config) if config.get("locks", {}).get("enabled") else []

    # Merge dry-runs
    merge_results = []
    if config.get("automation", {}).get("dry_run_merges"):
        branch_names = list(branch_changes.keys())
        for i, ba in enumerate(branch_names):
            for bb in branch_names[i + 1:]:
                clean, conflicts = try_merge_dry_run(repo_path, ba, bb)
                if clean is not None:
                    merge_results.append({
                        "branch_a": ba, "branch_b": bb,
                        "clean": clean, "conflict_files": conflicts
                    })

    # ── Score conflicts ──
    scoreable_conflicts = []
    for filepath, branches in direct_overlaps.items():
        scoreable_conflicts.append({
            "file": filepath,
            "category": categorize_file(filepath, config),
            "branches": [b["branch"] for b in branches],
        })

    scored_conflicts = []
    if HAS_EXTENSIONS:
        scoring_context = {
            "dep_graph": dep_graph,
            "merge_results": merge_results,
            "locks": locks,
            "history": _load_history(repo_path),
        }
        scored_conflicts = score_all_conflicts(scoreable_conflicts, scoring_context)

    # ── Update snapshots (rolling history) ──
    trigger = os.environ.get("CANARY_TRIGGER", "manual")
    trigger_detail = os.environ.get("CANARY_TRIGGER_DETAIL", "")

    if HAS_EXTENSIONS:
        snapshot = create_snapshot(
            trigger=trigger,
            trigger_detail=trigger_detail,
            conflicts=scoreable_conflicts,
            dep_conflicts=dep_conflicts,
            merge_results=merge_results,
            scores=scored_conflicts,
            branch_changes=branch_changes
        )
        snapshot_status = update_snapshots(repo_path, snapshot)
    else:
        snapshot_status = "no_extensions"

    # ── Generate logs ──
    md_content = generate_log(
        repo_path, base_branch, branch_changes, direct_overlaps,
        dep_conflicts, locks, merge_results, impact_descs, config,
        scored_conflicts=scored_conflicts
    )

    # Append timeline and commit history if extensions available
    if HAS_EXTENSIONS:
        md_content += "\n" + render_timeline(repo_path)
        md_content += "\n" + render_commit_timeline(repo_path, base_branch, branch_changes)

    with open(log_path, "w") as f:
        f.write(md_content)

    json_data = generate_json_log(
        branch_changes, direct_overlaps, dep_conflicts,
        locks, merge_results, impact_descs, base_branch, config,
        scored_conflicts=scored_conflicts
    )
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2)

    # Update history
    update_history(repo_path, direct_overlaps, dep_conflicts, config)

    # Send notifications
    notifiable_conflicts = []
    for filepath, branches in direct_overlaps.items():
        severity = assess_severity(filepath, branches, config)
        # Use score-based severity if available
        score_severity = severity
        for sc in scored_conflicts:
            if sc.get("file") == filepath:
                total = sc.get("score", {}).get("total", 0)
                if total >= 80:
                    score_severity = "critical"
                elif total >= 60:
                    score_severity = "high"
                break

        notifiable_conflicts.append({
            "file": filepath,
            "severity": score_severity,
            "branches": [b["branch"] for b in branches],
            "description": impact_descs.get(filepath, {}).get("what_changed", ""),
            "score": next((sc.get("score", {}).get("total", 0)
                          for sc in scored_conflicts if sc.get("file") == filepath), 0)
        })
    for dc in dep_conflicts:
        notifiable_conflicts.append({
            "file": dc["source_file"],
            "severity": "medium",
            "branches": [dc["source_branch"], dc["affected_branch"]],
            "description": f"Dependency conflict: changes affect {', '.join(dc['affected_files'])}"
        })

    send_notifications(notifiable_conflicts, config, repo_path)

    # VS Code problem matcher output
    if vscode_output:
        for sc in scored_conflicts:
            score = sc.get("score", {})
            total = score.get("total", 0)
            label = score.get("label", "INFO")
            filepath = sc.get("file", "unknown")
            branches = sc.get("branches", [])
            branch_str = ", ".join(branches)

            # Map score to VS Code severity
            if total >= 60:
                severity = "error"
            elif total >= 30:
                severity = "warning"
            else:
                severity = "info"

            desc = impact_descs.get(filepath, {}).get("what_changed", "")
            msg = f"[{label} {total}] Modified on {branch_str}"
            if desc:
                msg += f". {desc}"

            print(f"{severity}: {filepath}:1: {msg}")

        for dc in dep_conflicts:
            affected = ", ".join(dc["affected_files"][:3])
            print(f"warning: {dc['source_file']}:1: Dependency conflict — changes on {dc['source_branch']} affect {affected} on {dc['affected_branch']}")

        print("[canary] Done.")

    # Print summary
    print(f"🦜 Canary log written to: {log_path}")
    print(f"  Branches: {len(branch_changes)}")
    print(f"  Direct overlaps: {len(direct_overlaps)}")
    print(f"  Dependency conflicts: {len(dep_conflicts)}")
    if scored_conflicts:
        top = scored_conflicts[0]
        print(f"  Top priority: {top['file']} (score: {top['score']['total']})")
    high = sum(1 for f, b in direct_overlaps.items()
               if assess_severity(f, b, config) == "high")
    if high:
        print(f"  ⚠️  {high} high-severity conflict(s)!")


def _load_history(repo_path):
    """Load conflict history for scoring age calculation."""
    history_path = Path(repo_path) / ".canary-history.json"
    if history_path.exists():
        with open(history_path) as f:
            return json.load(f)
    return []


# ── CLI ──

def init_config(repo_path, ntfy_topic=None):
    """Generate default config file."""
    config = DEFAULT_CONFIG.copy()
    if ntfy_topic:
        config["notifications"]["enabled"] = True
        config["notifications"]["ntfy_topic"] = ntfy_topic

    config_path = Path(repo_path) / ".canary.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config written to: {config_path}")
    if ntfy_topic:
        print(f"Notifications enabled: ntfy.sh/{ntfy_topic}")


def main():
    parser = argparse.ArgumentParser(description="Canary Watcher")
    parser.add_argument("--repo", default=".", help="Path to the git repository")
    parser.add_argument("--base", default=None, help="Base branch (overrides config)")
    parser.add_argument("--target", default=None, help="Single target branch (for CI mode)")
    parser.add_argument("--init", action="store_true", help="Generate default config")
    parser.add_argument("--ntfy-topic", default=None, help="ntfy.sh topic (used with --init)")
    parser.add_argument("--watch", action="store_true", help="Continuous file watching mode")
    parser.add_argument("--ci", action="store_true", help="CI/CD output mode")
    parser.add_argument("--vscode", action="store_true", help="Output in VS Code problem matcher format")
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo)

    if args.init:
        init_config(repo_path, args.ntfy_topic)
        return

    # Verify git repo
    if not run_git(repo_path, "rev-parse", "--git-dir"):
        print(f"Error: {repo_path} is not a git repository", file=sys.stderr)
        sys.exit(1)

    config = load_config(repo_path)

    if args.base:
        config["base_branch"] = args.base

    if args.watch:
        run_file_watcher(repo_path, config)
    else:
        run_scan(repo_path, config, vscode_output=args.vscode)

        if args.ci:
            # Check for high-severity and set exit code
            alert_path = Path(repo_path) / ".canary-alert"
            if alert_path.exists():
                sys.exit(1)


if __name__ == "__main__":
    main()
