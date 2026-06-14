#!/usr/bin/env python3
"""
ask-gpt / stop_hook.py  (OPTIONAL)
Claude Code Stop hook: blocks the session from finishing while uncommitted
changes exist that ask-gpt has not reviewed yet.

Loop safety -- this hook can never nag forever:
- honors stop_hook_active (never blocks twice in the same stop cycle)
- blocks at most ONCE per unique diff state (records the hash it nagged about;
  if Claude finishes anyway, the same diff will not trigger another block)
- any unexpected error -> allows the stop (fails open)

State files: ~/.claude/ask-gpt-state/<repo-id>.json
  reviewed_hash -- written by review.py after a successful review
  blocked_hash  -- written here when we nag about an unreviewed diff

Install: see hooks/README.md
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "ask-gpt-state"


def allow() -> None:
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        allow()

    # Already continuing because of a stop hook -- never block twice per cycle
    if payload.get("stop_hook_active"):
        allow()

    cwd = payload.get("cwd") or "."
    try:
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        diff = subprocess.check_output(
            ["git", "--no-pager", "diff", "HEAD"],
            cwd=repo_root, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        allow()  # not a git repo / git missing -> nothing to enforce

    if not diff.strip():
        allow()

    diff_hash = hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()
    repo_id = hashlib.sha256(
        str(Path(repo_root).resolve()).lower().encode("utf-8")
    ).hexdigest()[:16]
    state_file = STATE_DIR / f"{repo_id}.json"

    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    # Reviewed already, or we already nagged once for this exact diff state
    if diff_hash in (state.get("reviewed_hash"), state.get("blocked_hash")):
        allow()

    # Record the nag BEFORE blocking, so this diff state blocks only once
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state["blocked_hash"] = diff_hash
        state_file.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass

    print(json.dumps({
        "decision": "block",
        "reason": (
            "ask-gpt gate: there are uncommitted changes that have not been "
            "reviewed. If you changed code this session, run the ask-gpt "
            "skill now (review.py with a summary covering: the original "
            "requirement, what changed, which tests/commands ran and their "
            "results). If you made no code changes this session, briefly tell "
            "the user that unreviewed changes exist and finish."
        ),
    }))
    sys.exit(0)


main()
