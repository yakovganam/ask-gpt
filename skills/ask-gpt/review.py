#!/usr/bin/env python3
"""
ask-gpt/review.py
Sends git diff + summary to an external AI model (OpenAI) for adversarial code review.
Fails open: any API/network error prints a message and exits 0 — never blocks the agent.
Exception: if the safety filters themselves fail, the send is ABORTED (fail closed).

Safety layers:
- sensitive files excluded from the diff (.env, *.pem, *.key, credentials, ...)
- best-effort secret redaction (common key/token formats; NOT a guarantee)
- --dry-run prints the exact payload and makes no API call

Config: ~/.claude/ask-gpt-config.json   (created on first-run consent)
State:  ~/.claude/ask-gpt-state/        (diff hashes for the optional Stop hook)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONFIG_PATH = Path.home() / ".claude" / "ask-gpt-config.json"
STATE_DIR = Path.home() / ".claude" / "ask-gpt-state"

PRIVACY_NOTICE = """\
================================================================
              Ask GPT -- FIRST-RUN PRIVACY NOTICE
================================================================

This skill sends your git diff to an external AI provider (OpenAI).

WHAT GETS SENT TO THE PROVIDER:
  - Code-review mode: your git diff (added/removed lines), a short
    summary written by Claude, and file names from git status.
  - Interpretation mode (--interpret, pre-work): your verbatim request,
    any project context Claude includes, and Claude's planned approach.

AUTOMATIC PROTECTIONS (BEST-EFFORT, NOT A GUARANTEE):
  - .env / *.pem / *.key / credential files -> excluded from the diff
  - Common secret formats (OpenAI/GitHub/Slack/AWS/Google keys,
    JWTs, Bearer tokens, quoted password assignments, PEM blocks)
    -> redacted to [REDACTED]
  - Proprietary or unusual token formats WILL NOT be caught.

Use --dry-run at any time to preview exactly what would be sent.

DATA POLICY: per OpenAI's policies, API inputs/outputs are not used
for model training by default, but requests may be retained and
processed depending on your account settings. Do not use on
regulated (GDPR/HIPAA), confidential, or NDA-protected code unless
your organization has approved the relevant data-processing terms
(DPA / BAA / Zero Data Retention).

FIRST_RUN_SETUP_NEEDED
"""

# ---------------------------------------------------------------------------
# Secret redaction (best-effort -- catches common formats, not everything)
# ---------------------------------------------------------------------------
# (pattern, replacement, label) -- replacement may use backreferences
REDACT_RULES: List[Tuple[str, str, str]] = [
    (r"sk-[A-Za-z0-9_-]{20,}",
     "[REDACTED:openai-key]", "openai-key"),
    (r"gh[pso]_[A-Za-z0-9]{36,}",
     "[REDACTED:github-token]", "github-token"),
    (r"xox[baprs]-[A-Za-z0-9\-]+",
     "[REDACTED:slack-token]", "slack-token"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
     "[REDACTED:jwt]", "jwt"),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
     "[REDACTED:aws-key]", "aws-key"),
    (r"\bAIza[0-9A-Za-z_-]{35}",
     "[REDACTED:google-api-key]", "google-api-key"),
    (r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/=]{20,}",
     "Bearer [REDACTED:bearer]", "bearer-token"),
    # quoted credential assignments:  password = "x" / "api_key": "x" / secret: 'x'
    (r"""(?i)\b((?:password|passwd|secret|api_?key|access_?key|auth_?token)s?["']?\s*[=:]\s*["'])([^"']{4,})(["'])""",
     r"\1[REDACTED]\3", "credential-assignment"),
    (r"-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----",
     "[REDACTED:private-key]", "private-key"),
]

# Sensitive file patterns -- matched against paths in the diff headers
SENSITIVE_FILE_PATTERNS = [
    r"(^|/)\.env($|\.)",
    r"\.(pem|key|p12|pfx|jks|crt|cer)$",
    r"(credential|secret|password)s?[._]",
    r"(^|/)\.ssh/",
    r"(^|/)\.netrc$",
    r"id_(rsa|ecdsa|ed25519)",
]


