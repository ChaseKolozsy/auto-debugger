from __future__ import annotations

import os
import sys
import uuid
from typing import Optional

import click

from .runner import AutoDebugger
from .audio_ui import run_audio_interface
from .db import LineReportStore
from .ui import create_app as create_legacy_app
from .unified_ui import create_unified_app, UnifiedReviewInterface


@click.group()
def main() -> None:  # pragma: no cover
    pass


@main.command("run")
@click.option("--python", "python_exe", type=click.Path(), default=None, help="Path to Python executable to run debugpy.")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--stop/--no-stop", "stop_on_entry", default=True, help="Stop on entry.")
@click.option("--just-my-code/--all-code", "just_my_code", default=True, help="Restrict to user code.")
@click.option("--manual/--auto", "manual", default=False, help="Enable manual stepping mode (press Enter to step).")
@click.option(
    "--manual-from",
    "manual_from",
    type=str,
    default=None,
    help="Activate manual mode when reaching file:line (e.g., path/to/file.py:123).",
)
@click.option("--manual-web/--no-manual-web", "manual_web", default=False, help="Expose a local web controller for manual stepping.")
@click.option("--manual-audio/--no-manual-audio", "manual_audio", default=False, help="Speak current line and changes during manual stepping.")
@click.option("--manual-voice", "manual_voice", type=str, default=None, help="Voice name for macOS 'say' in manual audio mode.")
@click.option("--manual-rate", "manual_rate_wpm", type=int, default=210, show_default=True, help="Speech rate for manual audio mode.")
@click.option("--max-loop-iterations", "max_loop_iterations", type=int, default=None, help="Maximum iterations allowed in a loop before aborting (resource management).")
@click.option("--max-memory-mb", "max_memory_mb", type=int, default=None, help="Maximum memory usage in MB before aborting (resource management).")
@click.option("--max-disk-usage-mb", "max_disk_usage_mb", type=int, default=None, help="Maximum disk usage increase in MB before aborting (resource management).")
@click.option("--record-resources/--no-record-resources", "record_resources", default=False, help="Record resource usage (loop iterations, memory, disk) for each line executed.")
@click.argument("script", type=click.Path(exists=True))
@click.argument("script_args", nargs=-1)
def run_cmd(
    python_exe: Optional[str],
    db_path: Optional[str],
    stop_on_entry: bool,
    just_my_code: bool,
    manual: bool,
    manual_from: Optional[str],
    manual_web: bool,
    manual_audio: bool,
    manual_voice: Optional[str],
    manual_rate_wpm: int,
    max_loop_iterations: Optional[int],
    max_memory_mb: Optional[int],
    max_disk_usage_mb: Optional[int],
    record_resources: bool,
    script: str,
    script_args: tuple[str, ...],
) -> None:
    print(f"[CLI DEBUG] Starting with max_loop_iterations={max_loop_iterations}, max_memory_mb={max_memory_mb}, max_disk_usage_mb={max_disk_usage_mb}, record_resources={record_resources}", file=sys.stderr, flush=True)
    dbg = AutoDebugger(python_exe=python_exe, db_path=db_path)
    session_id = dbg.run(
        script,
        list(script_args),
        just_my_code=just_my_code,
        stop_on_entry=stop_on_entry,
        manual=manual,
        manual_from=manual_from,
        manual_web=manual_web,
        manual_audio=manual_audio,
        manual_voice=manual_voice,
        manual_rate_wpm=manual_rate_wpm,
        max_loop_iterations=max_loop_iterations,
        max_memory_mb=max_memory_mb,
        max_disk_usage_mb=max_disk_usage_mb,
        record_resources=record_resources,
    )
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
@click.option("--unified/--legacy", "use_unified", default=True, help="Use unified interface with enhanced features")
def ui_cmd(db_path: Optional[str], host: str, port: int, open_browser: bool, use_unified: bool) -> None:
    """Launch web UI for reviewing debug sessions."""
    if use_unified:
        app = create_unified_app(db_path)
    else:
        app = create_legacy_app(db_path)
    
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}/")
    app.run(host=host, port=port)


@main.command("audio")
@click.option("--db", "db_path", type=click.Path(), default=None, help="SQLite DB path for reports.")
@click.option("--voice", default=None, show_default=True, help="macOS voice name for 'say' (default system voice)")
@click.option("--rate", default=210, show_default=True, type=int, help="Speech rate (words per minute)")
@click.option("--delay", default=0.01, show_default=True, type=float, help="Delay between lines during autoplay (seconds)")
@click.option("--verbose", is_flag=True, default=False, help="Print spoken text and selection info to console")
@click.option("--mode", type=click.Choice(["auto", "manual"], case_sensitive=False), default="manual", show_default=True, help="Playback mode")
@click.option("--recite-func", type=click.Choice(["off", "sig", "full"], case_sensitive=False), default="off", show_default=True, help="Recite function signature/body for each line")
@click.option("--no-scope", is_flag=True, default=False, help="Do not speak scope summary for each line")
@click.option("--no-explore", is_flag=True, default=False, help="Disable interactive nested value exploration")
@click.option("--unified/--legacy", "use_unified", default=True, help="Use unified interface with full feature parity")
def audio_cmd(db_path: Optional[str], voice: Optional[str], rate: int, delay: float, verbose: bool, mode: str, recite_func: str, no_scope: bool, no_explore: bool, use_unified: bool) -> None:
    """macOS audio interface for reviewing sessions (TTS + optional voice commands)."""
    if use_unified:
        interface = UnifiedReviewInterface(db_path)
        code = interface.run_audio_interface(
            voice=voice,
            rate_wpm=rate,
            delay_s=delay,
            verbose=verbose,
            mode=mode.lower()
        )
    else:
        code = run_audio_interface(
            db_path=db_path,
            voice=voice,
            rate_wpm=rate,
            delay_s=delay,
            verbose=verbose,
            mode=mode.lower(),
            recite_function=recite_func.lower(),
            speak_scope=(not no_scope),
            explore_nested=(not no_explore),
        )
    # propagate exit code
    sys.exit(code)
