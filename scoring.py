#!/usr/bin/env python3
"""
Conflict Scoring Engine

Calculates a priority score (0-100) for each detected conflict based on
multiple weighted signals. Higher score = more urgent to resolve.

Scoring Factors:
    - Category weight (API contracts > shared types > database > config > utils)
    - Dependency fan-out (how many downstream files are affected)
    - Branch count (more branches = more coordination needed)
    - Merge dry-run failure (confirmed conflict vs potential)
    - Age (older unresolved conflicts escalate over time)
    - Cascade risk (does resolving other conflicts depend on this one?)
    - Lock status (actively being worked on = higher urgency)
"""

from datetime import datetime, timezone
from collections import defaultdict


# ── Weight Configuration ──

CATEGORY_WEIGHTS = {
    "api_contracts": 30,
    "shared_types": 25,
    "database": 25,
    "config": 15,
    "shared_utilities": 15,
    "other": 5
}

# Maximum points per factor (must sum to 100)
FACTOR_WEIGHTS = {
    "category": 25,         # What kind of file is it
    "dependency_fanout": 25, # How many files break downstream
    "merge_failure": 20,    # Does a dry-run merge actually fail
    "branch_count": 10,     # How many branches are involved
    "age": 10,              # How long has this been unresolved
    "cascade_risk": 5,      # Do other conflicts depend on this
    "lock_active": 5        # Is someone actively working on it
}


def score_conflict(conflict, context):
    """
    Score a single conflict.

    Args:
        conflict: dict with keys:
            - file: str (filepath)
            - category: str
            - branches: list of branch names
            - first_detected: str (ISO timestamp, optional)
        context: dict with keys:
            - dep_graph: dict (file -> [imports])
            - merge_results: list of dry-run results
            - locks: list of active locks
            - all_conflicts: list of all conflicts (for cascade calc)
            - history: list of history entries

    Returns:
        dict with total score and per-factor breakdown
    """
    scores = {}

    # 1. Category weight
    scores["category"] = score_category(conflict)

    # 2. Dependency fan-out
    scores["dependency_fanout"] = score_dependency_fanout(
        conflict, context.get("dep_graph", {})
    )

    # 3. Merge dry-run failure
    scores["merge_failure"] = score_merge_failure(
        conflict, context.get("merge_results", [])
    )

    # 4. Branch count
    scores["branch_count"] = score_branch_count(conflict)

    # 5. Age escalation
    scores["age"] = score_age(conflict, context.get("history", []))

    # 6. Cascade risk
    scores["cascade_risk"] = score_cascade_risk(
        conflict, context.get("all_conflicts", []), context.get("dep_graph", {})
    )

    # 7. Lock status
    scores["lock_active"] = score_lock_status(
        conflict, context.get("locks", [])
    )

    # Calculate total (weighted)
    total = 0
    for factor, raw_score in scores.items():
        weight = FACTOR_WEIGHTS.get(factor, 0)
        # raw_score is 0.0-1.0, weight is max points for this factor
        total += raw_score * weight

    total = min(100, max(0, round(total)))

    return {
        "total": total,
        "label": score_to_label(total),
        "factors": {k: round(v, 2) for k, v in scores.items()},
        "breakdown": format_breakdown(scores)
    }


def score_category(conflict):
    """Score based on file category. Returns 0.0-1.0."""
    category = conflict.get("category", "other")
    weight = CATEGORY_WEIGHTS.get(category, 5)
    max_weight = max(CATEGORY_WEIGHTS.values())
    return weight / max_weight


def score_dependency_fanout(conflict, dep_graph):
    """Score based on how many files depend on the conflicting file. Returns 0.0-1.0."""
    filepath = conflict.get("file", "")
    if not dep_graph:
        return 0.0

    # Count direct and transitive dependents
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

    count = len(dependents)

    # Scale: 0 deps = 0.0, 1-3 = 0.3, 4-8 = 0.6, 9-15 = 0.8, 16+ = 1.0
    if count == 0:
        return 0.0
    elif count <= 3:
        return 0.3
    elif count <= 8:
        return 0.6
    elif count <= 15:
        return 0.8
    else:
        return 1.0


def score_merge_failure(conflict, merge_results):
    """Score based on whether a merge dry-run fails on this file. Returns 0.0-1.0."""
    filepath = conflict.get("file", "")

    for result in merge_results:
        if not result.get("clean", True):
            if filepath in result.get("conflict_files", []):
                return 1.0  # Confirmed merge conflict

    return 0.0  # No confirmed conflict (may still be a semantic break)


def score_branch_count(conflict):
    """Score based on how many branches are involved. Returns 0.0-1.0."""
    branches = conflict.get("branches", [])
    count = len(branches)

    if count <= 1:
        return 0.0
    elif count == 2:
        return 0.5
    elif count == 3:
        return 0.8
    else:
        return 1.0


