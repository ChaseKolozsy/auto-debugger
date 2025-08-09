from __future__ import annotations

import os
import sys
import uuid
from typing import Optional

import click

from .runner import AutoDebugger
from .audio_ui import run_audio_interface
from .db import LineReportStore
from .ui import create_app


@click.group()
def main() -> None:  # pragma: no cover
    pass


@main.command("run")
@click.option("--python", "python_exe", type=click.Path(), default=None, help="Path to Python executable to run debugpy.")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--stop/--no-stop", "stop_on_entry", default=True, help="Stop on entry.")
@click.option("--just-my-code/--all-code", "just_my_code", default=True, help="Restrict to user code.")
@click.argument("script", type=click.Path(exists=True))
@click.argument("script_args", nargs=-1)
def run_cmd(python_exe: Optional[str], db_path: Optional[str], stop_on_entry: bool, just_my_code: bool, script: str, script_args: tuple[str, ...]) -> None:
    dbg = AutoDebugger(python_exe=python_exe, db_path=db_path)
    session_id = dbg.run(script, list(script_args), just_my_code=just_my_code, stop_on_entry=stop_on_entry)
    click.echo(session_id)


@main.command("export")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--session", "session_id", type=str, required=True)
def export_cmd(db_path: Optional[str], session_id: str) -> None:
    store = LineReportStore(db_path)
    store.open()
    try:
        click.echo(store.export_session_json(session_id))
    finally:
        store.close()


@main.command("ui")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=5001, show_default=True, type=int)
@click.option("--open/--no-open", "open_browser", default=True)
def ui_cmd(db_path: Optional[str], host: str, port: int, open_browser: bool) -> None:
    app = create_app(db_path)
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}/")
    app.run(host=host, port=port)


@main.command("audio")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--voice", default="Samantha", show_default=True, help="macOS voice name for 'say'")
@click.option("--rate", default=210, show_default=True, type=int, help="Speech rate (words per minute)")
@click.option("--no-voice", "no_voice", is_flag=True, default=False, help="Disable voice recognition (keyboard only)")
@click.option("--delay", default=0.4, show_default=True, type=float, help="Delay between lines during autoplay (seconds)")
def audio_cmd(db_path: Optional[str], voice: str, rate: int, no_voice: bool, delay: float) -> None:
    """macOS audio interface for reviewing sessions (TTS + optional voice commands)."""
    code = run_audio_interface(
        db_path=db_path,
        voice=voice,
        rate_wpm=rate,
        enable_voice=(not no_voice),
        delay_s=delay,
    )
    # propagate exit code
    sys.exit(code)
