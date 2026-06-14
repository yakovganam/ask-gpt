# gpt-review — adversarial AI code review for Claude Code

A Claude Code skill (with an optional enforcement hook) that reviews your git diff
with a second AI model (GPT via OpenAI) before a coding task is considered complete.

A second model can catch **independent failure modes** — bugs the first model
confidently missed. It is a second-opinion reviewer, **not a proof of correctness**:
it can miss bugs and can produce false positives. Run tests and use human review
for high-risk changes.

Without the hook, the skill is invoked by Claude when relevant or manually —
automatic execution is not guaranteed. For deterministic enforcement, install the
optional Stop hook (see [skills/gpt-review/hooks/](skills/gpt-review/hooks/README.md)).

---

## What it does

Two modes, used at opposite ends of a task:

**Code review (default) — after work, before handoff:**
1. Collects the git diff of the changes
2. Sends it to GPT with a context summary (requirement, tests run, results)
3. Claude judges each finding and fixes the real ones
4. At most one more review round (2 max), then the task is reported done

**Interpretation check (`--interpret`) — before work starts:**
Sends the user's request + Claude's planned approach to GPT and asks whether the
plan actually matches the ask — catching scope creep, missed requirements, and
risky assumptions *before* any code is written. Best for ambiguous or high-stakes
requests; skip it for trivial ones.

---

## Privacy & safety

**This skill sends code to OpenAI's API.** Understand what is and isn't sent:

| Sent to OpenAI | Stays on your machine |
|---|---|
| Changed lines (git diff) | Unchanged files |
| A short summary Claude writes | `.env` / `*.pem` / `*.key` / credential files (auto-excluded) |
| File names from git status | Common secret formats (best-effort redaction) |

In **interpretation mode** (`--interpret`), the diff is replaced by the user's
verbatim request, any project context Claude includes, and the planned approach.
That text is redacted best-effort too, but include context deliberately.

**Secret redaction is best-effort and cannot guarantee removal of all sensitive
data.** It catches common formats (OpenAI/GitHub/Slack/AWS/Google keys, JWTs,
Bearer tokens, quoted password assignments, PEM blocks) but will miss proprietary
token formats, database DSNs, signed URLs, internal hostnames, and customer
identifiers.

**Always run `--dry-run` before first use in a repository** to see the exact payload.

**Data policy:** per OpenAI's published policies, API inputs/outputs are not used
for model training by default (sharing is opt-in), but requests may be retained
and processed depending on endpoint and account settings; Zero Data Retention is
available for eligible organizations. Verify your own account's settings.

**Do not use on regulated (GDPR/HIPAA), confidential, or NDA-protected code unless
your organization has approved the relevant OpenAI data-processing arrangements
(DPA / BAA / ZDR).**

---

## Requirements

- Python 3.8+
- An OpenAI API key
- git

No external Python packages — uses only the standard library.

---

## Installation

### 1. Install the skill

**Option A — as a Claude Code plugin (recommended, gets updates):**

```
/plugin marketplace add YOUR_GITHUB_USERNAME/gpt-review
/plugin install gpt-review
```

**Option B — manual copy:**

```bash
# Mac / Linux
git clone https://github.com/YOUR_GITHUB_USERNAME/gpt-review
cp -r gpt-review/skills/gpt-review ~/.claude/skills/gpt-review

# Windows (PowerShell)
git clone https://github.com/YOUR_GITHUB_USERNAME/gpt-review
Copy-Item -Recurse gpt-review\skills\gpt-review "$env:USERPROFILE\.claude\skills\gpt-review"
```

### 2. Set your OpenAI API key

Pick one method. **Never put the key in the config file, never commit it to git,
and don't sync it to dotfiles or cloud backups.**

```bash
# Option A — environment variable
export OPENAI_API_KEY=sk-...        # add to ~/.bashrc / ~/.zshrc

# Option B — key file with restricted permissions
echo "sk-..." > ~/.claude/.openai-key.txt
chmod 600 ~/.claude/.openai-key.txt
```

### 3. First run — Claude asks for your consent

