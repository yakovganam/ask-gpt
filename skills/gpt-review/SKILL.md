---
name: gpt-review
description: Before handing work back to the user, send code changes to an external AI model (GPT via OpenAI) for adversarial review, then fix the real issues it finds. Run as the LAST step before any coding task is reported done. Use whenever finishing a code change and wanting a second opinion from a different AI model. Triggers on: finishing any code edit, "review before done", "check your work", "second opinion", or completing any significant implementation.
---

# gpt-review — adversarial second-opinion from another AI model

A second AI model (GPT via OpenAI) gives an independent opinion. It can catch
failure modes the first model missed — but it is a second opinion, not a proof of
correctness: it can miss bugs and produce false positives. Tests and human review
still matter for high-risk changes.

There are **two modes**, used at opposite ends of a task:

| Mode | When | What it checks |
|---|---|---|
| **Code review** (default) | AFTER work, before handoff | Does the finished diff have bugs / missed requirements? |
| **Interpretation check** (`--interpret`) | BEFORE work starts | Does your *plan* actually match what the user asked for? |

Use code review on every coding task. Use interpretation check when a request is
ambiguous, high-stakes, or could touch surfaces beyond what was asked — catching a
misunderstanding before writing code is far cheaper than after.

Note: this skill runs when you decide it's relevant or when invoked manually.
If the user installed the optional Stop hook (`hooks/`), finishing with unreviewed
changes will be blocked once per diff state — a successful review records the diff
hash so the hook lets you finish.

---

## ⚠️ Privacy — what gets sent to OpenAI

| Sent to OpenAI | Stays local |
|---|---|
| Git diff (changed lines only) | Unchanged files |
| A short summary Claude writes | `.env`, `*.pem`, `*.key`, credential files (auto-excluded) |
| File names from git status | Common secret formats — keys, tokens, passwords (best-effort redaction) |

In **interpretation mode**, what's sent is different: the user's verbatim request,
any project context you include, and your planned approach (no diff). This text is
also redacted best-effort before sending.

Redaction is **best-effort, not a guarantee** — proprietary token formats won't be
caught. **Use `--dry-run`** to preview the exact payload before any API call.

---

## First run — setup

Check if `~/.claude/gpt-review-config.json` exists.

**If the config does NOT exist**, show the user this message and ask their preference:

---
> **gpt-review needs one-time setup.**
>
> This skill sends your git diff to OpenAI for adversarial code review.
>
> **What gets sent:** changed lines in your code (git diff), a short summary of the change, and file names.
> **What is protected (best-effort):** `.env` files, `*.pem`/`*.key` files are excluded; common secret formats are redacted. This is not a guarantee — you can run `--dry-run` to see exactly what would be sent.
> **Data policy:** per OpenAI, API data is not used for training by default, but may be retained per your account settings. Don't use on regulated or NDA-protected code without approved data-processing terms (DPA/BAA/ZDR).
>
> **To use this skill you need an OpenAI API key.** Set it as:
> - Environment variable: `OPENAI_API_KEY=sk-...`
> - Or in a file: `~/.claude/.openai-key.txt` (chmod 600)
>
> **Do you want to enable GPT code review? (yes / no)**
---

- If **yes**: write the config file and continue to the review.
  ```json
  {"enabled": true, "model": "gpt-4o"}
  ```
  Path: `~/.claude/gpt-review-config.json`

- If **no**: skip the review. The user can enable it later by creating the config file.

- If **yes but no API key is found**: remind them to set `OPENAI_API_KEY` or create `~/.claude/.openai-key.txt`, then try again.

---

## Interpretation check (pre-work) — optional

Before starting an ambiguous or high-stakes task, verify your plan matches the ask:

```bash
python <skill-path>/review.py --interpret \
  --request "<the user's verbatim request>" \
  --plan "<what you intend to do, step by step>" \
  --context "<optional: relevant project facts, protected surfaces>"
```

GPT checks whether your plan is a faithful, complete, **minimal** reading of the
request — flagging scope creep, missed requirements, risky assumptions, and
surfaces your plan might touch as side effects. Read its verdict, then:
- If it surfaces a real misunderstanding → adjust your plan, or ask the user
  before coding (especially anything under "CONFIRM BEFORE CODING:")
- If it's a false alarm → proceed

This is most valuable right after the user's request and before any edits. Skip it
for trivial, unambiguous tasks — it adds a round-trip.

---

## Writing the summary (code-review mode)

The reviewer only sees what you send it — a diff without context produces shallow
or wrong critiques. The summary (2–6 lines) must include:

1. **The original requirement** — what the user actually asked for
2. **What changed and why** — files, behavior change
3. **Which tests/commands ran and their results** — or "not run" honestly
4. **Known risks or assumptions**
5. **Untracked new files** — they don't appear in `git diff HEAD`, so name them here

---

## Running the review

Once config exists:

```bash
python <skill-path>/review.py \
  --repo-path "<repo root>" \
  --summary "<requirement + what changed + tests run + risks>"
```

**All options:**

| Flag | Default | Description |
|---|---|---|
| `--summary "..."` | `""` | See "Writing the summary" above |
| `--mode` | `uncommitted` | `uncommitted` / `staged` / `lastcommit` |
| `--files a.py b.py` | all files | Scope diff to specific files |
| `--text "..."` | — | Review text directly (non-git) |
| `--model gpt-4o` | from config | Override the model |
| `--max-chars 20000` | `20000` | Truncate diff at N chars |
| `--dry-run` | — | Preview payload, no API call |
| `--check-setup` | — | Print privacy notice and exit |
| `--interpret` | — | Pre-work mode (see "Interpretation check" above) |
| `--request "..."` | — | [interpret] the user's verbatim request |
| `--plan "..."` | — | [interpret] your planned approach |
| `--context "..."` | — | [interpret] optional project context |

---

## After the review

For each finding:
1. Decide if it is **genuinely correct** — GPT can be wrong, do NOT apply fixes blindly
2. Apply fixes for real issues
3. If you fixed something substantive, run the review once more

**Maximum 2 review rounds per task.** If the second round still finds issues, do not
loop — report the remaining findings to the user with your own judgment on each.
Endless review-fix-review cycles burn time and money without converging.

In your final message: state what was flagged and what you fixed (or that nothing
real was found, or that the review didn't run).

---

## Notes
- Config: `~/.claude/gpt-review-config.json` (`enabled`, `model`)
- API key lookup: `~/.claude/.openai-key.txt` → `OPENAI_API_KEY` env var
- Fails open: API/network errors print a message and exit 0 — never blocks you from finishing. Exception: if the safety filters themselves fail, the send is aborted.
- Python 3.8+ required, no external dependencies
- Optional deterministic enforcement: `hooks/README.md`
