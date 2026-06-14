# Optional Stop hook — deterministic enforcement

The skill alone is invoked at Claude's discretion — automatic execution is **not
guaranteed**. This hook closes that gap: when Claude tries to finish a response
while uncommitted changes exist that ask-gpt hasn't reviewed, the hook blocks
the stop once and tells Claude to run the review.

## How it stays loop-safe

| Guard | Effect |
|---|---|
| `stop_hook_active` | Never blocks twice in the same stop cycle |
| `blocked_hash` state | Blocks at most **once per unique diff state** — if Claude finishes anyway, the same diff never triggers another nag |
| `reviewed_hash` state | `review.py` records the diff hash after a successful review, so a reviewed state passes silently |
| Fail open | Any error (no git, bad input, unwritable state) → the stop is allowed |

State lives in `~/.claude/ask-gpt-state/<repo-id>.json`. Safe to delete at any time.

## Install

Add to `~/.claude/settings.json` (all projects) or `<project>/.claude/settings.json`
(one project). Use the **absolute path** to wherever you installed the skill:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /home/you/.claude/skills/ask-gpt/hooks/stop_hook.py"
          }
        ]
      }
    ]
  }
}
```

On Windows use `python` and escape backslashes, or use forward slashes:

```json
"command": "python C:/Users/you/.claude/skills/ask-gpt/hooks/stop_hook.py"
```

If you installed via `/plugin install`, the skill lives in the plugin cache
instead — find the hook's absolute path with:

```bash
# Mac / Linux
find ~/.claude/plugins -name stop_hook.py

# Windows (PowerShell)
Get-ChildItem "$env:USERPROFILE\.claude\plugins" -Recurse -Filter stop_hook.py
```

## Behavior you should expect

- The block fires **at most once per diff state per repo** — not on every message.
- If you keep uncommitted changes around while just chatting, you'll get one nag,
  then silence until the diff actually changes.
- After Claude runs a review and then fixes issues, the diff changes → one more
  nag on the next stop → second review round. This matches the skill's
  "maximum 2 rounds" rule.

## Uninstall

Remove the hook entry from settings.json. Optionally delete
`~/.claude/ask-gpt-state/`.
