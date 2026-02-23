#!/usr/bin/env python3
"""
🦜 Canary MCP Server

Exposes the conflict awareness system as an MCP (Model Context Protocol) server,
allowing Claude Code sessions to query conflict state programmatically rather than
reading a markdown file.

Tools provided:
    - check_conflicts:      Get all active conflicts with scores
    - check_file:           Check if a specific file has conflicts or locks
    - get_dependents:       Find all files that depend on a given file
    - get_locks:            List active file locks
    - claim_file:           Claim/lock a file before making breaking changes
    - release_file:         Release a file lock
    - merge_compatibility:  Check if two branches can merge cleanly
    - log_change:           Record a breaking change for other sessions to see
    - get_timeline:         Get the rolling conflict snapshot timeline

Setup:
    Add to your Claude Code MCP config (.claude/mcp.json):

    {
      "mcpServers": {
        "canary": {
          "command": "python3",
          "args": [".claude/skills/canary/scripts/mcp_server.py"],
          "cwd": "${workspaceFolder}"
        }
      }
    }

    Or run standalone for testing:
    python mcp_server.py --repo /path/to/repo
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add scripts directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from watch_conflicts import (
    load_config, get_active_branches, get_changed_files,
    find_direct_overlaps, find_dependency_conflicts,
    build_dependency_graph, find_dependents,
    categorize_file, assess_severity, run_git,
    get_diff_details, generate_impact_description,
    try_merge_dry_run
)
from lock_manager import load_locks as load_locks_raw, save_locks
from scoring import score_all_conflicts, score_to_label, score_to_icon
from snapshots import load_snapshots, render_timeline


# ── MCP Protocol Implementation ──

class MCPServer:
    """Minimal MCP server implementing the stdio transport."""

    def __init__(self, repo_path):
        self.repo_path = os.path.abspath(repo_path)
        self.config = load_config(self.repo_path)
        self._dep_graph = None
        self._dep_graph_age = 0

    @property
    def dep_graph(self):
        """Lazy-load dependency graph, rebuild if stale (>5 min)."""
        import time
        now = time.time()
        if self._dep_graph is None or (now - self._dep_graph_age) > 300:
            self._dep_graph = build_dependency_graph(self.repo_path, self.config)
            self._dep_graph_age = now
        return self._dep_graph

    def get_tools(self):
        """Return tool definitions for MCP discovery."""
        return [
            {
                "name": "check_conflicts",
                "description": (
                    "Get all active cross-branch conflicts with priority scores. "
                    "Returns direct file overlaps, dependency-based conflicts, and "
                    "a priority ranking. Call this before modifying any shared file."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "min_score": {
                            "type": "integer",
                            "description": "Only return conflicts with score >= this value (0-100)",
                            "default": 0
                        }
                    }
                }
            },
            {
                "name": "check_file",
                "description": (
                    "Check if a specific file has active conflicts, locks, or "
                    "dependency risks. Call this before editing a file to see if "
                    "other branches are modifying it or anything it depends on."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to the file to check (relative to repo root)"
                        }
                    },
                    "required": ["filepath"]
                }
            },
            {
                "name": "get_dependents",
                "description": (
                    "Find all files that import from or depend on a given file. "
                    "Useful for understanding the blast radius of a change."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to the file to trace dependents for"
                        }
                    },
                    "required": ["filepath"]
                }
            },
            {
                "name": "get_locks",
                "description": "List all active file locks/claims.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "claim_file",
                "description": (
                    "Claim a file before making breaking changes. Other sessions "
                    "will see the lock and be warned not to build against this file."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to the file to claim"
                        },
                        "branch": {
                            "type": "string",
                            "description": "Branch making the claim"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why you're claiming this file"
                        }
                    },
                    "required": ["filepath", "branch", "reason"]
                }
            },
            {
                "name": "release_file",
                "description": "Release a file lock/claim.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to the file to release"
                        }
                    },
                    "required": ["filepath"]
                }
            },
            {
                "name": "merge_compatibility",
                "description": (
                    "Check if two branches can merge cleanly using git merge-tree. "
                    "Returns clean/conflict status with affected files."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "branch_a": {
                            "type": "string",
                            "description": "First branch name"
                        },
                        "branch_b": {
                            "type": "string",
                            "description": "Second branch name"
                        }
                    },
                    "required": ["branch_a", "branch_b"]
                }
            },
            {
                "name": "log_change",
                "description": (
                    "Record a breaking change you just made so other sessions "
                    "are immediately aware. Use after modifying shared types, "
                    "API contracts, or other high-impact files."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "File that was changed"
                        },
                        "branch": {
                            "type": "string",
                            "description": "Branch the change was made on"
                        },
                        "description": {
                            "type": "string",
                            "description": "Plain-language description of what changed and why"
                        },
                        "breaking": {
                            "type": "boolean",
                            "description": "Whether this is a breaking change",
                            "default": False
                        }
                    },
                    "required": ["filepath", "branch", "description"]
                }
            },
            {
                "name": "get_timeline",
                "description": (
                    "Get the rolling conflict snapshot timeline showing current "
                    "state and last 2 resolved states."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    # ── Tool Implementations ──

    def handle_check_conflicts(self, args):
        """Get all active conflicts with scores."""
        min_score = args.get("min_score", 0)
        base = self.config.get("base_branch", "main")

        branches = get_active_branches(self.repo_path, base)
        branch_changes = {}
        for _, branch in branches:
            changes = get_changed_files(self.repo_path, base, branch)
            if changes:
                branch_changes[branch] = changes

        if not branch_changes:
            return {"status": "clean", "message": "No active changes on any branch.", "conflicts": []}

        direct_overlaps = find_direct_overlaps(branch_changes)
        dep_conflicts = find_dependency_conflicts(branch_changes, self.dep_graph, self.config)

        # Score conflicts
        scoreable = []
        for filepath, branches_info in direct_overlaps.items():
            scoreable.append({
                "file": filepath,
                "category": categorize_file(filepath, self.config),
                "branches": [b["branch"] for b in branches_info],
            })

        history_path = Path(self.repo_path) / ".canary-history.json"
        history = []
        if history_path.exists():
            with open(history_path) as f:
                history = json.load(f)

        locks = self._get_locks()
        merge_results = self._quick_merge_check(list(branch_changes.keys()))

        scored = score_all_conflicts(scoreable, {
            "dep_graph": self.dep_graph,
            "merge_results": merge_results,
            "locks": locks,
            "history": history,
        })

        # Filter by min_score
        scored = [s for s in scored if s["score"]["total"] >= min_score]

        return {
            "status": "conflicts" if scored else "clean",
            "total_conflicts": len(scored),
            "high_severity": sum(1 for s in scored if s["score"]["total"] >= 60),
            "conflicts": [
                {
                    "file": s["file"],
                    "score": s["score"]["total"],
                    "label": s["score"]["label"],
                    "category": s.get("category", "other"),
                    "branches": s.get("branches", []),
                    "factors": s["score"]["factors"]
                }
                for s in scored
            ],
            "dependency_conflicts": [
                {
                    "source_file": dc["source_file"],
                    "source_branch": dc["source_branch"],
                    "affected_files": dc["affected_files"],
                    "affected_branch": dc["affected_branch"]
                }
                for dc in dep_conflicts
            ]
        }

    def handle_check_file(self, args):
        """Check a specific file for conflicts, locks, and dependency risks."""
        filepath = args["filepath"]
        base = self.config.get("base_branch", "main")

        result = {
            "file": filepath,
            "category": categorize_file(filepath, self.config),
            "locked": False,
            "lock_info": None,
            "modified_on_branches": [],
            "dependents_count": 0,
            "dependency_risk": [],
            "safe_to_edit": True,
            "warnings": []
        }

        # Check locks
        locks = self._get_locks()
        for lock in locks:
            if lock.get("file") == filepath:
                result["locked"] = True
                result["safe_to_edit"] = False
                result["lock_info"] = {
                    "branch": lock["branch"],
                    "author": lock.get("author", "unknown"),
                    "reason": lock.get("reason", ""),
                    "claimed_at": lock.get("claimed_at", "")
                }
                result["warnings"].append(
                    f"LOCKED by {lock['branch']}: {lock.get('reason', 'no reason')}"
                )

        # Check which branches have modified this file
        branches = get_active_branches(self.repo_path, base)
        for _, branch in branches:
            changes = get_changed_files(self.repo_path, base, branch)
            for change in changes:
                if change["file"] == filepath:
                    result["modified_on_branches"].append(branch)

        if len(result["modified_on_branches"]) > 1:
            result["safe_to_edit"] = False
            result["warnings"].append(
                f"Modified on {len(result['modified_on_branches'])} branches: "
                f"{', '.join(result['modified_on_branches'])}"
            )

        # Check dependents (blast radius)
        dependents = find_dependents(filepath, self.dep_graph)
        result["dependents_count"] = len(dependents)
        if dependents:
            result["dependents"] = sorted(list(dependents))[:20]  # Limit output

        # Check if anything this file depends on is conflicting
        imports = self.dep_graph.get(filepath, [])
        for imp in imports:
            for _, branch in branches:
                changes = get_changed_files(self.repo_path, base, branch)
                for change in changes:
                    if change["file"] == imp:
                        result["dependency_risk"].append({
                            "upstream_file": imp,
                            "changed_on": branch,
                            "note": f"This file imports from {imp}, which was modified on {branch}"
                        })

        if result["dependency_risk"]:
            result["warnings"].append(
                f"Upstream dependencies modified: "
                f"{', '.join(r['upstream_file'] for r in result['dependency_risk'])}"
            )

        return result

    def handle_get_dependents(self, args):
        """Find all files that depend on a given file."""
        filepath = args["filepath"]
        dependents = find_dependents(filepath, self.dep_graph)

        # Separate direct vs transitive
        direct = set()
        for source_file, imports in self.dep_graph.items():
            if filepath in imports:
                direct.add(source_file)
        transitive = dependents - direct

        return {
            "file": filepath,
            "total_dependents": len(dependents),
            "direct": sorted(list(direct)),
            "transitive": sorted(list(transitive)),
            "blast_radius": "high" if len(dependents) > 10 else "medium" if len(dependents) > 3 else "low"
        }

    def handle_get_locks(self, args):
        """List active locks."""
        locks = self._get_locks()
        return {
            "total": len(locks),
            "locks": [
                {
                    "file": l["file"],
                    "branch": l["branch"],
                    "author": l.get("author", "unknown"),
                    "reason": l.get("reason", ""),
                    "claimed_at": l.get("claimed_at", ""),
                    "expires_at": l.get("expires_at", "")
                }
                for l in locks
            ]
        }

    def handle_claim_file(self, args):
        """Claim a file."""
        filepath = args["filepath"]
        branch = args["branch"]
        reason = args.get("reason", "")

        lock_file_path = Path(self.repo_path) / self.config.get("locks", {}).get(
            "lock_file", ".canary-locks.json"
        )
        locks = []
        if lock_file_path.exists():
            with open(lock_file_path) as f:
                locks = json.load(f)

        # Check existing
        expire_mins = self.config.get("locks", {}).get("auto_expire_minutes", 120)
        now = datetime.now(timezone.utc)
        for lock in locks:
            if lock["file"] == filepath:
                claimed = datetime.fromisoformat(lock["claimed_at"])
                if (now - claimed).total_seconds() < expire_mins * 60:
                    return {
                        "success": False,
                        "error": f"Already locked by {lock['branch']}: {lock.get('reason', '')}"
                    }
                else:
                    locks.remove(lock)
                    break

        expires = now + timedelta(minutes=expire_mins)
        lock = {
            "file": filepath,
            "branch": branch,
            "author": os.environ.get("USER", "claude-code"),
            "reason": reason,
            "claimed_at": now.isoformat(),
            "expires_at": expires.isoformat()
        }
        locks.append(lock)
        save_locks(lock_file_path, locks)

        return {
            "success": True,
            "file": filepath,
            "expires_at": expires.isoformat(),
            "message": f"Locked {filepath}. Other sessions will be warned."
        }

    def handle_release_file(self, args):
        """Release a file lock."""
        filepath = args["filepath"]

        lock_file_path = Path(self.repo_path) / self.config.get("locks", {}).get(
            "lock_file", ".canary-locks.json"
        )
        if not lock_file_path.exists():
            return {"success": False, "error": "No locks found"}

        with open(lock_file_path) as f:
            locks = json.load(f)

        new_locks = [l for l in locks if l["file"] != filepath]
        if len(new_locks) == len(locks):
            return {"success": False, "error": f"No lock found for {filepath}"}

        save_locks(lock_file_path, new_locks)
        return {"success": True, "file": filepath, "message": f"Released lock on {filepath}"}

    def handle_merge_compatibility(self, args):
        """Check merge compatibility using git merge-tree."""
        branch_a = args["branch_a"]
        branch_b = args["branch_b"]

        # Try git merge-tree first (faster, no filesystem changes)
        result = self._merge_tree_check(branch_a, branch_b)
        if result is not None:
            return result

        # Fall back to traditional dry-run
        clean, conflict_files = try_merge_dry_run(self.repo_path, branch_a, branch_b)
        if clean is None:
            return {"error": "Could not perform merge check"}

        return {
            "branch_a": branch_a,
            "branch_b": branch_b,
            "clean": clean,
            "conflict_files": conflict_files,
            "method": "dry-run",
            "message": "Clean merge possible" if clean else f"Conflicts in {len(conflict_files)} file(s)"
        }

    def handle_log_change(self, args):
        """Record a breaking change for other sessions."""
        filepath = args["filepath"]
        branch = args["branch"]
        description = args["description"]
        breaking = args.get("breaking", False)

        broadcast_path = Path(self.repo_path) / ".canary-broadcast.json"
        broadcasts = []
        if broadcast_path.exists():
            with open(broadcast_path) as f:
                broadcasts = json.load(f)

        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "file": filepath,
            "branch": branch,
            "description": description,
            "breaking": breaking,
            "category": categorize_file(filepath, self.config),
            "author": os.environ.get("USER", "claude-code")
        }
        broadcasts.append(entry)

        # Keep last 50 broadcasts
        broadcasts = broadcasts[-50:]

        with open(broadcast_path, "w") as f:
            json.dump(broadcasts, f, indent=2)

        # Also write alert flag if breaking
        if breaking:
            alert_path = Path(self.repo_path) / ".canary-alert"
            with open(alert_path, "w") as f:
                json.dump({
                    "timestamp": now.isoformat(),
                    "conflicts": [entry]
                }, f, indent=2)

        return {
            "success": True,
            "message": f"Change recorded. {'⚠️ Breaking change alert sent.' if breaking else 'Other sessions will see this on next check.'}"
        }

    def handle_get_timeline(self, args):
        """Get the snapshot timeline."""
        snapshots = load_snapshots(self.repo_path)
        timeline = render_timeline(self.repo_path)

        current = snapshots.get("current")
        resolved = snapshots.get("resolved", [])

        return {
            "current": {
                "timestamp": current.get("timestamp") if current else None,
                "total_conflicts": current.get("total_conflicts", 0) if current else 0,
                "high_severity": current.get("high_severity", 0) if current else 0,
                "trigger": current.get("trigger") if current else None,
            } if current else None,
            "resolved_count": len(resolved),
            "resolved": [
                {
                    "timestamp": r.get("timestamp"),
                    "resolved_at": r.get("resolved_at"),
                    "total_conflicts": r.get("total_conflicts", 0),
                    "resolved_files": r.get("resolved_files", [])
                }
                for r in resolved
            ],
            "timeline_markdown": timeline
        }

    # ── Internal Helpers ──

    def _get_locks(self):
        """Get active locks with expiry filtering."""
        lock_path = Path(self.repo_path) / self.config.get("locks", {}).get(
            "lock_file", ".canary-locks.json"
        )
        if not lock_path.exists():
            return []

        with open(lock_path) as f:
            locks = json.load(f)

        expire_mins = self.config.get("locks", {}).get("auto_expire_minutes", 120)
        now = datetime.now(timezone.utc)
        return [
            l for l in locks
            if (now - datetime.fromisoformat(l["claimed_at"])).total_seconds() < expire_mins * 60
        ]

    def _merge_tree_check(self, branch_a, branch_b):
        """Use git merge-tree for fast, filesystem-safe merge check."""
        # git merge-tree --write-tree requires git 2.38+
        merge_base = run_git(self.repo_path, "merge-base", branch_a, branch_b)
        if not merge_base:
            return None

        result = subprocess.run(
            ["git", "-C", self.repo_path, "merge-tree", "--write-tree",
             "--no-messages", merge_base, branch_a, branch_b],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            return {
                "branch_a": branch_a,
                "branch_b": branch_b,
                "clean": True,
                "conflict_files": [],
                "method": "merge-tree",
                "message": "Clean merge possible"
            }
        elif result.returncode == 1:
            # Parse conflicting files from output
            conflict_files = []
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith("CONFLICT"):
                    # Tree SHA line or filename
                    if "/" in line or "." in line:
                        conflict_files.append(line)

            # Also check stderr for CONFLICT markers
            for line in result.stderr.split("\n"):
                if "CONFLICT" in line:
                    # Extract filename from "CONFLICT (content): Merge conflict in <file>"
                    if "Merge conflict in " in line:
                        fname = line.split("Merge conflict in ")[-1].strip()
                        if fname and fname not in conflict_files:
                            conflict_files.append(fname)

            return {
                "branch_a": branch_a,
                "branch_b": branch_b,
                "clean": False,
                "conflict_files": conflict_files,
                "method": "merge-tree",
                "message": f"Conflicts in {len(conflict_files)} file(s)"
            }

        return None  # Unsupported git version, fall back

    def _quick_merge_check(self, branch_names):
        """Run merge checks between all branch pairs."""
        results = []
        for i, ba in enumerate(branch_names):
            for bb in branch_names[i + 1:]:
                check = self._merge_tree_check(ba, bb)
                if check:
                    results.append({
                        "branch_a": ba,
                        "branch_b": bb,
                        "clean": check["clean"],
                        "conflict_files": check.get("conflict_files", [])
                    })
        return results

    # ── MCP Protocol Handling ──

    def handle_request(self, request):
        """Route an MCP request to the appropriate handler."""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "canary",
                    "version": "2.0.0"
                }
            })

        elif method == "notifications/initialized":
            return None  # No response needed for notifications

        elif method == "tools/list":
            return self._respond(req_id, {"tools": self.get_tools()})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            handler_map = {
                "check_conflicts": self.handle_check_conflicts,
                "check_file": self.handle_check_file,
                "get_dependents": self.handle_get_dependents,
                "get_locks": self.handle_get_locks,
                "claim_file": self.handle_claim_file,
                "release_file": self.handle_release_file,
                "merge_compatibility": self.handle_merge_compatibility,
                "log_change": self.handle_log_change,
                "get_timeline": self.handle_get_timeline,
            }

            handler = handler_map.get(tool_name)
            if not handler:
                return self._error(req_id, -32601, f"Unknown tool: {tool_name}")

            try:
                result = handler(tool_args)
                return self._respond(req_id, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }]
                })
            except Exception as e:
                return self._error(req_id, -32603, str(e))

        else:
            return self._error(req_id, -32601, f"Unknown method: {method}")

    def _respond(self, req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def run_stdio(self):
        """Run the MCP server on stdio."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                request = json.loads(line)
                response = self.handle_request(request)

                if response is not None:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

            except json.JSONDecodeError:
                continue
            except KeyboardInterrupt:
                break
            except Exception as e:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(e)}
                }
                sys.stdout.write(json.dumps(error_response) + "\n")
                sys.stdout.flush()


# ── CLI for Testing ──

def main():
    import argparse

    parser = argparse.ArgumentParser(description="🦜 Canary MCP Server")
    parser.add_argument("--repo", default=".", help="Path to repo")
    parser.add_argument("--test", metavar="TOOL", help="Test a tool directly")
    parser.add_argument("--args", default="{}", help="JSON args for --test")
    args = parser.parse_args()

    server = MCPServer(args.repo)

    if args.test:
        tool_args = json.loads(args.args)
        handler_map = {
            "check_conflicts": server.handle_check_conflicts,
            "check_file": server.handle_check_file,
            "get_dependents": server.handle_get_dependents,
            "get_locks": server.handle_get_locks,
            "claim_file": server.handle_claim_file,
            "release_file": server.handle_release_file,
            "merge_compatibility": server.handle_merge_compatibility,
            "log_change": server.handle_log_change,
            "get_timeline": server.handle_get_timeline,
        }

        handler = handler_map.get(args.test)
        if handler:
            result = handler(tool_args)
            print(json.dumps(result, indent=2))
        else:
            print(f"Unknown tool: {args.test}")
            print(f"Available: {', '.join(handler_map.keys())}")
    else:
        server.run_stdio()


if __name__ == "__main__":
    main()
