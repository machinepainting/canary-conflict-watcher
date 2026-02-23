#!/usr/bin/env python3
"""
File Lock Manager

Allows developers to "claim" critical files before making breaking changes,
preventing others from building against an unstable interface.

Usage:
    python lock_manager.py --claim src/types/user.ts --branch feature-api --reason "Restructuring"
    python lock_manager.py --release src/types/user.ts
    python lock_manager.py --list
    python lock_manager.py --cleanup
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_config(repo_path):
    config_path = Path(repo_path) / ".canary.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {"locks": {"lock_file": ".canary-locks.json", "auto_expire_minutes": 120}}


def load_locks(lock_path):
    if lock_path.exists():
        with open(lock_path) as f:
            return json.load(f)
    return []


def save_locks(lock_path, locks):
    with open(lock_path, "w") as f:
        json.dump(locks, f, indent=2)


def claim_file(repo_path, filepath, branch, reason=None, author=None):
    config = load_config(repo_path)
    lock_path = Path(repo_path) / config["locks"]["lock_file"]
    locks = load_locks(lock_path)

    # Check if already locked
    for lock in locks:
        if lock["file"] == filepath:
            expire_mins = config["locks"]["auto_expire_minutes"]
            claimed = datetime.fromisoformat(lock["claimed_at"])
            if (datetime.now(timezone.utc) - claimed).total_seconds() < expire_mins * 60:
                print(f"❌ Already locked by `{lock['branch']}`: {lock.get('reason', 'no reason')}")
                print(f"   Release first or wait for expiry.")
                sys.exit(1)
            else:
                locks.remove(lock)
                break

    now = datetime.now(timezone.utc)
    expire_mins = config["locks"]["auto_expire_minutes"]
    expires = now + timedelta(minutes=expire_mins)

    lock = {
        "file": filepath,
        "branch": branch,
        "author": author or os.environ.get("USER", "unknown"),
        "reason": reason or "No reason given",
        "claimed_at": now.isoformat(),
        "expires_at": expires.isoformat()
    }

    locks.append(lock)
    save_locks(lock_path, locks)

    print(f"🔒 Locked: {filepath}")
    print(f"   Branch: {branch}")
    print(f"   Reason: {reason or 'No reason given'}")
    print(f"   Expires: {expires.strftime('%Y-%m-%d %H:%M UTC')}")


def release_file(repo_path, filepath):
    config = load_config(repo_path)
    lock_path = Path(repo_path) / config["locks"]["lock_file"]
    locks = load_locks(lock_path)

    found = False
    locks_new = []
    for lock in locks:
        if lock["file"] == filepath:
            found = True
            print(f"🔓 Released: {filepath} (was locked by `{lock['branch']}`)")
        else:
            locks_new.append(lock)

    if not found:
        print(f"No lock found for: {filepath}")
        return

    save_locks(lock_path, locks_new)


def list_locks(repo_path):
    config = load_config(repo_path)
    lock_path = Path(repo_path) / config["locks"]["lock_file"]
    locks = load_locks(lock_path)
    expire_mins = config["locks"]["auto_expire_minutes"]

    now = datetime.now(timezone.utc)
    active = []
    expired = []

    for lock in locks:
        claimed = datetime.fromisoformat(lock["claimed_at"])
        if (now - claimed).total_seconds() < expire_mins * 60:
            active.append(lock)
        else:
            expired.append(lock)

    if not active and not expired:
        print("No locks found.")
        return

    if active:
        print(f"🔒 Active locks ({len(active)}):\n")
        for lock in active:
            claimed = datetime.fromisoformat(lock["claimed_at"])
            expires = claimed + timedelta(minutes=expire_mins)
            remaining = expires - now
            mins_left = int(remaining.total_seconds() / 60)
            print(f"  {lock['file']}")
            print(f"    Branch: {lock['branch']} | Author: {lock.get('author', 'unknown')}")
            print(f"    Reason: {lock.get('reason', 'none')}")
            print(f"    Expires in: {mins_left} minutes")
            print()

    if expired:
        print(f"⏰ Expired locks ({len(expired)}) — will be cleaned up on next run")
        for lock in expired:
            print(f"  {lock['file']} (was: {lock['branch']})")


def cleanup_locks(repo_path):
    config = load_config(repo_path)
    lock_path = Path(repo_path) / config["locks"]["lock_file"]
    locks = load_locks(lock_path)
    expire_mins = config["locks"]["auto_expire_minutes"]

    now = datetime.now(timezone.utc)
    active = []
    removed = 0

    for lock in locks:
        claimed = datetime.fromisoformat(lock["claimed_at"])
        if (now - claimed).total_seconds() < expire_mins * 60:
            active.append(lock)
        else:
            removed += 1

    save_locks(lock_path, active)
    print(f"Cleaned up {removed} expired lock(s). {len(active)} active lock(s) remaining.")


def main():
    parser = argparse.ArgumentParser(description="Canary Lock Manager")
    parser.add_argument("--repo", default=".", help="Path to repo")
    parser.add_argument("--claim", metavar="FILE", help="Claim/lock a file")
    parser.add_argument("--release", metavar="FILE", help="Release a lock")
    parser.add_argument("--branch", help="Branch name (for --claim)")
    parser.add_argument("--reason", help="Reason for claiming (for --claim)")
    parser.add_argument("--author", help="Your name (defaults to $USER)")
    parser.add_argument("--list", action="store_true", help="List all locks")
    parser.add_argument("--cleanup", action="store_true", help="Remove expired locks")
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo)

    if args.claim:
        if not args.branch:
            print("--branch is required when claiming a file", file=sys.stderr)
            sys.exit(1)
        claim_file(repo_path, args.claim, args.branch, args.reason, args.author)
    elif args.release:
        release_file(repo_path, args.release)
    elif args.list:
        list_locks(repo_path)
    elif args.cleanup:
        cleanup_locks(repo_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
