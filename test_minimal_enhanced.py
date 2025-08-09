#!/usr/bin/env python3
"""Minimal test of enhanced control to isolate the issue."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

class TestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress logging
    
    def do_GET(self):
        if self.path == "/":
            html = """<!DOCTYPE html>
<html>
<head>
    <title>Test F Key</title>
    <style>
        .hidden { display: none; }
        .visible { display: block; background: yellow; padding: 20px; }
    </style>
</head>
<body>
    <h1>Test F Key Hanging</h1>
    <p>Current state: <span id="state">Ready</span></p>
    <p>Press 'f' to toggle the box below:</p>
    <div id="box" class="hidden">
        This is the function context box.
    </div>
    <p>Press 's' to simulate a step action</p>
    
    <script>
        let counter = 0;
        
        function updateState() {
            document.getElementById('state').textContent = 'Counter: ' + counter;
        }
        
        function toggleFunction() {
            console.log('Toggle function called');
            const box = document.getElementById('box');
            box.classList.toggle('hidden');
            box.classList.toggle('visible');
            console.log('Toggle complete');
        }
        
        async function sendStep() {
            console.log('Sending step...');
            try {
                const response = await fetch('/step', {method: 'POST'});
                const data = await response.json();
                counter = data.counter;
                updateState();
                console.log('Step complete');
            } catch(e) {
                console.error('Step failed:', e);
            }
        }
        
        document.addEventListener('keydown', (e) => {
            console.log('Key pressed:', e.key);
            if (e.key === 'f' || e.key === 'F') {
                toggleFunction();
            } else if (e.key === 's' || e.key === 'S') {
                sendStep();
            }
        });
        
        // Poll for updates
        setInterval(async () => {
            try {
                const response = await fetch('/state');
                const data = await response.json();
                counter = data.counter;
                updateState();
            } catch(e) {
                // Ignore polling errors
            }
        }, 500);
    </script>
</body>
</html>"""
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(html.encode())
        elif self.path == "/state":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"counter": self.server.counter}).encode())
        else:
            self.send_error(404)
    
    def do_POST(self):
        if self.path == "/step":
            self.server.counter += 1
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"counter": self.server.counter}).encode())
        else:
            self.send_error(404)

def main():
    server = HTTPServer(('127.0.0.1', 8888), TestHandler)
    server.counter = 0
    print("Test server running at http://127.0.0.1:8888")
    print("Press Ctrl+C to stop")
    server.serve_forever()

if __name__ == "__main__":
    main()