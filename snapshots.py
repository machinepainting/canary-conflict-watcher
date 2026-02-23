#!/usr/bin/env python3
"""
Conflict Snapshot Manager

Maintains a rolling window of conflict snapshots — keeping the current state
plus the last 2 resolved snapshots. Each snapshot captures the full conflict
state at a point in time, tagged with what triggered it (commit, merge, manual).

Snapshots are stored in .canary-snapshots.json and rendered as a visual
timeline in the conflict log.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy


MAX_RESOLVED_SNAPSHOTS = 2


def load_snapshots(repo_path):
    """Load snapshot history."""
    path = Path(repo_path) / ".canary-snapshots.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"current": None, "resolved": []}


def save_snapshots(repo_path, snapshots):
    """Save snapshot history."""
    path = Path(repo_path) / ".canary-snapshots.json"
    with open(path, "w") as f:
        json.dump(snapshots, f, indent=2)


def create_snapshot(trigger, trigger_detail, conflicts, dep_conflicts,
                    merge_results, scores, branch_changes):
    """Create a new snapshot of the current conflict state."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "timestamp": now,
        "trigger": trigger,           # "commit", "merge", "manual", "scheduled"
        "trigger_detail": trigger_detail,  # e.g. "abc1234 feat: add phone verification"
        "conflicts": conflicts,
        "dependency_conflicts": dep_conflicts,
        "merge_results": merge_results,
        "scores": scores,
        "branches": list(branch_changes.keys()),
        "total_conflicts": len(conflicts),
        "high_severity": sum(1 for c in scores if c.get("score", {}).get("total", 0) >= 60),
        "resolved_from_previous": []  # Filled in by compare_snapshots
    }


def update_snapshots(repo_path, new_snapshot):
    """
    Update the rolling snapshot history.

    Logic:
    - If this is the first snapshot, it becomes current.
    - If conflicts have changed since the last snapshot, rotate:
      - Previous current → resolved (if it had conflicts)
      - New snapshot → current
      - Trim resolved to MAX_RESOLVED_SNAPSHOTS
    - If conflicts are identical, just update the timestamp on current.
    """
    snapshots = load_snapshots(repo_path)

    if snapshots["current"] is None:
        snapshots["current"] = new_snapshot
        save_snapshots(repo_path, snapshots)
        return "initial"

    old_current = snapshots["current"]

    # Compare conflict sets
    old_files = {c["file"] for c in old_current.get("conflicts", [])}
    new_files = {c["file"] for c in new_snapshot.get("conflicts", [])}

    resolved_files = old_files - new_files
    new_conflict_files = new_files - old_files

    if resolved_files or new_conflict_files or _scores_changed(old_current, new_snapshot):
        # Tag what was resolved
        new_snapshot["resolved_from_previous"] = list(resolved_files)
        new_snapshot["new_since_previous"] = list(new_conflict_files)

        # Rotate: old current → resolved (only if it had conflicts)
        if old_current.get("total_conflicts", 0) > 0:
            # Mark it as resolved
            old_current["status"] = "resolved"
            old_current["resolved_at"] = new_snapshot["timestamp"]
            old_current["resolved_files"] = list(resolved_files)
            snapshots["resolved"].insert(0, old_current)

            # Trim to max
            snapshots["resolved"] = snapshots["resolved"][:MAX_RESOLVED_SNAPSHOTS]

        snapshots["current"] = new_snapshot
        save_snapshots(repo_path, snapshots)
        return "rotated"
    else:
        # No meaningful change — just update timestamp
        snapshots["current"]["timestamp"] = new_snapshot["timestamp"]
        save_snapshots(repo_path, snapshots)
        return "unchanged"


def _scores_changed(old_snapshot, new_snapshot):
    """Check if scores have meaningfully changed between snapshots."""
    old_scores = {s.get("file"): s.get("score", {}).get("total", 0)
                  for s in old_snapshot.get("scores", [])}
    new_scores = {s.get("file"): s.get("score", {}).get("total", 0)
                  for s in new_snapshot.get("scores", [])}

    # Check for score changes > 10 points
    all_files = set(old_scores.keys()) | set(new_scores.keys())
    for f in all_files:
        old = old_scores.get(f, 0)
        new = new_scores.get(f, 0)
        if abs(old - new) > 10:
            return True
    return False


# ── Timeline Rendering ──

