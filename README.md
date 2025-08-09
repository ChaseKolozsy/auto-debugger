Autodebugger (Python)

A simple Python-only auto-debugger that steps every executed line of a target program using debugpy and records per-line reports into SQLite, with a session id for each run.

Status: MVP focusing on Python/debugpy only.

Usage
- Install: `pip install -e .`
- Run: `autodebug run path/to/script.py [-- args...]`
- Export JSON: `autodebug export --db .autodebug/line_reports.db --session <id>`

Audio review (macOS)
- Review sessions with audio + typing: `autodebug audio --db .autodebug/line_reports.db`
- Options: `--voice <name>` (omit to use system default), `--rate 210`, `--delay 0.4`, `--verbose`
- Session selection (paged 0–9): type a digit 0–9 and Enter to select; type `okay` for 0; `next` for next page.
- During playback: it reads each executed line and variable changes. Type notes and press Enter to save them as observations for the current line. Type `next` (or `n`) and Enter to advance; `q` quits.

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
