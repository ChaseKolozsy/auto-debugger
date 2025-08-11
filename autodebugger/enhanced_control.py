"""Enhanced manual stepping controls with variable and function display."""

from __future__ import annotations

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
    """Extract function context for a given file and line - optimized version."""
    if not file_path or not os.path.isfile(file_path):
        return {"name": None, "sig": None, "body": None}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        if line <= 0 or line > len(lines):
            return {"name": None, "sig": None, "body": None}
        
        # Simple heuristic: look backwards for def/class without AST parsing
        func_name = None
        func_sig = None
        func_body_lines = []
        
        # Start from current line and go backwards
        for i in range(line - 1, max(-1, line - 100), -1):
            if i < len(lines):
                stripped = lines[i].strip()
                # Check indentation to see if we're still in the same scope
                if i < line - 1:
                    # If we hit a line with no indentation (except empty lines), we're out of the function
                    if lines[i] and not lines[i][0].isspace() and not stripped.startswith('#'):
                        # Unless it's a decorator
                        if not stripped.startswith('@'):
                            break
                
                if stripped.startswith(('def ', 'async def ')):
                    # Found a function definition
                    func_sig = lines[i].rstrip()
                    # Extract name from signature
                    if 'def ' in stripped:
                        name_part = stripped.split('def ', 1)[1]
                    else:
                        name_part = stripped.split('async def ', 1)[1]
                    func_name = name_part.split('(')[0].strip()
                    
                    # Get next few lines for body
                    for j in range(i + 1, min(i + 6, len(lines))):
                        if j < len(lines):
                            func_body_lines.append(lines[j].rstrip())
                    break
                elif stripped.startswith('class '):
                    # Found a class definition
                    func_sig = lines[i].rstrip()
                    func_name = stripped.split('class ', 1)[1].split('(')[0].split(':')[0].strip()
                    # For classes, check if we're in __init__ or another method
                    for j in range(i + 1, min(line, i + 50)):
                        if j < len(lines):
                            method_stripped = lines[j].strip()
                            if method_stripped.startswith(('def ', 'async def ')):
                                # We might be in a method
                                if j <= line - 1:
                                    # Update to the method
                                    func_sig = lines[j].rstrip()
                                    if 'def ' in method_stripped:
                                        method_name = method_stripped.split('def ', 1)[1].split('(')[0].strip()
                                    else:
                                        method_name = method_stripped.split('async def ', 1)[1].split('(')[0].strip()
                                    func_name = f"{func_name}.{method_name}"
                                    func_body_lines = []
                                    for k in range(j + 1, min(j + 6, len(lines))):
                                        func_body_lines.append(lines[k].rstrip())
                    break
        
        func_body = '\n'.join(func_body_lines[:5]) if func_body_lines else None
        return {"name": func_name, "sig": func_sig, "body": func_body}
        
    except Exception:
        return {"name": None, "sig": None, "body": None}


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
            "function_body": None,
            "audio_enabled": False,  # Will be set based on --manual-audio flag
            "audio_available": False,  # Will be set if TTS is available
            "function_panel_open": False  # Track if function panel is visible
        }
        self._function_cache = {}  # Cache function contexts by (file, line)
    
    def update_state(self, **kwargs):
        # Check if we need to extract function context
        file_path = kwargs.get("file")
        line = kwargs.get("line", 0)
        
        # Extract function context BEFORE updating state (synchronously)
        if file_path and line > 0:
            cache_key = (file_path, line)
            if cache_key not in self._function_cache:
                # Extract synchronously
                try:
                    func_context = extract_function_context(file_path, line)
                    self._function_cache[cache_key] = {
                        "function_name": func_context["name"],
                        "function_sig": func_context["sig"],
                        "function_body": func_context["body"]
                    }
                except Exception:
                    self._function_cache[cache_key] = {
                        "function_name": None,
                        "function_sig": None,
                        "function_body": None
                    }
        
        with self._lock:
            self._current_state.update(kwargs)
            
            # Apply cached function context if available
            if file_path and line > 0:
                cache_key = (file_path, line)
                if cache_key in self._function_cache:
                    self._current_state.update(self._function_cache[cache_key])
    
    
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
    
    def toggle_audio(self) -> bool:
        """Toggle audio on/off and return new state."""
        with self._lock:
            self._current_state["audio_enabled"] = not self._current_state["audio_enabled"]
            return self._current_state["audio_enabled"]
    
    def get_audio_state(self) -> bool:
        """Get current audio state."""
        with self._lock:
            return self._current_state["audio_enabled"]


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
            # Don't log state requests as they happen every 500ms
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
        button.warning {
            background: #ffc107;
            color: #212529;
            border-color: #ffc107;
        }
        button.warning:hover {
            background: #e0a800;
        }
        #audioToggle {
            background: #6c757d;
            color: white;
            border-color: #6c757d;
        }
        #audioToggle:hover {
            background: #5a6268;
        }
        #audioToggle.audio-on {
            background: #17a2b8;
            border-color: #17a2b8;
        }
        #audioToggle.audio-on:hover {
            background: #138496;
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
                    <button id="audioToggle" onclick="toggleAudio()">🔇 Audio Off</button>
                </div>
            </div>
            
            <div class="card">
                <h3>Keyboard Shortcuts</h3>
                <ul>
                    <li><b>Enter</b> - Step to next line</li>
                    <li><b>a</b> - Switch to auto mode</li>
                    <li><b>c</b> - Continue execution</li>
                    <li><b>q</b> - Quit debugging</li>
                    <li><b>x</b> - Toggle function context</li>
                    <li><b>m</b> - Toggle audio mute/unmute</li>
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
        let lastAnnouncedFunction = null;  // Track last function we announced
        let functionPanelOpen = false;     // Track if function panel is open
        
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
            
            // Track function changes but don't trigger audio from here
            // Audio is handled entirely by runner.py to avoid conflicts
            if (state.function_name !== lastAnnouncedFunction) {
                lastAnnouncedFunction = state.function_name;
            }
            
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
            
            // Update audio button
            const audioBtn = document.getElementById('audioToggle');
            if (state.audio_available) {
                audioBtn.style.display = 'inline-block';
                if (state.audio_enabled) {
                    audioBtn.textContent = '🔊 Audio On';
                    audioBtn.classList.add('audio-on');
                } else {
                    audioBtn.textContent = '🔇 Audio Off';
                    audioBtn.classList.remove('audio-on');
                }
            } else {
                audioBtn.style.display = 'none';
            }
        }
        
        function toggleFunction() {
            console.log('toggleFunction called');
            const ctx = document.getElementById('functionContext');
            console.log('Got element:', ctx);
            ctx.classList.toggle('visible');
            functionPanelOpen = ctx.classList.contains('visible');
            console.log('Toggle complete, visible:', functionPanelOpen);
            
            // Notify server about panel state change
            fetch('/panel-state', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({panel_open: functionPanelOpen})
            }).catch(e => {
                console.error('Failed to update panel state:', e);
            });
        }
        
        
        async function toggleAudio() {
            try {
                const response = await fetch('/toggle-audio', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'}
                });
                const result = await response.json();
                // Update will happen via regular state polling
            } catch (e) {
                console.error('Failed to toggle audio:', e);
            }
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
            } else if (e.key === 'x' || e.key === 'X') {
                toggleFunction();
            } else if (e.key === 'm' || e.key === 'M') {
                toggleAudio();
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
        elif self.path == "/toggle-audio":
            new_state = self.shared.toggle_audio()
            self._send(200, {"status": "ok", "audio_enabled": new_state})
        elif self.path == "/panel-state":
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length))
            panel_open = data.get('panel_open', False)
            self.shared.update_state(function_panel_open=panel_open)
            self._send(200, {"status": "ok", "panel_open": panel_open})
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
    
    def __init__(self, port: Optional[int] = None, tts: Optional[Any] = None):
        self.port = port or find_free_port()
        self.shared_state = SharedState()
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.tts = tts
    
    def start(self):
        """Start the HTTP server in a background thread."""
        self.server = HTTPServer(('127.0.0.1', self.port), StepControlHandler)
        self.server.shared_state = self.shared_state  # type: ignore
        self.server.tts = self.tts  # type: ignore
        
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
    
    def is_audio_enabled(self) -> bool:
        """Check if audio is enabled."""
        return self.shared_state.get_audio_state()
    
    def set_audio_state(self, enabled: bool, available: bool = True):
        """Set audio state."""
        self.shared_state.update_state(audio_enabled=enabled, audio_available=available)


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