from __future__ import annotations

"""
Audio UI for reviewing auto-debugger sessions on macOS.

Exposed via CLI: `autodebug audio` (see cli.py).

- Paginates sessions (10 at a time), speaks entries enumerated 0-9.
- Voice commands (if available via macOS NSSpeechRecognizer / PyObjC):
  - "okay": choose current session item being read (index 0 on page)
  - "next": skip to next item / next page in session list; during playback, skip to next line
  - Numbers "zero".."nine" or digits "0".."9": choose an item 0-9 while listing
- Fallback to keyboard if voice is unavailable: Enter selects 0 quickly,
  digits 0-9 to select, 'n' for next item/page, 'q' to quit.

This module interacts directly with the SQLite DB schema managed by LineReportStore.
"""

import json
import os
import queue
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .db import DEFAULT_DB_PATH, LineReportStore


class MacSayTTS:
    def __init__(self, voice: Optional[str] = None, rate_wpm: int = 210, verbose: bool = False) -> None:
        self.voice = voice  # None means use system default voice
        self.rate_wpm = rate_wpm
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.verbose = verbose

    def is_speaking(self) -> bool:
        with self._lock:
            return bool(self._proc and self._proc.poll() is None)

    def speak(self, text: str, interrupt: bool = False) -> None:
        if not text:
            return
        proc_to_stop = None
        with self._lock:
            if interrupt and self._proc and self._proc.poll() is None:
                # Don't call stop() while holding the lock - it needs the lock too!
                proc_to_stop = self._proc
                self._proc = None
        # Stop outside the lock to avoid deadlock
        if proc_to_stop:
            try:
                proc_to_stop.terminate()
                proc_to_stop.wait(timeout=0.2)
            except Exception:
                try:
                    proc_to_stop.kill()
                except Exception:
                    pass
        
        with self._lock:
            # If a previous utterance is still playing and we are not interrupting, wait for it
            if not interrupt and self._proc and self._proc.poll() is None:
                try:
                    self._proc.wait(timeout=10.0)
                except Exception:
                    pass
            try:
                args = ["say"]
                if self.voice:
                    args += ["-v", self.voice]
                args += ["-r", str(self.rate_wpm), text]
                self._proc = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                print(f"[TTS] {text}")
                self._proc = None
            finally:
                if self.verbose:
                    print(f"[speak] {text}")

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=0.2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None


@dataclass
class SessionItem:
    session_id: str
    file: str
    start_time: str

    @property
    def script_name(self) -> str:
        return os.path.basename(self.file)


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    return conn


def fetch_sessions(conn: sqlite3.Connection, offset: int, limit: int) -> List[SessionItem]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT session_id, file, start_time
        FROM session_summaries
        ORDER BY updated_at DESC, start_time DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cur.fetchall()
    return [SessionItem(session_id=r[0], file=r[1], start_time=r[2]) for r in rows]