def redact(text: str) -> Tuple[str, List[str]]:
    """Apply all redaction rules. Returns (redacted_text, list of 'Nx label')."""
    found: List[str] = []
    for pattern, replacement, label in REDACT_RULES:
        text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        if count:
            found.append(f"{count}x {label}")
    return text, found


def filter_sensitive_files(diff: str) -> Tuple[str, List[str]]:
    """Remove diff sections for sensitive files. Returns (filtered_diff, removed_paths)."""
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    kept: List[str] = []
    removed: List[str] = []
    for section in sections:
        if not section.strip():
            continue
        m = re.match(r"diff --git a/(.+?) b/", section)
        if m:
            path = m.group(1)
            if any(re.search(p, path, re.IGNORECASE) for p in SENSITIVE_FILE_PATTERNS):
                removed.append(path)
                continue
        kept.append(section)
    return "".join(kept), removed


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------
def get_git_diff(mode: str, repo_path: Optional[str], files: List[str]) -> Tuple[str, str]:
    cwd = repo_path or None
    file_args = ["--"] + files if files else []
    cmds: Dict[str, List[str]] = {
        "staged":      ["git", "--no-pager", "diff", "--cached"] + file_args,
        "lastcommit":  ["git", "--no-pager", "show", "HEAD"]     + file_args,
        "uncommitted": ["git", "--no-pager", "diff", "HEAD"]     + file_args,
    }
    diff = subprocess.check_output(
        cmds[mode], cwd=cwd, text=True, stderr=subprocess.PIPE
    )
    status = subprocess.check_output(
        ["git", "--no-pager", "status", "--short"],
        cwd=cwd, text=True, stderr=subprocess.PIPE,
    )
    return diff, status


def record_review_state(repo_path: Optional[str]) -> None:
    """Record the hash of the current full diff after a successful review, so the
    optional Stop hook (hooks/stop_hook.py) knows this state was reviewed.
    Hashes the FULL unscoped `git diff HEAD` -- must match the hook's computation,
    regardless of which --mode/--files the review itself used. Best-effort."""
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_path or None, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        full_diff = subprocess.check_output(
            ["git", "--no-pager", "diff", "HEAD"],
            cwd=root, text=True, stderr=subprocess.DEVNULL,
        )
        diff_hash = hashlib.sha256(full_diff.encode("utf-8", "replace")).hexdigest()
        repo_id = hashlib.sha256(
            str(Path(root).resolve()).lower().encode("utf-8")
        ).hexdigest()[:16]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{repo_id}.json").write_text(
            json.dumps({"reviewed_hash": diff_hash}), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def get_api_key() -> Optional[str]:
    key_file = Path.home() / ".claude" / ".openai-key.txt"
    if key_file.exists():
        try:
            key = key_file.read_text(encoding="utf-8").strip()
            if key:
                return key
        except OSError:
            pass
    return os.environ.get("OPENAI_API_KEY")


def call_openai(
    api_key: str, model: str, system: str, user_msg: str
) -> Tuple[str, str, dict]:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return content, data["model"], data["usage"]


# ---------------------------------------------------------------------------
# Review prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a rigorous senior code reviewer. An AI coding agent gives you:
(1) a short description of what it changed and why
(2) a git diff or change description

Find CONCRETE problems BEFORE this reaches the human developer:
real bugs, logic errors, off-by-one errors, null/edge cases, broken requirements,
security issues, destructive operations, and anything the description CLAIMS
but the diff does not actually do. If the summary lists requirements or test
results, verify the diff is actually consistent with them.
Do NOT nitpick style or formatting.
If the change is genuinely solid, say so plainly -- do not invent problems.

