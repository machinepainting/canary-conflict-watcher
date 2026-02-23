# 🦜 Claude Code Hook Template — Canary Alerts

This hook makes Claude Code sessions automatically aware of cross-branch
conflicts without the developer needing to check the log manually.

## Setup

In your Claude Code session, run `/hooks` and add a Notification hook with
the following configuration.

### Option 1: Check on session start

Add a PreToolUse hook that reads the conflict log at the start of each session:

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "matcher": "Write|Edit|MultiEdit",
      "command": "cat .canary-log.md 2>/dev/null | head -50"
    }
  ]
}
```

This surfaces the top of the conflict log every time Claude is about to write
or edit a file, so it sees active conflicts before making changes.

### Option 2: Alert on new conflicts

Add a Notification hook that checks the `.canary-alert` flag file:

```json
{
  "hooks": [
    {
      "event": "Notification",
      "matcher": "*.idle",
      "command": "if [ -f .canary-alert ]; then cat .canary-alert && rm .canary-alert; fi"
    }
  ]
}
```

When the watcher detects a new high-severity conflict, it writes `.canary-alert`.
The next time Claude is idle, the hook fires, Claude reads the alert, and it can
inform the developer or adjust its approach.

### Option 3: Check locks before editing

Add a PreToolUse hook that checks file locks before editing:

```bash
#!/bin/bash
# Save as .claude/hooks/check-locks.sh
FILE="$1"
if [ -f .canary-locks.json ]; then
    LOCKED=$(python3 -c "
import json, sys
locks = json.load(open('.canary-locks.json'))
for lock in locks:
    if lock['file'] in sys.argv[1]:
        print(f'⚠️ LOCKED by {lock[\"branch\"]}: {lock.get(\"reason\", \"no reason\")}')
        sys.exit(0)
" "$FILE" 2>/dev/null)
    if [ -n "$LOCKED" ]; then
        echo "$LOCKED"
    fi
fi
```

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "matcher": "Write|Edit",
      "command": "bash .claude/hooks/check-locks.sh $INPUT_FILE"
    }
  ]
}
```

## CLAUDE.md Addition for Hook-Aware Sessions

Add this to your CLAUDE.md if using hooks:

```markdown
## Canary Hooks

This project has conflict awareness hooks installed. You will automatically
see alerts when:
- A file you're about to edit is locked by another branch
- A new high-severity conflict has been detected across branches
- The conflict log has relevant information for your current work

When you see a conflict alert:
1. Read the full log at .canary-log.md
2. Assess whether your planned changes will make the conflict worse
3. If so, adjust your approach or flag it to the developer
4. Do not modify locked files without explicit approval
```

## Notes

- Hooks run in the shell, so they need `python3` available
- The PreToolUse hook adds a small delay to each edit — acceptable for the
  safety benefit, but disable if you need maximum speed
- The Notification hook only fires when Claude is idle, so there's no
  interruption during active work
- All hooks are optional — the system works fine with just the conflict log
  and manual checking
