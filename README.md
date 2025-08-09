Autodebugger (Python)

A simple Python-only auto-debugger that steps every executed line of a target program using debugpy and records per-line reports into SQLite, with a session id for each run.

Status: MVP focusing on Python/debugpy only.

Usage
- Install: `pip install -e .`
- Run: `autodebug run --script path/to/script.py [-- args...]`
- Export JSON: `autodebug export --db .autodebug/line_reports.db --session <id>`
