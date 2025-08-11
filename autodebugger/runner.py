from __future__ import annotations

import os
import re
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
from .nested_explorer import NestedValueExplorer, format_nested_value_summary
from .syntax_to_speech import syntax_to_speech_code


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
        self._nested_explorer: Optional[NestedValueExplorer] = None
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
    
    def _parse_string_to_object(self, value_str: str) -> Any:
        """Try to parse a string representation back to a Python object.
        
        Handles common cases like lists, dicts, tuples, sets that get stringified by DAP.
        """
        if not isinstance(value_str, str):
            return value_str
            
        # Try to parse as JSON first (handles lists, dicts, numbers, bools, null)
        try:
            import json
            return json.loads(value_str)
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Try to evaluate as Python literal (handles tuples, sets, more complex dicts)
        try:
            import ast
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            pass
        
        # Return original string if parsing fails
        return value_str
    
    def _extract_display_values(self, vars_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Extract displayable values from structured variable format.
        
        Converts {"value": ..., "ref": ..., "children": ...} format to display-friendly format
        while preserving the ability to show nested structures.
        """
        result = {}
        for scope_name, scope_vars in vars_dict.items():
            if not isinstance(scope_vars, dict):
                result[scope_name] = scope_vars
                continue
            
            scope_result = {}
            for var_name, var_info in scope_vars.items():
                if isinstance(var_info, dict):
                    # Handle structured format
                    if "value" in var_info:
                        # If it has children, create a nested display format
                        if "children" in var_info and isinstance(var_info["children"], dict):
                            child_display = {}
                            for child_name, child_info in var_info["children"].items():
                                if isinstance(child_info, dict) and "value" in child_info:
                                    child_display[child_name] = child_info["value"]
                                else:
                                    child_display[child_name] = child_info
                            # Create a display object with the value and children
                            scope_result[var_name] = {
                                "value": var_info["value"],
                                "children": child_display
                            }
                        else:
                            # Just the value for simple variables
                            scope_result[var_name] = var_info["value"]
                    else:
                        # Fallback to the whole object if no value field
                        scope_result[var_name] = var_info
                else:
                    # Handle simple values (backward compatibility)
                    scope_result[var_name] = var_info
            result[scope_name] = scope_result
        return result

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
        exploration_instructions_given = False  # Track if we've given exploration instructions
        
        # Initialize TTS first if audio is enabled
        if manual_audio:
            self._tts = MacSayTTS(voice=manual_voice, rate_wpm=manual_rate_wpm, verbose=False)
            # Initialize nested explorer for interactive variable exploration
            self._nested_explorer = NestedValueExplorer(self._tts, verbose=False)
            
            # Give initial instructions if starting in manual mode
            if manual_mode_active:
                self._tts.speak("Manual mode. Press V for variables, F for function, E to explore changes when available")
                while self._tts.is_speaking():
                    time.sleep(0.05)
                exploration_instructions_given = True
        
        if manual_web:
            # Pass TTS instance to controller if available
            self._controller = HttpStepController(tts=self._tts) if self._tts else HttpStepController()
            self._controller.start()
            # Set initial audio state if controller supports it
            if hasattr(self._controller, 'set_audio_state'):
                self._controller.set_audio_state(enabled=manual_audio, available=manual_audio)
            # If we have a nested explorer and a controller, wire an action provider for web inputs
            if self._nested_explorer and self._controller:
                def _provider() -> Optional[str]:
                    return self._controller.wait_for_action(0.1)
                # Children provider to lazily fetch DAP children when a node has a `ref`
                def _children_provider(node: Dict[str, Any]) -> Dict[str, Any]:
                    try:
                        vref = node.get("ref") or node.get("variablesReference")
                        if isinstance(vref, int) and vref > 0 and self.client:
                            res = self.client.request("variables", {"variablesReference": vref})
                            var_list = res.body.get("variables", []) if res.body else []
                            child_map: Dict[str, Any] = {}
                            for c in var_list[:50]:
                                cname = str(c.get("name"))
                                cval = c.get("value")
                                cref = c.get("variablesReference")
                                entry: Dict[str, Any] = {"value": cval}
                                if isinstance(cref, int) and cref > 0:
                                    entry["ref"] = cref
                                child_map[cname] = entry
                            return child_map
                    except Exception:
                        pass
                    return {}
                try:
                    self._nested_explorer._action_provider = _provider  # type: ignore[attr-defined]
                    self._nested_explorer._children_provider = _children_provider  # type: ignore[attr-defined]
                except Exception:
                    pass
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
                        
                        # Track if we should step after processing
                        should_step_after = True
                        just_activated_manual = False  # Track if we just switched to manual mode
                        
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
                                        just_activated_manual = True  # Mark that we just activated
                                        if self._controller:
                                            self._controller.update_state(mode='manual')
                                        
                                        # Give instructions when activating manual mode
                                        if self._tts and not exploration_instructions_given:
                                            self._tts.speak(f"Manual mode activated. Press V for variables, F for function, E to explore changes")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                            exploration_instructions_given = True
                                        
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
                                entry: Dict[str, Any] = {"value": vvalue}
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
                                            cval = c.get("value")
                                            cref = c.get("variablesReference")
                                            centry: Dict[str, Any] = {"value": cval}
                                            if isinstance(cref, int) and cref > 0:
                                                centry["ref"] = cref
                                            child_map[cname] = centry
                                        if child_map:
                                            entry["children"] = child_map
                                    except Exception:
                                        pass
                                # Always include entry; even without children, it keeps structure for explorer
                                # Store a 'ref' if expandable so explorer can lazily fetch deeper
                                if isinstance(vref, int) and vref > 0 and "children" not in entry:
                                    entry["ref"] = vref
                                scope_map[vname] = entry
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
                                # Convert to display format for web view
                                display_vars = self._extract_display_values(vars_payload)
                                display_delta = self._extract_display_values(variables_delta) if variables_delta else {}
                                self._controller.update_state(
                                    session_id=self.session_id,
                                    file=file_path,
                                    line=line,
                                    code=code,
                                    variables=display_vars,
                                    variables_delta=display_delta,
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
                                # Mark as code since it contains code snippets
                                self._tts.speak(announcement, interrupt=True, is_code=True)
                                
                                # Wait for main announcement to finish before scope
                                while self._tts.is_speaking():
                                    time.sleep(0.05)
                                
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
                                
                                # Don't automatically read variables - only changes
                                # Variables can be read on demand with 'v' key
                                
                                # Announce changes
                                if variables_delta:
                                    delta_summary = summarize_delta(variables_delta)
                                    if delta_summary and delta_summary != "no changes":
                                        self._tts.speak(f"Changes: {delta_summary}")
                                        # Wait for changes announcement to finish
                                        while self._tts.is_speaking():
                                            time.sleep(0.05)
                            
                            # Loop while at this stopped event to allow multiple actions
                            while True:
                                # Wait for user action
                                action = None
                                if self._controller:
                                    # Indicate waiting in UI
                                    self._controller.update_state(waiting=True)
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
                                # Clear the just_activated_manual flag since we've now received user input
                                just_activated_manual = False
                                
                                if action == 'quit':
                                    try:
                                        client.request("disconnect", {"terminateDebuggee": True}, wait=2.0)
                                    except Exception:
                                        pass
                                    running = False
                                    should_step_after = False
                                    break
                                elif action == 'auto':
                                    manual_mode_active = False
                                    if self._controller:
                                        self._controller.update_state(mode='auto')
                                    print("\n[manual] Switched to auto mode\n", flush=True)
                                    should_step_after = True  # Continue stepping in auto
                                    break
                                elif action == 'continue':
                                    client.request("continue", {"threadId": thread_id})
                                    should_step_after = False  # Don't step, we already continued
                                    # Exit action loop; do not stepIn since we continued
                                    break
                                elif action == 'step':
                                    # Step to next line explicitly
                                    should_step_after = True
                                    break
                                elif action == 'variables':
                                    # Read ALL current variables on demand
                                    if self._tts and vars_payload:
                                        # Find the main scope (usually Locals)
                                        read_any = False
                                        for scope_name in ("Locals", "locals", "Local", "Globals", "globals"):
                                            if scope_name in vars_payload:
                                                scope_vars = vars_payload[scope_name]
                                                if isinstance(scope_vars, dict) and scope_vars:
                                                    self._tts.speak(f"Variables in {scope_name}")
                                                    while self._tts.is_speaking():
                                                        time.sleep(0.05)
                                                    
                                                    # Read each variable
                                                    for var_name, var_info in scope_vars.items():
                                                        if isinstance(var_info, dict) and "value" in var_info:
                                                            value = var_info["value"]
                                                        else:
                                                            value = var_info
                                                        
                                                        # Format value for speech
                                                        value_str = str(value)
                                                        if len(value_str) > 100:
                                                            value_str = value_str[:100] + "..."
                                                        
                                                        self._tts.speak(f"{var_name} is {value_str}")
                                                        while self._tts.is_speaking():
                                                            time.sleep(0.05)
                                                    
                                                    read_any = True
                                                    break  # Usually we only need Locals
                                        
                                        if not read_any:
                                            self._tts.speak("No variables in scope")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                    else:
                                        self._tts.speak("No variables available")
                                        while self._tts.is_speaking():
                                            time.sleep(0.05)
                                    # After speaking variables, re-prompt for another action
                                    should_step_after = False
                                    continue
                                elif action == 'function':
                                    # Read function context on demand
                                    if self._tts:
                                        # Try to get function name from frame info
                                        func_name = None
                                        if file and line:
                                            # Simple heuristic - read function from file
                                            try:
                                                with open(file, 'r') as f:
                                                    lines = f.readlines()
                                                # Look backwards for def
                                                for i in range(min(line - 1, len(lines) - 1), -1, -1):
                                                    if lines[i].strip().startswith('def '):
                                                        func_match = re.search(r'def\s+(\w+)', lines[i])
                                                        if func_match:
                                                            func_name = func_match.group(1)
                                                            # Get full signature
                                                            func_sig = lines[i].strip()
                                                            # Get body (next few lines)
                                                            func_body_lines = []
                                                            for j in range(i + 1, min(i + 6, len(lines))):
                                                                if lines[j].strip() and not lines[j][0].isspace():
                                                                    break
                                                                func_body_lines.append(lines[j].rstrip())
                                                            func_body = '\n'.join(func_body_lines)
                                                            
                                                            self._tts.speak(f"In function {func_name}. Signature: {func_sig}")
                                                            while self._tts.is_speaking():
                                                                time.sleep(0.05)
                                                            if func_body:
                                                                self._tts.speak(f"Body preview: {func_body[:200]}")
                                                                while self._tts.is_speaking():
                                                                    time.sleep(0.05)
                                                            break
                                            except Exception:
                                                pass
                                        
                                        if not func_name:
                                            self._tts.speak("Not currently in a function")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                        # After speaking function context, re-prompt for another action
                                        should_step_after = False
                                        continue
                                elif action == 'explore':
                                    # Interactive exploration with numbered selection
                                    # Compute changed variables by scope for UI and selection
                                    changed_vars: List[Tuple[str, str, Any]] = []  # (scope, var_name, value)
                                    for scope_name in ("Locals", "locals", "Local", "Globals", "globals"):
                                        scope_delta = variables_delta.get(scope_name)
                                        if isinstance(scope_delta, dict):
                                            scope_vars = vars_payload.get(scope_name, {}) if isinstance(vars_payload.get(scope_name), dict) else {}
                                            for var_name in scope_delta.keys():
                                                var_info = scope_vars.get(var_name)
                                                if isinstance(var_info, dict) and "value" in var_info:
                                                    # Extract the string value and try to parse it
                                                    value_str = var_info["value"]
                                                    value = self._parse_string_to_object(value_str)
                                                    # Store both the parsed value and the reference if available
                                                    if "ref" in var_info or "children" in var_info:
                                                        # Keep the structured info for lazy loading
                                                        value = {"_parsed": value, "_ref": var_info.get("ref"), "_children": var_info.get("children")}
                                                else:
                                                    value = self._parse_string_to_object(var_info) if isinstance(var_info, str) else var_info
                                                changed_vars.append((scope_name, var_name, value))

                                    if changed_vars:
                                        page = 0
                                        page_size = 10
                                        exploring = True
                                        while exploring:
                                            start_idx = page * page_size
                                            end_idx = min(start_idx + page_size, len(changed_vars))
                                            page_vars = changed_vars[start_idx:end_idx]

                                            # Update web UI to show enumerated items
                                            if self._controller:
                                                items_payload = []
                                                for i, (scope_name, var_name, value) in enumerate(page_vars):
                                                    preview = format_nested_value_summary(value)
                                                    items_payload.append({
                                                        "index": i,
                                                        "name": f"{var_name} ({scope_name})",
                                                        "preview": preview,
                                                    })
                                                self._controller.update_state(
                                                    explore_active=True,
                                                    explore_items=items_payload,
                                                    explore_page=page,
                                                    explore_total=len(changed_vars),
                                                )

                                            # Audio announcements if enabled
                                            if self._tts:
                                                self._tts.speak(f"Changed variables, page {page + 1}. Select 0 to {len(page_vars) - 1}")
                                                while self._tts.is_speaking():
                                                    time.sleep(0.05)
                                                for i, (_s, var_name, value) in enumerate(page_vars):
                                                    brief_value = format_nested_value_summary(value)
                                                    self._tts.speak(f"{i}: {var_name} — {brief_value}")
                                                    while self._tts.is_speaking():
                                                        time.sleep(0.05)
                                                if end_idx < len(changed_vars):
                                                    self._tts.speak("Press 0 to 9 to explore, P for next page, or N to cancel")
                                                else:
                                                    self._tts.speak("Press 0 to 9 to explore, or N to cancel")
                                                while self._tts.is_speaking():
                                                    time.sleep(0.05)

                                            # Await selection
                                            selection: Optional[str] = None
                                            if self._controller:
                                                while selection is None:
                                                    selection = self._controller.wait_for_action(0.2)
                                                    if selection:
                                                        selection = selection.strip().lower()
                                                        break
                                                    if self._abort_requested:
                                                        selection = 'n'
                                                        break
                                            else:
                                                print(f"\n[Explorer] Select (0-{len(page_vars)-1}, p=next page, n=cancel): ", end='', flush=True)
                                                selection = input().strip().lower()

                                            if not selection:
                                                continue
                                            if selection == 'n' or selection == 'cancel':
                                                exploring = False
                                                break
                                            if selection == 'p' and end_idx < len(changed_vars):
                                                page += 1
                                                continue
                                            if selection.isdigit():
                                                idx = int(selection)
                                                if 0 <= idx < len(page_vars):
                                                    _scope, var_name, value = page_vars[idx]
                                                    if self._tts:
                                                        self._tts.speak(f"Exploring {var_name}")
                                                        while self._tts.is_speaking():
                                                            time.sleep(0.05)
                                                    # Explore via nested explorer if available
                                                    if self._nested_explorer:
                                                        self._nested_explorer.explore_value(var_name, value)
                                                    # Ask whether to explore another
                                                    if self._tts:
                                                        self._tts.speak("Explore another variable? Y for yes, N to stop")
                                                        while self._tts.is_speaking():
                                                            time.sleep(0.05)
                                                    cont_ans: Optional[str] = None
                                                    if self._controller:
                                                        while cont_ans is None:
                                                            cont_ans = self._controller.wait_for_action(0.2)
                                                            if cont_ans:
                                                                cont_ans = cont_ans.strip().lower()
                                                                break
                                                    else:
                                                        print("\n[Explorer] Continue? y/n: ", end='', flush=True)
                                                        cont_ans = input().strip().lower()
                                                    if cont_ans not in ('y', 'yes'):
                                                        exploring = False
                                                else:
                                                    if self._tts:
                                                        self._tts.speak(f"Invalid selection {selection}")
                                                        while self._tts.is_speaking():
                                                            time.sleep(0.05)
                                            # loop continues

                                        if self._tts:
                                            self._tts.speak("Exploration complete")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                        # Clear explore UI
                                        if self._controller:
                                            self._controller.update_state(explore_active=False, explore_items=[], explore_total=0)
                                    else:
                                        if self._tts:
                                            self._tts.speak("No changes to explore")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                        # Don't auto-step after exploring; re-prompt for next action
                                        should_step_after = False
                                        continue
                            
                            if action == 'variables_explore':
                                # Explore across all variables, not just changes
                                all_vars: List[Tuple[str, str, Any]] = []  # (scope, var_name, value)
                                for scope_name in ("Locals", "locals", "Local", "Globals", "globals"):
                                    scope_vars = vars_payload.get(scope_name)
                                    if isinstance(scope_vars, dict):
                                        for var_name, var_info in scope_vars.items():
                                            if isinstance(var_info, dict) and "value" in var_info:
                                                # Extract the string value and try to parse it
                                                value_str = var_info["value"]
                                                value = self._parse_string_to_object(value_str)
                                                # Store both the parsed value and the reference if available
                                                if "ref" in var_info or "children" in var_info:
                                                    # Keep the structured info for lazy loading
                                                    value = {"_parsed": value, "_ref": var_info.get("ref"), "_children": var_info.get("children")}
                                            else:
                                                value = self._parse_string_to_object(var_info) if isinstance(var_info, str) else var_info
                                            all_vars.append((scope_name, var_name, value))

                                if all_vars:
                                    page = 0
                                    page_size = 10
                                    exploring = True
                                    while exploring:
                                        start_idx = page * page_size
                                        end_idx = min(start_idx + page_size, len(all_vars))
                                        page_vars = all_vars[start_idx:end_idx]

                                        # Update web UI to show enumerated items
                                        if self._controller:
                                            items_payload = []
                                            for i, (scope_name, var_name, value) in enumerate(page_vars):
                                                preview = format_nested_value_summary(value)
                                                items_payload.append({
                                                    "index": i,
                                                    "name": f"{var_name} ({scope_name})",
                                                    "preview": preview,
                                                })
                                            self._controller.update_state(
                                                explore_active=True,
                                                explore_items=items_payload,
                                                explore_page=page,
                                                explore_total=len(all_vars),
                                            )

                                        # Audio announcements
                                        if self._tts:
                                            self._tts.speak(f"Variables list, page {page + 1}. Select 0 to {len(page_vars) - 1}")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)
                                            for i, (_s, var_name, value) in enumerate(page_vars):
                                                brief_value = format_nested_value_summary(value)
                                                self._tts.speak(f"{i}: {var_name} — {brief_value}")
                                                while self._tts.is_speaking():
                                                    time.sleep(0.05)
                                            if end_idx < len(all_vars):
                                                self._tts.speak("Press 0 to 9 to explore, P for next page, or N to cancel")
                                            else:
                                                self._tts.speak("Press 0 to 9 to explore, or N to cancel")
                                            while self._tts.is_speaking():
                                                time.sleep(0.05)

                                        # Await selection
                                        selection: Optional[str] = None
                                        if self._controller:
                                            while selection is None:
                                                selection = self._controller.wait_for_action(0.2)
                                                if selection:
                                                    selection = selection.strip().lower()
                                                    break
                                                if self._abort_requested:
                                                    selection = 'n'
                                                    break
                                        else:
                                            print(f"\n[Vars] Select (0-{len(page_vars)-1}, p=next page, n=cancel): ", end='', flush=True)
                                            selection = input().strip().lower()

                                        if not selection:
                                            continue
                                        if selection == 'n' or selection == 'cancel':
                                            exploring = False
                                            break
                                        if selection == 'p' and end_idx < len(all_vars):
                                            page += 1
                                            continue
                                        if selection.isdigit():
                                            idx = int(selection)
                                            if 0 <= idx < len(page_vars):
                                                _scope, var_name, value = page_vars[idx]
                                                if self._tts:
                                                    self._tts.speak(f"Exploring {var_name}")
                                                    while self._tts.is_speaking():
                                                        time.sleep(0.05)
                                                if self._nested_explorer:
                                                    self._nested_explorer.explore_value(var_name, value)
                                                if self._tts:
                                                    self._tts.speak("Explore another variable? Y for yes, N to stop")
                                                    while self._tts.is_speaking():
                                                        time.sleep(0.05)
                                                cont_ans: Optional[str] = None
                                                if self._controller:
                                                    while cont_ans is None:
                                                        cont_ans = self._controller.wait_for_action(0.2)
                                                        if cont_ans:
                                                            cont_ans = cont_ans.strip().lower()
                                                            break
                                                else:
                                                    print("\n[Vars] Continue? y/n: ", end='', flush=True)
                                                    cont_ans = input().strip().lower()
                                                if cont_ans not in ('y', 'yes'):
                                                    exploring = False
                                            else:
                                                if self._tts:
                                                    self._tts.speak(f"Invalid selection {selection}")
                                                    while self._tts.is_speaking():
                                                        time.sleep(0.05)
                                        # loop continues

                                    if self._tts:
                                        self._tts.speak("Variables exploration complete")
                                        while self._tts.is_speaking():
                                            time.sleep(0.05)
                                    if self._controller:
                                        self._controller.update_state(explore_active=False, explore_items=[], explore_total=0)
                                    # Re-prompt for next action
                                    should_step_after = False
                                    continue
                        else:
                            # Not in manual mode - continue stepping automatically
                            pass
                        
                        # Step into to capture lines inside function calls as well
                        # But only if appropriate based on mode and state
                        if manual_mode_active:
                            # In manual mode, only step if we processed an action
                            # BUT not if we just activated manual mode this iteration
                            if should_step_after and not just_activated_manual:
                                client.request("stepIn", {"threadId": thread_id})
                        elif not manual_from:
                            # Not using --manual-from, always step (normal auto mode)
                            client.request("stepIn", {"threadId": thread_id})
                        else:
                            # Using --manual-from but not yet in manual mode
                            # Continue execution to reach the trigger line
                            if not manual_trigger_activated:
                                client.request("continue", {"threadId": thread_id})
                            else:
                                # We've activated manual from trigger, now we should be in manual mode
                                # This shouldn't happen - if we activated, we should be in manual_mode_active
                                pass

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
