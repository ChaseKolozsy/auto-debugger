#!/usr/bin/env python3
"""
Test script specifically for Cursor
"""

import socket
import time

print("🔍 Testing Roo Bridge in Cursor")
print("=" * 40)

# Wait a moment for extension to activate
print("⏰ Waiting 2 seconds for extension to activate...")
time.sleep(2)

try:
    print("📡 Connecting to localhost:9999...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)  # 5 second timeout
    sock.connect(('127.0.0.1', 9999))
    
    print("✅ CONNECTED! Extension is working!")
    
    # Send ping
    print("📤 Sending: 'ping'")
    sock.send(b'ping')
    
    response = sock.recv(1024).decode().strip()
    print(f"📥 Received: '{response}'")
    
    if response == 'pong':
        print("\n🎉 SUCCESS! We can communicate with Cursor/Roo!")
    
    sock.close()
    
except socket.timeout:
    print("\n❌ Connection timed out!")
    print("The extension might not be activated.")
    print("Try: View -> Command Palette -> 'Developer: Reload Window'")
    
except ConnectionRefusedError:
    print("\n❌ Connection refused!")
    print("Please make sure you:")
    print("1. Installed the VSIX file")
    print("2. Reloaded Cursor")
    print("3. See 'Roo Bridge Test: Listening on port 9999' notification")
    
except Exception as e:
    print(f"\n❌ Error: {e}")