Format:
- One-line verdict first
- Prioritized list: prefix each item with [critical] / [medium] / [low]
- Problem description + concrete fix
- End with "MANUAL CHECK: ..." only if something genuinely needs human verification
"""

INTERPRET_SYSTEM_PROMPT = """\
You are a rigorous senior engineer acting as an INTERPRETATION checker. A coding
agent is about to start work -- no code exists yet. You are given:
(1) the user's verbatim request
(2) optional project context
(3) the agent's planned interpretation / approach

Your job is to catch MISUNDERSTANDINGS before any code is written. Check:
- Does the plan actually address what the user literally asked for?
- What does the plan include that the user did NOT ask for? (scope creep)
- What requirement in the request does the plan miss or gloss over?
- What assumptions does the plan make that should be confirmed first?
- Which existing files/surfaces could the plan touch as side effects?
- Ambiguities in the request that would change the correct approach.

Do NOT review code quality -- there is no code yet. Focus only on whether the
plan is a faithful, complete, and MINIMAL interpretation of the request.
If the plan is solid, say so plainly -- do not invent problems.

Format:
- One-line verdict first
- Prioritized list: prefix each item with [critical] / [medium] / [low]
- The gap + a concrete clarification or adjustment
- End with "CONFIRM BEFORE CODING: ..." listing anything that needs the user's
  confirmation before work starts (omit the line if nothing does)