The first time the skill runs, Claude shows a privacy notice (what gets sent,
what's protected, data-policy caveats) and asks whether to enable GPT review.
If you confirm, it creates `~/.claude/gpt-review-config.json`.

You can also create it manually:

```json
{"enabled": true, "model": "gpt-4o"}
```

### 4. Optional — install the enforcement hook

The skill alone depends on Claude choosing to invoke it. For a deterministic
gate that blocks finishing while unreviewed changes exist, install the Stop
hook — it's loop-safe (blocks at most once per unique diff state).
See [skills/gpt-review/hooks/README.md](skills/gpt-review/hooks/README.md).

---

## Usage

Claude invokes the skill when finishing coding tasks (or always, with the hook
installed). You can also invoke it manually:

```
/gpt-review
```

Or run the script directly:

```bash
python ~/.claude/skills/gpt-review/review.py \
  --repo-path "/path/to/repo" \
  --summary "Requirement: fix auth null-crash. Changed middleware/auth.js. Tests: npm test passed (42/42)."
```

### All flags

| Flag | Default | Description |
|---|---|---|
| `--summary "..."` | `""` | Requirement + what changed + tests run + risks |
| `--mode` | `uncommitted` | `uncommitted` / `staged` / `lastcommit` |
| `--files a.py b.py` | all | Scope review to specific files |
| `--text "..."` | — | Review a description/diff directly |
| `--model gpt-4o` | from config | Override the model |
| `--max-chars 20000` | 20000 | Truncate diff at N chars |
| `--dry-run` | — | See what would be sent — no API call |
| `--check-setup` | — | Print privacy notice and exit |
| `--interpret` | — | Pre-work mode: check the plan against the request |
| `--request "..."` | — | [interpret] the user's verbatim request |
| `--plan "..."` | — | [interpret] Claude's planned approach |
| `--context "..."` | — | [interpret] optional project context |

### Interpretation check example

```bash
python ~/.claude/skills/gpt-review/review.py --interpret \
  --request "make the login button blue" \
  --plan "restyle the button AND refactor the whole auth module" \
  --dry-run
```

GPT would flag the auth refactor as scope creep — the user only asked about a
button color. Catching that before coding saves an entire wrong implementation.

---

## The review loop (bounded)

1. Claude reviews → GPT responds with findings
2. Claude judges each finding — real issues get fixed, false positives get ignored
3. If something substantive changed: **one** more review round
4. **Hard cap: 2 rounds.** Remaining findings are reported to you instead of looping

---

## Example output

```
[gpt-review] Excluded sensitive files: config/secrets.env
[gpt-review] Redacted from diff (best-effort): 1x credential-assignment
===== GPT review (gpt-4o-2024-11-20) =====
Solid change overall. One real issue found.

[critical] The new retry loop has no backoff — under network failure it will
           hammer the endpoint at maximum rate. Fix: add exponential backoff
           with jitter (start at 100ms, cap at 30s).

[low] The error message on line 47 leaks the internal service URL.
      Fix: replace with a generic "service unavailable" message.

[tokens: prompt=1842 completion=198]
```

---

## Configuration

`~/.claude/gpt-review-config.json`:

```json
{
  "enabled": true,
  "model": "gpt-4o"
}
```

Fields:
- `enabled` — set to `false` to disable without deleting the config
- `model` — any OpenAI chat model (`gpt-4o`, `gpt-4-turbo`, `gpt-4o-mini`, etc.)

The API key is deliberately **not** a config field — use the env var or the
key file (see Installation step 2).

---

## How it protects your code (best-effort)

### Sensitive file exclusion
These file patterns are never included in the diff sent to OpenAI:
- `.env`, `.env.*`
- `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.crt`, `*.cer`, `*.jks`
- Files matching: `credential`, `secret`, `password`
- `.ssh/` directory contents, `.netrc`
- `id_rsa`, `id_ecdsa`, `id_ed25519`

### Secret redaction
These patterns are replaced with `[REDACTED]` before sending:
- OpenAI API keys (`sk-...`)
- GitHub tokens (`ghp_...`, `gho_...`, `gps_...`)
- Slack tokens (`xoxb-...`, `xoxp-...`)
- AWS access keys (`AKIA...`, `ASIA...`)
- Google API keys (`AIza...`)
- JWTs (`eyJ...eyJ...`)
- Bearer tokens in headers
- Quoted password/secret/api_key assignment values
- PEM private key blocks

Anything not in this list — internal token formats, DSNs, signed URLs — **goes
through unredacted**. When in doubt, `--dry-run` first.

### Fail-closed filters
If the filtering or redaction code itself errors, the send is **aborted** —
better no review than an unfiltered payload. (API/network errors still fail
open: they print a message and never block Claude from finishing.)

---

## Files

```
gpt-review/
├── .claude-plugin/
│   ├── marketplace.json    plugin marketplace manifest
│   └── plugin.json         plugin manifest
├── skills/gpt-review/
│   ├── SKILL.md            instructions Claude follows
│   ├── review.py           the review script (stdlib only)
│   ├── hooks/
│   │   ├── stop_hook.py    optional deterministic enforcement
│   │   └── README.md       hook install + loop-safety details
│   └── tests/
│       └── smoke_test.py   redaction + file-filter sanity checks
├── README.md               this file
└── LICENSE                 MIT
```

The Stop hook is deliberately **not** auto-registered by the plugin — enforcement
changes how your sessions behave, so it stays an explicit opt-in
(see [skills/gpt-review/hooks/README.md](skills/gpt-review/hooks/README.md)).

---

## License

MIT
