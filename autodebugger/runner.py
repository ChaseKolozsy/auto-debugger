from __future__ import annotations

import os
import select
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import debugpy

# Re-enable enhanced control to fix the issues
USE_ENHANCED = True
if USE_ENHANCED:
    try:
        from .enhanced_control import HttpStepController, prompt_for_action
    except ImportError:
        from .control import HttpStepController, prompt_for_action
else:
    from .control import HttpStepController, prompt_for_action
from .dap_client import DapClient
from .db import LineReport, LineReportStore, SessionSummary
from .audio_ui import MacSayTTS, summarize_delta


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutoDebugger:
    def __init__(self, python_exe: Optional[str] = None, db_path: Optional[str] = None) -> None:
        self.python_exe = python_exe or sys.executable
        self.db = LineReportStore(db_path)
        self.session_id = str(uuid.uuid4())
        self.adapter_host = "127.0.0.1"
        self.adapter_port = self._find_free_port()
        self._adapter_proc: Optional[subprocess.Popen] = None
        self.client: Optional[DapClient] = None
        self._controller: Optional[HttpStepController] = None
        self._tts: Optional[MacSayTTS] = None
        self._abort_requested: bool = False

    def _find_free_port(self) -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def _start_adapter(self, log_dir: Optional[str] = None) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if log_dir:
            env["DEBUGPY_LOG_DIR"] = log_dir
            env["DEBUGPY_LOG_LEVEL"] = "debug"
        cmd = [self.python_exe, "-m", "debugpy.adapter", "--host", self.adapter_host, "--port", str(self.adapter_port)]
        self._adapter_proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # pump stderr to aid troubleshooting
        if self._adapter_proc.stderr is not None:
            def _pump():
                try:
                    for line in self._adapter_proc.stderr:  # type: ignore[assignment]
                        sys.stderr.write("[debugpy.adapter] " + line.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            t = threading.Thread(target=_pump, daemon=True)
            t.start()

    def _stop_adapter(self) -> None:
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        if self._adapter_proc is not None:
            try:
                self._adapter_proc.terminate()
            except Exception:
                pass
            try:
                self._adapter_proc.wait(timeout=3)
            except Exception:
                try:
                    self._adapter_proc.kill()
                except Exception:
                    pass
            self._adapter_proc = None

    def run(
        self,
        script_path: str,
        args: Optional[List[str]] = None,
        just_my_code: bool = True,
        stop_on_entry: bool = True,
        manual: bool = False,
        manual_from: Optional[str] = None,
        manual_web: bool = False,
        manual_audio: bool = False,
        manual_voice: Optional[str] = None,
        manual_rate_wpm: int = 210,
    ) -> str:
        script_abs = os.path.abspath(script_path)
        
        # Parse manual_from trigger
        manual_trigger_file: Optional[str] = None
        manual_trigger_line: int = 0
        manual_trigger_activated = False  # Track if we've already activated from trigger
        if manual_from:
            parts = manual_from.rsplit(':', 1)
            if len(parts) == 2 and parts[1].isdigit():
                manual_trigger_file = os.path.abspath(parts[0])
                manual_trigger_line = int(parts[1])
        
        # Setup manual control interfaces
        manual_mode_active = manual and not manual_from  # Start in manual if no trigger
        
        # Initialize TTS first if audio is enabled
        if manual_audio:
            self._tts = MacSayTTS(voice=manual_voice, rate_wpm=manual_rate_wpm, verbose=False)
        
        if manual_web:
            # Pass TTS instance to controller if available
            self._controller = HttpStepController(tts=self._tts) if self._tts else HttpStepController()
            self._controller.start()
            # Set initial audio state if controller supports it
            if hasattr(self._controller, 'set_audio_state'):
                self._controller.set_audio_state(enabled=manual_audio, available=manual_audio)
        # Detect git provenance
        git_root: Optional[str] = None
        git_commit: Optional[str] = None
        git_dirty: int = 0
        try:
            import subprocess as _sp
            # Find repo root containing the script
            probe = _sp.run(["git", "rev-parse", "--show-toplevel"], cwd=os.path.dirname(script_abs), capture_output=True, text=True)
            if probe.returncode == 0:
                git_root = probe.stdout.strip()
                head = _sp.run(["git", "rev-parse", "HEAD"], cwd=git_root, capture_output=True, text=True)
                if head.returncode == 0:
                    git_commit = head.stdout.strip()
                status = _sp.run(["git", "status", "--porcelain"], cwd=git_root, capture_output=True, text=True)
                if status.returncode == 0 and status.stdout.strip():
                    git_dirty = 1
        except Exception:
            pass

        self.db.open()
        self.db.create_session(
            SessionSummary(
                session_id=self.session_id,
                file=script_abs,
                language="python",
                start_time=utc_now_iso(),
                git_root=git_root,
                git_commit=git_commit,
                git_dirty=git_dirty,
            )
        )

        self._start_adapter()
        try:
            # Connect DAP client with retries while adapter starts
            client = DapClient(self.adapter_host, self.adapter_port, timeout=10)
            start = time.time()
            while True:
                try:
                    client.connect()
                    break
                except ConnectionRefusedError:
                    if time.time() - start > 15.0:
                        raise
                    time.sleep(0.1)
            self.client = client

            # Initialize
            init_resp = client.request("initialize", {
                "clientID": "autodebugger",
                "adapterID": "python",
                "pathFormat": "path",
                "linesStartAt1": True,
                "columnsStartAt1": True,
                "locale": "en-US",
                "supportsVariableType": True,
                "supportsVariablePaging": True,
            }, wait=15.0)
            # Choose best launch strategy: module for packages, program for single files
            script_dir = os.path.dirname(script_abs)
            parent_dir = os.path.dirname(script_dir)
            module_name: Optional[str] = None
            if os.path.exists(os.path.join(script_dir, "__init__.py")):
                # Derive module path relative to parent on sys.path
                pkg = os.path.basename(script_dir)
                base = os.path.basename(parent_dir)
                # If parent is tests (e.g., tests/calculator), import as calculator.main
                if base == "tests":
                    module_name = f"{pkg}.main"
                else:
                    module_name = f"{pkg}.main"

            env_vars = {
                "PYTHONPATH": os.pathsep.join(filter(None, [os.environ.get("PYTHONPATH"), parent_dir]))
            }

            # Send launch but do not block waiting for response yet
            # Don't stop on entry if using --manual-from (we want to run to breakpoint)
            effective_stop_on_entry = stop_on_entry and not manual_from
            
            launch_args = {
                "name": "Python: AutoDebug",
                "type": "python",
                "request": "launch",
                "console": "internalConsole",
                "cwd": parent_dir or os.getcwd(),
                "justMyCode": just_my_code,
                "stopOnEntry": effective_stop_on_entry,
                "showReturnValue": True,
                "redirectOutput": True,
                "env": env_vars,
                "args": args or [],
            }
            if module_name:
                launch_args.update({"module": module_name})
            else:
                launch_args.update({"program": script_abs})

            launch_seq = client.send_request("launch", launch_args)
            # Wait for 'initialized' event from adapter before sending breakpoints/configuration
            start_wait = time.time()
            initialized_seen = False
            while time.time() - start_wait < 15.0 and not initialized_seen:
                for ev in client.pop_events():
                    if ev.event == "initialized":
                        initialized_seen = True
                        break
                if not initialized_seen:
                    time.sleep(0.05)

            # Set default exception breakpoints (common filters)
            try:
                client.request("setExceptionBreakpoints", {"filters": ["uncaught"], "filterOptions": []}, wait=10.0)
            except Exception:
                pass
            # Set breakpoints to ensure we stop at the right place
            try:
                breakpoints: List[Dict[str, int]] = []
                
                if manual_from and manual_trigger_file and manual_trigger_line:
                    # Set a breakpoint at the trigger line for --manual-from
                    breakpoints = [{"line": manual_trigger_line}]
                    client.request("setBreakpoints", {
                        "source": {"path": manual_trigger_file},
                        "breakpoints": breakpoints
                    }, wait=10.0)
                elif manual_mode_active or stop_on_entry:
                    # Set dense breakpoints on all likely executable lines
                    try:
                        with open(script_abs, "r", encoding="utf-8") as _sf:
                            _lines = _sf.readlines()
                        for idx, text in enumerate(_lines, start=1):
                            stripped = text.strip()
                            # Skip empty lines and comments
                            if not stripped or stripped.startswith('#'):
                                continue
                            # Skip docstrings and multiline strings
                            if '"""' in stripped or "'''" in stripped:
                                continue
                            # Add breakpoint for likely executable lines
                            if any(kw in text for kw in ['=', 'if ', 'for ', 'while ', 'def ', 'class ', 'return', 'print', 'import']):
                                breakpoints.append({"line": idx})
                            # Also add for lines that don't start with whitespace (top-level)
                            elif not text.startswith(' ') and not text.startswith('\t'):
                                breakpoints.append({"line": idx})
                    except Exception:
                        # Fallback to line 1
                        breakpoints = [{"line": 1}]
                    
                    client.request("setBreakpoints", {
                        "source": {"path": script_abs},
                        "breakpoints": breakpoints
                    }, wait=10.0)
            except Exception:
                pass
            # Send configurationDone to start execution
            client.request("configurationDone", {}, wait=15.0)
            
            # Force initial pause in manual mode (only if starting in manual, not waiting for trigger)
            if manual_mode_active:
                try:
                    # Get threads and pause them
                    thr = client.request("threads", {}, wait=5.0)
                    tids = [int(t.get("id")) for t in thr.body.get("threads", [])] if thr.body else []
                    if not tids:
                        # Give debuggee time to start
                        time.sleep(0.05)
                        thr2 = client.request("threads", {}, wait=5.0)
                        tids = [int(t.get("id")) for t in thr2.body.get("threads", [])] if thr2.body else []
                    for tid in tids:
                        try:
                            client.request("pause", {"threadId": tid}, wait=2.0)
                        except Exception:
                            pass
                except Exception:
                    pass
            # Now wait for launch response (non-fatal if it times out quickly)
            try:
                _ = client.wait_response(launch_seq, wait=10.0)
            except TimeoutError:
                pass

            # Event loop: collect stopped events and fetch scopes/variables, emit line reports until terminated
            threads: Dict[int, None] = {}
            running = True
            prev_vars: Dict[str, Any] = {}
            # Snapshot each source file once per session so UI can render exact code for dirty/no-git runs
            snapshotted_files: Set[str] = set()
            
            def _check_for_action(timeout: float = 0.0) -> Optional[str]:
                """Check for user action from web or stdin."""
                action = None
                if self._controller:
                    action = self._controller.wait_for_action(timeout)
                elif manual_mode_active and timeout > 0:
                    # Check stdin with select if available
                    if hasattr(select, 'select'):
                        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                        if rlist:
                            try:
                                response = input().strip().lower()
                                if response == '' or response == 'step':
                                    action = 'step'
                                elif response in ['a', 'auto']:
                                    action = 'auto'
                                elif response in ['c', 'continue']:
                                    action = 'continue'
                                elif response in ['q', 'quit', 'exit']:
                                    action = 'quit'
                            except (EOFError, KeyboardInterrupt):
                                action = 'quit'
                return action
            
            while running:
                # Check for abort even while running
                if self._controller is not None:
                    act = _check_for_action(0.0)
                    if act == 'quit':
                        self._abort_requested = True
                        try:
                            client.request("disconnect", {"terminateDebuggee": True}, wait=2.0)
                        except Exception:
                            pass
                        break
                    elif act == 'auto':
                        manual_mode_active = False
                        if self._controller:
                            self._controller.update_state(mode='auto')
                events = client.pop_events()
                # If nothing arrived, small sleep to avoid spin
                if not events:
                    time.sleep(0.01)
                    continue
                for ev in events:
                    if ev.event == "initialized":
                        # ignore; already launched
                        continue
                    if ev.event == "stopped":
                        thread_id = int(ev.body.get("threadId")) if ev.body else 0
                        reason = ev.body.get("reason") if ev.body else ""
                        
                        # Check if we should activate manual mode
                        if manual_from and not manual_mode_active and not manual_trigger_activated:
                            # Query stack to check location
                            st_check = client.request("stackTrace", {"threadId": thread_id})
                            frames_check = st_check.body.get("stackFrames", []) if st_check.body else []
                            if frames_check:
                                frame_check = frames_check[0]
                                file_check = frame_check.get("source", {}).get("path") or ""
                                line_check = int(frame_check.get("line") or 0)
                                
                                # Normalize paths for comparison
                                if manual_trigger_file:
                                    file_check_abs = os.path.abspath(file_check)
                                    if file_check_abs == manual_trigger_file and line_check >= manual_trigger_line:
                                        manual_mode_active = True
                                        manual_trigger_activated = True  # Mark as activated
                                        if self._controller:
                                            self._controller.update_state(mode='manual')
                                        if self._tts:
                                            self._tts.speak(f"Manual mode activated at line {line_check}", interrupt=True)
                                        print(f"\n[manual] Activated at {os.path.basename(file_check)}:{line_check}\n", flush=True)
                        
                        # Query stack, scopes, variables
                        st = client.request("stackTrace", {"threadId": thread_id})
                        frames = st.body.get("stackFrames", []) if st.body else []
                        if not frames:
                            continue
                        frame = frames[0]
                        file_path = frame.get("source", {}).get("path") or ""
                        line = int(frame.get("line") or 0)

                        # Snapshot the file content the first time we encounter it in this session
                        # This ensures the UI can fetch function details from the exact source that ran,
                        # even when the working tree is dirty or the file changes after execution.
                        if file_path and file_path not in snapshotted_files:
                            try:
                                with open(file_path, "rb") as _f:
                                    _content = _f.read()
                                self.db.add_file_snapshot(self.session_id, file_path, _content)
                                snapshotted_files.add(file_path)
                            except Exception:
                                # Best-effort; continue even if snapshotting fails
                                pass

                        # Grab code line
                        code = ""
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                if 1 <= line <= len(lines):
                                    code = lines[line - 1].rstrip("\n")
                        except Exception:
                            code = ""

                        # For dirty/no-git sessions: snapshot original file content once
                        try:
                            if not git_commit or git_dirty:
                                with open(file_path, "rb") as sf:
                                    content_bytes = sf.read()
                                # Store snapshot best-effort; ignore duplicates
                                self.db.add_file_snapshot(self.session_id, file_path, content_bytes)
                        except Exception:
                            pass

                        # Scopes -> variables
                        scopes = client.request("scopes", {"frameId": frame.get("id")})
                        vars_payload: Dict[str, Any] = {}
                        skip_names = {"special variables", "function variables", "class variables"}
                        for sc in scopes.body.get("scopes", []) if scopes.body else []:
                            scope_name = str(sc.get("name"))
                            vr = sc.get("variablesReference")
                            if not vr:
                                continue
                            vres = client.request("variables", {"variablesReference": vr})
                            var_list = vres.body.get("variables", []) if vres.body else []
                            scope_map: Dict[str, Any] = {}
                            for v in var_list:
                                vname = str(v.get("name"))
                                if vname in skip_names:
                                    continue
                                vvalue = v.get("value")
                                scope_map[vname] = vvalue
                                # Shallow expand user variables if expandable
                                vref = v.get("variablesReference")
                                if isinstance(vref, int) and vref > 0:
                                    try:
                                        child_res = client.request("variables", {"variablesReference": vref})
                                        children = child_res.body.get("variables", []) if child_res.body else []
                                        child_map: Dict[str, Any] = {}
                                        for c in children[:30]:  # limit to first 30
                                            cname = str(c.get("name"))
                                            if cname in skip_names:
                                                continue
                                            child_map[cname] = c.get("value")
                                        if child_map:
                                            scope_map[vname] = {"value": vvalue, "children": child_map}
                                    except Exception:
                                        pass
                            vars_payload[scope_name] = scope_map

                        # Status/error info
                        status = "success"
                        error_message = None
                        error_type = None
                        stack_trace_text = None
                        if reason in {"exception", "error"}:
                            status = "error"
                            # Try exceptionInfo
                            try:
                                einfo = client.request("exceptionInfo", {"threadId": thread_id})
                                if einfo.body:
                                    error_type = einfo.body.get("exceptionId")
                                    details = einfo.body.get("details") or {}
                                    error_message = details.get("message")
                            except Exception:
                                pass
                            # Also try last stack trace
                            try:
                                stre = client.request("stackTrace", {"threadId": thread_id})
                                frames2 = stre.body.get("stackFrames", []) if stre.body else []
                                stack_lines = []
                                for fr in frames2:
                                    sp = fr.get("source", {}).get("path")
                                    sl = fr.get("line")
                                    if sp and sl:
                                        stack_lines.append(f"{sp}:{sl}")
                                stack_trace_text = "\n".join(stack_lines)
                            except Exception:
                                pass

                        # Compute delta vs previous captured vars
                        def compute_delta(curr: Dict[str, Any], prev: Dict[str, Any]) -> Dict[str, Any]:
                            delta: Dict[str, Any] = {}
                            keys = set(curr.keys()) | set(prev.keys())
                            for k in sorted(keys):
                                cv = curr.get(k)
                                pv = prev.get(k)
                                if isinstance(cv, dict) and isinstance(pv, dict):
                                    sub = compute_delta(cv, pv)
                                    if sub:
                                        delta[k] = sub
                                else:
                                    if cv != pv:
                                        delta[k] = cv
                            return delta

                        variables_delta = compute_delta(vars_payload, prev_vars)
                        prev_vars = vars_payload

                        self.db.add_line_report(
                            LineReport(
                                session_id=self.session_id,
                                file=file_path,
                                line_number=line,
                                code=code,
                                timestamp=utc_now_iso(),
                                variables=vars_payload,
                                variables_delta=variables_delta,
                                stack_depth=len(frames),
                                thread_id=thread_id,
                                observations=None,
                                status=status,
                                error_message=error_message,
                                error_type=error_type,
                                stack_trace=stack_trace_text,
                            )
                        )
                        
                        # Handle manual stepping
                        if manual_mode_active:
                            # Update controller state
                            if self._controller:
                                self._controller.update_state(
                                    session_id=self.session_id,
                                    file=file_path,
                                    line=line,
                                    code=code,
                                    variables=vars_payload,
                                    variables_delta=variables_delta,
                                    waiting=True,
                                    mode='manual'
                                )
                            
                            # Speak current line if audio enabled
                            should_speak = self._tts and (
                                not self._controller or 
                                not hasattr(self._controller, 'is_audio_enabled') or 
                                self._controller.is_audio_enabled()
                            )
                            
                            if should_speak:
                                # Track if we're entering a new function
                                if not hasattr(self, '_last_announced_function'):
                                    self._last_announced_function = None
                                
                                current_function = None
                                should_announce_function = False
                                
                                if self._controller and hasattr(self._controller.shared_state, 'get_state'):
                                    state = self._controller.shared_state.get_state()
                                    current_function = state.get('function_name')
                                    panel_open = state.get('function_panel_open', False)
                                    
                                    # Check if this is a new function and panel is open
                                    if current_function != self._last_announced_function:
                                        if current_function and panel_open:
                                            should_announce_function = True
                                        self._last_announced_function = current_function
                                
                                # Build the announcement
                                if should_announce_function:
                                    # Include function context in the announcement
                                    state = self._controller.shared_state.get_state()
                                    func_sig = state.get('function_sig', '')
                                    func_body = state.get('function_body', '')
                                    
                                    announcement = f"Entering function {current_function}. "
                                    if func_sig:
                                        announcement += f"Signature: {func_sig}. "
                                    if func_body:
                                        # Limit body length
                                        if len(func_body) > 150:
                                            func_body = func_body[:150] + "..."
                                        announcement += f"Body: {func_body}. "
                                    announcement += f"Line {line}: {code}"
                                else:
                                    # Just announce the line
                                    announcement = f"Line {line}: {code}"
                                
                                # Speak with interrupt to clear any previous speech
                                self._tts.speak(announcement, interrupt=True)
                                
                                # Summarize scope
                                def _scope_brief(variables: Dict[str, Any], max_pairs: int = 10) -> str:
                                    try:
                                        chosen = None
                                        for name in ("Locals", "locals", "Local", "Globals", "globals"):
                                            v = variables.get(name)
                                            if isinstance(v, dict):
                                                chosen = v
                                                break
                                        if chosen is None:
                                            for v in variables.values():
                                                if isinstance(v, dict):
                                                    chosen = v
                                                    break
                                        if chosen:
                                            items = list(chosen.items())[:max_pairs]
                                            parts = []
                                            for k, v in items:
                                                if isinstance(v, dict) and "value" in v:
                                                    v = v["value"]
                                                s = str(v)
                                                if len(s) > 40:
                                                    s = s[:37] + "..."
                                                parts.append(f"{k} is {s}")
                                            return "; ".join(parts)
                                    except Exception:
                                        pass
                                    return ""
                                
                                scope_summary = _scope_brief(vars_payload)
                                if scope_summary:
                                    self._tts.speak(scope_summary)
                                
                                # Announce changes
                                if variables_delta:
                                    delta_summary = summarize_delta(variables_delta)
                                    if delta_summary and delta_summary != "no changes":
                                        self._tts.speak(f"Changes: {delta_summary}")
                            
                            # Wait for user action
                            action = None
                            if self._controller:
                                # Web interface
                                while action is None:
                                    action = self._controller.wait_for_action(0.5)
                                    if self._abort_requested:
                                        action = 'quit'
                                        break
                            else:
                                # Terminal interface
                                action = prompt_for_action()
                            
                            # Update controller state
                            if self._controller:
                                self._controller.update_state(waiting=False)
                            
                            # Process action
                            if action == 'quit':
                                try:
                                    client.request("disconnect", {"terminateDebuggee": True}, wait=2.0)
                                except Exception:
                                    pass
                                running = False
                                break
                            elif action == 'auto':
                                manual_mode_active = False
                                if self._controller:
                                    self._controller.update_state(mode='auto')
                                print("\n[manual] Switched to auto mode\n", flush=True)
                            elif action == 'continue':
                                client.request("continue", {"threadId": thread_id})
                                continue
                            # Default: step
                        else:
                            # Not in manual mode - continue stepping automatically
                            pass
                        
                        # Step into to capture lines inside function calls as well
                        # But only if we're in manual mode or not using --manual-from
                        if manual_mode_active:
                            # In manual mode, step after processing
                            client.request("stepIn", {"threadId": thread_id})
                        elif not manual_from:
                            # Not using --manual-from, always step (normal auto mode)
                            client.request("stepIn", {"threadId": thread_id})
                        else:
                            # Using --manual-from but not yet in manual mode
                            # Continue execution to reach the trigger line
                            if not manual_trigger_activated:
                                client.request("continue", {"threadId": thread_id})

                    elif ev.event == "continued":
                        # ignore
                        continue
                    elif ev.event == "terminated" or ev.event == "exited":
                        running = False
                        break
                    elif ev.event == "output":
                        # optionally could store outputs; skipping for MVP
                        continue

            self.db.end_session(self.session_id, utc_now_iso())
            return self.session_id
        finally:
            # Clean up manual control resources
            if self._controller:
                try:
                    self._controller.stop()
                except Exception:
                    pass
            if self._tts:
                try:
                    self._tts.stop()
                except Exception:
                    pass
            self._stop_adapter()
            self.db.close()
