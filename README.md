Autodebugger (Python)

A simple Python-only auto-debugger that steps every executed line of a target program using debugpy and records per-line reports into SQLite, with a session id for each run.

Status: MVP focusing on Python/debugpy only.

Usage
- Install: `pip install -e .`
- Run: `autodebug run path/to/script.py [-- args...]`
- Export JSON: `autodebug export --db .autodebug/line_reports.db --session <id>`

Manual stepping mode
- Interactive debugging: `autodebug run --manual path/to/script.py`
  - Step through code line-by-line with manual control
  - Press Enter to step, 'a' for auto mode, 'c' to continue, 'q' to quit
- Web interface: `autodebug run --manual --manual-web path/to/script.py`
  - Opens a web UI at `http://127.0.0.1:PORT` with Step/Auto/Continue/Quit buttons
  - Real-time display of current file, line number, and code
  - Keyboard shortcuts: Enter (step), a (auto), c (continue), q (quit)
- Conditional activation: `autodebug run --manual --manual-from path/to/file.py:123 path/to/script.py`
  - Starts in automatic mode and switches to manual when reaching the specified file:line
  - Useful for debugging specific sections without stepping through initialization code
- Audio feedback: `autodebug run --manual --manual-audio --manual-voice Samantha --manual-rate 210 path/to/script.py`
  - Announces current line and code via macOS text-to-speech
  - Speaks variable values and changes at each step
  - Configurable voice and speech rate
  - **Syntax-to-speech conversion**: Automatically converts brackets, braces, and parentheses to natural language
    - `[1, 2]` becomes "list open bracket 1, 2 close bracket"
    - `{'key': 'value'}` becomes "dictionary open brace 'key': 'value' close brace"
    - `foo()` becomes "foo open paren close paren"
