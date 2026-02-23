# 🦜 IDE Integration

## VS Code

The conflict awareness system can surface warnings directly in VS Code's
Problems panel without requiring a custom extension. This works through
VS Code's "problem matcher" system — we output warnings in a format VS Code
recognizes, and a background task picks them up.

### Setup

Add this to your `.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Canary: Watch",
      "type": "shell",
      "command": "python3",
      "args": [
        "${workspaceFolder}/.claude/skills/canary/scripts/watch_conflicts.py",
        "--repo", "${workspaceFolder}",
        "--vscode"
      ],
      "isBackground": true,
      "problemMatcher": {
        "owner": "canary",
        "fileLocation": ["relative", "${workspaceFolder}"],
        "pattern": {
          "regexp": "^(error|warning|info):\\s+(.+):(\\d+):\\s+(.+)$",
          "severity": 1,
          "file": 2,
          "line": 3,
          "message": 4
        },
        "background": {
          "activeOnStart": true,
          "beginsPattern": "^\\[canary\\] Scanning\\.\\.\\.$",
          "endsPattern": "^\\[canary\\] Done\\.$"
        }
      },
      "presentation": {
        "reveal": "never",
        "panel": "dedicated"
      },
      "runOptions": {
        "runOn": "folderOpen"
      }
    },
    {
      "label": "Canary: Check Now",
      "type": "shell",
      "command": "python3",
      "args": [
        "${workspaceFolder}/.claude/skills/canary/scripts/watch_conflicts.py",
        "--repo", "${workspaceFolder}",
        "--vscode"
      ],
      "problemMatcher": {
        "owner": "canary",
        "fileLocation": ["relative", "${workspaceFolder}"],
        "pattern": {
          "regexp": "^(error|warning|info):\\s+(.+):(\\d+):\\s+(.+)$",
          "severity": 1,
          "file": 2,
          "line": 3,
          "message": 4
        }
      },
      "presentation": {
        "reveal": "silent"
      }
    }
  ]
}
```

### How It Works

When `--vscode` is passed, the watcher outputs conflict warnings in a format
that VS Code's problem matcher understands:

```
[canary] Scanning...
error: src/types/user.ts:1: [CRITICAL 85] Modified on feature-api-v2, feature-frontend. Renamed email→emailAddress (breaks UserProfile.tsx, SignupForm.tsx)
warning: src/api/routes/users.ts:1: [HIGH 62] Modified on feature-api-v2, feature-frontend. Response shape changed.
warning: src/config/endpoints.ts:1: [MEDIUM 40] Modified on feature-api-v2, feature-frontend. Added VERIFY_PHONE endpoint.
[canary] Done.
```

These show up in VS Code's Problems panel:
- 🔴 CRITICAL/HIGH conflicts appear as **errors** (red squiggles)
- 🟡 MEDIUM conflicts appear as **warnings** (yellow squiggles)
- 🔵 LOW/INFO conflicts appear as **info** (blue)

### Auto-Run on File Open

The `"runOn": "folderOpen"` setting makes the watcher start automatically
when you open the project. For continuous watching, use the background task
version.

### Status Bar

For a status bar indicator, add this to `.vscode/settings.json`:

```json
{
  "canary.enabled": true
}
```

And create a simple extension in `.vscode/extensions/conflict-status/`:
(Or just check the Problems panel — it updates automatically.)


## JetBrains (IntelliJ, WebStorm, PyCharm, etc.)

JetBrains IDEs support "File Watchers" that can run external tools on file save.

### Setup

1. Go to **Settings → Tools → File Watchers**
2. Add a new watcher:
   - **Name:** Canary
   - **File type:** Any
   - **Scope:** Project Files
   - **Program:** `python3`
   - **Arguments:** `$ProjectFileDir$/.claude/skills/canary/scripts/watch_conflicts.py --repo $ProjectFileDir$`
   - **Output filters:** `$FILE_PATH$:$LINE$: $MESSAGE$`

### External Tools (Manual Run)

1. Go to **Settings → Tools → External Tools**
2. Add:
   - **Name:** Check Conflicts
   - **Program:** `python3`
   - **Arguments:** `$ProjectFileDir$/.claude/skills/canary/scripts/watch_conflicts.py --repo $ProjectFileDir$`
   - **Working directory:** `$ProjectFileDir$`

Bind to a keyboard shortcut for quick access.


## Vim / Neovim

### ALE (Asynchronous Lint Engine)

Add a custom linter in your `.vimrc` or `init.lua`:

```vim
" .vimrc
let g:ale_linters = {
\   '*': ['canary'],
\}

function! ConflictAwarenessLinter(buffer) abort
    return {
    \   'command': 'python3 %s --repo %s --vscode',
    \   'callback': 'ConflictAwarenessHandler',
    \}
endfunction

call ale#linter#Define('*', {
\   'name': 'canary',
\   'lsp': '',
\   'executable': 'python3',
\   'command': 'python3 .claude/skills/canary/scripts/watch_conflicts.py --repo . --vscode',
\   'callback': 'ale#handlers#gcc#HandleGCCFormat',
\})
```

### Neovim Diagnostics (native)

```lua
-- init.lua
local function check_conflicts()
  local handle = io.popen("python3 .claude/skills/canary/scripts/watch_conflicts.py --repo . --json-diagnostics 2>/dev/null")
  if handle then
    local result = handle:read("*a")
    handle:close()
    -- Parse and set diagnostics
    local ok, data = pcall(vim.json.decode, result)
    if ok and data.diagnostics then
      local ns = vim.api.nvim_create_namespace("canary")
      for _, diag in ipairs(data.diagnostics) do
        vim.diagnostic.set(ns, vim.fn.bufnr(diag.file), {{
          lnum = 0,
          col = 0,
          severity = diag.severity == "error" and vim.diagnostic.severity.ERROR or vim.diagnostic.severity.WARN,
          message = diag.message,
          source = "canary",
        }})
      end
    end
  end
end

-- Run on BufWrite
vim.api.nvim_create_autocmd("BufWritePost", {
  callback = check_conflicts,
})
```


## CI/CD Integrations

### GitHub Actions

```yaml
name: Conflict Check
on: [pull_request]

jobs:
  conflict-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history for merge-base

      - name: Check conflicts
        run: |
          python3 scripts/watch_conflicts.py \
            --ci --repo . \
            --base ${{ github.base_ref }} \
            --target ${{ github.head_ref }}

      - name: Post results
        if: always()
        run: |
          if [ -f .canary-log.md ]; then
            echo "## Canary Report" >> $GITHUB_STEP_SUMMARY
            cat .canary-log.md >> $GITHUB_STEP_SUMMARY
          fi
```

### GitLab CI

```yaml
conflict-check:
  stage: test
  script:
    - python3 scripts/watch_conflicts.py --ci --repo . --base $CI_MERGE_REQUEST_TARGET_BRANCH_NAME --target $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
  artifacts:
    paths:
      - .canary-log.md
    when: always
  rules:
    - if: $CI_MERGE_REQUEST_IID
```

### Pre-push Hook

```bash
#!/bin/sh
# .git/hooks/pre-push
python3 .claude/skills/canary/scripts/watch_conflicts.py --repo . --ci
if [ -f .canary-alert ]; then
    echo "⚠️  High-severity conflicts detected. Check .canary-log.md"
    echo "Push anyway? (y/n)"
    read -r answer
    if [ "$answer" != "y" ]; then
        exit 1
    fi
fi
```
