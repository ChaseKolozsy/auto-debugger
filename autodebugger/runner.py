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
        print("[DEBUG] Using enhanced_control.py", file=sys.stderr)
    except ImportError as e:
        print(f"[DEBUG] Failed to import enhanced_control: {e}, falling back to control.py", file=sys.stderr)
        from .control import HttpStepController, prompt_for_action
else:
    print("[DEBUG] Using control.py (USE_ENHANCED=False)", file=sys.stderr)
    from .control import HttpStepController, prompt_for_action
from .common import extract_function_context, summarize_value, summarize_delta
from .function_blocks import FunctionBlockExplorer, get_block_preview
from .dap_client import DapClient
from .db import LineReport, LineReportStore, SessionSummary
from .audio_ui import MacSayTTS
from .nested_explorer import NestedValueExplorer, format_nested_value_summary
from .syntax_to_speech import syntax_to_speech_code


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_nearest_executable_line(file_path: str, target_line: int) -> int:
    """Find the nearest executable line to the target line number.
    
    Returns the target line if it's executable, or the next executable line after it.
    Returns 0 if no executable line found.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Check if target line is executable
        if 1 <= target_line <= len(lines):
            text = lines[target_line - 1]
            stripped = text.strip()
            
            # Check if it's likely executable
            if stripped and not stripped.startswith('#') and '"""' not in stripped and "'''" not in stripped:
                if any(kw in text for kw in ['=', 'if ', 'for ', 'while ', 'def ', 'class ', 'return', 'print', 'import']):
                    return target_line
                elif not text.startswith(' ') and not text.startswith('\t'):
                    return target_line
        
        # Search forward for next executable line
        for idx in range(target_line, len(lines) + 1):
            if idx > len(lines):
                break
            text = lines[idx - 1]
            stripped = text.strip()
            
            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                continue
            # Skip docstrings and multiline strings
            if '"""' in stripped or "'''" in stripped:
                continue
            # Check if likely executable
            if any(kw in text for kw in ['=', 'if ', 'for ', 'while ', 'def ', 'class ', 'return', 'print', 'import']):
                return idx
            elif not text.startswith(' ') and not text.startswith('\t'):
                return idx
        
        # Search backward if nothing found forward
        for idx in range(target_line - 1, 0, -1):
            text = lines[idx - 1]
            stripped = text.strip()
            
            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                continue
            # Skip docstrings and multiline strings
            if '"""' in stripped or "'''" in stripped:
                continue
            # Check if likely executable
            if any(kw in text for kw in ['=', 'if ', 'for ', 'while ', 'def ', 'class ', 'return', 'print', 'import']):
                return idx
            elif not text.startswith(' ') and not text.startswith('\t'):
                return idx
                
    except Exception:
        pass
    
    return 0


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
        self._goto_target_line: Optional[int] = None  # Target line for goto mode
        self._goto_mode_active: bool = False  # Whether we're fast-forwarding to a line
        self._audio_state_before_goto: bool = False  # To restore audio state after goto

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
    
    def _wait_for_speech_with_interrupt(self) -> bool:
        """Wait for TTS to finish speaking, but check for stop_audio interrupts.
        Returns True if speech completed, False if interrupted."""
        if not self._tts:
            return True
            
        while self._tts.is_speaking():
            # Check for stop_audio action if controller is available
            if self._controller:
                action = self._controller.wait_for_action(0.05)
                if action:
                    act = action.strip().lower()
                    # If explicit stop, or any other action (like a new selection), halt current speech
                    self._tts.stop()
                    # Re-queue non-stop actions so outer loop can process them
                    if act != 'stop_audio' and hasattr(self._controller, 'shared_state'):
                        try:
                            self._controller.shared_state.send_action(action)
                        except Exception:
                            pass
                    return False
            else:
                time.sleep(0.05)
        return True
    
    def _parse_string_to_object(self, value_str: str) -> Any:
        """Try to parse a string representation back to a Python object.
        
        Handles common cases like lists, dicts, tuples, sets that get stringified by DAP.
        """
        if not isinstance(value_str, str):
            return value_str
            
        # Check if the string contains ellipsis markers like [...] or {...}
        # These indicate truncated data that needs to be fetched from debugpy
        if '[...]' in value_str or '{...}' in value_str:
            # Return a marker that this needs to be fetched via DAP
            return {"_needs_fetch": True, "_preview": value_str}
            
        # Try to parse as JSON first (handles lists, dicts, numbers, bools, null)
        try:
            import json
            parsed = json.loads(value_str)
            # Check if parsed result contains ellipsis
            if self._contains_ellipsis(parsed):
                return {"_needs_fetch": True, "_preview": value_str}
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Try to evaluate as Python literal (handles tuples, sets, more complex dicts)
        try:
            import ast
            parsed = ast.literal_eval(value_str)
            # Check if parsed result contains ellipsis
            if self._contains_ellipsis(parsed):
                return {"_needs_fetch": True, "_preview": value_str}
            return parsed
        except (ValueError, SyntaxError):
            pass
        
        # Return original string if parsing fails
        return value_str
    
    def _contains_ellipsis(self, obj: Any) -> bool:
        """Check if an object contains ellipsis (truncated data marker)."""
        if obj is Ellipsis:
            return True
        if isinstance(obj, (list, tuple)):
            return any(self._contains_ellipsis(item) for item in obj)
        if isinstance(obj, dict):
            return any(self._contains_ellipsis(v) for v in obj.values())
        return False
    
    def _fetch_complete_value(self, var_ref: int, max_depth: int = 20, seen_refs: Optional[set] = None) -> Any:
        """Fetch ONLY the actual data from debugpy - no Python internals.
        
        Filters out all methods, special variables, and Python-specific clutter.
        """
        if not self.client or var_ref <= 0 or max_depth <= 0:
            return None
        
        # Track seen references to avoid infinite loops
        if seen_refs is None:
            seen_refs = set()
        if var_ref in seen_refs:
            return "<circular reference>"
        seen_refs.add(var_ref)
            
        try:
            # Fetch ALL variables for this reference
            response = self.client.request("variables", {"variablesReference": var_ref})
            if not response or not response.body:
                return None
                
            variables = response.body.get("variables", [])
            
            # Filter out all the Python clutter
            filtered_vars = []
            for var in variables:
                name = var.get("name", "")
                # Skip ALL these:
                # - Special variables section
                # - Function variables section  
                # - Methods (append, clear, copy, etc.)
                # - Private attributes (__xxx__)
                # - len() and other special entries
                if (name == "special variables" or 
                    name == "function variables" or
                    name.startswith("__") or
                    name == "len()" or
                    name in {"append", "clear", "copy", "count", "extend", "index", 
                            "insert", "pop", "remove", "reverse", "sort", "get",
                            "items", "keys", "values", "update", "setdefault",
                            "popitem", "fromkeys"}):
                    continue
                filtered_vars.append(var)
            
            # Now check if this is a list/dict based on actual data keys
            if not filtered_vars:
                return None
                
            # Check if all remaining keys are numeric (it's a list)
            is_sequence = all(v.get("name", "").isdigit() for v in filtered_vars)
            
            if is_sequence:
                # Build as a list - ONLY the actual values
                items = []
                # Sort by numeric index
                sorted_vars = sorted(filtered_vars, key=lambda v: int(v.get("name", "0")))
                for var in sorted_vars:
                    value_str = var.get("value", "")
                    child_ref = var.get("variablesReference", 0)
                    
                    if child_ref > 0:
                        # Recursively fetch children
                        child_value = self._fetch_complete_value(child_ref, max_depth - 1, seen_refs)
                        if child_value is not None:
                            items.append(child_value)
                        else:
                            # Parse the string value
                            parsed = self._parse_string_to_object(value_str)
                            items.append(parsed)
                    else:
                        # Parse the value
                        parsed = self._parse_string_to_object(value_str)
                        if isinstance(parsed, dict) and parsed.get("_needs_fetch"):
                            # Still has ellipsis, just use string
                            items.append(value_str)
                        else:
                            items.append(parsed)
                return items
            else:
                # Build as a dict - ONLY actual key-value pairs
                result = {}
                for var in filtered_vars:
                    name = var.get("name", "")
                    # Clean up quoted keys like "'key'" -> "key"
                    if name.startswith("'") and name.endswith("'"):
                        name = name[1:-1]
                    
                    value_str = var.get("value", "")
                    child_ref = var.get("variablesReference", 0)
                    
                    if child_ref > 0:
                        # Recursively fetch children
                        child_value = self._fetch_complete_value(child_ref, max_depth - 1, seen_refs)
                        if child_value is not None:
                            result[name] = child_value
                        else:
                            parsed = self._parse_string_to_object(value_str)
                            result[name] = parsed
                    else:
                        # Parse the value
                        parsed = self._parse_string_to_object(value_str)
                        if isinstance(parsed, dict) and parsed.get("_needs_fetch"):
                            result[name] = value_str
                        else:
                            result[name] = parsed
                return result
                
        except Exception as e:
            print(f"[Debug] Failed to fetch complete value for ref {var_ref}: {e}")
            return None
    
    def _extract_display_values(self, vars_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Extract displayable values from structured variable format.
        
        Converts {"value": ..., "ref": ..., "children": ...} format to clean, display-friendly format.
        Parses string representations back to actual Python objects for clean display.
        Fetches complete data from debugpy when ellipsis is encountered.
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
                        # Parse the string value to get the actual Python object
                        value_str = var_info["value"]
                        parsed_value = self._parse_string_to_object(value_str)
                        
                        # Check if we need to fetch complete data
                        if isinstance(parsed_value, dict) and parsed_value.get("_needs_fetch"):
                            # We have truncated data, fetch the complete value
                            var_ref = var_info.get("ref", 0)
                            if var_ref > 0 and self.client:
                                complete_value = self._fetch_complete_value(var_ref)
                                if complete_value is not None:
                                    parsed_value = complete_value
                                else:
                                    # Couldn't fetch, use preview but clean it up
                                    preview = parsed_value.get("_preview", value_str)
                                    # Remove ellipsis markers for display
                                    preview = preview.replace('[...]', '[…]').replace('{...}', '{…}')
                                    parsed_value = preview
                        
                        # For the display, just show the clean parsed value
                        scope_result[var_name] = parsed_value
                    else:
                        # Fallback to the whole object if no value field
                        scope_result[var_name] = var_info
                else:
                    # Handle simple values (backward compatibility)
                    # Try to parse if it's a string representation
                    if isinstance(var_info, str):
                        parsed = self._parse_string_to_object(var_info)
                        # Handle needs_fetch case
                        if isinstance(parsed, dict) and parsed.get("_needs_fetch"):
                            scope_result[var_name] = parsed.get("_preview", var_info)
                        else:
                            scope_result[var_name] = parsed
                    else:
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
            # Pass data fetcher to allow fetching complete data when ellipsis is encountered
            def _data_fetcher(ref: int) -> Any:
                return self._fetch_complete_value(ref) if self.client else None
            
            # Don't pass action provider - it was consuming valid selection actions
            # Instead, the _wait_for_speech_with_interrupt in runner.py handles interrupts
            self._action_provider = None
            
            self._nested_explorer = NestedValueExplorer(
                self._tts, 
                verbose=False, 
                data_fetcher=_data_fetcher
            )
            
            # Give initial instructions if starting in manual mode
            if manual_mode_active:
                self._tts.speak("Manual mode. Press V for variables, F for function, P for function parts, E to explore changes")
                self._wait_for_speech_with_interrupt()
                exploration_instructions_given = True
        
        if manual_web:
            # Pass TTS instance to controller if available (enhanced controller only)
            if USE_ENHANCED and self._tts:
                try:
                    self._controller = HttpStepController(tts=self._tts)
                except TypeError:
                    # Fallback to no tts parameter if using simple controller
                    self._controller = HttpStepController()
            else:
                self._controller = HttpStepController()
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
                                            self._tts.speak(f"Manual mode activated. Press V for variables, F for function, P for function parts, E to explore")
                                            self._wait_for_speech_with_interrupt()
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

                        # Check if we've reached the goto target
                        if self._goto_mode_active and self._goto_target_line:
                            if line >= self._goto_target_line:
                                # We've reached or passed the target line
                                print(f"\n[goto] Reached line {line} (target was {self._goto_target_line})\n", flush=True)
                                
                                # Restore audio state
                                if self._controller and hasattr(self._controller, 'set_audio_state'):
                                    self._controller.set_audio_state(enabled=self._audio_state_before_goto, available=True)
                                
                                # Clear goto mode
                                self._goto_mode_active = False
                                self._goto_target_line = None
                                
                                # Continue normally from here
                            # Don't skip anything here - let it record the line!

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
                                vref = v.get("variablesReference")
                                
                                # ALWAYS fetch complete data if there's a reference
                                if isinstance(vref, int) and vref > 0:
                                    complete = self._fetch_complete_value(vref)
                                    if complete is not None:
                                        scope_map[vname] = complete
                                    else:
                                        # Fallback to string value
                                        scope_map[vname] = self._parse_string_to_object(vvalue)
                                else:
                                    # No reference, just parse the value
                                    scope_map[vname] = self._parse_string_to_object(vvalue)
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
                        if manual_mode_active and self._goto_mode_active:
                            # In goto mode - skip ALL interaction and just step
                            if self._controller:
                                self._controller.update_state(
                                    session_id=self.session_id,
                                    file=file_path,
                                    line=line,
                                    code=code,
                                    waiting=False,
                                    mode='goto'
                                )
                            # Force stepping without any user interaction
                            should_step_after = True
                        elif manual_mode_active:
                            # Normal manual mode with user interaction
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
                                
                                # Speak current line if audio enabled (but skip in goto mode)
                                should_speak = self._tts and not self._goto_mode_active and (
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
                                            # Don't truncate too aggressively - allow reasonable function bodies
                                            # The extraction already limits to 3000 chars
                                            announcement += f"Body: {func_body}. "
                                        announcement += f"Line {line}: {code}"
                                    else:
                                        # Just announce the line
                                        announcement = f"Line {line}: {code}"
                                    
                                    # Speak with interrupt to clear any previous speech
                                    # Mark as code since it contains code snippets
                                    self._tts.speak(announcement, interrupt=True, is_code=True)
                                    
                                    # Wait for main announcement to finish before scope
                                    self._wait_for_speech_with_interrupt()
                                
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
                                
                                # Don't automatically read variables or full changes. Variables can be read with 'v'.
                                # For changes, only announce the count; details available with 'e' (explore changes).
                                if variables_delta:
                                    clean_delta = self._extract_display_values(variables_delta)
                                    total_changes = 0
                                    for scope_name, scope_vars in clean_delta.items():
                                        if isinstance(scope_vars, dict) and not scope_name.startswith('_'):
                                            for var_name in scope_vars.keys():
                                                if not var_name.startswith('_'):
                                                    total_changes += 1
                                    if total_changes > 0:
                                        msg = "1 change. Press E to explore" if total_changes == 1 else f"{total_changes} changes. Press E to explore"
                                        self._tts.speak(msg)
                                        self._wait_for_speech_with_interrupt()
                            
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
                                
                                if action and action.startswith('goto:'):
                                    # Handle goto line command
                                    try:
                                        target_line = int(action.split(':')[1])
                                        # Find nearest executable line from target
                                        actual_target = find_nearest_executable_line(file_path, target_line)
                                        
                                        if actual_target > 0:
                                            # Save current audio state and mute
                                            if self._controller and hasattr(self._controller, 'is_audio_enabled'):
                                                self._audio_state_before_goto = self._controller.is_audio_enabled()
                                                # Temporarily disable audio for fast-forward
                                                if self._controller and hasattr(self._controller, 'set_audio_state'):
                                                    self._controller.set_audio_state(enabled=False, available=True)
                                            
                                            # Stop any current audio
                                            if self._tts:
                                                self._tts.stop()
                                            
                                            # Set goto mode
                                            self._goto_target_line = actual_target
                                            self._goto_mode_active = True
                                            
                                            print(f"\n[goto] Fast-forwarding to line {actual_target} (target: {target_line})...\n", flush=True)
                                            
                                            # Start stepping immediately
                                            should_step_after = True
                                            break
                                        else:
                                            print(f"\n[goto] No executable line found near {target_line}\n", flush=True)
                                            should_step_after = False
                                            continue
                                    except (ValueError, IndexError):
                                        print("\n[goto] Invalid line number\n", flush=True)
                                        should_step_after = False
                                        continue
                                elif action == 'stop_audio':
                                    # Stop any playing audio
                                    if self._tts:
                                        self._tts.stop()
                                    should_step_after = False
                                    continue
                                elif action == 'quit':
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
                                elif action == 'speed':
                                    # Cycle through speech speeds
                                    if hasattr(self._controller, 'cycle_audio_speed'):
                                        new_speed = self._controller.cycle_audio_speed()
                                        if self._tts:
                                            # Map speed to TTS rate
                                            speed_rates = {"slow": 150, "medium": 210, "fast": 270}
                                            self._tts.rate_wpm = speed_rates.get(new_speed, 210)
                                            self._tts.speak(f"Speed {new_speed}")
                                            self._wait_for_speech_with_interrupt()
                                    should_step_after = False
                                    continue  # Don't break, just continue to re-prompt
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
                                                    self._wait_for_speech_with_interrupt()
                                                    
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
                                                        self._wait_for_speech_with_interrupt()
                                                    
                                                    read_any = True
                                                    break  # Usually we only need Locals
                                        
                                        if not read_any:
                                            self._tts.speak("No variables in scope")
                                            self._wait_for_speech_with_interrupt()
                                    else:
                                        self._tts.speak("No variables available")
                                        self._wait_for_speech_with_interrupt()
                                    # After speaking variables, re-prompt for another action
                                    should_step_after = False
                                    continue
                                elif action == 'function':
                                    # Read function context on demand
                                    if self._tts:
                                        # First try to get from state if available
                                        func_name = None
                                        func_sig = None
                                        func_body = None
                                        
                                        if self._controller and hasattr(self._controller.shared_state, 'get_state'):
                                            state = self._controller.shared_state.get_state()
                                            func_name = state.get('function_name')
                                            func_sig = state.get('function_sig')
                                            func_body = state.get('function_body')
                                        
                                        # If not in state, extract using common module
                                        if not func_name and file_path and line:
                                            func_context = extract_function_context(file_path, line)
                                            func_name = func_context['name']
                                            func_sig = func_context['sig']
                                            func_body = func_context['body']
                                        
                                        # Announce the function info
                                        if func_name:
                                            self._tts.speak(f"In function {func_name}")
                                            self._wait_for_speech_with_interrupt()
                                            if func_sig:
                                                self._tts.speak(f"Signature: {func_sig}", is_code=True)
                                                self._wait_for_speech_with_interrupt()
                                            if func_body:
                                                # Read the full function body (or up to reasonable limit)
                                                # No need to truncate further - common.py already limits to 3000 chars
                                                self._tts.speak(f"Body: {func_body}", is_code=True)
                                                self._wait_for_speech_with_interrupt()
                                        else:
                                            self._tts.speak("Not currently in a function")
                                            self._wait_for_speech_with_interrupt()
                                        # After speaking function context, re-prompt for another action
                                        should_step_after = False
                                        continue
                                elif action == 'parts' or action == 'p':
                                    # Function block exploration mode
                                    if self._tts:
                                        # Get function body
                                        func_body = None
                                        
                                        # First try to get from state if available
                                        if self._controller and hasattr(self._controller.shared_state, 'get_state'):
                                            state = self._controller.shared_state.get_state()
                                            func_body = state.get('function_body')
                                        
                                        # If not in state, extract using common module
                                        if not func_body and file_path and line:
                                            func_context = extract_function_context(file_path, line)
                                            func_body = func_context['body']
                                        
                                        if func_body:
                                            # Create block explorer
                                            explorer = FunctionBlockExplorer(func_body, self._tts)
                                            
                                            # Clear any pending actions before entering exploration mode
                                            if self._controller:
                                                self._controller.clear_actions()
                                            
                                            # Announce page info
                                            self._tts.speak(explorer.announce_page_info())
                                            self._wait_for_speech_with_interrupt()

                                            # Helper: update web popup with current page blocks
                                            def _update_blocks_popup() -> None:
                                                if not self._controller:
                                                    return
                                                try:
                                                    page_blocks = explorer.get_current_page_blocks()
                                                    items_payload = []
                                                    for idx, block in page_blocks:
                                                        preview = get_block_preview(block)
                                                        items_payload.append({
                                                            "index": idx,
                                                            "title": preview,
                                                            "code": block,
                                                        })
                                                    self._controller.update_state(
                                                        blocks_active=True,
                                                        blocks_items=items_payload,
                                                        blocks_page=explorer.current_page,
                                                        blocks_total=len(explorer.blocks),
                                                    )
                                                except Exception:
                                                    pass

                                            # Initial render of popup
                                            _update_blocks_popup()
                                            
                                            # Enter block selection loop
                                            exploring_blocks = True
                                            while exploring_blocks:
                                                # Get user input for block exploration
                                                if self._controller:
                                                    # Get action from web controller only - no terminal fallback
                                                    block_action = self._controller.shared_state.get_action(timeout=0.5)
                                                    if not block_action:
                                                        # Continue waiting for web input
                                                        continue
                                                    # Handle stop_audio immediately
                                                    if block_action.strip().lower() == 'stop_audio':
                                                        if self._tts:
                                                            self._tts.stop()
                                                        continue  # Continue waiting for the real action
                                                else:
                                                    # Terminal interface - simple input for block exploration
                                                    print("\n[blocks] 0-9=select, n=next, p=prev, s=speed, q=quit: ", end='', flush=True)
                                                    try:
                                                        block_action = input().strip().lower()
                                                    except (EOFError, KeyboardInterrupt):
                                                        block_action = 'q'
                                                
                                                if block_action and block_action.isdigit():
                                                    # Select block by number
                                                    block_idx = int(block_action)
                                                    block = explorer.select_block(block_idx)
                                                    if block:
                                                        # Stop any current audio before starting new selection
                                                        if self._tts:
                                                            self._tts.stop()
                                                        # Speak block; if interrupted by another action, allow outer loop to handle it
                                                        explorer.speak_block(block)
                                                        # Keep popup open for further selections
                                                        _update_blocks_popup()
                                                    else:
                                                        # No code assigned to this index on current page
                                                        if self._tts:
                                                            self._tts.stop()
                                                            self._tts.speak("No Code")
                                                
                                                elif block_action in ['n', 'next']:
                                                    # Next page
                                                    if explorer.next_page():
                                                        _update_blocks_popup()
                                                    else:
                                                        # No further page; stay silent
                                                        pass
                                                
                                                elif block_action in ['prev', 'previous'] or (block_action == 'p' and not block_action.isdigit()):
                                                    # Previous page
                                                    if explorer.previous_page():
                                                        _update_blocks_popup()
                                                    else:
                                                        # No previous page; stay silent
                                                        pass
                                                
                                                elif block_action in ['s', 'speed']:
                                                    # Change speed
                                                    if self._controller and hasattr(self._controller.shared_state, 'cycle_audio_speed'):
                                                        new_speed = self._controller.shared_state.cycle_audio_speed()
                                                        self._tts.speak(f"Speed set to {new_speed}")
                                                        # Update TTS rate
                                                        if new_speed == "slow":
                                                            self._tts.rate_wpm = 150
                                                        elif new_speed == "fast":
                                                            self._tts.rate_wpm = 270
                                                        else:  # medium
                                                            self._tts.rate_wpm = 210
                                                        self._wait_for_speech_with_interrupt()
                                                
                                                elif block_action in ['q', 'quit', 'exit', 'done']:
                                                    # Exit block exploration
                                                    exploring_blocks = False
                                                    self._tts.speak("Exiting block exploration")
                                                    self._wait_for_speech_with_interrupt()
                                                    # Clear popup
                                                    if self._controller:
                                                        self._controller.update_state(
                                                            blocks_active=False,
                                                            blocks_items=[],
                                                            blocks_total=0,
                                                        )
                                                
                                                elif block_action in ['h', 'help']:
                                                    # Help
                                                    self._tts.speak("Enter 0 to 9 to select block, N for next page, P for previous page, S for speed, Q to quit")
                                                    self._wait_for_speech_with_interrupt()
                                        else:
                                            self._tts.speak("No function body available")
                                            self._wait_for_speech_with_interrupt()
                                    
                                    # Don't step after block exploration
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
                                                    # ALWAYS fetch complete data if there's a reference
                                                    var_ref = var_info.get("ref", 0)
                                                    if var_ref > 0 and self.client:
                                                        print(f"[Debug] Fetching COMPLETE data for '{var_name}' (ref={var_ref})")
                                                        complete_value = self._fetch_complete_value(var_ref)
                                                        if complete_value is not None:
                                                            value = complete_value
                                                            print(f"[Debug] Got complete structure for '{var_name}': {type(complete_value).__name__}")
                                                        else:
                                                            # Couldn't fetch, parse the string
                                                            value = self._parse_string_to_object(var_info["value"])
                                                    else:
                                                        # No reference, just parse the value
                                                        value = self._parse_string_to_object(var_info["value"])
                                                else:
                                                    value = self._parse_string_to_object(var_info) if isinstance(var_info, str) else var_info
                                                changed_vars.append((scope_name, var_name, value))

                                    if changed_vars:
                                        # Separate locals and globals
                                        local_vars = [(s, n, v) for s, n, v in changed_vars if s.lower() in ("locals", "local")]
                                        global_vars = [(s, n, v) for s, n, v in changed_vars if s.lower() in ("globals", "global")]
                                        
                                        # Determine scope selection mode
                                        scope_mode = None  # None = need to select, 'local', 'global', or 'both'
                                        if local_vars and global_vars:
                                            scope_mode = 'select'  # Need to choose
                                            active_vars = None  # Will be set after selection
                                        elif local_vars:
                                            scope_mode = 'local'
                                            active_vars = local_vars
                                        elif global_vars:
                                            scope_mode = 'global'
                                            active_vars = global_vars
                                        else:
                                            active_vars = changed_vars  # Fallback to all
                                        
                                        page = 0
                                        page_size = 10
                                        exploring = True
                                        announce_list = True  # Flag to control list announcement
                                        instructions_spoken = False  # Speak navigation hints only once per session
                                        while exploring:
                                            # Handle scope selection first if needed
                                            if scope_mode == 'select' and active_vars is None:
                                                if self._tts:
                                                    self._tts.speak("Select scope: 0 for local, 1 for global", interrupt=True)
                                                
                                                # Wait for scope selection
                                                scope_selection = None
                                                if self._controller:
                                                    while scope_selection is None:
                                                        scope_selection = self._controller.wait_for_action(0.2)
                                                        if scope_selection:
                                                            if scope_selection.strip().lower() == 'stop_audio':
                                                                if self._tts:
                                                                    self._tts.stop()
                                                                scope_selection = None
                                                                continue
                                                            scope_selection = scope_selection.strip().lower()
                                                            break
                                                
                                                if scope_selection == '0':
                                                    active_vars = local_vars
                                                    if self._tts:
                                                        self._tts.speak("Local variables selected", interrupt=True)
                                                elif scope_selection == '1':
                                                    active_vars = global_vars
                                                    if self._tts:
                                                        self._tts.speak("Global variables selected", interrupt=True)
                                                elif scope_selection == 'q':
                                                    exploring = False
                                                    break
                                                else:
                                                    # Default to local
                                                    active_vars = local_vars
                                                    if self._tts:
                                                        self._tts.speak("Defaulting to local variables", interrupt=True)
                                            
                                            if not active_vars:
                                                break
                                            
                                            start_idx = page * page_size
                                            end_idx = min(start_idx + page_size, len(active_vars))
                                            page_vars = active_vars[start_idx:end_idx]

                                            # Update web UI to show enumerated items
                                            if self._controller:
                                                items_payload = []
                                                for i, (scope_name, var_name, value) in enumerate(page_vars):
                                                    # Extract the actual value for preview if it's a DAP structure
                                                    preview_value = value["_parsed"] if isinstance(value, dict) and "_parsed" in value else value
                                                    preview = format_nested_value_summary(preview_value)
                                                    items_payload.append({
                                                        "index": i,
                                                        "name": f"{var_name} ({scope_name})",
                                                        "preview": preview,
                                                    })
                                                self._controller.update_state(
                                                    explore_active=True,
                                                    explore_mode='changes',  # Exploring changes section
                                                    explore_items=items_payload,
                                                    explore_page=page,
                                                    explore_total=len(active_vars),
                                                )

                                            # Audio announcements if enabled (only when announce_list is True)
                                            if self._tts and announce_list:
                                                self._tts.speak(f"Changed variables, page {page + 1}. Select 0 to {len(page_vars) - 1}", interrupt=True)
                                                # Don't wait - allow immediate selection
                                                for i, (_s, var_name, value) in enumerate(page_vars):
                                                    # Check if we should stop announcing
                                                    if self._controller:
                                                        action = self._controller.wait_for_action(0.01)
                                                        if action:
                                                            # Put it back and break out of announcements
                                                            self._controller.shared_state.send_action(action)
                                                            break
                                                    # Extract value and speak with code-style narration for punctuation
                                                    actual_value = value["_parsed"] if isinstance(value, dict) and "_parsed" in value else value
                                                    brief_value = format_nested_value_summary(actual_value)
                                                    self._tts.speak(f"{i}: {var_name} — {brief_value}", interrupt=True, is_code=True)
                                                if not instructions_spoken:
                                                    if end_idx < len(active_vars):
                                                        if page > 0:
                                                            self._tts.speak("Press 0 to 9 to explore, N for next page, P for previous, Q to quit", interrupt=True)
                                                        else:
                                                            self._tts.speak("Press 0 to 9 to explore, N for next page, Q to quit", interrupt=True)
                                                    else:
                                                        if page > 0:
                                                            self._tts.speak("Press 0 to 9 to explore, P for previous page, Q to quit", interrupt=True)
                                                        else:
                                                            self._tts.speak("Press 0 to 9 to explore, Q to quit", interrupt=True)
                                                    instructions_spoken = True
                                                announce_list = False  # Don't announce again until page changes

                                            # Await selection
                                            selection: Optional[str] = None
                                            if self._controller:
                                                while selection is None:
                                                    selection = self._controller.wait_for_action(0.2)
                                                    if selection:
                                                        # Handle stop_audio immediately
                                                        if selection.strip().lower() == 'stop_audio':
                                                            if self._tts:
                                                                self._tts.stop()
                                                            selection = None  # Continue waiting for the real selection
                                                            continue
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
                                            if selection == 's' or selection == 'speed':
                                                # Handle speed change during exploration
                                                if hasattr(self._controller, 'cycle_audio_speed'):
                                                    new_speed = self._controller.cycle_audio_speed()
                                                    if self._tts:
                                                        speed_rates = {"slow": 150, "medium": 210, "fast": 270}
                                                        self._tts.rate_wpm = speed_rates.get(new_speed, 210)
                                                        self._tts.speak(f"Speed {new_speed}")
                                                        self._wait_for_speech_with_interrupt()
                                                continue
                                            if selection == 'q' or selection == 'quit':
                                                exploring = False
                                                break
                                            if selection == 'n' and end_idx < len(active_vars):
                                                # Next page
                                                if self._tts:
                                                    self._tts.stop()
                                                page += 1
                                                announce_list = True  # Re-announce when changing pages
                                                continue
                                            if selection == 'p' and page > 0:
                                                # Previous page
                                                if self._tts:
                                                    self._tts.stop()
                                                page -= 1
                                                announce_list = True  # Re-announce when changing pages
                                                continue
                                            if selection.isdigit():
                                                idx = int(selection)
                                                if 0 <= idx < len(page_vars):
                                                    _scope, var_name, value = page_vars[idx]
                                                    if self._tts:
                                                        # Stop any current audio before starting new selection
                                                        self._tts.stop()
                                                    # Explore via nested explorer if available
                                                    if self._nested_explorer:
                                                        # Read the complete structure naturally
                                                        self._nested_explorer.read_complete_structure(var_name, value)
                                                    # Don't announce "Select another" - it interrupts the variable content
                                                else:
                                                    if self._tts:
                                                        self._tts.speak(f"Invalid selection {selection}")
                                                        # Don't wait - allow immediate retry
                                            # loop continues

                                        if self._tts:
                                            self._tts.speak("Exploration complete")
                                            self._wait_for_speech_with_interrupt()
                                        # Clear explore UI
                                        if self._controller:
                                            self._controller.update_state(explore_active=False, explore_mode=None, explore_items=[], explore_total=0)
                                    else:
                                        if self._tts:
                                            self._tts.speak("No changes to explore")
                                            self._wait_for_speech_with_interrupt()
                                        # Don't auto-step after exploring; re-prompt for next action
                                        should_step_after = False
                                        continue
                                
                                elif action == 'variables_explore':
                                    # Explore across all variables, not just changes
                                    all_vars: List[Tuple[str, str, Any]] = []  # (scope, var_name, value)
                                    for scope_name in ("Locals", "locals", "Local", "Globals", "globals"):
                                        scope_vars = vars_payload.get(scope_name)
                                        if isinstance(scope_vars, dict):
                                            for var_name, value in scope_vars.items():
                                                # Variables are now already parsed/fetched - use them directly
                                                all_vars.append((scope_name, var_name, value))

                                    if all_vars:
                                        # Separate locals and globals
                                        local_vars = [(s, n, v) for s, n, v in all_vars if s.lower() in ("locals", "local")]
                                        global_vars = [(s, n, v) for s, n, v in all_vars if s.lower() in ("globals", "global")]
                                        
                                        # Determine scope selection mode
                                        scope_mode = None
                                        if local_vars and global_vars:
                                            scope_mode = 'select'  # Need to choose
                                            active_vars = None  # Will be set after selection
                                        elif local_vars:
                                            scope_mode = 'local'
                                            active_vars = local_vars
                                        elif global_vars:
                                            scope_mode = 'global'
                                            active_vars = global_vars
                                        else:
                                            active_vars = all_vars  # Fallback to all
                                        
                                        page = 0
                                        page_size = 10
                                        exploring = True
                                        announce_list = True  # Flag to control list announcement
                                        instructions_spoken = False  # Speak navigation hints only once per session
                                        while exploring:
                                            # Handle scope selection first if needed
                                            if scope_mode == 'select' and active_vars is None:
                                                if self._tts:
                                                    self._tts.speak("Select scope: 0 for local, 1 for global", interrupt=True)
                                                
                                                # Wait for scope selection
                                                scope_selection = None
                                                if self._controller:
                                                    while scope_selection is None:
                                                        scope_selection = self._controller.wait_for_action(0.2)
                                                        if scope_selection:
                                                            if scope_selection.strip().lower() == 'stop_audio':
                                                                if self._tts:
                                                                    self._tts.stop()
                                                                scope_selection = None
                                                                continue
                                                            scope_selection = scope_selection.strip().lower()
                                                            break
                                                
                                                if scope_selection == '0':
                                                    active_vars = local_vars
                                                    if self._tts:
                                                        self._tts.speak("Local variables selected", interrupt=True)
                                                elif scope_selection == '1':
                                                    active_vars = global_vars
                                                    if self._tts:
                                                        self._tts.speak("Global variables selected", interrupt=True)
                                                elif scope_selection == 'q':
                                                    exploring = False
                                                    break
                                                else:
                                                    # Default to local
                                                    active_vars = local_vars
                                                    if self._tts:
                                                        self._tts.speak("Defaulting to local variables", interrupt=True)
                                            
                                            if not active_vars:
                                                break
                                            
                                            start_idx = page * page_size
                                            end_idx = min(start_idx + page_size, len(active_vars))
                                            page_vars = active_vars[start_idx:end_idx]

                                            # Update web UI to show enumerated items
                                            if self._controller:
                                                items_payload = []
                                                for i, (scope_name, var_name, value) in enumerate(page_vars):
                                                    # Value is already parsed/fetched - use directly
                                                    preview = format_nested_value_summary(value)
                                                    items_payload.append({
                                                        "index": i,
                                                        "name": f"{var_name} ({scope_name})",
                                                        "preview": preview,
                                                    })
                                                self._controller.update_state(
                                                    explore_active=True,
                                                    explore_mode='variables',  # Exploring variables section
                                                    explore_items=items_payload,
                                                    explore_page=page,
                                                    explore_total=len(active_vars),
                                                )

                                            # Audio announcements (only when announce_list is True)
                                            if self._tts and announce_list:
                                                self._tts.speak(f"Variables list, page {page + 1}. Select 0 to {len(page_vars) - 1}", interrupt=True)
                                                # Don't wait - allow immediate selection
                                                for i, (_s, var_name, value) in enumerate(page_vars):
                                                    # Check if we should stop announcing
                                                    if self._controller:
                                                        action = self._controller.wait_for_action(0.01)
                                                        if action:
                                                            # Put it back and break out of announcements
                                                            self._controller.shared_state.send_action(action)
                                                            break
                                                    # Value is already parsed/fetched - use directly
                                                    brief_value = format_nested_value_summary(value)
                                                    self._tts.speak(f"{i}: {var_name} — {brief_value}", interrupt=True, is_code=True)
                                                if not instructions_spoken:
                                                    if end_idx < len(active_vars):
                                                        if page > 0:
                                                            self._tts.speak("Press 0 to 9 to explore, N for next page, P for previous, Q to quit", interrupt=True)
                                                        else:
                                                            self._tts.speak("Press 0 to 9 to explore, N for next page, Q to quit", interrupt=True)
                                                    else:
                                                        if page > 0:
                                                            self._tts.speak("Press 0 to 9 to explore, P for previous page, Q to quit", interrupt=True)
                                                        else:
                                                            self._tts.speak("Press 0 to 9 to explore, Q to quit", interrupt=True)
                                                    instructions_spoken = True
                                                announce_list = False  # Don't announce again until page changes

                                            # Await selection
                                            selection: Optional[str] = None
                                            if self._controller:
                                                while selection is None:
                                                    selection = self._controller.wait_for_action(0.2)
                                                    if selection:
                                                        # Handle stop_audio immediately
                                                        if selection.strip().lower() == 'stop_audio':
                                                            if self._tts:
                                                                self._tts.stop()
                                                            selection = None  # Continue waiting for the real selection
                                                            continue
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
                                            if selection == 's' or selection == 'speed':
                                                # Handle speed change during exploration
                                                if hasattr(self._controller, 'cycle_audio_speed'):
                                                    new_speed = self._controller.cycle_audio_speed()
                                                    if self._tts:
                                                        speed_rates = {"slow": 150, "medium": 210, "fast": 270}
                                                        self._tts.rate_wpm = speed_rates.get(new_speed, 210)
                                                        self._tts.speak(f"Speed {new_speed}")
                                                        self._wait_for_speech_with_interrupt()
                                                continue
                                            if selection == 'q' or selection == 'quit':
                                                exploring = False
                                                break
                                            if selection == 'n' and end_idx < len(active_vars):
                                                # Next page
                                                if self._tts:
                                                    self._tts.stop()
                                                page += 1
                                                announce_list = True  # Re-announce when changing pages
                                                continue
                                            if selection == 'p' and page > 0:
                                                # Previous page
                                                if self._tts:
                                                    self._tts.stop()
                                                page -= 1
                                                announce_list = True  # Re-announce when changing pages
                                                continue
                                            if selection.isdigit():
                                                idx = int(selection)
                                                if 0 <= idx < len(page_vars):
                                                    _scope, var_name, value = page_vars[idx]
                                                    if self._tts:
                                                        # Stop any current audio before starting new selection
                                                        self._tts.stop()
                                                    if self._nested_explorer:
                                                        # Read the complete structure naturally
                                                        self._nested_explorer.read_complete_structure(var_name, value)
                                                    # Don't announce "Select another" - it interrupts the variable content
                                                else:
                                                    if self._tts:
                                                        self._tts.speak(f"Invalid selection {selection}")
                                                        # Don't wait - allow immediate retry
                                            # loop continues

                                        if self._tts:
                                            self._tts.speak("Variables exploration complete")
                                            self._wait_for_speech_with_interrupt()
                                        if self._controller:
                                            self._controller.update_state(explore_active=False, explore_mode=None, explore_items=[], explore_total=0)
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
