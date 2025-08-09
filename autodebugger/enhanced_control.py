"""Enhanced manual stepping controls with variable and function display."""

from __future__ import annotations

import ast
import json
import os
import queue
import select
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs


def find_free_port() -> int:
    """Find an available port for the HTTP server."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def extract_function_context(file_path: str, line: int) -> Dict[str, Any]:
    """Extract function context for a given file and line."""
    if not file_path or not os.path.isfile(file_path):
        return {"name": None, "sig": None, "body": None}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except Exception:
        return {"name": None, "sig": None, "body": None}
    
    def _extract_sig_and_body(source: str, node: ast.AST) -> Tuple[str, str]:
        try:
            segment = ast.get_source_segment(source, node) or ""
        except Exception:
            segment = ""
        lines = segment.splitlines()
        if not lines:
            return "", ""
        
        # Capture signature lines up to the first line ending with ':'
        sig_lines: List[str] = []
        body_lines: List[str] = []
        found_colon = False
        for ln in lines:
            sig_lines.append(ln)
            if ln.rstrip().endswith(":"):
                found_colon = True
                break
        if found_colon:
            body_lines = lines[len(sig_lines):]
        else:
            sig_lines = [lines[0]] if lines else []
            body_lines = lines[1:] if len(lines) > 1 else []
        
        sig = "\n".join(sig_lines).strip()
        body = "\n".join(body_lines).strip()
        # Truncate body for UI
        if len(body) > 800:
            body = body[:797] + "..."
        return sig, body
    
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.result = {"name": None, "sig": None, "body": None}
            self.stack: List[str] = []
            
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()
            
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._check_function(node)
            self.generic_visit(node)
            
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._check_function(node)
            self.generic_visit(node)
            
        def _check_function(self, node):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if isinstance(start, int) and isinstance(end, int):
                if start <= line <= end:
                    qual = ".".join(self.stack + [node.name]) if self.stack else node.name
                    sig, body = _extract_sig_and_body(source, node)
                    # Keep the most specific (innermost) function
                    if self.result["name"] is None or (end - start) < len(self.result.get("body", "")):
                        self.result = {"name": qual, "sig": sig, "body": body}
    
    visitor = Visitor()
    visitor.visit(tree)
    return visitor.result


class SharedState:
    """Thread-safe shared state for the manual stepping controller."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._action_queue = queue.Queue()
        self._current_state = {
            "session_id": "",
            "file": "",
            "line": 0,
            "code": "",
            "waiting": False,
            "mode": "manual",
            "variables": {},
            "variables_delta": {},
            "function_name": None,
            "function_sig": None,
            "function_body": None
        }
    
    def update_state(self, **kwargs):
        with self._lock:
            # Extract function context if file and line are provided
            if "file" in kwargs and "line" in kwargs and kwargs["line"] > 0:
                func_context = extract_function_context(kwargs["file"], kwargs["line"])
                kwargs.update({
                    "function_name": func_context["name"],
                    "function_sig": func_context["sig"],
                    "function_body": func_context["body"]
                })
            self._current_state.update(kwargs)
    
    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._current_state.copy()
    
    def send_action(self, action: str):
        self._action_queue.put(action)
    
    def get_action(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self._action_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def clear_actions(self):
        while not self._action_queue.empty():
            try:
                self._action_queue.get_nowait()
            except queue.Empty:
                break


class StepControlHandler(BaseHTTPRequestHandler):
    """HTTP request handler for manual step control."""
    
    @property
    def shared(self) -> SharedState:
        return self.server.shared_state  # type: ignore
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass
    
    def _send(self, code: int, data: Any):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)
    
    def _send_html(self, html: str):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def do_GET(self):
        if self.path == "/state":
            self._send(200, self.shared.get_state())
        elif self.path == "/" or self.path.startswith("/index.html"):
            html = """<!doctype html>
<html>
<head>
    <meta charset='utf-8'>
    <title>AutoDebugger Manual Control</title>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 1fr 400px;
            gap: 20px;
        }
        @media (max-width: 1000px) {
            .container {
                grid-template-columns: 1fr;
            }
        }
        .card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .status {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 10px;
            margin-bottom: 20px;
        }
        .status-label {
            font-weight: bold;
            color: #666;
        }
        .status-value {
            font-family: monospace;
        }
        .code-block {
            background: #f8f8f8;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 15px;
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 14px;
            margin: 15px 0;
            overflow-x: auto;
            white-space: pre;
            cursor: pointer;
            position: relative;
        }
        .code-block:hover {
            background: #f0f0f0;
        }
        .function-context {
            background: #e8f4fd;
            border: 1px solid #bee5eb;
            border-radius: 4px;
            padding: 15px;
            margin: 15px 0;
            display: none;
        }
        .function-context.visible {
            display: block;
        }
        .function-sig {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 14px;
            font-weight: bold;
            color: #0066cc;
            margin-bottom: 10px;
        }
        .function-body {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 13px;
            color: #333;
            white-space: pre;
            overflow-x: auto;
            max-height: 300px;
            overflow-y: auto;
        }
        .controls {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        button {
            padding: 10px 20px;
            border-radius: 6px;
            border: 1px solid #ccc;
            background: white;
            cursor: pointer;
            font-size: 16px;
            transition: all 0.2s;
        }
        button:hover {
            background: #f0f0f0;
        }
        button.primary {
            background: #007bff;
            color: white;
            border-color: #0056b3;
        }
        button.primary:hover {
            background: #0056b3;
        }
        button.danger {
            background: #dc3545;
            color: white;
            border-color: #bd2130;
        }
        button.danger:hover {
            background: #bd2130;
        }
        button.success {
            background: #28a745;
            color: white;
            border-color: #1e7e34;
        }
        button.success:hover {
            background: #1e7e34;
        }
        .waiting {
            color: #28a745;
            font-weight: bold;
        }
        .running {
            color: #ffc107;
            font-weight: bold;
        }
        .mode-indicator {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .mode-manual {
            background: #007bff;
            color: white;
        }
        .mode-auto {
            background: #28a745;
            color: white;
        }
        .variables {
            max-height: 500px;
            overflow-y: auto;
        }
        .variable-item {
            display: grid;
            grid-template-columns: minmax(100px, auto) 1fr;
            gap: 10px;
            padding: 8px;
            border-bottom: 1px solid #eee;
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 13px;
        }
        .variable-item:hover {
            background: #f8f8f8;
        }
        .variable-name {
            font-weight: bold;
            color: #0066cc;
            word-break: break-word;
        }
        .variable-value {
            color: #333;
            word-break: break-word;
        }
        .variable-changed {
            background: #fff3cd;
            animation: highlight 1s ease-out;
        }
        @keyframes highlight {
            from { background: #ffc107; }
            to { background: #fff3cd; }
        }
        .section-title {
            font-size: 14px;
            font-weight: bold;
            color: #666;
            margin: 15px 0 10px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .hint {
            color: #999;
            font-size: 12px;
            font-style: italic;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="main">
            <div class="card">
                <h1>AutoDebugger Manual Control</h1>
                <div class="status">
                    <span class="status-label">Session:</span>
                    <span class="status-value" id="session">-</span>
                    
                    <span class="status-label">File:</span>
                    <span class="status-value" id="file">-</span>
                    
                    <span class="status-label">Line:</span>
                    <span class="status-value" id="line">-</span>
                    
                    <span class="status-label">Function:</span>
                    <span class="status-value" id="function">-</span>
                    
                    <span class="status-label">Status:</span>
                    <span class="status-value" id="status">-</span>
                    
                    <span class="status-label">Mode:</span>
                    <span class="status-value"><span id="mode" class="mode-indicator">-</span></span>
                </div>
                
                <div class="section-title">Current Line</div>
                <div class="code-block" id="code" onclick="toggleFunction()">Waiting for debugger...</div>
                <div class="hint">Click to show/hide function context</div>
                
                <div class="function-context" id="functionContext">
                    <div class="function-sig" id="functionSig">-</div>
                    <div class="function-body" id="functionBody">-</div>
                </div>
                
                <div class="controls">
                    <button class="primary" onclick="sendAction('step')">Step (Enter)</button>
                    <button class="success" onclick="sendAction('auto')">Auto Mode</button>
                    <button onclick="sendAction('continue')">Continue</button>
                    <button class="danger" onclick="sendAction('quit')">Quit</button>
                </div>
            </div>
            
            <div class="card">
                <h3>Keyboard Shortcuts</h3>
                <ul>
                    <li><b>Enter</b> - Step to next line</li>
                    <li><b>a</b> - Switch to auto mode</li>
                    <li><b>c</b> - Continue execution</li>
                    <li><b>q</b> - Quit debugging</li>
                    <li><b>f</b> - Toggle function context</li>
                </ul>
            </div>
        </div>
        
        <div class="sidebar">
            <div class="card">
                <h3>Variables</h3>
                <div class="variables" id="variables">
                    <div style="color: #999; text-align: center; padding: 20px;">
                        No variables yet
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h3>Changes</h3>
                <div class="variables" id="changes">
                    <div style="color: #999; text-align: center; padding: 20px;">
                        No changes yet
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentState = {};
        let previousVariables = {};
        
        async function fetchState() {
            try {
                const response = await fetch('/state');
                const state = await response.json();
                updateUI(state);
                currentState = state;
            } catch (e) {
                console.error('Failed to fetch state:', e);
            }
        }
        
        function formatValue(value) {
            if (typeof value === 'object' && value !== null) {
                try {
                    return JSON.stringify(value, null, 2);
                } catch {
                    return String(value);
                }
            }
            return String(value);
        }
        
        function renderVariables(variables, containerId, highlight = false) {
            const container = document.getElementById(containerId);
            if (!variables || Object.keys(variables).length === 0) {
                container.innerHTML = '<div style="color: #999; text-align: center; padding: 20px;">No ' + 
                                     (containerId === 'changes' ? 'changes' : 'variables') + ' yet</div>';
                return;
            }
            
            let html = '';
            for (const [scope, vars] of Object.entries(variables)) {
                if (vars && typeof vars === 'object' && Object.keys(vars).length > 0) {
                    html += '<div class="section-title">' + scope + '</div>';
                    for (const [name, value] of Object.entries(vars)) {
                        const changed = highlight && previousVariables[scope] && 
                                      previousVariables[scope][name] !== value;
                        html += '<div class="variable-item' + (changed ? ' variable-changed' : '') + '">';
                        html += '<div class="variable-name">' + name + '</div>';
                        html += '<div class="variable-value">' + formatValue(value) + '</div>';
                        html += '</div>';
                    }
                }
            }
            container.innerHTML = html || '<div style="color: #999; text-align: center; padding: 20px;">Empty</div>';
        }
        
        function updateUI(state) {
            document.getElementById('session').textContent = state.session_id || '-';
            document.getElementById('file').textContent = state.file ? 
                state.file.split('/').pop() : '-';
            document.getElementById('line').textContent = state.line || '-';
            document.getElementById('function').textContent = state.function_name || '-';
            
            const statusEl = document.getElementById('status');
            if (state.waiting) {
                statusEl.innerHTML = '<span class="waiting">Waiting for input</span>';
            } else {
                statusEl.innerHTML = '<span class="running">Running</span>';
            }
            
            const modeEl = document.getElementById('mode');
            modeEl.textContent = state.mode || 'manual';
            modeEl.className = 'mode-indicator mode-' + (state.mode || 'manual');
            
            document.getElementById('code').textContent = state.code || 'No code';
            
            // Update function context
            if (state.function_sig) {
                document.getElementById('functionSig').textContent = state.function_sig;
                document.getElementById('functionBody').textContent = state.function_body || '';
            } else {
                document.getElementById('functionSig').textContent = 'Not in a function';
                document.getElementById('functionBody').textContent = '';
            }
            
            // Update variables
            if (state.variables) {
                renderVariables(state.variables, 'variables');
                previousVariables = state.variables;
            }
            
            // Update changes
            if (state.variables_delta) {
                renderVariables(state.variables_delta, 'changes', true);
            }
        }
        
        function toggleFunction() {
            const ctx = document.getElementById('functionContext');
            ctx.classList.toggle('visible');
        }
        
        async function sendAction(action) {
            try {
                await fetch('/command', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action: action})
                });
                // Update state immediately after action
                setTimeout(fetchState, 100);
            } catch (e) {
                console.error('Failed to send action:', e);
            }
        }
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendAction('step');
            } else if (e.key === 'a' || e.key === 'A') {
                sendAction('auto');
            } else if (e.key === 'c' || e.key === 'C') {
                sendAction('continue');
            } else if (e.key === 'q' || e.key === 'Q') {
                sendAction('quit');
            } else if (e.key === 'f' || e.key === 'F') {
                toggleFunction();
            }
        });
        
        // Poll for state updates
        setInterval(fetchState, 500);
        fetchState();
    </script>
</body>
</html>"""
            self._send_html(html)
        else:
            self.send_error(404)
    
    def do_POST(self):
        if self.path == "/command":
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length))
            action = data.get('action', '')
            if action in ['step', 'auto', 'continue', 'quit']:
                self.shared.send_action(action)
                self._send(200, {"status": "ok", "action": action})
            else:
                self._send(400, {"error": "Invalid action"})
        else:
            self.send_error(404)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


