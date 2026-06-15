# Optional hooks — deterministic enforcement

The skill alone is invoked at Claude's discretion — automatic execution is **not
guaranteed**. These two hooks close that gap at opposite ends of a turn:

| Hook | Event | When it fires | What it does |
|---|---|---|---|
| `prompt_hook.py` | `UserPromptSubmit` | the moment **you send a message** | sends your request to GPT and injects GPT's interpretation as context **before** the agent acts ("every request goes to GPT") |
| `stop_hook.py` | `Stop` | when the agent **tries to finish** | blocks finishing while there are unreviewed code changes, until a review is run |

Install one or both. They're independent.

---

## prompt_hook.py — the entry gate ("every request to GPT")

On every prompt, it shells to `review.py --interpret` (reusing that logic, no
separate OpenAI code) and injects GPT's read of the request. It's **quiet and
fail-open**: if there's no API key, the skill is disabled, or the API errors, it
emits nothing and never blocks you.

- Disable just this hook (keep the skill): set `{"prompt_check": false}` in
  `~/.claude/ask-gpt-config.json`.
- **Cost/latency note:** this calls GPT on *every* message, adding a few seconds
  and tokens per turn. That's the intended trade for "always sent to GPT." Turn
  it off with the flag above if it gets in the way.

## stop_hook.py — the exit gate (code review)

Blocks the stop once when uncommitted changes haven't been reviewed. Loop-safe:

| Guard | Effect |
|---|---|
| `stop_hook_active` | Never blocks twice in the same stop cycle |
| `blocked_hash` state | Blocks at most **once per unique diff state** — if Claude finishes anyway, the same diff never triggers another nag |
| `reviewed_hash` state | `review.py` records the diff hash after a successful review, so a reviewed state passes silently |
| Fail open | Any error (no git, bad input, unwritable state) → the stop is allowed |

State lives in `~/.claude/ask-gpt-state/<repo-id>.json`. Safe to delete any time.

---

## Install

Add to `~/.claude/settings.json` (all projects) or `<project>/.claude/settings.json`
(one project). Use the **absolute path** to wherever you installed the skill:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /home/you/.claude/skills/ask-gpt/hooks/prompt_hook.py"
          }
        ]
      }
    ],
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

On Windows use the Python path + forward slashes (no escaping needed if the paths
have no spaces):

```json
"command": "C:/Users/you/AppData/Local/Programs/Python/Python312/python.exe C:/Users/you/.claude/skills/ask-gpt/hooks/prompt_hook.py"
```

If you installed via `/plugin install`, the skill lives in the plugin cache —
find the hooks with:

```bash
# Mac / Linux
find ~/.claude/plugins -name "*_hook.py"

# Windows (PowerShell)
Get-ChildItem "$env:USERPROFILE\.claude\plugins" -Recurse -Filter *_hook.py
```

## Behavior you should expect

- **prompt_hook**: a short GPT interpretation appears as context on each message;
  on trivial messages GPT will just say it's clear. Skipped silently if GPT is
  slow (>60s) or unavailable.
- **stop_hook**: the block fires **at most once per diff state per repo** — not on
  every message. After a review + fixes, the diff changes → one more nag → second
  review round, matching the skill's "maximum 2 rounds" rule.

## Uninstall

Remove the hook entries from settings.json. Optionally delete
`~/.claude/ask-gpt-state/`.
