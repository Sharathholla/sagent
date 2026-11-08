# sagent

A cross-platform (macOS, Linux, Windows) CLI agent. It drives an AI CLI you are already signed in to, either **Claude Code** (Claude subscription, `claude -p`) or **Antigravity** (`agy`, Google account), with no pay-per-token API key, and gives it a set of safe, scoped tools for Bitbucket, Jira, Confluence, Slack, AWS and Azure.

```
sagent <action> [args]
```

## How it works

```
sagent run/chat ──► claude -p  or  agy -p   (your login; their own shell/file/web tools are blocked)
                           │  MCP (stdio)
                           ▼
                    sagent mcp-serve ──► integrations (REST / aws / az)
                       │  confirmation, --dry-run, scope checks, audit log, secret masking
```

* The AI can only call the tools sagent exposes; its own shell/file/web tools are disabled (Claude) or denied (agy, which also runs in an empty private workspace), so it cannot read `~/.sagent/config`. Credentials are never sent to it.
* `ANTHROPIC_API_KEY` is removed from Claude's environment so your subscription login is always used.
* Every tool call passes through one safety layer: write detection, `allow_writes`, confirmation, dry-run, audit, masking.

## Requirements

* Python 3.9+
* [Claude Code](https://claude.com/claude-code) logged in with your Claude subscription, **or** the Antigravity CLI (`agy`) signed in with a Google account. The installer asks which one to use and can install it for you.
* Optional: AWS CLI v2 (for AWS), Azure CLI (for Azure)

## Install

Clone the repo, then run one command. It checks Python, creates a private virtual environment in `~/.sagent/venv`, installs dependencies, and puts a `sagent` command on your PATH.

### macOS
```bash
git clone <repo-url> sagent && cd sagent
python3 install.py
```
### Linux
```bash
git clone <repo-url> sagent && cd sagent
python3 install.py        # Debian/Ubuntu may need: sudo apt install python3-venv
```
### Windows (PowerShell or cmd)
```powershell
git clone <repo-url> sagent; cd sagent
py install.py             # or: python install.py
```
Open a **new terminal** afterwards (Windows updates your user PATH; no admin needed).
On macOS/Linux the command is linked at `~/.local/bin/sagent`. If that directory is not on your PATH, the installer adds a marked block (`# >>> sagent >>>` ... `# <<< sagent <<<`) to your shell profile (`~/.zshrc`, `~/.bash_profile`/`~/.bashrc`, `~/.profile` or fish's `config.fish`, chosen from `$SHELL`); open a new terminal or `source` that file. `--uninstall` removes the block again.

Keep the cloned folder: sagent is installed in editable mode and loads `skills/` and `config.example.toml` from it. `python install.py --reinstall` rebuilds the environment; re-running `python install.py` after `git pull` updates it.

### Choose and sign in to your AI (Claude Code or Antigravity)
The installer asks which AI sagent should use and, if its CLI is missing, offers to install it (you confirm first). Switch at any time; the choice is stored in `~/.sagent/provider`:
```bash
sagent use            # pick from a menu
sagent use claude     # Claude Code (Claude subscription)
sagent use agy        # Antigravity CLI (Google account)
```
`sagent --help` and `sagent doctor` show the active backend and these switch commands; `sagent doctor` also reports whether `claude` and `agy` are installed. `SAGENT_AI_PROVIDER=claude|agy` overrides the choice for one shell.

```bash
# Claude Code
# macOS / Linux
curl -fsSL https://claude.ai/install.sh | bash
# Windows PowerShell
irm https://claude.ai/install.ps1 | iex
# any OS with Node 18+
npm install -g @anthropic-ai/claude-code
claude        # then type /login and sign in with your Claude subscription

# Antigravity CLI (macOS / Linux; on Windows download from https://antigravity.google/download#antigravity-cli)
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy           # sign in with your Google account
```

With `agy`, sagent registers its tools once in agy's MCP config (`agy mcp list` shows `sagent`; remove with `agy mcp remove sagent`) and runs agy in the empty folder `~/.sagent/agy-workspace`.

### Uninstall
```bash
python3 install.py --uninstall            # removes the venv, the command and the PATH entry; keeps ~/.sagent
python3 install.py --uninstall --purge    # also deletes ~/.sagent (config, logs, skills)
```

## First run

```bash
sagent init       # creates ~/.sagent, logs/, skills/, and a config template (0600)
# edit ~/.sagent/config (Windows: %USERPROFILE%\.sagent\config)
sagent doctor     # checks Python, which AI CLIs (claude, agy) are installed and signed in, config permissions, and each integration
```

## Usage

```bash
sagent run "Summarise open pull requests in the platform repo"   # skills auto-matched from the text
sagent run "Which Jira tickets are assigned to me?" --skill jira
sagent run bitbucket "Review PR 42 in repo payments-api"          # skill named explicitly
sagent bitbucket list repos        # shorthand for: sagent run bitbucket "list repos"
sagent run ./my-team/release-skills.md "Prepare release notes for v1.4"
sagent chat                       # interactive; /skills, /help, /exit
sagent skills                     # list skills
sagent run slack "Post 'deploy done' to C0123456789" --dry-run
sagent run jira "Move ABC-12 to Done" --yes      # skip confirmation (use with care)
```

| Action | Purpose |
|---|---|
| `run [skill] "<task>"` | Run a task. Skills are auto-selected from the text; an optional leading skill name or `.md` path forces one. `ask` is an alias |
| `chat` | Interactive session (context kept across turns) |
| `init` | Create `~/.sagent` and a config template |
| `doctor` | Verify config, AI CLIs (claude / agy), credentials and connectivity per integration |
| `use [claude\|agy]` | Choose which AI backend sagent drives |
| `skills` | List available skills |

Flags for `run` (and `ask`), `chat`: `--dry-run` (write actions only describe what they would do), `-y/--yes` (skip confirmations).

## Configuration

`~/.sagent/config` is TOML with one section per service; see [config.example.toml](config.example.toml). `SAGENT_HOME` relocates `~/.sagent`.

Environment variables override the file: `SAGENT_<SECTION>_<KEY>`, e.g. `SAGENT_JIRA_API_TOKEN`, `SAGENT_SLACK_TOKEN`, `SAGENT_BITBUCKET_ALLOWED_WORKSPACES=a,b`. Placeholder values (containing `REPLACE`) are treated as unset.

| Service | Auth | Keys |
|---|---|---|
| Bitbucket | Atlassian API token (or app password, or repo/workspace access token) | `email`+`api_token` / `username`+`app_password` / `access_token`, `allowed_workspaces` (required), `allowed_projects` |
| Jira | API token + email | `base_url`, `email`, `api_token`, `allowed_projects` |
| Confluence | API token + email | `base_url`, `email`, `api_token`, `allowed_spaces` |
| Slack | Bot/user token | `token`, `allowed_channels` (names or IDs; required for posting) |
| AWS | Your `~/.aws` files by default (`aws configure`, `aws sso login`); optionally a profile or access keys | none, or `profile`, `access_key_id`, `secret_access_key`, `session_token`, `region` |
| Azure | Your `az login` state in `~/.azure` by default; optionally a service principal | none, or `tenant_id`, `client_id`, `client_secret`, `subscription_id` |

Create Atlassian API tokens at <https://id.atlassian.com/manage-profile/security/api-tokens>. Use least-privilege credentials for every service.

### Permissions
The config is created with `0600` on macOS/Linux and a user-only ACL (`icacls`) on Windows. sagent warns on every run if the file is readable by others.

## Safety model

* **Read-only by default.** Each integration has `allow_writes = false`; write tools are refused until you enable it.
* **Confirmation.** Every state-changing tool call prompts you on the terminal (`/dev/tty`, or the console on Windows). `--yes` skips it; with no terminal available and no `--yes`, writes are declined.
* **Dry-run.** `--dry-run` never executes writes; it reports what would happen.
* **Scope.** Bitbucket workspaces/projects, Jira projects, Confluence spaces and Slack channels are limited to the config allow-lists, enforced in code (not just in the prompt).
* **AWS/Azure.** No shell is used. Only `describe/list/get`-style commands run unconfirmed; unknown operations count as writes. Commands that return secrets (secret values, decrypt, access tokens, account keys, `az rest`, ...), `--endpoint-url`, `--profile`, `file://`/`@file`/URL arguments are blocked. This is a best-effort filter: still give the identity least privilege.
* **Secrets.** Never printed, logged or sent to Claude. All output, errors and audit entries are masked (known values plus common token shapes). Note: the Azure CLI only accepts a service principal secret as a command-line argument; sagent runs it in an isolated profile (`~/.sagent/azure`) and only when a login is needed. With the default `~/.azure` login no secret passes through sagent at all.
* **Audit log.** `~/.sagent/logs/audit-YYYY-MM-DD.jsonl` records commands and every tool call (status: ok, dry_run, declined, executed_confirmed, ...), redacted.

## Skills

Markdown files in `skills/` (repo) and `~/.sagent/skills/` (yours; same `name` overrides the built-in). Front matter plus sections:

```markdown
---
name: grafana
description: One line used for auto-selection and the skill catalog.
integrations: grafana
keywords: dashboard, alert, panel
---
# Grafana skills
## Purpose
## Allowed operations
## Procedures
## Output format
## Guardrails
```

Selection: `run` and `chat` score every skill in `skills/` and `~/.sagent/skills/` by `name`, `integrations` and `keywords` against your text and load the best matches (up to 3); a skill named first (`sagent run jira ...`) or passed with `-s` is always loaded. Claude also receives the skill catalog and can call `load_skill`. See [skills/bitbucket-skills.md](skills/bitbucket-skills.md) for a complete example.

## Adding an integration

1. Create `sagent/integrations/<name>.py`:
   ```python
   from .base import Integration, RestClient, Tool, s, schema

   class Grafana(Integration):
       name, title, section = "grafana", "Grafana", "grafana"
       config_help = "[grafana]: base_url, token"

       def configured(self): return bool(self.conf.get("token"))
       def tools(self):
           return [
               Tool("grafana_list_dashboards", "List dashboards.", schema({}), self.list_dashboards),
               Tool("grafana_delete_alert", "WRITE: delete an alert.", schema({"uid": s("Alert uid")}, ["uid"]),
                    self.delete_alert, write=True, summary=lambda a: f"Grafana: delete alert {a['uid']}"),
           ]
       def check(self): ...   # cheap authenticated call; return a short string or raise IntegrationError

   INTEGRATION = Grafana
   ```
2. Add a `[grafana]` section to `config.example.toml` and a `skills/grafana-skills.md`.

It is discovered automatically. Mark state-changing tools `write=True` (or a callable of the args); confirmation, dry-run, `allow_writes`, masking and audit are applied for free. Validate and scope every argument inside the handler, and never return credentials.

## Project layout

```
install.py              installer / uninstaller (stdlib only)
config.example.toml     placeholder config
skills/*.md             built-in skills
sagent/cli.py           argparse CLI
sagent/config.py        ~/.sagent/config loading, env overrides, permissions
sagent/agent.py         claude -p / agy -p wrapper, prompt assembly, login checks
sagent/providers.py     claude vs agy: selection, detection, install offer
sagent/mcp_server.py    stdio MCP server exposing tools to Claude
sagent/safety.py        confirmation, dry-run, audit around every tool call
sagent/skills.py        skill discovery and selection
sagent/redact.py        secret masking
sagent/audit.py         audit log
sagent/doctor.py        `sagent doctor`
sagent/integrations/    bitbucket, jira, confluence, slack, aws, azure
```

## Troubleshooting

* **`not logged in` / `not signed in`**: for Claude run `claude`, then `/login` (`claude auth status` shows the state); for Antigravity run `agy` and sign in. `sagent doctor` shows which one is active; `sagent use claude|agy` switches.
* **`sagent: command not found`**: open a new terminal; on macOS/Linux ensure `~/.local/bin` is on PATH.
* **Confirmation prompt never appears (Windows, odd terminals)**: sagent needs the console; use `--yes` only if you accept the action, or `--dry-run`.
* **Writes refused**: set `allow_writes = true` in that service's section.
* Check `~/.sagent/logs/` for what was attempted.