"""


def send_review(
    model: str, system_prompt: str, user_msg: str, dry_run: bool, label: str
) -> Optional[bool]:
    """Shared send path for both review types. Returns True on a successful API
    call, None on dry-run / missing key / any error (all fail open)."""
    if dry_run:
        sep = "=" * 64
        print(sep)
        print(f"DRY RUN ({label}) -- this is exactly what would be sent to the API:")
        print(sep)
        print("Provider : OpenAI")
        print(f"Model    : {model}")
        print(f"System   : {len(system_prompt)} chars")
        print(f"User msg : {len(user_msg)} chars")
        print()
        print(user_msg)
        print(sep)
        print("No API call was made. Redaction is best-effort -- review it yourself.")
        return None

    api_key = get_api_key()
    if not api_key:
        print(
            "No API key found.\n"
            "Options:\n"
            "  1. Env var:   OPENAI_API_KEY=sk-...\n"
            "  2. Key file:  ~/.claude/.openai-key.txt  (chmod 600, never commit or sync it)"
        )
        return None

    try:
        review, used_model, usage = call_openai(api_key, model, system_prompt, user_msg)
        print(f"===== Ask GPT: {label} ({used_model}) =====")
        print(review)
        print()
        print(f"[tokens: prompt={usage['prompt_tokens']} completion={usage['completion_tokens']}]")
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"OpenAI API error {exc.code} (not blocking): {body[:400]}")
        return None
    except Exception as exc:
        print(f"Ask GPT request failed (not blocking): {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adversarial code review via OpenAI -- sends your diff to GPT"
    )
    parser.add_argument("--summary",     default="",            help="What changed and why")
    parser.add_argument("--mode",        default="uncommitted",
                        choices=["uncommitted", "staged", "lastcommit"])
    parser.add_argument("--files",       nargs="*", default=[],  help="Scope diff to these files")
    parser.add_argument("--text",        default="",             help="Review this text instead of git diff")
    parser.add_argument("--model",       default="",             help="Model override (default: from config or gpt-5.4)")
    parser.add_argument("--max-chars",   type=int, default=20000)
    parser.add_argument("--repo-path",   default="",             help="Git repo root")
    parser.add_argument("--dry-run",     action="store_true",    help="Preview payload -- no API call")
    parser.add_argument("--check-setup", action="store_true",    help="Print privacy notice and exit")
    # Interpretation-check mode (pre-work): verify the plan matches the request
    parser.add_argument("--interpret",   action="store_true",
                        help="Pre-work mode: check your plan against the user's request (no git/diff)")
    parser.add_argument("--request",     default="",             help="[interpret] the user's verbatim request")
    parser.add_argument("--plan",        default="",             help="[interpret] your planned interpretation/approach")
    parser.add_argument("--context",     default="",             help="[interpret] optional project context")
    args = parser.parse_args()

    # Load config
    config: dict = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception:
            pass

    # First run or explicit check
    if args.check_setup or not config:
        print(PRIVACY_NOTICE)
        sys.exit(0)

    if config.get("enabled") is False:
        print("ask-gpt is disabled (enabled=false in ~/.claude/ask-gpt-config.json).")
        sys.exit(0)

    model = args.model or config.get("model", "gpt-5.4")

    # ── Interpretation-check mode (pre-work) ──────────────────────────────
    # Different job from code review: there is no code yet. We send the user's
    # request + plan and ask GPT whether the plan matches the ask, BEFORE work
    # starts. No git, no diff. The request/context/plan can contain pasted
    # secrets, so we still redact (best-effort) before sending.
    if args.interpret:
        if not args.request.strip() or not args.plan.strip():
            print("Interpret mode needs --request (the user's verbatim ask) and "
                  "--plan (your planned approach).")
            sys.exit(0)
        parts = [f"--- user's verbatim request ---\n{args.request}"]
        if args.context.strip():
            parts.append(f"--- project context ---\n{args.context}")
        parts.append(f"--- the agent's planned interpretation / approach ---\n{args.plan}")
        user_msg = "\n\n".join(parts)
        try:
            user_msg, redacted = redact(user_msg)
        except Exception as exc:
            print(f"Redaction failed ({exc}) -- aborting send to be safe.")
            sys.exit(0)
        if redacted:
            print(f"[ask-gpt] Redacted (best-effort): {', '.join(redacted)}")
        if len(user_msg) > args.max_chars:
            user_msg = user_msg[: args.max_chars] + f"\n[truncated to {args.max_chars} chars]"
        send_review(model, INTERPRET_SYSTEM_PROMPT, user_msg, args.dry_run,
                    "interpretation check")
        sys.exit(0)

    # Gather changes
    if args.text:
        diff, status = args.text, ""
    else:
        try:
            diff, status = get_git_diff(
                args.mode, args.repo_path or None, args.files or []
            )
        except subprocess.CalledProcessError as exc:
            print(f"git command failed (not blocking): {exc}")
            sys.exit(0)
        except FileNotFoundError:
            print("git not found on PATH (not blocking).")
            sys.exit(0)

    if not diff.strip():
        print("No changes found to review. For non-git work, pass --text.")
        sys.exit(0)

    # Safety: filter sensitive files. If the filter itself breaks, abort the
    # send (fail closed) -- better no review than an unfiltered payload.
    try:
        diff, removed = filter_sensitive_files(diff)
    except Exception as exc:
        print(f"Sensitive-file filtering failed ({exc}) -- aborting send to be safe.")
        sys.exit(0)
    if removed:
        print(f"[ask-gpt] Excluded sensitive files: {', '.join(removed)}")

    if not diff.strip():
        print("After excluding sensitive files, no reviewable diff remains.")
        sys.exit(0)

    # Safety: redact secrets (best-effort). Same fail-closed rule.
    try:
        diff, redacted = redact(diff)
    except Exception as exc:
        print(f"Secret redaction failed ({exc}) -- aborting send to be safe.")
        sys.exit(0)
    if redacted:
        print(f"[ask-gpt] Redacted from diff (best-effort): {', '.join(redacted)}")

    # Truncate
    trunc_note = ""
    if len(diff) > args.max_chars:
        diff = diff[: args.max_chars]
        trunc_note = f"\n[diff truncated to {args.max_chars} chars]"

    # Build message
    user_msg = f"What the developer (Claude) claims they did:\n{args.summary}\n"
    if status.strip():
        user_msg += f"\n--- git status ---\n{status}"
    user_msg += f"\n--- changes (diff) ---\n{diff}{trunc_note}"

    # Send (or preview). Record state for the optional Stop hook only on a
    # successful real review of git changes.
    result = send_review(model, SYSTEM_PROMPT, user_msg, args.dry_run, "review")
    if result and not args.text:
        record_review_state(args.repo_path or None)


if __name__ == "__main__":
    main()
