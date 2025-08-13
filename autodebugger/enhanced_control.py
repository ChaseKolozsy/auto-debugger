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

from .common import extract_function_context


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
            "function_body": None,
            "audio_enabled": False,  # Will be set based on --manual-audio flag
            "audio_available": False,  # Will be set if TTS is available
            "audio_speed": "medium",  # Speech speed: slow, medium, fast
            "function_panel_open": False,  # Track if function panel is visible
            # Explore UX state for web interactions
            "explore_active": False,
            "explore_items": [],   # list of {index, name, preview}
            "explore_page": 0,
            "explore_total": 0,
            "explore_mode": None  # 'changes' or 'variables' to indicate which section
            ,
            # Popup for function parts (blocks) exploration
            "blocks_active": False,
            "blocks_items": [],   # list of {index, title, code}
            "blocks_page": 0,
            "blocks_total": 0
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
    
    def cycle_audio_speed(self) -> str:
        """Cycle through audio speeds and return new speed."""
        with self._lock:
            current = self._current_state["audio_speed"]
            if current == "slow":
                self._current_state["audio_speed"] = "medium"
            elif current == "medium":
                self._current_state["audio_speed"] = "fast"
            else:  # fast
                self._current_state["audio_speed"] = "slow"
            return self._current_state["audio_speed"]
    
    def get_audio_speed(self) -> str:
        """Get current audio speed."""
        with self._lock:
            return self._current_state["audio_speed"]


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
            # Check if our changes are in the HTML
            if '#f9fafb' in open(__file__).read():
                print(f"[DEBUG] Source file has new background color #f9fafb", file=sys.stderr)
            else:
                print(f"[DEBUG] WARNING: Source file missing new background color!", file=sys.stderr)
            html = """<!doctype html>
<html>
<head>
    <meta charset='utf-8'>
    <title>AutoDebugger Manual Control v2</title>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
        /* VERSION 2 STYLES - DARK BLUE THEME LIKE SESSION REVIEWER */
        body {
            font-family: system-ui, -apple-system, sans-serif;
            margin: 24px;
            background: #0b1020 !important;  /* Dark blue background */
            color: #e5e7eb;
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
            background: linear-gradient(180deg, #0f172a, #0b1020);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.4);
            border: 1px solid #263041;
        }
        h1, h2, h3 {
            color: #e5e7eb;
        }
        .status {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 10px;
            margin-bottom: 20px;
        }
        .status-label {
            font-weight: bold;
            color: #94a3b8;
        }
        .status-value {
            font-family: monospace;
            color: #e5e7eb;
        }
        .code-block {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 16px;
            font-family: 'Monaco', 'Menlo', 'SF Mono', monospace;
            font-size: 13px;
            margin: 15px 0;
            overflow-x: auto;
            white-space: pre;
            cursor: pointer;
            position: relative;
            color: #e2e8f0;
        }
        .code-block:hover {
            background: #1e293b;
            border-color: #60a5fa;
        }
        .function-context {
            background: #1e293b;
            border: 1px solid #334155;
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
            color: #60a5fa;
            margin-bottom: 10px;
        }
        .function-body {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 13px;
            color: #e2e8f0;
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
            padding: 8px 16px;
            border-radius: 6px;
            border: 1px solid #334155;
            background: #1e293b;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.2s;
            color: #e2e8f0;
        }
        button:hover {
            background: #334155;
            border-color: #475569;
        }
        button.primary {
            background: #2563eb;
            color: white;
            border-color: #1d4ed8;
        }
        button.primary:hover {
            background: #1d4ed8;
        }
        button.danger {
            background: #dc2626;
            color: white;
            border-color: #b91c1c;
        }
        button.danger:hover {
            background: #b91c1c;
        }
        button.success {
            background: #059669;
            color: white;
            border-color: #047857;
        }
        button.success:hover {
            background: #047857;
        }
        button.warning {
            background: #d97706;
            color: white;
            border-color: #b45309;
        }
        button.warning:hover {
            background: #b45309;
        }
        button.info {
            background: #0891b2;
            color: white;
            border-color: #0e7490;
        }
        button.info:hover {
            background: #0e7490;
        }
        button.secondary {
            background: #6366f1;
            color: white;
            border-color: #4f46e5;
        }
        button.secondary:hover {
            background: #4f46e5;
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
            color: #10b981;
            font-weight: bold;
        }
        .running {
            color: #f59e0b;
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
            background: #2563eb;
            color: white;
        }
        .mode-auto {
            background: #059669;
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
            padding: 10px;
            margin: 4px 0;
            border: 1px solid #334155;
            border-radius: 6px;
            font-family: 'Monaco', 'Menlo', 'SF Mono', monospace;
            font-size: 13px;
            background: #1e293b;
            transition: all 0.2s;
        }
        .variable-item:hover {
            background: #263041;
            border-color: #60a5fa;
            cursor: pointer;
        }
        .variable-name {
            font-weight: 600;
            color: #60a5fa;
            word-break: break-word;
        }
        .variable-value {
            color: #e2e8f0;
            word-break: break-word;
        }
        .variable-changed {
            background: #422006;
            border-color: #f59e0b;
            animation: highlight 1s ease-out;
        }
        @keyframes highlight {
            from { background: #78350f; }
            to { background: #422006; }
        }
        .section-title {
            font-size: 14px;
            font-weight: bold;
            color: #94a3b8;
            margin: 15px 0 10px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .hint {
            color: #64748b;
            font-size: 12px;
            font-style: italic;
            margin-top: 5px;
        }
        /* Popup overlay for function parts */
        .overlay {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.6);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }
        .modal {
            background: #0f172a;
            border: 1px solid #263041;
            border-radius: 12px;
            width: 92%;
            max-width: 1000px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .modal-header, .modal-footer {
            padding: 12px 16px;
            border-bottom: 1px solid #263041;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .modal-footer { border-top: 1px solid #263041; border-bottom: none; }
        .modal-title { font-weight: 600; color: #e5e7eb; }
        .modal-content {
            padding: 16px;
            overflow: auto;
        }
        .block-item { 
            background: #1e293b; 
            border: 1px solid #334155; 
            border-radius: 8px; 
            padding: 12px; 
            margin: 10px 0; 
            font-family: 'Monaco', 'Menlo', 'SF Mono', monospace; 
            color: #e2e8f0; 
            cursor: pointer; 
        }
        .block-item:hover { background: #263041; border-color: #60a5fa; }
        .block-title { font-weight: 600; color: #60a5fa; margin-bottom: 6px; }
        .block-code { background: #0b1220; border: 1px solid #263041; border-radius: 6px; padding: 10px; white-space: pre; overflow-x: auto; }
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
                    <button class="info" onclick="sendAction('variables')">Variables (V)</button>
                    <button class="info" onclick="sendAction('function')">Function (F)</button>
                    <button class="info" onclick="sendAction('parts')">Function Parts (P)</button>
                    <button class="warning" onclick="sendAction('explore')">Explore Changes (E)</button>
                    <button class="warning" onclick="sendAction('variables_explore')">Explore Variables (W)</button>
                    <button class="success" onclick="sendAction('auto')">Auto Mode (A)</button>
                    <button class="secondary" onclick="sendAction('continue')">Continue</button>
                    <button class="danger" onclick="sendAction('quit')">Quit (Q)</button>
                    <button id="audioToggle" onclick="toggleAudio()">🔇 Audio Off</button>
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
                    <li><b>x</b> - Toggle function context display</li>
                    <li><b>m</b> - Toggle audio mute/unmute</li>
                    <li><b>s</b> - Cycle speech speed (slow/medium/fast)</li>
                    <li><b>l</b> - Go to line number</li>
                </ul>
            </div>
        </div>
        
        <div class="sidebar">
            <div class="card">
                <h3>Variables</h3>
                <div class="variables" id="variables">
                    <div style="color: #64748b; text-align: center; padding: 20px;">
                        No variables yet
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h3>Changes</h3>
                <div class="variables" id="changes">
                    <div style="color: #64748b; text-align: center; padding: 20px;">
                        No changes yet
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Function Parts Popup -->
    <div id="blocksOverlay" class="overlay">
        <div class="modal">
            <div class="modal-header">
                <div class="modal-title">Function Parts</div>
                <div id="blocksPageInfo" class="hint"></div>
            </div>
            <div id="blocksList" class="modal-content"></div>
            <div class="modal-footer">
                <div>
                    <button onclick="sendAction('q')" class="danger">Close (Q)</button>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <button id="blocksPrevBtn" onclick="sendAction('p')">Prev (P)</button>
                    <button id="blocksNextBtn" onclick="sendAction('n')">Next (N)</button>
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
                container.innerHTML = '<div style="color: #64748b; text-align: center; padding: 20px;">No ' + 
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
            container.innerHTML = html || '<div style="color: #64748b; text-align: center; padding: 20px;">Empty</div>';
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
                // If exploration is active and mode is 'variables', render enumerated items
                if (state.explore_active && state.explore_mode === 'variables' && 
                    Array.isArray(state.explore_items) && state.explore_items.length > 0) {
                    const container = document.getElementById('variables');
                    let html = '';
                    html += '<div class="section-title">Variables (page ' + (Number(state.explore_page || 0) + 1) + ')</div>';
                    for (const item of state.explore_items) {
                        html += '<div class="variable-item">';
                        html += '<div class="variable-name">[' + item.index + '] ' + item.name + '</div>';
                        html += '<div class="variable-value">' + String(item.preview || '') + '</div>';
                        html += '</div>';
                    }
                    const total = Number(state.explore_total || 0);
                    const page = Number(state.explore_page || 0);
                    const hasNext = (page + 1) * 10 < total;
                    const hasPrev = page > 0;
                    html += '<div class="hint">Press 0-9 to explore' + 
                            (hasNext ? ', N next page' : '') + 
                            (hasPrev ? ', P previous' : '') + 
                            ', Q to quit</div>';
                    container.innerHTML = html;
                    // Click to select
                    container.querySelectorAll('.variable-item').forEach((node, idx) => {
                        node.style.cursor = 'pointer';
                        node.addEventListener('click', () => {
                            sendAction(String(idx));
                        });
                    });
                } else {
                    renderVariables(state.variables, 'variables');
                    previousVariables = state.variables;
                }
            }
            
            // Update changes
            if (state.variables_delta) {
                // If exploration is active and mode is 'changes', render enumerated items for selection
                if (state.explore_active && state.explore_mode === 'changes' && 
                    Array.isArray(state.explore_items) && state.explore_items.length > 0) {
                    const container = document.getElementById('changes');
                    let html = '';
                    html += '<div class="section-title">Changed (page ' + (Number(state.explore_page || 0) + 1) + ')</div>';
                    for (const item of state.explore_items) {
                        html += '<div class="variable-item">';
                        html += '<div class="variable-name">[' + item.index + '] ' + item.name + '</div>';
                        html += '<div class="variable-value">' + String(item.preview || '') + '</div>';
                        html += '</div>';
                    }
                    const total = Number(state.explore_total || 0);
                    const page = Number(state.explore_page || 0);
                    const hasNext = (page + 1) * 10 < total;
                    const hasPrev = page > 0;
                    html += '<div class="hint">Press 0-9 to explore' + 
                            (hasNext ? ', N next page' : '') + 
                            (hasPrev ? ', P previous' : '') + 
                            ', Q to quit</div>';
                    container.innerHTML = html;
                    // Click to select
                    container.querySelectorAll('.variable-item').forEach((node, idx) => {
                        node.style.cursor = 'pointer';
                        node.addEventListener('click', () => {
                            sendAction(String(idx));
                        });
                    });
                } else {
                    renderVariables(state.variables_delta, 'changes', true);
                }
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

            // Render function parts popup if active
            const overlay = document.getElementById('blocksOverlay');
            const listEl = document.getElementById('blocksList');
            const pageInfoEl = document.getElementById('blocksPageInfo');
            const prevBtn = document.getElementById('blocksPrevBtn');
            const nextBtn = document.getElementById('blocksNextBtn');
            if (state.blocks_active && Array.isArray(state.blocks_items) && state.blocks_items.length > 0) {
                overlay.style.display = 'flex';
                // Build list
                let html = '';
                for (const item of state.blocks_items) {
                    const title = '[' + item.index + '] ' + (item.title || 'Block');
                    const code = String(item.code || '');
                    html += '<div class="block-item" data-index="' + item.index + '">';
                    html += '<div class="block-title">' + title + '</div>';
                    html += '<div class="block-code">' + code.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
                    html += '</div>';
                }
                listEl.innerHTML = html;
                // Bind clicks
                listEl.querySelectorAll('.block-item').forEach(node => {
                    node.addEventListener('click', () => {
                        const idx = node.getAttribute('data-index');
                        if (idx !== null) sendAction(String(idx));
                    });
                });
                // Page info and buttons
                const page = Number(state.blocks_page || 0);
                const total = Number(state.blocks_total || 0);
                const totalPages = Math.max(1, Math.ceil(total / 10));
                pageInfoEl.textContent = 'Page ' + (page + 1) + ' of ' + totalPages;
                prevBtn.disabled = (page <= 0);
                nextBtn.disabled = ((page + 1) * 10 >= total);
            } else {
                overlay.style.display = 'none';
                listEl.innerHTML = '';
                pageInfoEl.textContent = '';
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
                // Send stop audio command first to interrupt any playing audio
                // (except for the stop_audio action itself to avoid recursion)
                if (action !== 'stop_audio') {
                    await fetch('/command', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({action: 'stop_audio'})
                    });
                }
                
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
            // If blocks popup is active, override certain keys for exploration
            if (currentState.blocks_active) {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    // Only stop audio; do not exit exploration
                    sendAction('stop_audio');
                    return;
                }
                if (e.key >= '0' && e.key <= '9') {
                    e.preventDefault();
                    // Stop current audio before switching selection
                    sendAction('stop_audio');
                    sendAction(e.key);
                    return;
                }
                if (e.key === 'n' || e.key === 'N') {
                    e.preventDefault();
                    sendAction('stop_audio');
                    sendAction('n');
                    return;
                }
                if (e.key === 'p' || e.key === 'P') {
                    e.preventDefault();
                    // In popup, 'p' means previous page
                    sendAction('stop_audio');
                    sendAction('p');
                    return;
                }
                if (e.key === 'q' || e.key === 'Q') {
                    e.preventDefault();
                    sendAction('stop_audio');
                    sendAction('q');
                    return;
                }
                // Fall through for other keys during popup
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                sendAction('stop_audio');
            } else if (e.key === 'Enter') {
                e.preventDefault();
                sendAction('step');
            } else if (e.key === 'v' || e.key === 'V') {
                sendAction('variables');
            } else if (e.key === 'f' || e.key === 'F') {
                sendAction('function');
            } else if (e.key === 'p' || e.key === 'P') {
                sendAction('parts');
            } else if (e.key === 'e' || e.key === 'E') {
                sendAction('explore');
            } else if (e.key === 'w' || e.key === 'W') {
                sendAction('variables_explore');
            } else if (e.key === 'a' || e.key === 'A') {
                sendAction('auto');
            // 'c' shortcut removed to allow copy/paste
            } else if (e.key === 'q' || e.key === 'Q') {
                sendAction('quit');
            } else if (e.key === 'x' || e.key === 'X') {
                toggleFunction();
            } else if (e.key === 'm' || e.key === 'M') {
                toggleAudio();
            } else if (e.key === 's' || e.key === 'S') {
                sendAction('speed');  // Cycle speed
            } else if (e.key === 'l' || e.key === 'L') {
                e.preventDefault();
                // Prompt for line number
                const lineNum = prompt('Enter line number to go to:');
                if (lineNum && !isNaN(parseInt(lineNum))) {
                    // Send a check request first to validate the line
                    sendAction('goto:' + lineNum);
                }
            } else if (e.key >= '0' && e.key <= '9') {
                // Selection in explore mode
                sendAction(e.key);
            } else if (e.key === 'n' || e.key === 'N') {
                // Send 'n' - backend will interpret based on context
                // (next page in exploration, or other uses)
                sendAction('n');
            }
            // Note: p is handled above for 'parts'
            // q is handled above for 'quit'
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
            # Accept any action string (e.g., 'variables', 'function', 'explore', digits for selection)
            if action:
                self.shared.send_action(action)
                self._send(200, {"status": "ok", "action": action})
            else:
                self._send(400, {"error": "Missing action"})
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
        
        # Auto-open browser
        import webbrowser
        url = f"http://127.0.0.1:{self.port}"
        print(f"\n[manual-web] Opening {url} in browser...\n", flush=True)
        webbrowser.open(url)
    
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
    
    def cycle_audio_speed(self) -> str:
        """Cycle through audio speeds and return new speed."""
        return self.shared_state.cycle_audio_speed()


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
        elif response in ['p', 'parts']:
            return 'parts'
        else:
            return 'step'  # Default to step
    except (EOFError, KeyboardInterrupt):
        return 'quit'