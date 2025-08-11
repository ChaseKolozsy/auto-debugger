from __future__ import annotations

import json
import queue
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs


def find_free_port() -> int:
    """Find an available port for the HTTP server."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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
            max-width: 900px;
            margin: 0 auto;
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
            font-family: monospace;
            font-size: 14px;
            margin: 15px 0;
            overflow-x: auto;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>AutoDebugger Manual Control</h1>
            <div class="status">
                <span class="status-label">Session:</span>
                <span class="status-value" id="session">-</span>
                
                <span class="status-label">File:</span>
                <span class="status-value" id="file">-</span>
                
                <span class="status-label">Line:</span>
                <span class="status-value" id="line">-</span>
                
                <span class="status-label">Status:</span>
                <span class="status-value" id="status">-</span>
                
                <span class="status-label">Mode:</span>
                <span class="status-value"><span id="mode" class="mode-indicator">-</span></span>
            </div>
            
            <div class="code-block" id="code">Waiting for debugger...</div>
            
            <div class="controls">
                <button class="primary" onclick="sendAction('step')">Step (Enter)</button>
                <button onclick="sendAction('variables')">Variables (V)</button>
                <button onclick="sendAction('function')">Function (F)</button>
                <button onclick="sendAction('explore')">Explore Changes (E)</button>
                <button onclick="sendAction('variables_explore')">Explore Variables (W)</button>
                <button class="success" onclick="sendAction('auto')">Auto Mode (A)</button>
                <button onclick="sendAction('continue')">Continue</button>
                <button class="danger" onclick="sendAction('quit')">Quit (Q)</button>
            </div>
        </div>
        
        <div class="card">
            <h3>Keyboard Shortcuts</h3>
            <ul>
                <li><b>Enter</b> - Step to next line</li>
                <li><b>v</b> - Read all variables</li>
                <li><b>f</b> - Read function context</li>
                <li><b>e</b> - Explore changed variables</li>
                <li><b>w</b> - Explore all variables</li>
                <li><b>a</b> - Switch to auto mode</li>
                <li><b>Continue button</b> - Continue execution (c shortcut removed for copy/paste)</li>
                <li><b>q</b> - Quit debugging</li>
            </ul>
        </div>
    </div>
    
    <script>
        let currentState = {};
        
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
        
        function updateUI(state) {
            document.getElementById('session').textContent = state.session_id || '-';
            document.getElementById('file').textContent = state.file ? 
                state.file.split('/').pop() : '-';
            document.getElementById('line').textContent = state.line || '-';
            
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
            } else if (e.key === 'v' || e.key === 'V') {
                sendAction('variables');
            } else if (e.key === 'f' || e.key === 'F') {
                sendAction('function');
            } else if (e.key === 'e' || e.key === 'E') {
                sendAction('explore');
            } else if (e.key === 'w' || e.key === 'W') {
                sendAction('variables_explore');
            } else if (e.key === 'a' || e.key === 'A') {
                sendAction('auto');
            // 'c' shortcut removed to allow copy/paste
            } else if (e.key === 'q' || e.key === 'Q') {
                sendAction('quit');
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
            action = str(data.get('action', '') or '')
            # Accept all actions and forward to the queue. The runner will validate.
            if action:
                self.shared.send_action(action)
                self._send(200, {"status": "ok", "action": action})
            else:
                self._send(400, {"error": "Missing action"})
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
    print("\n[manual] Enter=step, v=vars, f=function, e=explore, a=auto, c=continue, q=quit: ", end='', flush=True)
    
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
        elif response in ['v', 'vars', 'variables']:
            return 'variables'
        elif response in ['f', 'func', 'function']:
            return 'function'
        elif response in ['e', 'explore']:
            return 'explore'
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