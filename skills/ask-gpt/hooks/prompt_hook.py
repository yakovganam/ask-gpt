#!/usr/bin/env python3
"""
ask-gpt / prompt_hook.py  (OPTIONAL)
Claude Code UserPromptSubmit hook: on EVERY user message, send the request to
GPT for a quick interpretation / sanity check and inject GPT's take as extra
context BEFORE the agent acts. This is the "every request goes to GPT" gate --
the entry counterpart to stop_hook.py's exit (code-review) gate.

It REUSES review.py's --interpret logic (no separate OpenAI code lives here).

Fails open & QUIET: any problem -> emit nothing and exit 0. It never blocks you,
and it never pollutes the context with error/setup text -- it only speaks when
GPT actually returned an analysis.

Disable without uninstalling: set {"prompt_check": false} in
~/.claude/ask-gpt-config.json  (or {"enabled": false} to disable the whole skill)

Install: see hooks/README.md
"""
import json
import subprocess
import sys
from pathlib import Path


def nothing() -> None:
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        nothing()

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        nothing()

    # Honor on/off switches in the shared config.
    cfg = {}
    cfg_path = Path.home() / ".claude" / "ask-gpt-config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if cfg.get("enabled") is False or cfg.get("prompt_check") is False:
        nothing()

    review_py = Path(__file__).resolve().parent.parent / "review.py"
    if not review_py.exists():
        nothing()

    cwd = payload.get("cwd", "")
    try:
        proc = subprocess.run(
            [sys.executable, str(review_py), "--interpret",
             "--request", prompt, "--context", f"working directory: {cwd}"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        nothing()

    out = (proc.stdout or "").strip()
    # Only inject when review.py actually produced a GPT analysis. Anything else
    # (no key, disabled, API error, empty diff message) is swallowed silently.
    if proc.returncode != 0 or "===== Ask GPT" not in out:
        nothing()

    print(
        "A pre-work interpretation check ran via Ask GPT (the user's standing "
        "setup: every request is sent to GPT before you act). GPT's read of "
        "this request is below -- weigh it before acting; it can be wrong, so "
        "use judgment rather than following it blindly:\n\n" + out
    )
    sys.exit(0)


main()
