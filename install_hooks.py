#!/usr/bin/env python3
"""
Git Hook Installer for Canary

Installs post-commit, post-merge, and post-checkout hooks that automatically
regenerate the conflict log after relevant git operations.

Usage:
    python install_hooks.py --repo /path/to/repo
    python install_hooks.py --repo . --uninstall
"""

import argparse
import os
import stat
import sys
from pathlib import Path


HOOK_MARKER = "# CANARY-HOOK"

POST_COMMIT_HOOK = '''#!/bin/sh
{marker}
# Auto-regenerate conflict awareness log after each commit
SCRIPT_DIR="$(git rev-parse --show-toplevel)"
if [ -f "$SCRIPT_DIR/.canary.json" ]; then
    COMMIT_MSG="$(git log --oneline -1 HEAD)"
    CANARY_TRIGGER=commit CANARY_TRIGGER_DETAIL="$COMMIT_MSG" \
    python3 "$SCRIPT_DIR/.claude/skills/canary/scripts/watch_conflicts.py" \
        --repo "$SCRIPT_DIR" 2>/dev/null &
fi
'''

POST_MERGE_HOOK = '''#!/bin/sh
{marker}
# Clean up stale entries and regenerate log after merge
SCRIPT_DIR="$(git rev-parse --show-toplevel)"
if [ -f "$SCRIPT_DIR/.canary.json" ]; then
    MERGE_MSG="$(git log --oneline -1 HEAD)"
    CANARY_TRIGGER=merge CANARY_TRIGGER_DETAIL="$MERGE_MSG" \
    python3 "$SCRIPT_DIR/.claude/skills/canary/scripts/watch_conflicts.py" \
        --repo "$SCRIPT_DIR" 2>/dev/null &
fi
'''

POST_CHECKOUT_HOOK = '''#!/bin/sh
{marker}
# Refresh conflict log when switching branches
# Only run on branch checkout (not file checkout)
if [ "$3" = "1" ]; then
    SCRIPT_DIR="$(git rev-parse --show-toplevel)"
    if [ -f "$SCRIPT_DIR/.canary.json" ]; then
        python3 "$SCRIPT_DIR/.claude/skills/canary/scripts/watch_conflicts.py" \\
            --repo "$SCRIPT_DIR" 2>/dev/null &
    fi
fi
'''


def get_hooks_dir(repo_path):
    """Find the git hooks directory."""
    # Check for custom hooks path
    import subprocess
    result = subprocess.run(
        ["git", "-C", repo_path, "config", "core.hooksPath"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        hooks_dir = Path(result.stdout.strip())
        if not hooks_dir.is_absolute():
            hooks_dir = Path(repo_path) / hooks_dir
        return hooks_dir

    # Default location
    git_dir = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--git-dir"],
        capture_output=True, text=True
    )
    if git_dir.returncode == 0:
        return Path(repo_path) / git_dir.stdout.strip() / "hooks"

    return Path(repo_path) / ".git" / "hooks"


def install_hook(hooks_dir, hook_name, hook_content):
    """Install or append to a git hook."""
    hook_path = hooks_dir / hook_name

    if hook_path.exists():
        existing = hook_path.read_text()

        # Already installed
        if HOOK_MARKER in existing:
            print(f"  {hook_name}: already installed, updating...")
            # Remove old version and re-add
            lines = existing.split("\n")
            new_lines = []
            skip = False
            for line in lines:
                if HOOK_MARKER in line:
                    skip = True
                    continue
                if skip and line.startswith("fi"):
                    skip = False
                    continue
                if not skip:
                    new_lines.append(line)

            # Append new hook content (without shebang since file already has one)
            content_lines = hook_content.format(marker=HOOK_MARKER).split("\n")
            # Skip shebang line
            content_no_shebang = "\n".join(content_lines[1:])
            new_content = "\n".join(new_lines).rstrip() + "\n\n" + content_no_shebang
            hook_path.write_text(new_content)
        else:
            # Append to existing hook
            content_lines = hook_content.format(marker=HOOK_MARKER).split("\n")
            content_no_shebang = "\n".join(content_lines[1:])
            new_content = existing.rstrip() + "\n\n" + content_no_shebang
            hook_path.write_text(new_content)
            print(f"  {hook_name}: appended to existing hook")
    else:
        hook_path.write_text(hook_content.format(marker=HOOK_MARKER))
        print(f"  {hook_name}: created")

    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)


def uninstall_hook(hooks_dir, hook_name):
    """Remove conflict awareness from a git hook."""
    hook_path = hooks_dir / hook_name
    if not hook_path.exists():
        return

    existing = hook_path.read_text()
    if HOOK_MARKER not in existing:
        return

    lines = existing.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if HOOK_MARKER in line:
            skip = True
            continue
        if skip and line.strip() == "fi":
            skip = False
            continue
        if not skip:
            new_lines.append(line)

    cleaned = "\n".join(new_lines).strip()

    # If only shebang remains, delete the file
    if cleaned in ("#!/bin/sh", "#!/bin/bash", ""):
        hook_path.unlink()
        print(f"  {hook_name}: removed (was only canary)")
    else:
        hook_path.write_text(cleaned + "\n")
        print(f"  {hook_name}: canary section removed")


def main():
    parser = argparse.ArgumentParser(description="Install Canary Git Hooks")
    parser.add_argument("--repo", default=".", help="Path to the git repository")
    parser.add_argument("--uninstall", action="store_true", help="Remove hooks")
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo)
    hooks_dir = get_hooks_dir(repo_path)

    if not hooks_dir.parent.exists():
        print(f"Error: {repo_path} doesn't appear to be a git repository", file=sys.stderr)
        sys.exit(1)

    hooks_dir.mkdir(parents=True, exist_ok=True)

    hooks = {
        "post-commit": POST_COMMIT_HOOK,
        "post-merge": POST_MERGE_HOOK,
        "post-checkout": POST_CHECKOUT_HOOK,
    }

    if args.uninstall:
        print("Uninstalling conflict awareness hooks...")
        for name in hooks:
            uninstall_hook(hooks_dir, name)
        print("Done.")
    else:
        print(f"Installing hooks to: {hooks_dir}")
        for name, content in hooks.items():
            install_hook(hooks_dir, name, content)
        print("\nDone. The conflict log will auto-update on commit, merge, and branch switch.")
        print("Run `python scripts/watch_conflicts.py --watch` for real-time monitoring.")


if __name__ == "__main__":
    main()
