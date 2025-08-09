from __future__ import annotations

import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import debugpy

from .dap_client import DapClient
from .db import LineReport, LineReportStore, SessionSummary


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

    def run(self, script_path: str, args: Optional[List[str]] = None, just_my_code: bool = True, stop_on_entry: bool = True) -> str:
        script_abs = os.path.abspath(script_path)
        self.db.open()
        self.db.create_session(
            SessionSummary(
                session_id=self.session_id,
                file=script_abs,
                language="python",
                start_time=utc_now_iso(),
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
            launch_args = {
                "name": "Python: AutoDebug",
                "type": "python",
                "request": "launch",
                "console": "internalConsole",
                "cwd": parent_dir or os.getcwd(),
                "justMyCode": just_my_code,
                "stopOnEntry": stop_on_entry,
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
            # Provide an empty setBreakpoints for the main script to conform to some adapters
            try:
                client.request("setBreakpoints", {
                    "source": {"path": script_abs},
                    "breakpoints": []
                }, wait=10.0)
            except Exception:
                pass
            # Send configurationDone to start execution
            client.request("configurationDone", {}, wait=15.0)
            # Now wait for launch response (non-fatal if it times out quickly)
            try:
                _ = client.wait_response(launch_seq, wait=10.0)
            except TimeoutError:
                pass

            # Event loop: collect stopped events and fetch scopes/variables, emit line reports until terminated
            threads: Dict[int, None] = {}
            running = True
            while running:
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
                        # Query stack, scopes, variables
                        st = client.request("stackTrace", {"threadId": thread_id})
                        frames = st.body.get("stackFrames", []) if st.body else []
                        if not frames:
                            continue
                        frame = frames[0]
                        file_path = frame.get("source", {}).get("path") or ""
                        line = int(frame.get("line") or 0)

                        # Grab code line
                        code = ""
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                if 1 <= line <= len(lines):
                                    code = lines[line - 1].rstrip("\n")
                        except Exception:
                            code = ""

                        # Scopes -> variables
                        scopes = client.request("scopes", {"frameId": frame.get("id")})
                        vars_payload: Dict[str, Any] = {}
                        for sc in scopes.body.get("scopes", []) if scopes.body else []:
                            name = sc.get("name")
                            vr = sc.get("variablesReference")
                            if not vr:
                                continue
                            vres = client.request("variables", {"variablesReference": vr})
                            var_list = vres.body.get("variables", []) if vres.body else []
                            # Convert to simple name->value map for quick glance
                            simple_map: Dict[str, Any] = {}
                            for v in var_list:
                                simple_map[str(v.get("name"))] = v.get("value")
                            vars_payload[str(name)] = simple_map

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

                        self.db.add_line_report(
                            LineReport(
                                session_id=self.session_id,
                                file=file_path,
                                line_number=line,
                                code=code,
                                timestamp=utc_now_iso(),
                                variables=vars_payload,
                                stack_depth=len(frames),
                                thread_id=thread_id,
                                observations=None,
                                status=status,
                                error_message=error_message,
                                error_type=error_type,
                                stack_trace=stack_trace_text,
                            )
                        )

                        # Step into to capture lines inside function calls as well
                        client.request("stepIn", {"threadId": thread_id})

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
            self._stop_adapter()
            self.db.close()