def score_age(conflict, history):
    """Score based on how long the conflict has been unresolved. Returns 0.0-1.0."""
    first_detected = conflict.get("first_detected")

    if not first_detected:
        # Check history for earliest mention of this file
        filepath = conflict.get("file", "")
        for entry in history:
            if filepath in entry.get("files", []):
                first_detected = entry.get("timestamp")
                break

    if not first_detected:
        return 0.0  # Just detected, no age penalty

    try:
        detected_time = datetime.fromisoformat(first_detected.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours_old = (now - detected_time).total_seconds() / 3600

        # Scale: <1hr = 0.0, 1-4hr = 0.2, 4-12hr = 0.5, 12-24hr = 0.8, >24hr = 1.0
        if hours_old < 1:
            return 0.0
        elif hours_old < 4:
            return 0.2
        elif hours_old < 12:
            return 0.5
        elif hours_old < 24:
            return 0.8
        else:
            return 1.0
    except (ValueError, TypeError):
        return 0.0


def score_cascade_risk(conflict, all_conflicts, dep_graph):
    """Score based on whether other conflicts depend on resolving this one first. Returns 0.0-1.0."""
    filepath = conflict.get("file", "")

    if not dep_graph or not all_conflicts:
        return 0.0

    # Find how many other conflicting files import from this file
    other_conflict_files = {c["file"] for c in all_conflicts if c["file"] != filepath}

    dependents_that_conflict = set()
    for source_file, imports in dep_graph.items():
        if filepath in imports and source_file in other_conflict_files:
            dependents_that_conflict.add(source_file)

    count = len(dependents_that_conflict)

    if count == 0:
        return 0.0
    elif count == 1:
        return 0.5
    else:
        return 1.0


def score_lock_status(conflict, locks):
    """Score based on whether the file is actively locked. Returns 0.0-1.0."""
    filepath = conflict.get("file", "")

    for lock in locks:
        if lock.get("file") == filepath:
            return 1.0  # Actively being modified — high urgency to coordinate

    return 0.0


# ── Output Formatting ──

def score_to_label(score):
    """Convert numeric score to a human-readable priority label."""
    if score >= 80:  # Phoenix - on fire
        return "CRITICAL"
    elif score >= 60:
        return "HIGH"
    elif score >= 40:
        return "MEDIUM"
    elif score >= 20:
        return "LOW"
    else:
        return "INFO"


def score_to_icon(score):
    """Convert score to a Canary bird icon."""
    if score >= 80:
        return "🐦‍🔥"  # Phoenix — on fire
    elif score >= 60:
        return "🦜"   # Canary — loud alert
    elif score >= 40:
        return "🐦"   # Bluebird — pay attention
    elif score >= 20:
        return "🪶"   # Feather — light touch
    else:
        return "🪹"   # Empty nest — awareness only


def score_to_bar(score, width=20):
    """Render score as a visual bar: [████████░░░░░░░░░░░░] 45/100."""
    filled = round(score / 100 * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {score}/100"


def format_breakdown(factors):
    """Format the scoring factors as a readable string."""
    lines = []
    for factor, raw in sorted(factors.items(), key=lambda x: -x[1]):
        weight = FACTOR_WEIGHTS.get(factor, 0)
        points = round(raw * weight, 1)
        max_pts = weight
        label = factor.replace("_", " ").title()
        if raw > 0:
            lines.append(f"{label}: {points}/{max_pts}")
    return " | ".join(lines) if lines else "No significant factors"


def score_all_conflicts(conflicts, context):
    """Score all conflicts and return sorted by priority (highest first)."""
    scored = []
    for conflict in conflicts:
        result = score_conflict(conflict, {**context, "all_conflicts": conflicts})
        scored.append({**conflict, "score": result})

    scored.sort(key=lambda x: -x["score"]["total"])
    return scored


def format_scored_log_section(scored_conflicts):
    """Format scored conflicts as a markdown section for the log."""
    if not scored_conflicts:
        return ""

    lines = []
    lines.append("## 🦜 Canary Priority Ranking")
    lines.append("")
    lines.append("Conflicts ranked by resolution urgency. Higher score = resolve first.")
    lines.append("")
    lines.append("| Priority | Score | File | Category | Key Factors |")
    lines.append("|----------|-------|------|----------|-------------|")

    for conflict in scored_conflicts:
        score = conflict["score"]
        icon = score_to_icon(score["total"])
        label = score["label"]
        filepath = conflict.get("file", "unknown")
        category = conflict.get("category", "other").replace("_", " ").title()
        breakdown = score["breakdown"]

        # Truncate filepath if needed
        display_path = filepath if len(filepath) < 40 else "..." + filepath[-37:]

        lines.append(
            f"| {icon} {label} | **{score['total']}** | `{display_path}` | {category} | {breakdown} |"
        )

    lines.append("")

    # Detailed breakdown for top conflicts
    top = [c for c in scored_conflicts if c["score"]["total"] >= 40]
    if top:
        lines.append("### Resolution Order")
        lines.append("")
        lines.append("Resolve these in order — higher-scored conflicts may unblock lower ones.")
        lines.append("")

        for i, conflict in enumerate(top, 1):
            score = conflict["score"]
            lines.append(f"**{i}. `{conflict['file']}`** — {score_to_bar(score['total'])}")
            lines.append(f"   {score['label']}: {score['breakdown']}")

            branches = conflict.get("branches", [])
            if branches:
                lines.append(f"   Branches: {', '.join(f'`{b}`' for b in branches)}")
            lines.append("")

    return "\n".join(lines)