class HttpStepController:
    """HTTP server for manual step control."""
    
    def __init__(self, port: Optional[int] = None):
        self.port = port or find_free_port()
        self.shared_state = SharedState()
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start the HTTP server in a background thread."""
        self.server = HTTPServer(('127.0.0.1', self.port), StepControlHandler)
        self.server.shared_state = self.shared_state  # type: ignore
        
        def run_server():
            try:
                self.server.serve_forever()
            except Exception:
                pass
        
        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        
        # Give server time to start
        import time
        time.sleep(0.1)
        print(f"\n[manual-web] Open http://127.0.0.1:{self.port} to control stepping.\n", flush=True)
    
    def stop(self):
        """Stop the HTTP server."""
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass
    
    def update_state(self, **kwargs):
        """Update the current debugger state."""
        self.shared_state.update_state(**kwargs)
    
    def wait_for_action(self, timeout: Optional[float] = None) -> Optional[str]:
        """Wait for a user action."""
        return self.shared_state.get_action(timeout)
    
    def clear_actions(self):
        """Clear any pending actions."""
        self.shared_state.clear_actions()


def prompt_for_action(timeout: Optional[float] = None) -> Optional[str]:
    """Prompt user for manual stepping action via stdin."""
    print("\n[manual] Press Enter to step, 'a' for auto, 'c' to continue, 'q' to quit: ", end='', flush=True)
    
    if timeout is not None:
        # Use select for timeout on Unix-like systems
        if hasattr(select, 'select'):
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if not rlist:
                return None
        else:
            # On Windows, just do blocking read
            pass
    
    try:
        response = input().strip().lower()
        if response == '' or response == 'step':
            return 'step'
        elif response in ['a', 'auto']:
            return 'auto'
        elif response in ['c', 'continue']:
            return 'continue'
        elif response in ['q', 'quit', 'exit']:
            return 'quit'
        else:
            return 'step'  # Default to step
    except (EOFError, KeyboardInterrupt):
        return 'quit'