def render_timeline(repo_path, current_snapshot=None):
    """Render the full visual timeline for the conflict log."""
    snapshots = load_snapshots(repo_path)

    if current_snapshot:
        snapshots["current"] = current_snapshot

    if not snapshots["current"] and not snapshots["resolved"]:
        return ""

    lines = []
    lines.append("## 🪺 Canary Timeline")
    lines.append("")
    lines.append("Rolling history showing current conflicts and the last 2 resolved states.")
    lines.append("")

    # ── Current state ──
    current = snapshots.get("current")
    if current:
        lines.append("━" * 70)
        lines.append("")
        lines.append("### 🦜 CURRENT — Active Conflicts")
        lines.append(f"**Since:** {current['timestamp']}")
        lines.append(f"**Trigger:** {_format_trigger(current)}")
        lines.append(f"**Branches:** {', '.join(f'`{b}`' for b in current.get('branches', []))}")
        lines.append("")

        total = current.get("total_conflicts", 0)
        high = current.get("high_severity", 0)

        if total == 0:
            lines.append("✅ No active conflicts")
        else:
            lines.append(f"**{total} conflict(s)** — {high} high severity")
            lines.append("")

            # Show scored conflicts
            for scored in current.get("scores", []):
                score = scored.get("score", {})
                icon = _score_icon(score.get("total", 0))
                label = score.get("label", "INFO")
                filepath = scored.get("file", "unknown")
                lines.append(f"  {icon} **{score.get('total', 0)}** `{filepath}` — {label}")

        # Show what's new since last snapshot
        new_since = current.get("new_since_previous", [])
        resolved_from = current.get("resolved_from_previous", [])

        if new_since:
            lines.append("")
            lines.append(f"**🐣 New since last snapshot:** {', '.join(f'`{f}`' for f in new_since)}")
        if resolved_from:
            lines.append(f"**✅ Resolved since last snapshot:** {', '.join(f'`{f}`' for f in resolved_from)}")

        lines.append("")

    # ── Resolved snapshots ──
    resolved_list = snapshots.get("resolved", [])

    for i, resolved in enumerate(resolved_list):
        lines.append("━" * 70)
        lines.append("")

        age_label = "PREVIOUS" if i == 0 else "OLDER"
        lines.append(f"### 🐦‍⬛ {age_label} — Resolved")
        lines.append(f"**Period:** {resolved['timestamp']} → {resolved.get('resolved_at', 'unknown')}")
        lines.append(f"**Trigger:** {_format_trigger(resolved)}")
        lines.append("")

        total = resolved.get("total_conflicts", 0)
        resolved_files = resolved.get("resolved_files", [])

        if resolved_files:
            lines.append(f"**Resolved ({len(resolved_files)}):**")
            for f in resolved_files:
                lines.append(f"  ✅ `{f}`")
            lines.append("")

        # Show what the conflicts looked like at that time (dimmed/summarized)
        remaining_at_time = [
            s for s in resolved.get("scores", [])
            if s.get("file") not in resolved_files
        ]
        if remaining_at_time:
            lines.append(f"**Still active at that time ({len(remaining_at_time)}):**")
            for scored in remaining_at_time:
                score = scored.get("score", {})
                filepath = scored.get("file", "unknown")
                lines.append(f"  ↳ `{filepath}` (score: {score.get('total', 0)})")
            lines.append("")

        # Show all conflicts that existed during this period
        all_at_time = resolved.get("scores", [])
        if all_at_time and not remaining_at_time:
            lines.append(f"**Conflicts during this period ({len(all_at_time)}):**")
            for scored in all_at_time:
                score = scored.get("score", {})
                filepath = scored.get("file", "unknown")
                lines.append(f"  ↳ `{filepath}` (score: {score.get('total', 0)}) — resolved")
            lines.append("")

    # ── Merge boundary markers ──
    lines.append("━" * 70)
    lines.append("")

    return "\n".join(lines)


def render_commit_timeline(repo_path, base_branch, branch_changes):
    """Render a commit-level timeline between the last merge and now."""
    lines = []
    lines.append("## 📝 Recent Changes (Since Last Merge)")
    lines.append("")

    # Import git helpers
    import subprocess

    for branch, changes in branch_changes.items():
        # Get commits since merge base
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log",
             "--oneline", "--no-merges", "--date=short",
             "--format=%h %ad %s",
             f"{base_branch}..{branch}"],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0 or not result.stdout.strip():
            continue

        commits = result.stdout.strip().split("\n")

        lines.append(f"### `{branch}` — {len(commits)} commit(s)")
        lines.append("")
        lines.append("```")

        for commit in commits:
            parts = commit.split(" ", 2)
            if len(parts) >= 3:
                sha, date, msg = parts[0], parts[1], parts[2]
                # Mark commits that touch high-impact files
                lines.append(f"  {sha} [{date}] {msg}")
            else:
                lines.append(f"  {commit}")

        lines.append("```")
        lines.append("")

        # Show which high-impact files were touched per commit range
        high_impact = [c for c in changes
                       if _is_high_impact(c["file"])]
        if high_impact:
            lines.append("  ⚡ High-impact files in this range:")
            for c in high_impact:
                status_map = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed"}
                lines.append(f"  - `{c['file']}` — {status_map.get(c['status'], c['status'])}")
            lines.append("")

    return "\n".join(lines)


def _format_trigger(snapshot):
    """Format the trigger info for display."""
    trigger = snapshot.get("trigger", "unknown")
    detail = snapshot.get("trigger_detail", "")

    icons = {
        "commit": "💾",
        "merge": "🔀",
        "manual": "👤",
        "scheduled": "⏰",
        "file_watch": "👁️"
    }

    icon = icons.get(trigger, "❓")
    if detail:
        return f"{icon} {trigger}: {detail}"
    return f"{icon} {trigger}"


def _score_icon(score):
    """Score to icon."""
    if score >= 80:
        return "🐦‍🔥"
    elif score >= 60:
        return "🦜"
    elif score >= 40:
        return "🐦"
    elif score >= 20:
        return "🪶"
    return "🪹"


def _is_high_impact(filepath):
    """Quick check if a file is in a high-impact category."""
    patterns = [
        "api/", "routes/", "controllers/", "types/", "interfaces/",
        "models/", "schemas/", "migrations/", "prisma/", ".env",
        "config/", "utils/", "lib/", "shared/", "common/"
    ]
    filepath_lower = filepath.lower()
    return any(p in filepath_lower for p in patterns)
