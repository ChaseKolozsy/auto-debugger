"""
Unified UI module combining audio and visual interfaces with full feature parity.

This module provides:
- Audio review interface (from audio_ui.py)
- Web review interface (from ui.py)  
- Variable exploration
- Data structure summarization
- Interactive selection
- Speed control
"""

from __future__ import annotations

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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Flask, render_template, request, redirect, url_for, jsonify

from .common import extract_function_context, summarize_delta, summarize_value, parse_dap_variables
from .db import DEFAULT_DB_PATH, LineReportStore
from .nested_explorer import NestedValueExplorer
from .syntax_to_speech import syntax_to_speech_code, syntax_to_speech_value
from .function_blocks import FunctionBlockExplorer


class AudioTTS:
    """Unified TTS handler for macOS say command with speed control."""
    
    def __init__(self, voice: Optional[str] = None, rate_wpm: int = 210, verbose: bool = False, convert_syntax: bool = True):
        self.voice = voice
        self.rate_wpm = rate_wpm
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.verbose = verbose
        self.convert_syntax = convert_syntax
        self._speed_multiplier = 1.0  # 1.0 = normal, 0.7 = slow, 1.3 = fast
    
    def set_speed(self, speed: str) -> None:
        """Set speech speed: 'slow', 'medium', or 'fast'."""
        if speed == "slow":
            self._speed_multiplier = 0.7
        elif speed == "fast":
            self._speed_multiplier = 1.3
        else:
            self._speed_multiplier = 1.0
    
    def get_effective_rate(self) -> int:
        """Get the effective speech rate with speed multiplier."""
        return int(self.rate_wpm * self._speed_multiplier)
    
    def is_speaking(self) -> bool:
        with self._lock:
            return bool(self._proc and self._proc.poll() is None)
    
    def _convert_text_for_speech(self, text: str, is_code: bool = False) -> str:
        """Convert text for better speech output."""
        if not self.convert_syntax or not text:
            return text
        
        # Use common summarization for data structures
        if "{" in text or "[" in text:
            # Check if it's a data structure announcement
            if text.startswith(("Changes:", "Scope:", "Variables:")):
                prefix, _, content = text.partition(":")
                # Parse and summarize the content
                try:
                    import ast
                    parsed = ast.literal_eval(content.strip())
                    summary = summarize_value(parsed)
                    return f"{prefix}: {summary}"
                except:
                    pass
        
        # Determine if this looks like code or a value
        if is_code or any(keyword in text for keyword in ["def ", "if ", "for ", "while ", "class ", "import ", "return "]):
            return syntax_to_speech_code(text)
        elif any(char in text for char in ["[", "]", "{", "}", "(", ")"]):
            if text.strip().startswith(("Line ", "Scope:", "Changes:", "Function:", "Error:")):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    prefix, value = parts
                    return f"{prefix}: {syntax_to_speech_value(value.strip())}"
            return syntax_to_speech_value(text)
        return text
    
    def speak(self, text: str, interrupt: bool = False, is_code: bool = False) -> None:
        if not text:
            return
        
        proc_to_stop = None
        with self._lock:
            if interrupt and self._proc and self._proc.poll() is None:
                proc_to_stop = self._proc
                self._proc = None
        
        if proc_to_stop:
            try:
                proc_to_stop.terminate()
                proc_to_stop.wait(timeout=0.2)
            except Exception:
                try:
                    proc_to_stop.kill()
                except:
                    pass
        
        with self._lock:
            if not interrupt and self._proc and self._proc.poll() is None:
                try:
                    self._proc.wait(timeout=10.0)
                except:
                    pass
            
            try:
                converted_text = self._convert_text_for_speech(text, is_code)
                args = ["say"]
                if self.voice:
                    args += ["-v", self.voice]
                args += ["-r", str(self.get_effective_rate()), converted_text]
                self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                converted_text = self._convert_text_for_speech(text, is_code)
                print(f"[TTS] {converted_text}")
                self._proc = None
            finally:
                if self.verbose:
                    converted_text = self._convert_text_for_speech(text, is_code)
                    if converted_text != text:
                        print(f"[speak-original] {text}")
                        print(f"[speak-converted] {converted_text}")
                    else:
                        print(f"[speak] {text}")
    
    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=0.2)
                except:
                    try:
                        self._proc.kill()
                    except:
                        pass
            self._proc = None


