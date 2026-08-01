# sleuth

A Python reimplementation of [opencode](https://opencode.ai)'s core coding-agent loop — an MVP that ports the essential architecture (provider-agnostic streaming, a validated tool registry, an agentic session loop, a layered config system, SQLite persistence, and git-tree snapshots) to idiomatic Python.

> **Not affiliated with opencode.** This project reimplements opencode's design in Python for learning and experimentation. It is **not** built by the opencode team and is not an official opencode product. If you ship something using "opencode" in its name, add a similar disclaimer per opencode's own guidance.

## What it does

sleuth is an interactive CLI coding agent. You give it a task in natural language; it streams a response and — when needed — calls tools (read/write/edit files, run bash, grep, glob, webfetch, spawn subagents, track todos, ask you questions) to actually do the work, looping with the model until the task is done or it runs out of steps.

Sessions are persisted to SQLite as they run, so you can resume a conversation later; each assistant step captures a git tree-hash snapshot of the working directory so a step can be reverted. The first user message also gets an auto-generated session title (via `small_model`).

```
>>> find the function that handles http errors and add a retry counter
⚙ read(file_path='sleuth/session.py')
  ✓ read
⚙ edit(...)
  ✓ edit
(end_turn; in=1204 out=186 tokens)
[title: HTTP error retry counter]
```

## Quick start

Requires **Python ≥ 3.9** and an OpenAI-compatible API key.

```bash
# 1. install (editable → `sleuth` console script + `python -m sleuth`)
pip install -e .

# 2. configure credentials (once)
cp .env.example .env
# edit .env: set OPENCODE_MODEL and the matching API key

# 3. run
python -m sleuth
# or: sleuth
```

**Windows (PowerShell):**

```powershell
cd C:\Users\15385\myproject\sleuth
pip install -e .
Copy-Item .env.example .env   # skip if .env already exists
# notepad .env   # set OPENCODE_MODEL + OPENAI_API_KEY (or your gateway)
python -m sleuth
```

Minimal `.env`:

```bash
OPENCODE_MODEL=openai/gpt-4o
OPENAI_API_KEY=sk-...
# optional cheaper model for titles / compaction:
# OPENCODE_SMALL_MODEL is not an env var — set "small_model" in opencode.jsonc
```

### First things to try

```bash
# interactive REPL (best way to feel the product)
python -m sleuth

# one-shot: ask about the codebase
python -m sleuth "explain what sleuth/session.py does in 5 bullets"

# plan mode (read-only; denies write/edit)
python -m sleuth --agent plan "how would I add a websearch tool?"

# auto-approve all tool permissions (handy for demos)
python -m sleuth --yolo "list the tools in sleuth/tools and summarise each in one line"

# exercise webfetch
python -m sleuth --yolo "use webfetch to get https://example.com as markdown and summarise it"

# exercise a subagent (explore is read-oriented)
python -m sleuth --yolo "use the task tool with subagent_type=explore to find where permissions are defined"

# continue the last session in this directory
python -m sleuth -c
```

In the REPL:

| input | effect |
|-------|--------|
| natural language | runs the agent loop |
| `/name args` | expands a command from `.opencode/command/*.md` |
| `exit` / `quit` / Ctrl+C at prompt | leave |
| Ctrl+C mid-turn | abort the running stream; stay in the REPL |

What you should notice when it works:

- Streaming assistant text (+ dim reasoning if the model emits it)
- `⚙ tool(...)` lines and `✓` / `✗` results
- Token usage on stop: `(end_turn; in=… out=… tokens)`
- Auto title after the first message: `[title: …]`
- Permission prompts (`[o] once` / `[a] always` / `[r] reject`) unless `--yolo`
- Retry lines on transient 429/5xx: `retry 1: … (waiting 2.0s)`

## Configure

Everything can live in a single `.env` file — no `opencode.json` required. Copy the template and edit:

```bash
cp .env.example .env
```

| thing | env var | also configurable in |
|---|---|---|
| model | `OPENCODE_MODEL` | `--model` flag, `opencode.json` `model` |
| api key | `<PROVIDER>_API_KEY` (e.g. `OPENAI_API_KEY`) | `opencode.json` `provider.<id>.options.apiKey` |
| base url | `<PROVIDER>_BASE_URL` (e.g. `OPENAI_BASE_URL`) | `opencode.json` `provider.<id>.options.baseURL` |
| small model (titles/compaction) | — | `opencode.json` `small_model` |
| context window (compaction) | `OPENCODE_CONTEXT_LIMIT` | `opencode.json` `context_limit` |

Precedence: `--model` flag > `opencode.json` > `.env` for the model; config options > `.env` > SDK default for keys/base_url.

Optional `opencode.json` / `opencode.jsonc` (project root or `~/.config/opencode/`). See [`opencode.jsonc.example`](./opencode.jsonc.example). Discovered bottom-up from cwd → git root and deep-merged with the global file.

Example extras:

```jsonc
{
  "model": "openai/gpt-4o",
  "small_model": "openai/gpt-4o-mini",
  "context_limit": 128000,
  "compaction": { "auto": true, "reserved": 20000 },
  "subagent_depth": 1,
  "provider": {
    "openai": {
      "options": {
        // optional $/1M rates → session.cost
        "cost": { "input": 2.5, "output": 10, "cache_read": 1.25, "cache_write": 0 }
      }
    }
  }
}
```

> **Providers:** OpenAI and any OpenAI-compatible gateway (OpenRouter, Groq, Together, xAI, local llama.cpp, …) via the `openai` SDK. For a gateway set matching `*_API_KEY` + `*_BASE_URL`, and `OPENCODE_MODEL=<provider>/<model>`.

### Instruction files

On each turn, sleuth loads (first match wins per class), port of opencode `instruction.ts`:

1. Global: `~/.config/opencode/AGENTS.md`, else `~/.claude/CLAUDE.md`
2. Project: walk up for `AGENTS.md` / `CLAUDE.md` / `CONTEXT.md`
3. Config `instructions`: literal lines, file paths, or `http(s)://` URLs

### Custom agents & slash commands

Place markdown under the global config dir or a project `.opencode/` folder:

```
.opencode/
  agent/
    reviewer.md          # → agent name "reviewer"
  command/
    explain.md           # → REPL `/explain …`
```

```markdown
<!-- .opencode/agent/reviewer.md -->
---
description: Strict code reviewer
mode: subagent
permission:
  edit: deny
  write: deny
---
You review diffs for bugs and style. Be terse.
```

```markdown
<!-- .opencode/command/explain.md -->
---
description: Explain a file
agent: explore
---
Explain $ARGUMENTS clearly for a new teammate.
```

In the REPL: `/explain sleuth/session.py`.

## Run reference

```bash
python -m sleuth                              # interactive REPL
python -m sleuth "…"                          # one-shot
python -m sleuth --agent plan "…"             # read-only agent
python -m sleuth --agent build --yolo "…"     # auto-approve tools
python -m sleuth --model openai/gpt-4o "…"
python -m sleuth --print "…" > out.txt        # hide tool chrome (scripting)
python -m sleuth -c                           # continue most recent session here
python -m sleuth --session sess_…             # resume by id
python -m sleuth --session sess_… --revert msg_…   # restore tree to that step's snapshot
```

### Sessions

Session id is printed at startup (`[session sess_…]`). DB path:

- POSIX: `~/.local/share/opencode/sleuth.db`
- Windows: `%LOCALAPPDATA%\opencode\sleuth.db`
- Override: `OPENCODE_DATA_DIR`

### Snapshots

Each assistant step stores a git tree-hash (throwaway index — your real index is untouched). Non-git dirs skip snapshots. Revert:

```bash
python -m sleuth --session sess_… --revert msg_…
```

### OpenAI-compatible endpoints

```bash
# .env
OPENCODE_MODEL=groq/llama-3.3-70b-versatile
GROQ_API_KEY=gsk_...
GROQ_BASE_URL=https://api.groq.com/openai/v1
```

## Tools

| tool | purpose |
|------|---------|
| read | file (line-numbered) / directory listing / image+PDF attachments |
| write | create/overwrite a file (permission-gated) |
| edit | exact string replace with fuzzy whitespace fallbacks |
| bash | shell in workdir, timeout + truncation |
| glob | file patterns by mtime |
| grep | regex search across files |
| todo | structured multi-step task list |
| question | ask the user mid-run (blocks) |
| webfetch | HTTP GET → markdown / text / html |
| task | sync nested subagent (`general`, `explore`, or custom); returns `task_id` |

**Built-in agents**

| agent | role |
|-------|------|
| `build` | default; asks before write/edit/bash/webfetch/task |
| `plan` | read-only; denies write/edit/task |
| `general` | subagent for multi-step work (via `task`) |
| `explore` | subagent; exploration tools only (via `task`) |

Permissions use opencode-style wildcard rules (`{ bash: { "git *": "allow" } }`). Interactive `once / always / reject` on `ask`; `--yolo` allows everything.

Adding a tool: pydantic `BaseModel` params + `execute(args, ctx) -> ToolResult`, register in `tools/registry.py`. JSON schema is generated from the model.

## Architecture

The design mirrors opencode's core subsystems, without Effect.ts or a server/client split:

| opencode (TS) | sleuth (Python) | role |
|---------------|-----------------|------|
| `session/prompt.ts` loop | `sleuth/session.py` `_run_loop` | agentic loop (+ abort, snapshots, retry, title, compaction) |
| `session/retry.ts` | `sleuth/retry.py` | exponential backoff on transient errors |
| `session.getUsage` | `sleuth/usage.py` | token normalisation + optional cost |
| `session/instruction.ts` | `sleuth/instruction.py` | AGENTS.md / CLAUDE.md discovery |
| `session/compaction.ts` | `sleuth/compaction.py` | summarise older turns near context limit |
| `tool/tool.ts` + `registry.ts` | `sleuth/tools/` | validated tools + registry |
| `tool/webfetch.ts` / `task.ts` | `sleuth/tools/webfetch.py`, `task.py` | web fetch + sync subagents |
| `provider/provider.ts` | `sleuth/provider/` | streaming providers + event protocol |
| `session/system.ts` | `sleuth/prompts/` | system-prompt assembly |
| `config` + agent/command md | `sleuth/config.py` | layered JSONC + `.opencode/**/*.md` |
| `permission` | `sleuth/permission.py` | allow/ask/deny wildcard rules |
| `session/sql.ts` | `sleuth/storage/sqlite.py` | SQLite persistence |
| `snapshot/index.ts` | `sleuth/snapshot.py` | git tree-hash checkpoints + revert |

**The loop**, in short:

1. Append the user message (SQLite); maybe generate a session title.
2. Assemble system prompt (base + env + AGENTS.md + config instructions) + visible tools.
3. Optionally compact history if token usage is near `context_limit`.
4. Capture a working-tree snapshot; stream the model (`TextDelta` / `ReasoningDelta` / `ToolUse` / `Stop`), with abort between chunks and retry on transient errors.
5. Persist the assistant message (snapshots + usage/cost). No tool calls → done.
6. Execute tools (permission-gated), append results (including image attachments), persist.
7. Repeat (up to `max_steps`).

## Project layout

```
sleuth/
├── pyproject.toml
├── requirements.txt
├── .env.example
├── opencode.jsonc.example
└── sleuth/
    ├── __main__.py
    ├── cli.py               # argparse CLI + Rich renderer + /commands
    ├── config.py            # JSONC + .opencode agent/command markdown
    ├── agent.py             # build / plan / general / explore baselines
    ├── session.py           # agentic loop
    ├── retry.py
    ├── usage.py
    ├── title.py
    ├── instruction.py
    ├── compaction.py
    ├── snapshot.py
    ├── messages.py
    ├── permission.py
    ├── provider/
    ├── storage/
    ├── tools/               # read write edit bash glob grep todo question webfetch task
    ├── prompts/             # default plan title compaction
    └── util/
```

## Status & roadmap

**Implemented** (ported from opencode where noted):

- Persistence, resume (`--session` / `-c`)
- Snapshots + CLI `--revert`
- Abort (Ctrl+C), reasoning stream, question tool
- Retry with backoff (`retry.py`)
- Usage & optional cost tracking (`usage.py`)
- Session titles via `small_model` (`title.py`)
- AGENTS.md-style instruction discovery (`instruction.py`)
- Context compaction (`compaction.py`)
- `webfetch`, sync `task` / subagents (`general`, `explore`)
- Custom agents & `/commands` from `.opencode/**/*.md`
- Image/PDF attachments on `read`

**Not ported** (need server, SaaS keys, or large runtimes — see prior discussion):

- MCP client, websearch (Exa/Parallel), full TUI
- Plugins/hooks, LSP tool, server/client split, skills, apply_patch, background subagents

## License

MIT.