def count_sessions(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM session_summaries")
    row = cur.fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def iter_line_reports(conn: sqlite3.Connection, session_id: str) -> Iterable[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, file, line_number, code, timestamp, variables, variables_delta, status, error_message, error_type
        FROM line_reports
        WHERE session_id=?
        ORDER BY id ASC
        """,
        (session_id,),
    )
    columns = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(columns, row))
        try:
            rec["variables"] = json.loads(rec.get("variables") or "{}")
        except Exception:
            rec["variables"] = {}
        try:
            rec["variables_delta"] = json.loads(rec.get("variables_delta") or "{}")
        except Exception:
            rec["variables_delta"] = {}
        yield rec


def _update_observations(conn: sqlite3.Connection, line_id: Any, note: str) -> None:
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE line_reports SET observations = COALESCE(observations,'') || ? WHERE id=?",
            ("\n" + note if note else note, line_id),
        )
        conn.commit()
    except Exception:
        pass


def _prompt(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def summarize_delta(delta: Dict[str, Any], max_len: int = 120) -> str:
    parts: List[str] = []
    for key, value in delta.items():
        if isinstance(value, dict) and "value" in value:
            child = value.get("children")
            if isinstance(child, dict) and child:
                child_keys = ", ".join(list(child.keys())[:5])
                parts.append(f"{key} changed; fields: {child_keys}")
            else:
                parts.append(f"{key} changed")
        elif value is None:
            parts.append(f"{key} removed")
        else:
            s = str(value)
            if len(s) > 60:
                s = s[:57] + "..."
            parts.append(f"{key} = {s}")
    if not parts:
        return "no changes"
    text = "; ".join(parts)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _format_session_time(start_time_iso: str) -> str:
    try:
        # Handle possible trailing 'Z' for UTC
        st = start_time_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(st)
        # Convert to local time for user relevance
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return f"{local_dt.day:02d} {local_dt.hour:02d}:{local_dt.minute:02d}:{local_dt.second:02d}"
    except Exception:
        return ""


def speak_single_session_item(tts: MacSayTTS, idx: int, item: SessionItem) -> None:
    tts.speak(f"{idx}. {item.script_name}", interrupt=True)
    while tts.is_speaking():
        time.sleep(0.05)
    ts = _format_session_time(item.start_time)
    if ts:
        tts.speak(f"Time: {ts}")
        while tts.is_speaking():
            time.sleep(0.05)
    tts.speak(f"Path: {item.file}")
    while tts.is_speaking():
        time.sleep(0.05)


def paginate_sessions(conn: sqlite3.Connection, tts: MacSayTTS) -> Optional[SessionItem]:
    total = count_sessions(conn)
    if total == 0:
        tts.speak("There are no sessions in the database")
        return None

    page = 0
    page_size = 10
    tts.speak(
        f"There are {total} sessions. Use numbers zero to nine, say okay to choose current, or next for more."
    )

    while True:
        sessions = fetch_sessions(conn, offset=page * page_size, limit=page_size)
        if not sessions:
            tts.speak("No more sessions.")
            return None
        tts.speak(f"Page {page + 1}.")
        while tts.is_speaking():
            time.sleep(0.05)

        idx = 0
        while idx < len(sessions):
            speak_single_session_item(tts, idx, sessions[idx])
            resp = _prompt("Enter=choose current, number 0-9, 'next' for next item, 'page' next page, 'q' quit: ").strip().lower()
            if resp == "":
                return sessions[idx]
            if resp in ("ok", "okay"):
                return sessions[idx]
            if resp in ("q", "quit", "exit"):
                tts.speak("Goodbye")
                return None
            if resp in ("page", "p"):
                page += 1
                break
            if resp in ("next", "n"):
                idx += 1
                continue
            if resp.isdigit() and len(resp) == 1:
                num = int(resp)
                if 0 <= num < len(sessions):
                    return sessions[num]
            tts.speak("Invalid selection")
        else:
            # finished items on this page without selection -> go to next page
            page += 1


def _format_scope_brief(variables: Dict[str, Any], max_pairs: int = 10) -> str:
    scopes_priority = ["Locals", "locals", "Local", "Globals", "globals"]
    chosen: Optional[Dict[str, Any]] = None
    for name in scopes_priority:
        if isinstance(variables.get(name), dict):
            chosen = variables[name]
            break
    if chosen is None:
        # fall back to any first dict scope
        for v in variables.values():
            if isinstance(v, dict):
                chosen = v; break
    if not isinstance(chosen, dict):
        return "no scope"
    parts: List[str] = []
    for k, v in list(chosen.items())[:max_pairs]:
        val = v.get("value") if isinstance(v, dict) and "value" in v else v
        s = str(val)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts) if parts else "empty"


def _load_repo_provenance(conn: sqlite3.Connection, session_id: str) -> tuple[Optional[str], Optional[str], int]:
    cur = conn.cursor()
    cur.execute("SELECT git_root, git_commit, git_dirty FROM session_summaries WHERE session_id=?", (session_id,))
    row = cur.fetchone()
    if not row:
        return None, None, 0
    root = row[0]
    commit = row[1]
    dirty = int(row[2]) if row[2] is not None else 0
    return root, commit, dirty


def _function_signature_and_body(
    store: LineReportStore,
    pyfile: str,
    line_no: int,
    repo_root: Optional[str],
    commit: Optional[str],
    dirty: int,
    session_id: str,
) -> tuple[Optional[str], Optional[str]]:
    # Reuse the logic from ui.py to choose source (snapshot -> committed -> disk)
    source: Optional[str] = None
    if int(dirty or 0) != 0:
        try:
            snap = store.get_file_snapshot(session_id, pyfile)
        except Exception:
            snap = None
        if snap:
            source = snap
    if source is None and repo_root and commit and os.path.abspath(pyfile).startswith(os.path.abspath(repo_root)):
        rel = os.path.relpath(pyfile, repo_root)
        try:
            import subprocess as _sp
            show = _sp.run(["git", "show", f"{commit}:{rel}"], cwd=repo_root, capture_output=True)
            if show.returncode == 0:
                source = show.stdout.decode("utf-8", errors="replace")
        except Exception:
            source = None
    if source is None:
        try:
            with open(pyfile, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception:
            source = None
    if not source:
        return None, None
    try:
        import ast
        tree = ast.parse(source, filename=pyfile)
    except Exception:
        return None, None
    # Find containing function with signature and body
    sig_out: Optional[str] = None
    body_out: Optional[str] = None
    stack: List[str] = []
    import ast as _ast

    def _extract_sig_and_body(source_text: str, node: _ast.AST) -> tuple[str, str]:
        try:
            segment = _ast.get_source_segment(source_text, node) or ""
        except Exception:
            segment = ""
        lines = segment.splitlines()
        if not lines:
            return "", ""
        sig_lines: List[str] = []
        for ln in lines:
            sig_lines.append(ln)
            if ln.rstrip().endswith(":"):
                break
        body_lines = lines[len(sig_lines):]
        sig = "\n".join(sig_lines).strip()
        body = "\n".join(body_lines).strip()
        max_chars = 800
        if len(body) > max_chars:
            body = body[: max_chars - 1] + "\n…"
        return sig, body

    class Visitor(_ast.NodeVisitor):
        def visit_ClassDef(self, node: _ast.ClassDef) -> None:  # type: ignore[override]
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()
        def visit_FunctionDef(self, node: _ast.FunctionDef) -> None:  # type: ignore[override]
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if isinstance(start, int) and isinstance(end, int) and start <= line_no <= end:
                nonlocal sig_out, body_out
                sig_out, body_out = _extract_sig_and_body(source or "", node)
            self.generic_visit(node)
        def visit_AsyncFunctionDef(self, node: _ast.AsyncFunctionDef) -> None:  # type: ignore[override]
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if isinstance(start, int) and isinstance(end, int) and start <= line_no <= end:
                nonlocal sig_out, body_out
                sig_out, body_out = _extract_sig_and_body(source or "", node)
            self.generic_visit(node)

    Visitor().visit(tree)
    return sig_out, body_out


def autoplay_session(
    conn: sqlite3.Connection,
    tts: MacSayTTS,
    session: SessionItem,
    delay_s: float = 0.4,
    mode: str = "manual",  # "auto" or "manual"
    speak_scope: bool = True,
    recite_function: str = "off",  # "off" | "sig" | "full"
    db_path: Optional[str] = None,
) -> None:
    tts.speak(f"Playing session {session.script_name}")
    while tts.is_speaking():
        time.sleep(0.05)

    any_lines = False
    # Prepare function/source helpers once per session
    repo_root, repo_commit, repo_dirty = _load_repo_provenance(conn, session.session_id)
    store = LineReportStore(db_path or DEFAULT_DB_PATH)
    try:
        store.open()
    except Exception:
        pass
    for rec in iter_line_reports(conn, session.session_id):
        any_lines = True
        code = rec.get("code") or ""
        line_no = rec.get("line_number")
        line_id = rec.get("id")
        delta = rec.get("variables_delta") or {}
        status = rec.get("status") or "success"
        err = rec.get("error_message") if status == "error" else None

        prefix = f"Line {line_no}. "
        if code.strip():
            text = prefix + code.strip()
        else:
            text = prefix + "no code captured"
        tts.speak(text, interrupt=True)

        # Wait for speech to finish
        while tts.is_speaking():
            time.sleep(0.05)

        # Speak scope state if requested
        try:
            vars_obj = rec.get("variables") or {}
            if speak_scope:
                brief = _format_scope_brief(vars_obj)
                if brief:
                    tts.speak(f"Scope: {brief}")
                    while tts.is_speaking():
                        time.sleep(0.05)
        except Exception:
            pass

        summary = summarize_delta(delta)
        if summary and summary != "no changes":
            tts.speak(f"Changes: {summary}")
        else:
            tts.speak("No changes")
        while tts.is_speaking():
            time.sleep(0.05)

        if status == "error":
            if err:
                tts.speak(f"Error: {err}")
            else:
                tts.speak("An error occurred")
            while tts.is_speaking():
                time.sleep(0.05)

        # Optionally recite function context
        if recite_function in {"sig", "full"}:
            try:
                sig, body = _function_signature_and_body(store, rec.get("file") or "", int(line_no or 0), repo_root, repo_commit, repo_dirty, session.session_id)
                if sig:
                    tts.speak(f"Function: {sig}")
                    while tts.is_speaking():
                        time.sleep(0.05)
                if recite_function == "full" and body:
                    tts.speak("Body:")
                    while tts.is_speaking():
                        time.sleep(0.05)
                    tts.speak(body)
                    while tts.is_speaking():
                        time.sleep(0.05)
            except Exception:
                pass

        if mode == "manual":
            # Block until user submits 'next', saving any other entered text as notes
            while True:
                cmd_or_note = _prompt("Note or 'next' (commands: func|sig|full|body|repeat|scope|changes, q quit): ").strip()
                low = cmd_or_note.lower()
                if low in ("next", "n"):
                    break
                if low in ("quit", "q", "exit"):
                    tts.speak("Stopping playback", interrupt=True)
                    return
                # On-demand recitations
                if low in ("func", "sig"):
                    try:
                        sig, _body = _function_signature_and_body(store, rec.get("file") or "", int(line_no or 0), repo_root, repo_commit, repo_dirty, session.session_id)
                        if sig:
                            tts.speak(f"Function: {sig}")
                            while tts.is_speaking():
                                time.sleep(0.05)
                        else:
                            tts.speak("Function not available")
                            while tts.is_speaking():
                                time.sleep(0.05)
                    except Exception:
                        tts.speak("Function not available")
                        while tts.is_speaking():
                            time.sleep(0.05)
                    continue
                if low in ("full", "body"):
                    try:
                        sig, body = _function_signature_and_body(store, rec.get("file") or "", int(line_no or 0), repo_root, repo_commit, repo_dirty, session.session_id)
                        if sig:
                            tts.speak(f"Function: {sig}")
                            while tts.is_speaking():
                                time.sleep(0.05)
                        if body:
                            tts.speak("Body:")
                            while tts.is_speaking():
                                time.sleep(0.05)
                            tts.speak(body)
                            while tts.is_speaking():
                                time.sleep(0.05)
                        if not sig and not body:
                            tts.speak("Function not available")
                            while tts.is_speaking():
                                time.sleep(0.05)
                    except Exception:
                        tts.speak("Function not available")
                        while tts.is_speaking():
                            time.sleep(0.05)
                    continue
                if low in ("repeat", "r"):
                    tts.speak(text, interrupt=True)
                    while tts.is_speaking():
                        time.sleep(0.05)
                    continue
                if low == "scope":
                    try:
                        brief = _format_scope_brief(rec.get("variables") or {})
                        tts.speak(f"Scope: {brief}")
                        while tts.is_speaking():
                            time.sleep(0.05)
                    except Exception:
                        pass
                    continue
                if low == "changes":
                    tts.speak(f"Changes: {summary}" if summary and summary != "no changes" else "No changes")
                    while tts.is_speaking():
                        time.sleep(0.05)
                    continue
                if cmd_or_note:
                    try:
                        _update_observations(conn, line_id, cmd_or_note)
                        tts.speak("Noted")
                    except Exception:
                        pass
        else:
            # Auto mode: small pacing delay
            time.sleep(max(0.05, delay_s))

    if not any_lines:
        tts.speak("No line reports found for this session")
        while tts.is_speaking():
            time.sleep(0.05)
    tts.speak("End of session")
    try:
        store.close()
    except Exception:
        pass


def run_audio_interface(
    db_path: Optional[str] = None,
    voice: Optional[str] = None,
    rate_wpm: int = 210,
    delay_s: float = 0.4,
    verbose: bool = False,
    mode: str = "manual",
    speak_scope: bool = True,
    recite_function: str = "off",
) -> int:
    tts = MacSayTTS(voice=voice, rate_wpm=rate_wpm, verbose=verbose)

    def _sigint(_sig, _frm):
        try:
            tts.stop()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    path = db_path or DEFAULT_DB_PATH
    if not os.path.exists(path):
        print(f"Database not found: {path}")
        return 2

    with open_db(path) as conn:
        if verbose:
            print(f"[audio] Opening DB: {path}")
        session = paginate_sessions(conn, tts)
        if not session:
            return 0
        if verbose:
            print(f"[audio] Selected session: {session.session_id} {session.file}")
        autoplay_session(
            conn,
            tts,
            session,
            delay_s=delay_s,
            mode=mode,
            speak_scope=speak_scope,
            recite_function=recite_function,
        )

    tts.stop()
    return 0
