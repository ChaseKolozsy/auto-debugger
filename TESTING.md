## Auto-Debugger test guide (calculator + simple)

This guide shows how to run the Python auto‑debugger on the built‑in tests and verify commit/hash provenance and snapshots in the UI.

### Paths used below
- Submodule root: `submodules/auto-debugger`
- Venv Python/CLI: `submodules/auto-debugger/.venv/bin/`
- DB file: `submodules/auto-debugger/.autodebug/line_reports.db`

### 1) Use the submodule’s virtual environment
The submodule ships with its own virtual environment. Always use its CLI and Python.

```bash
# From repository root
ADIR="submodules/auto-debugger"
VENV="$ADIR/.venv/bin"
DB="$ADIR/.autodebug/line_reports.db"
```

If the venv is missing, create one and install dependencies:
```bash
python3 -m venv "$ADIR/.venv"
"$VENV/pip" install -r <(printf "debugpy\nclick\nflask\npydantic\n")
# Optional: install the package locally (not required to run the tests)
# "$VENV/pip" install -e "$ADIR"
```

### 2) Run the tests with the auto‑debugger
Each run prints a session id you can open in the UI or export as JSON.

- Run on `tests/simple.py`:
```bash
"$VENV/autodebug" run --db "$DB" "$ADIR/tests/simple.py"
```

- Run on the calculator implementation (`tests/calculator/main.py`):
```bash
"$VENV/autodebug" run --db "$DB" "$ADIR/tests/calculator/main.py"
```

Tip: If you edit files between runs (dirty working tree), the debugger now snapshots each encountered source file once per session, so the UI will render the exact source that executed.

### 3) Open the UI
The UI lets you browse sessions, lines, variables, and function details. It prefers file snapshots when the session was dirty; otherwise it loads the file at the recorded commit; finally it falls back to the live file on disk.

```bash
# Starts a local server and opens the browser
"$VENV/autodebug" ui --db "$DB" --host 127.0.0.1 --port 4994 --open
```

In the UI:
- Click a session to open it.
- Select a row to see the line details.
- Hover/click the banner to view the enclosing function signature/body used for that session (snapshot/commit/disk, in that order).

### 4) Verify the recorded commit/hash and dirty state
- In the UI, the session header includes provenance; or export as JSON:
```bash
SESSION_ID=<paste id from run>
"$VENV/autodebug" export --db "$DB" --session "$SESSION_ID" | jq '.session_info | {git_root, git_commit, git_dirty}'
```
- `git_dirty == 1` indicates the working tree had uncommitted changes during the run; in that case the UI uses the per‑file snapshots captured in the session.

### 5) Common issues
- Nothing appears in UI: ensure you pointed the UI to the same DB `--db "$DB"` used by the runs.
- Import errors when running calculator: the runner sets `PYTHONPATH` to the tests’ parent so `calculator` package resolves. Ensure you run the exact command shown above from the repository root (or adjust paths accordingly).
- Snapshot not used: confirm the session shows `git_dirty: 1`. If `0`, the UI loads committed source `git show <commit>:<path>`.

### 6) Cleanup
Remove the database if you want a fresh state:
```bash
rm -rf "$ADIR/.autodebug"
```
