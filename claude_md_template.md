# 🦜 Canary — CLAUDE.md Template

Add this to your project's CLAUDE.md so Claude Code sessions automatically
participate in the conflict awareness system.

---

## 🦜 Canary Protocol

This project uses an automated conflict awareness system to coordinate changes
across parallel branches and developers. The system maintains a shared conflict
log at `.canary-log.md` and a machine-readable version at `.canary-log.json`.

### Before making changes

**If the MCP server is configured**, use the `check_file` tool before editing any
shared file. It will tell you if the file is locked, modified on other branches,
or has upstream dependency risks. If using the MCP server, you can also use
`claim_file` to lock files and `log_change` to broadcast breaking changes.

**If reading the log manually**, check `.canary-log.md` before modifying any of
the following file categories:

- **API contracts**: Route definitions, request/response types, status codes
- **Shared types**: TypeScript interfaces, Python dataclasses, protobuf definitions
- **Database schemas**: Migrations, model definitions, column changes
- **Config files**: Environment variables, feature flags, build settings
- **Shared utilities**: Helper functions, middleware, shared hooks

Also check `.canary-locks.json` for active file locks. If a file is locked by
another branch, do not modify it — coordinate with the lock owner first.

If the log shows a conflict with your planned change:
1. Read the impact description to understand what changed and why
2. Check the dependency conflict section for indirect breaks
3. Adapt your approach to work with the other branch's changes
4. If the conflict is severe, pause and coordinate

### After making changes

The conflict log auto-updates via git hooks after each commit. However, if you
make a change to a high-impact file that the automated system might not fully
describe, add context by telling the developer what you changed and why.

For breaking changes to shared interfaces, consider claiming the file first:

```bash
python scripts/lock_manager.py --claim src/types/user.ts \
  --branch your-branch --reason "Restructuring User type"
```

### Checking merge compatibility

The log includes merge dry-run results showing whether your branch can merge
cleanly with other active branches. If your branch shows conflicts, address
them proactively rather than waiting for PR review.
