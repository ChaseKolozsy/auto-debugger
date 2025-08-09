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
from typing import Any, Dict, Iterable, List, Optional

from .db import DEFAULT_DB_PATH


class MacSayTTS:
    def __init__(self, voice: str = "Samantha", rate_wpm: int = 210, verbose: bool = False) -> None:
        self.voice = voice
        self.rate_wpm = rate_wpm
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.verbose = verbose

    def speak(self, text: str, interrupt: bool = True) -> None:
        if not text:
            return
        with self._lock:
            if interrupt:
                self.stop()
            try:
                self._proc = subprocess.Popen(
                    ["say", "-v", self.voice, "-r", str(self.rate_wpm), text],
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


class VoiceRecognizer:
    """Optional macOS voice recognizer using NSSpeechRecognizer via PyObjC.

    If not available, it becomes a no-op and never emits commands.
    """

    def __init__(self, commands: Iterable[str], listens_in_foreground_only: bool = False) -> None:
        self.available = False
        self._cmd_queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._should_stop = threading.Event()
        self._commands = list(commands)

        try:
            import AppKit  # type: ignore
            import Foundation  # type: ignore
            import objc  # type: ignore
            from Foundation import NSObject  # type: ignore

            self._objc = objc
            self._AppKit = AppKit
            self._Foundation = Foundation
            self._NSObject = NSObject

            class _Delegate(NSObject):  # type: ignore
                def initWithQueue_(self, q):  # type: ignore
                    self = objc.super(_Delegate, self).init()
                    if self is None:
                        return None
                    self.q = q
                    return self

                def speechRecognizer_didRecognizeCommand_(self, sender, command):  # type: ignore
                    try:
                        cmd = str(command)
                        self.q.put(cmd)
                    except Exception:
                        pass

            self._Delegate = _Delegate
            self.available = True
            self._recognizer = None
            self._delegate = None

            def _runner():
                pool = self._Foundation.NSAutoreleasePool.alloc().init()
                try:
                    recognizer = self._AppKit.NSSpeechRecognizer.alloc().init()
                    delegate = self._Delegate.alloc().initWithQueue_(self._cmd_queue)
                    recognizer.setDelegate_(delegate)
                    recognizer.setListensInForegroundOnly_(listens_in_foreground_only)
                    recognizer.setBlocksOtherRecognizers_(True)
                    recognizer.setCommands_(self._commands)
                    recognizer.startListening()
                    self._recognizer = recognizer
                    self._delegate = delegate

                    run_loop = self._Foundation.NSRunLoop.currentRunLoop()
                    while not self._should_stop.is_set():
                        run_loop.runUntilDate_(self._Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
                finally:
                    try:
                        if self._recognizer is not None:
                            self._recognizer.stopListening()
                    except Exception:
                        pass
                    del pool

            self._thread = threading.Thread(target=_runner, daemon=True)
            self._thread.start()
        except Exception:
            self.available = False

    def get_command_nowait(self) -> Optional[str]:
        try:
            return self._cmd_queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._should_stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


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


NUM_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}


class InputManager:
    def __init__(self, enable_voice: bool) -> None:
        commands = list(NUM_WORDS.keys()) + [str(i) for i in range(10)] + ["okay", "next"]
        self.recognizer = VoiceRecognizer(commands) if enable_voice else None

    def read_command(self, timeout: float) -> Optional[str]:
        if self.recognizer and self.recognizer.available:
            t_end = time.time() + timeout
            while time.time() < t_end:
                cmd = self.recognizer.get_command_nowait()
                if cmd:
                    return cmd.strip().lower()
                time.sleep(0.05)
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                data = sys.stdin.readline().strip().lower()
                return data
        except Exception:
            pass
        return None

    def stop(self) -> None:
        if self.recognizer:
            self.recognizer.stop()


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


def speak_session_page(tts: MacSayTTS, sessions: List[SessionItem], page_idx: int) -> None:
    if not sessions:
        tts.speak("No sessions found.")
        return
    tts.speak(f"Page {page_idx + 1}. Sessions.")
    for i, s in enumerate(sessions):
        idx_name = f"{i}"
        script = s.script_name
        tts.speak(f"{idx_name}. {script}")
        time.sleep(0.25)
        tts.speak(f"Path: {s.file}")
        time.sleep(0.2)


def paginate_sessions(conn: sqlite3.Connection, tts: MacSayTTS, input_mgr: InputManager) -> Optional[SessionItem]:
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
        speak_session_page(tts, sessions, page)

        while True:
            cmd = input_mgr.read_command(timeout=8.0)
            if cmd is None:
                if page == 0:
                    tts.speak("Selecting item zero by default")
                    return sessions[0]
                tts.speak("Moving to next page")
                page += 1
                break

            if cmd in NUM_WORDS:
                idx = NUM_WORDS[cmd]
                if idx < len(sessions):
                    tts.speak(f"Selected {idx} {sessions[idx].script_name}")
                    return sessions[idx]
                else:
                    tts.speak("That index is not on this page")
                    continue
            if cmd.isdigit() and len(cmd) == 1:
                idx = int(cmd)
                if idx < len(sessions):
                    tts.speak(f"Selected {idx} {sessions[idx].script_name}")
                    return sessions[idx]
                else:
                    tts.speak("That index is not on this page")
                    continue
            if cmd in ("ok", "okay"):
                tts.speak(f"Okay. Selecting {sessions[0].script_name}")
                return sessions[0]
            if cmd == "next" or cmd == "n":
                tts.speak("Next page")
                page += 1
                break
            if cmd in ("quit", "exit", "q"):
                tts.speak("Goodbye")
                return None
            tts.speak("Say a number, okay, or next")


def autoplay_session(
    conn: sqlite3.Connection,
    tts: MacSayTTS,
    session: SessionItem,
    input_mgr: InputManager,
    delay_s: float = 0.4,
) -> None:
    tts.speak(f"Playing session {session.script_name}")
    for rec in iter_line_reports(conn, session.session_id):
        code = rec.get("code") or ""
        line_no = rec.get("line_number")
        delta = rec.get("variables_delta") or {}
        status = rec.get("status") or "success"
        err = rec.get("error_message") if status == "error" else None

        prefix = f"Line {line_no}. "
        if code.strip():
            text = prefix + code.strip()
        else:
            text = prefix + "no code captured"
        tts.speak(text)

        summary = summarize_delta(delta)
        if summary and summary != "no changes":
            tts.speak(f"Changes: {summary}")
        else:
            tts.speak("No changes")

        if status == "error":
            if err:
                tts.speak(f"Error: {err}")
            else:
                tts.speak("An error occurred")

        t_end = time.time() + delay_s
        while time.time() < t_end:
            cmd = input_mgr.read_command(timeout=0.05)
            if not cmd:
                continue
            if cmd in ("next", "n"):
                break
            if cmd in ("quit", "q", "exit"):
                tts.speak("Stopping playback")
                return

    tts.speak("End of session")


def run_audio_interface(
    db_path: Optional[str] = None,
    voice: str = "Samantha",
    rate_wpm: int = 210,
    enable_voice: bool = True,
    delay_s: float = 0.4,
    verbose: bool = False,
) -> int:
    tts = MacSayTTS(voice=voice, rate_wpm=rate_wpm, verbose=verbose)
    input_mgr = InputManager(enable_voice=enable_voice)

    def _sigint(_sig, _frm):
        try:
            tts.stop()
            input_mgr.stop()
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
        session = paginate_sessions(conn, tts, input_mgr)
        if not session:
            return 0
        if verbose:
            print(f"[audio] Selected session: {session.session_id} {session.file}")
        autoplay_session(conn, tts, session, input_mgr, delay_s=delay_s)

    input_mgr.stop()
    tts.stop()
    return 0