@dataclass
class SessionInfo:
    """Unified session information."""
    session_id: str
    file: str
    start_time: str
    end_time: Optional[str] = None
    total_lines: int = 0
    successful_lines: int = 0
    error_lines: int = 0
    
    @property
    def script_name(self) -> str:
        return os.path.basename(self.file)


class UnifiedReviewInterface:
    """Unified interface for reviewing debug sessions with audio and web support."""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.store = LineReportStore(self.db_path)
        self.tts: Optional[AudioTTS] = None
        self.explorer: Optional[NestedValueExplorer] = None
        self.current_session: Optional[SessionInfo] = None
        self.current_speed = "medium"
    
    def open_db(self) -> sqlite3.Connection:
        """Open database connection."""
        return sqlite3.connect(self.db_path)
    
    def fetch_sessions(self, conn: sqlite3.Connection, offset: int = 0, limit: int = 10) -> List[SessionInfo]:
        """Fetch sessions from database."""
        cur = conn.cursor()
        cur.execute("""
            SELECT session_id, file, start_time, end_time, 
                   total_lines_executed, successful_lines, lines_with_errors
            FROM session_summaries
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        
        rows = cur.fetchall()
        return [
            SessionInfo(
                session_id=r[0],
                file=r[1], 
                start_time=r[2],
                end_time=r[3],
                total_lines=r[4] or 0,
                successful_lines=r[5] or 0,
                error_lines=r[6] or 0
            )
            for r in rows
        ]
    
    def count_sessions(self, conn: sqlite3.Connection) -> int:
        """Count total sessions."""
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM session_summaries")
        row = cur.fetchone()
        return int(row[0] if row and row[0] is not None else 0)
    
    def iter_line_reports(self, conn: sqlite3.Connection, session_id: str) -> Iterable[Dict[str, Any]]:
        """Iterate through line reports for a session."""
        cur = conn.cursor()
        cur.execute("""
            SELECT id, file, line_number, code, timestamp, variables, variables_delta, 
                   status, error_message, error_type, stack_depth, thread_id
            FROM line_reports
            WHERE session_id=?
            ORDER BY id ASC
        """, (session_id,))
        
        columns = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(columns, row))
            # Parse JSON fields
            try:
                rec["variables"] = json.loads(rec.get("variables") or "{}")
            except:
                rec["variables"] = {}
            try:
                rec["variables_delta"] = json.loads(rec.get("variables_delta") or "{}")
            except:
                rec["variables_delta"] = {}
            
            # Parse DAP variables to clean format
            rec["variables_parsed"] = parse_dap_variables(rec["variables"])
            rec["variables_delta_parsed"] = parse_dap_variables(rec["variables_delta"])
            
            yield rec
    
    def get_function_context(self, file_path: str, line: int, session_id: str) -> Dict[str, Any]:
        """Get function context for a line, using snapshots or git history if available."""
        source = None
        
        try:
            self.store.open()
            conn = self.store.conn
            
            # Get git provenance for the session
            cur = conn.cursor()
            cur.execute("SELECT git_root, git_commit, git_dirty FROM session_summaries WHERE session_id=?", 
                       (session_id,))
            row = cur.fetchone()
            repo_root = row[0] if row else None
            repo_commit = row[1] if row else None
            repo_dirty = int(row[2]) if row and row[2] is not None else 0
            
            # When dirty, prefer DB snapshot
            if repo_dirty != 0:
                source = self.store.get_file_snapshot(session_id, file_path)
            
            # When clean, try to get from git commit
            elif repo_root and repo_commit and os.path.abspath(file_path).startswith(os.path.abspath(repo_root)):
                source = self._get_committed_source(file_path, repo_root, repo_commit)
            
            # If no snapshot/git, try current snapshot as fallback
            if source is None:
                source = self.store.get_file_snapshot(session_id, file_path)
            
            self.store.close()
        except:
            pass
        
        # Use common extraction with source (from snapshot/git) or fallback to current file
        return extract_function_context(file_path, line, source)
    
    def _get_committed_source(self, file_path: str, repo_root: str, commit: str) -> Optional[str]:
        """Get source code from a specific git commit."""
        try:
            import subprocess
            rel_path = os.path.relpath(file_path, repo_root)
            result = subprocess.run(
                ["git", "show", f"{commit}:{rel_path}"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout
        except:
            pass
        return None
    
    def format_scope_brief(self, variables: Dict[str, Any], max_pairs: int = 10) -> str:
        """Format a brief summary of variables for speech."""
        # Use parsed variables if available
        if "_parsed" in variables:
            variables = variables["_parsed"]
        
        scopes_priority = ["Locals", "locals", "Local", "Globals", "globals"]
        chosen: Optional[Dict[str, Any]] = None
        
        for name in scopes_priority:
            if isinstance(variables.get(name), dict):
                chosen = variables[name]
                break
        
        if chosen is None:
            for v in variables.values():
                if isinstance(v, dict):
                    chosen = v
                    break
        
        if not isinstance(chosen, dict):
            return "no scope"
        
        parts: List[str] = []
        for k, v in list(chosen.items())[:max_pairs]:
            # Use common summarization
            summary = summarize_value(v, 40)
            parts.append(f"{k}={summary}")
        
        return ", ".join(parts) if parts else "empty"
    
    def playback_audio_session(
        self,
        conn: sqlite3.Connection,
        session: SessionInfo,
        mode: str = "manual",
        delay_s: float = 0.4,
        speak_scope: bool = True,
        recite_function: str = "off"
    ) -> None:
        """Play back a session with audio narration."""
        if not self.tts:
            self.tts = AudioTTS(verbose=True)
        
        if not self.explorer:
            self.explorer = NestedValueExplorer(self.tts, verbose=self.tts.verbose)
        
        self.tts.set_speed(self.current_speed)
        self.tts.speak(f"Playing session {session.script_name}")
        
        while self.tts.is_speaking():
            time.sleep(0.05)
        
        any_lines = False
        for rec in self.iter_line_reports(conn, session.session_id):
            any_lines = True
            code = rec.get("code") or ""
            line_no = rec.get("line_number")
            line_id = rec.get("id")
            delta = rec.get("variables_delta_parsed") or {}
            status = rec.get("status") or "success"
            err = rec.get("error_message") if status == "error" else None
            
            # Announce line
            prefix = f"Line {line_no}. "
            if code.strip():
                text = prefix + code.strip()
            else:
                text = prefix + "no code captured"
            self.tts.speak(text, interrupt=True, is_code=True)
            
            while self.tts.is_speaking():
                time.sleep(0.05)
            
            # Speak scope if requested
            if speak_scope:
                vars_obj = rec.get("variables_parsed") or {}
                brief = self.format_scope_brief(vars_obj)
                if brief:
                    self.tts.speak(f"Scope: {brief}")
                    while self.tts.is_speaking():
                        time.sleep(0.05)
            
            # Announce changes with smart summarization
            summary = summarize_delta(delta)
            if summary and summary != "no changes":
                self.tts.speak(f"Changes: {summary}")
            else:
                self.tts.speak("No changes")
            
            while self.tts.is_speaking():
                time.sleep(0.05)
            
            # Handle errors
            if status == "error" and err:
                self.tts.speak(f"Error: {err}")
                while self.tts.is_speaking():
                    time.sleep(0.05)
            
            # Function context
            if recite_function in {"sig", "full"}:
                func_ctx = self.get_function_context(rec.get("file") or "", int(line_no or 0), session.session_id)
                if func_ctx["sig"]:
                    self.tts.speak(f"Function: {func_ctx['sig']}")
                    while self.tts.is_speaking():
                        time.sleep(0.05)
                if recite_function == "full" and func_ctx["body"]:
                    self.tts.speak(f"Body: {func_ctx['body']}")
                    while self.tts.is_speaking():
                        time.sleep(0.05)
            
            # Handle manual mode interaction
            if mode == "manual":
                while True:
                    cmd = input("Note or 'next' (n), 'explore' (e), 'variables' (w), 'speed' (s), 'quit' (q): ").strip().lower()
                    
                    if cmd in ("next", "n"):
                        break
                    elif cmd in ("quit", "q"):
                        self.tts.speak("Stopping playback", interrupt=True)
                        return
                    elif cmd == "speed" or cmd == "s":
                        # Cycle speed
                        speeds = ["slow", "medium", "fast"]
                        idx = speeds.index(self.current_speed)
                        self.current_speed = speeds[(idx + 1) % 3]
                        self.tts.set_speed(self.current_speed)
                        self.tts.speak(f"Speed set to {self.current_speed}")
                        while self.tts.is_speaking():
                            time.sleep(0.05)
                    elif cmd in ("explore", "e"):
                        # Explore changes
                        if delta:
                            self.tts.speak("Exploring changes")
                            while self.tts.is_speaking():
                                time.sleep(0.05)
                            for key, value in delta.items():
                                if not key.startswith('_'):
                                    self.explorer.explore_interactive(key, value)
                        else:
                            self.tts.speak("No changes to explore")
                            while self.tts.is_speaking():
                                time.sleep(0.05)
                    elif cmd in ("variables", "w"):
                        # Explore all variables
                        vars_obj = rec.get("variables_parsed") or {}
                        if vars_obj:
                            self.tts.speak("Exploring variables")
                            while self.tts.is_speaking():
                                time.sleep(0.05)
                            for scope_name, scope_vars in vars_obj.items():
                                if not scope_name.startswith('_') and isinstance(scope_vars, dict):
                                    self.tts.speak(f"Scope: {scope_name}")
                                    while self.tts.is_speaking():
                                        time.sleep(0.05)
                                    for var_name, var_value in scope_vars.items():
                                        if not var_name.startswith('_'):
                                            self.explorer.explore_interactive(var_name, var_value)
                        else:
                            self.tts.speak("No variables to explore")
                            while self.tts.is_speaking():
                                time.sleep(0.05)
            else:
                # Auto mode
                time.sleep(max(0.05, delay_s))
        
        if not any_lines:
            self.tts.speak("No line reports found for this session")
            while self.tts.is_speaking():
                time.sleep(0.05)
        
        self.tts.speak("End of session")
    
    def run_audio_interface(
        self,
        voice: Optional[str] = None,
        rate_wpm: int = 210,
        delay_s: float = 0.4,
        verbose: bool = False,
        mode: str = "manual"
    ) -> int:
        """Run the audio review interface."""
        self.tts = AudioTTS(voice=voice, rate_wpm=rate_wpm, verbose=verbose)
        self.explorer = NestedValueExplorer(self.tts, verbose=verbose)
        
        def _sigint(_sig, _frm):
            try:
                self.tts.stop()
            finally:
                sys.exit(0)
        
        signal.signal(signal.SIGINT, _sigint)
        
        if not os.path.exists(self.db_path):
            print(f"Database not found: {self.db_path}")
            return 2
        
        with self.open_db() as conn:
            # Session selection with audio
            total = self.count_sessions(conn)
            if total == 0:
                self.tts.speak("There are no sessions in the database")
                return 0
            
            self.tts.speak(f"There are {total} sessions. Enter a number to select.")
            
            # List sessions
            sessions = self.fetch_sessions(conn, 0, 10)
            for idx, session in enumerate(sessions):
                self.tts.speak(f"{idx}. {session.script_name}")
                while self.tts.is_speaking():
                    time.sleep(0.05)
            
            # Get selection
            selection = input("Enter session number: ").strip()
            if selection.isdigit():
                idx = int(selection)
                if 0 <= idx < len(sessions):
                    self.current_session = sessions[idx]
                    self.playback_audio_session(conn, self.current_session, mode=mode, delay_s=delay_s)
        
        if self.tts:
            self.tts.stop()
        return 0


def create_unified_app(db_path: Optional[str] = None) -> Flask:
    """Create unified Flask app with enhanced features."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    interface = UnifiedReviewInterface(db_path)
    
    @app.teardown_request
    def _close_db(_exc: Optional[BaseException]) -> None:
        try:
            interface.store.close()
        except:
            pass
    
    @app.route("/")
    def index():
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        cur = conn.cursor()
        cur.execute("""
            SELECT session_id, file, language, start_time, end_time, 
                   total_lines_executed, successful_lines, lines_with_errors, 
                   total_crashes, updated_at 
            FROM session_summaries 
            ORDER BY updated_at DESC
        """)
        
        rows = cur.fetchall()
        sessions = []
        for r in rows:
            sessions.append({
                "session_id": r[0],
                "file": r[1],
                "language": r[2],
                "start_time": r[3],
                "end_time": r[4],
                "total_lines_executed": r[5],
                "successful_lines": r[6],
                "lines_with_errors": r[7],
                "total_crashes": r[8],
                "updated_at": r[9]
            })
        
        return render_template("index.html", sessions=sessions)
    
    @app.route("/sessions")
    def sessions():
        """Enhanced session listing with pagination and filtering."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        page = int(request.args.get("page", 1))
        per_page = 20
        search = request.args.get("search", "")
        
        cur = conn.cursor()
        
        # Build query with optional search
        base_query = """
            FROM session_summaries 
            WHERE 1=1
        """
        params = []
        
        if search:
            base_query += " AND (session_id LIKE ? OR file LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        
        # Count total
        count_query = "SELECT COUNT(*) " + base_query
        cur.execute(count_query, params)
        total = cur.fetchone()[0]
        
        # Get paginated results
        query = f"""
            SELECT session_id, file, language, start_time, end_time, 
                   total_lines_executed, successful_lines, lines_with_errors, 
                   total_crashes, updated_at 
            {base_query}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([per_page, (page - 1) * per_page])
        cur.execute(query, params)
        
        rows = cur.fetchall()
        sessions = []
        for r in rows:
            sessions.append({
                "session_id": r[0],
                "file": r[1],
                "language": r[2],
                "start_time": r[3],
                "end_time": r[4],
                "total_lines_executed": r[5],
                "successful_lines": r[6],
                "lines_with_errors": r[7],
                "total_crashes": r[8],
                "updated_at": r[9]
            })
        
        total_pages = (total + per_page - 1) // per_page
        
        return render_template("sessions.html", 
                             sessions=sessions, 
                             page=page, 
                             total_pages=total_pages,
                             search=search,
                             total=total)
    
    @app.route("/session/<session_id>")
    def session_detail(session_id: str):
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        # Get session summary
        cur = conn.cursor()
        cur.execute("SELECT * FROM session_summaries WHERE session_id=?", (session_id,))
        summary_row = cur.fetchone()
        if not summary_row:
            return render_template("session_detail.html", session=None, reports=[])
        
        columns = [d[0] for d in cur.description]
        summary = {columns[i]: summary_row[i] for i in range(len(columns))}
        
        # Get line reports with enhanced data
        reports = []
        for rec in interface.iter_line_reports(conn, session_id):
            # Add function context
            func_ctx = interface.get_function_context(
                rec.get("file") or "", 
                rec.get("line_number") or 0,
                session_id
            )
            rec["function_name"] = func_ctx["name"]
            rec["function_sig"] = func_ctx["sig"]
            
            # Add variable summaries
            rec["variables_summary"] = interface.format_scope_brief(rec["variables_parsed"])
            rec["changes_summary"] = summarize_delta(rec["variables_delta_parsed"])
            
            reports.append(rec)
        
        return render_template("session_detail.html", session=summary, reports=reports)
    
    @app.route("/api/session/<session_id>/explore")
    def explore_session(session_id: str):
        """API endpoint for interactive exploration."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        line_id = request.args.get("line_id")
        if not line_id:
            return jsonify({"error": "line_id required"}), 400
        
        # Get the specific line report
        cur = conn.cursor()
        cur.execute("""
            SELECT variables, variables_delta 
            FROM line_reports 
            WHERE session_id=? AND id=?
        """, (session_id, line_id))
        
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        variables = json.loads(row[0] or "{}")
        delta = json.loads(row[1] or "{}")
        
        # Parse to clean format
        variables_parsed = parse_dap_variables(variables)
        delta_parsed = parse_dap_variables(delta)
        
        return jsonify({
            "variables": variables_parsed,
            "changes": delta_parsed,
            "variables_summary": interface.format_scope_brief(variables_parsed),
            "changes_summary": summarize_delta(delta_parsed)
        })
    
    @app.route("/api/speed", methods=["POST"])
    def set_speed():
        """API endpoint to set speech speed."""
        speed = request.json.get("speed", "medium")
        if speed not in ["slow", "medium", "fast"]:
            return jsonify({"error": "Invalid speed"}), 400
        
        # Store in session or global state
        interface.current_speed = speed
        return jsonify({"speed": speed})
    
    @app.route("/session/<session_id>/delete", methods=["POST"])
    def delete_session(session_id: str):
        """Delete a single session."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        cur = conn.cursor()
        # Delete line reports first (foreign key constraint)
        cur.execute("DELETE FROM line_reports WHERE session_id = ?", (session_id,))
        # Delete session summary
        cur.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))
        conn.commit()
        
        return redirect(url_for("sessions"))
    
    @app.route("/sessions/delete", methods=["POST"])
    def delete_sessions():
        """Delete multiple sessions."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        session_ids = request.form.getlist("session_ids[]")
        if not session_ids:
            return jsonify({"error": "No sessions selected"}), 400
        
        cur = conn.cursor()
        for session_id in session_ids:
            # Delete line reports first
            cur.execute("DELETE FROM line_reports WHERE session_id = ?", (session_id,))
            # Delete session summary
            cur.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))
        conn.commit()
        
        return jsonify({"deleted": len(session_ids), "ids": session_ids})
    
    @app.route("/launch-manual", methods=["GET", "POST"])
    def launch_manual():
        """Launch a new manual debugging session (stretch goal)."""
        if request.method == "GET":
            return render_template("launch_manual.html")
        
        # Get parameters from form
        script_path = request.form.get("script_path", "")
        script_args = request.form.get("script_args", "")
        breakpoints = request.form.get("breakpoints", "")
        
        if not script_path:
            return jsonify({"error": "Script path is required"}), 400
        
        # Parse breakpoints (format: file:line,file:line,...)
        bp_list = []
        if breakpoints:
            for bp in breakpoints.split(","):
                bp = bp.strip()
                if ":" in bp:
                    bp_list.append(bp)
        
        # Build command to launch debugger
        import subprocess
        import shlex
        
        cmd = ["python", "-m", "autodebugger", "run", "--manual"]
        
        # Add breakpoints
        for bp in bp_list:
            cmd.extend(["-b", bp])
        
        # Add script and its arguments
        cmd.append(script_path)
        if script_args:
            cmd.extend(shlex.split(script_args))
        
        # Launch in background
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Get the session ID from the output (if available)
            # This would require parsing the debugger output
            session_id = f"manual_{int(time.time())}"
            
            return jsonify({
                "status": "launched",
                "session_id": session_id,
                "command": " ".join(cmd),
                "pid": proc.pid
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route("/api/sessions/active")
    def active_sessions():
        """Get list of currently active debugging sessions."""
        # This would check for running debugger processes
        # For now, return empty list
        return jsonify({"sessions": []})
    
    @app.route("/api/session/<session_id>/line/<line_id>/speak", methods=["POST"])
    def speak_line(session_id: str, line_id: str):
        """Speak a specific line's content and changes."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        # Initialize TTS if needed
        if not interface.tts:
            interface.tts = AudioTTS(verbose=True)
        
        cur = conn.cursor()
        cur.execute("""
            SELECT code, line_number, variables, variables_delta, status, error_message
            FROM line_reports 
            WHERE session_id=? AND id=?
        """, (session_id, line_id))
        
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        code, line_no, vars_json, delta_json, status, error = row
        
        # Parse variables
        variables = json.loads(vars_json or "{}")
        delta = json.loads(delta_json or "{}")
        variables_parsed = parse_dap_variables(variables)
        delta_parsed = parse_dap_variables(delta)
        
        # Speak the line
        text = f"Line {line_no}. {code.strip() if code else 'no code captured'}"
        interface.tts.speak(text, interrupt=True, is_code=True)
        
        # Optionally speak scope
        if request.json.get("speak_scope", True):
            brief = interface.format_scope_brief(variables_parsed)
            if brief:
                interface.tts.speak(f"Scope: {brief}")
        
        # Speak changes
        summary = summarize_delta(delta_parsed)
        if summary and summary != "no changes":
            interface.tts.speak(f"Changes: {summary}")
        
        # Speak error if any
        if status == "error" and error:
            interface.tts.speak(f"Error: {error}")
        
        return jsonify({"status": "speaking", "text": text})
    
    @app.route("/api/session/<session_id>/line/<line_id>/function", methods=["GET"])
    def get_function_context_api(session_id: str, line_id: str):
        """Get function context for a line."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        cur = conn.cursor()
        cur.execute("SELECT file, line_number FROM line_reports WHERE session_id=? AND id=?", 
                   (session_id, line_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        file_path, line_no = row
        func_ctx = interface.get_function_context(file_path, line_no, session_id)
        
        # Optionally speak it
        if request.args.get("speak") == "true":
            if not interface.tts:
                interface.tts = AudioTTS(verbose=True)
            
            if func_ctx["sig"]:
                interface.tts.speak(f"Function: {func_ctx['sig']}", interrupt=True)
            if request.args.get("full") == "true" and func_ctx["body"]:
                interface.tts.speak(f"Body: {func_ctx['body']}")
        
        return jsonify(func_ctx)
    
    @app.route("/api/session/<session_id>/line/<line_id>/blocks", methods=["GET", "POST"])
    def explore_function_blocks(session_id: str, line_id: str):
        """Explore function blocks with pagination and numbered selection."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        cur = conn.cursor()
        cur.execute("SELECT file, line_number FROM line_reports WHERE session_id=? AND id=?",
                   (session_id, line_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        file_path, line_no = row
        func_ctx = interface.get_function_context(file_path, line_no, session_id)
        
        if not func_ctx.get("body"):
            return jsonify({"error": "No function body available"}), 404
        
        # Create block explorer
        if not hasattr(interface, 'block_explorer') or request.method == "GET":
            interface.block_explorer = FunctionBlockExplorer(func_ctx["body"], interface.tts)
        
        explorer = interface.block_explorer
        
        if request.method == "GET":
            # Get current page info
            page_blocks = explorer.get_current_page_blocks()
            blocks_info = []
            for idx, block in page_blocks:
                # Get first line as preview
                lines = block.split('\n')
                preview = lines[0].strip() if lines else ""
                if len(preview) > 60:
                    preview = preview[:57] + "..."
                blocks_info.append({
                    "index": idx,
                    "preview": preview,
                    "full_text": block
                })
            
            # Announce page info if requested
            if request.args.get("announce") == "true":
                if not interface.tts:
                    interface.tts = AudioTTS(verbose=True)
                announcement = explorer.announce_page_info()
                interface.tts.speak(announcement, interrupt=True)
            
            return jsonify({
                "blocks": blocks_info,
                "current_page": explorer.current_page,
                "total_pages": explorer.total_pages,
                "total_blocks": len(explorer.blocks),
                "page_info": explorer.announce_page_info()
            })
        
        elif request.method == "POST":
            # Handle actions: select block, next page, previous page
            action = request.json.get("action")
            
            if action == "select":
                index = request.json.get("index", 0)
                block = explorer.select_block(index)
                if block:
                    if not interface.tts:
                        interface.tts = AudioTTS(verbose=True)
                    # Speak the block
                    interface.tts.speak(f"Block {index}: {block}", interrupt=True)
                    return jsonify({"success": True, "block": block})
                else:
                    return jsonify({"error": "Invalid block index"}), 400
            
            elif action == "next":
                if explorer.next_page():
                    # Get new page info and announce
                    if not interface.tts:
                        interface.tts = AudioTTS(verbose=True)
                    announcement = explorer.announce_page_info()
                    interface.tts.speak(announcement, interrupt=True)
                    
                    # Return updated page info
                    page_blocks = explorer.get_current_page_blocks()
                    blocks_info = []
                    for idx, block in page_blocks:
                        lines = block.split('\n')
                        preview = lines[0].strip() if lines else ""
                        if len(preview) > 60:
                            preview = preview[:57] + "..."
                        blocks_info.append({
                            "index": idx,
                            "preview": preview,
                            "full_text": block
                        })
                    
                    return jsonify({
                        "blocks": blocks_info,
                        "current_page": explorer.current_page,
                        "total_pages": explorer.total_pages,
                        "page_info": explorer.announce_page_info()
                    })
                else:
                    if not interface.tts:
                        interface.tts = AudioTTS(verbose=True)
                    interface.tts.speak("Already at last page", interrupt=True)
                    return jsonify({"error": "Already at last page"}), 400
            
            elif action == "previous":
                if explorer.previous_page():
                    # Get new page info and announce
                    if not interface.tts:
                        interface.tts = AudioTTS(verbose=True)
                    announcement = explorer.announce_page_info()
                    interface.tts.speak(announcement, interrupt=True)
                    
                    # Return updated page info
                    page_blocks = explorer.get_current_page_blocks()
                    blocks_info = []
                    for idx, block in page_blocks:
                        lines = block.split('\n')
                        preview = lines[0].strip() if lines else ""
                        if len(preview) > 60:
                            preview = preview[:57] + "..."
                        blocks_info.append({
                            "index": idx,
                            "preview": preview,
                            "full_text": block
                        })
                    
                    return jsonify({
                        "blocks": blocks_info,
                        "current_page": explorer.current_page,
                        "total_pages": explorer.total_pages,
                        "page_info": explorer.announce_page_info()
                    })
                else:
                    if not interface.tts:
                        interface.tts = AudioTTS(verbose=True)
                    interface.tts.speak("Already at first page", interrupt=True)
                    return jsonify({"error": "Already at first page"}), 400
            
            else:
                return jsonify({"error": "Invalid action"}), 400
    
    @app.route("/api/session/<session_id>/line/<line_id>/variables", methods=["GET"])
    def get_variables_detailed(session_id: str, line_id: str):
        """Get detailed variables for exploration."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        cur = conn.cursor()
        cur.execute("SELECT variables, variables_delta FROM line_reports WHERE session_id=? AND id=?",
                   (session_id, line_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        variables = json.loads(row[0] or "{}")
        delta = json.loads(row[1] or "{}")
        
        variables_parsed = parse_dap_variables(variables)
        delta_parsed = parse_dap_variables(delta)
        
        # Optionally speak variables
        if request.args.get("speak") == "true":
            if not interface.tts:
                interface.tts = AudioTTS(verbose=True)
            
            if request.args.get("mode") == "summary":
                brief = interface.format_scope_brief(variables_parsed)
                interface.tts.speak(f"Variables: {brief}", interrupt=True)
            elif request.args.get("mode") == "changes":
                summary = summarize_delta(delta_parsed)
                interface.tts.speak(f"Changes: {summary}", interrupt=True)
        
        return jsonify({
            "variables": variables_parsed,
            "changes": delta_parsed,
            "summary": interface.format_scope_brief(variables_parsed),
            "changes_summary": summarize_delta(delta_parsed)
        })
    
    @app.route("/api/session/<session_id>/line/<line_id>/explore", methods=["POST"])
    def explore_variable(session_id: str, line_id: str):
        """Explore a variable using the same logic as manual mode - read complete structure."""
        interface.store.open()
        conn = interface.store.conn
        assert conn is not None
        
        var_name = request.json.get("variable")
        var_path = request.json.get("path", [])  # For scope navigation
        mode = request.json.get("mode", "summary")  # "summary" or "detailed"
        
        cur = conn.cursor()
        cur.execute("SELECT variables, variables_delta FROM line_reports WHERE session_id=? AND id=?",
                   (session_id, line_id))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Line not found"}), 404
        
        # Determine if we're looking at changes or variables
        is_changes = request.json.get("is_changes", False)
        
        if is_changes:
            # Get changes
            delta = json.loads(row[1] or "{}")
            target = parse_dap_variables(delta)
        else:
            # Get variables
            variables = json.loads(row[0] or "{}")
            target = parse_dap_variables(variables)
        
        # Navigate to the scope if path provided
        for key in var_path:
            if isinstance(target, dict):
                target = target.get(key, {})
            else:
                return jsonify({"error": "Invalid path"}), 400
        
        if var_name and isinstance(target, dict):
            value = target.get(var_name)
            
            # Initialize TTS and explorer if needed
            if not interface.tts:
                interface.tts = AudioTTS(verbose=True)
            
            if not interface.explorer:
                interface.explorer = NestedValueExplorer(interface.tts, verbose=True)
            
            # Speak the value using the same method as manual mode
            if request.json.get("speak", True):
                if mode == "detailed":
                    # Use the same read_complete_structure method from manual mode
                    interface.explorer.read_complete_structure(var_name, value)
                else:
                    # Simple summary
                    summary = summarize_value(value) if isinstance(value, (dict, list)) else str(value)
                    interface.tts.speak(f"{var_name}: {summary}", interrupt=True)
            
            return jsonify({
                "name": var_name,
                "value": value,
                "summary": summarize_value(value),
                "type": type(value).__name__ if value is not None else "None",
                "mode": mode
            })
        
        return jsonify({"error": "Variable not found"}), 404
    
    @app.route("/api/tts/stop", methods=["POST"])
    def stop_tts():
        """Stop current TTS playback."""
        if interface.tts:
            interface.tts.stop()
        return jsonify({"status": "stopped"})
    
    @app.route("/api/tts/speak", methods=["POST"])
    def speak_text():
        """Speak arbitrary text."""
        text = request.json.get("text", "")
        interrupt = request.json.get("interrupt", True)
        
        if not interface.tts:
            interface.tts = AudioTTS(verbose=True)
        
        if text:
            interface.tts.speak(text, interrupt=interrupt)
        
        return jsonify({"status": "speaking", "text": text})
    
    @app.route("/api/tts/speed", methods=["POST"])
    def set_tts_speed():
        """Set TTS speed."""
        speed = request.json.get("speed", "medium")
        if speed not in ["slow", "medium", "fast"]:
            return jsonify({"error": "Invalid speed"}), 400
        
        interface.current_speed = speed
        if interface.tts:
            interface.tts.set_speed(speed)
        
        return jsonify({"speed": speed})
    
    @app.route("/api/session/<session_id>/playback", methods=["POST"])
    def start_playback(session_id: str):
        """Start automated playback of a session."""
        mode = request.json.get("mode", "auto")
        delay = request.json.get("delay", 0.4)
        speak_scope = request.json.get("speak_scope", True)
        
        # This would need to be async or use threading for real-time control
        # For now, return a status
        return jsonify({
            "status": "playback_started",
            "session_id": session_id,
            "mode": mode
        })
    
    return app