- Combined features: `autodebug run --manual --manual-web --manual-audio --manual-from path/to/file.py:50 path/to/script.py`
  - Use all features together for maximum control and accessibility
  - **Known Issue**: Web interface may hang when using --manual-from with --manual-audio together. See [issue #1](https://github.com/ChaseKolozsy/auto-debugger/issues/1). Workaround: use --manual-from without audio, or start in manual mode from the beginning if audio is needed.

Audio review (macOS)
- Review sessions with audio + typing: `autodebug audio --db .autodebug/line_reports.db`
- Options: `--voice <name>` (omit to use system default), `--rate 210`, `--delay 0.4`, `--verbose`, `--mode {manual,auto}`, `--recite-func {off,sig,full}`, `--no-scope`, `--no-explore`
- Session selection (paged 0–9): type a digit 0–9 and Enter to select; type `okay` for 0; `next` for next page.
- During playback: it reads each executed line and variable changes. Type notes and press Enter to save them as observations for the current line. Type `next` (or `n`) and Enter to advance; `q` quits.
  - Auto mode: pass `--mode auto` to advance automatically with a small pacing delay.
  - Scope summary: disable with `--no-scope`.
  - Function context: add `--recite-func sig` (signature) or `--recite-func full` (signature then body) to hear the containing function.
  - **Interactive nested exploration** (NEW - fixes [issue #2](https://github.com/ChaseKolozsy/auto-debugger/issues/2)): Type `explore` during playback to interactively explore nested data structures:
    - Select a scope and variable to explore
    - Press 'y' to dive deeper into nested structures (lists, dicts, tuples, objects)
    - Press 'n' to skip to the next item
    - Gracefully handles all Python data types with intelligent depth limits
    - Disable with `--no-explore` if not needed

MCP server (agents)
- A minimal MCP server is included to let agents query the SQLite DB precisely (sessions, lines, crashes, function context) without loading the full DB.
- Location: `submodules/auto-debugger/mcp-autodebug`
- Install and run (stdio):
  ```bash
  cd submodules/auto-debugger/mcp-autodebug
  npm install
  npm run build
  node dist/index.js
  ```
- Tools (summary):
  - `listSessions(db)`: session list (id, file, start/end, counts)
  - `getSession(db, sessionId)`: summary for one session
  - `listLineReports(db, sessionId, offset?, limit?, status?, file?)`: page through lines
  - `getLineReport(db, id)`: full record including variables and deltas
  - `getCrashes(db, sessionId)`: error lines
  - `getFunctionContext(db, sessionId, file, line, mode={sig|full})`: signature/body from snapshot/commit/disk

Completed Features
- Manual stepping mode with web interface and conditional activation
  - ✅ Interactive line-by-line stepping with keyboard controls
  - ✅ Web UI with real-time state display and control buttons
  - ✅ Conditional activation at specific file:line locations (--manual-from)
  - ✅ Audio announcements for current line and variable changes
  - ✅ Seamless switching between manual and auto modes during execution
  - ✅ Prevention of reactivation after mode switching

Roadmap / TODOs
- Audio UI
  - Back/rewind: go back 1 line or N lines; forward N
  - Jumping: by absolute line index, by file, by function boundary
  - Bookmarks: add/list/jump/remove bookmarks during playback
  - Filters/search: errors-only view; filter by file; search by variable name/key in scope or in deltas
  - Summaries: "summarize last N lines"; "summarize session so far"; quick error summary
  - Cross-platform TTS fallback (e.g., `pyttsx3`) and selectable synthesizers
  - Config file for defaults (mode, rate, scope on/off, function recitation)
  - Read notes/observations aloud during playback (distinguish between human, LLM, and other sources)

- MCP server
  - getFunctionContext tool (sig/full) mirroring UI snapshot/commit/disk resolution
  - Navigation tools: aroundLine(sessionId, id, radius), byFile(sessionId, file), firstError(sessionId)
  - Autopsy tool: exportAutopsy(sessionId) with narrative (first crash, lead-up deltas, function/scope context)
  - addNote tool: Allow LLMs to add observations/notes to specific line reports for collaborative debugging
  - Real-time collaborative debugging modes:
    - Batch mode: streamNotes tool that collects all human notes, then LLM applies all fixes in one go
    - Progressive mode: Each note becomes a subtask that LLM completes immediately as notes are added
    - Integration with claude-code, cursor-agent, cline, roo code, kilo code, and other MCP-compatible agents
    - Agent routing: Specify which agent handles which note/task (e.g., "roo: refactor this", "claude: explain why", "cursor: optimize")
  - Transport: optional SSE server in addition to stdio
  - Talon grammar examples for common tool invocations
  - Safety: read-only mode and field redaction options for sensitive data
  - Pagination/limits: enforce sane defaults and tool-level limits

- Agent workflow
  - When a run crashes: run autodebug, export JSON, auto-analyze, propose fix, and cue audio review of key excerpts
  - Loop: apply agent fix, re-run, compare deltas across sessions

- Web UI parity
  - Add audio controls and navigation shortcuts analogous to CLI
  - Inline summaries and quick filters for errors/variables
  - Display notes/observations in the web interface with source attribution (human, LLM, agent)
  - Allow adding/editing notes directly from the web interface

Selecting the Python interpreter / environments
- The debugger can target any Python interpreter via `--python`, otherwise it uses the interpreter running the CLI (`sys.executable`). The specified interpreter must have `debugpy` installed.

- Basic:
  - `autodebug run --python /full/path/to/python path/to/script.py -- arg1 arg2`

- venv (standard):
  - macOS/Linux: `autodebug run --python /path/to/project/.venv/bin/python path/to/script.py`
  - Windows: `autodebug run --python C:\\path\\to\\project\\.venv\\Scripts\\python.exe path\\to\\script.py`

- conda/mamba/micromamba:
  - Resolve interpreter path dynamically, then pass to `--python`:
    - macOS/Linux:
      - ``PY=$(conda run -n ENV python -c 'import sys; print(sys.executable)')``
      - ``autodebug run --python "$PY" path/to/script.py``
    - Windows (PowerShell):
      - `$py = conda run -n ENV python -c "import sys; print(sys.executable)"`
      - `autodebug run --python "$py" path\to\script.py`

- poetry:
  - macOS/Linux:
    - ``PY=$(poetry env info --path)/bin/python``
    - ``autodebug run --python "$PY" path/to/script.py``
  - Windows:
    - `$py = (poetry env info --path) + "\Scripts\python.exe"`
    - `autodebug run --python "$py" path\to\script.py`

- pipenv:
  - ``PY=$(pipenv --py)`` then ``autodebug run --python "$PY" path/to/script.py``

- uv:
  - Create/manage a venv with `uv venv` (or use an existing one) and pass that venv's `python` to `--python`.

Environment variables
- The debuggee inherits the environment of the launcher. Export any required variables (e.g., credentials, feature flags) before running `autodebug`. The runner may append to `PYTHONPATH` for certain layouts (e.g., tests) but otherwise does not manage environments for you.
