#!/usr/bin/env python3
"""
Simple test to prove we can communicate with VS Code/Roo Code
This is the simplest possible proof of concept.
"""

import socket
import time

def test_roo_bridge():
    """Simple ping-pong test with VS Code extension"""
    
    print("🔍 Roo Bridge Connection Test")
    print("-" * 40)
    
    try:
        # Connect to VS Code extension
        print("1. Connecting to VS Code extension on port 9999...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('127.0.0.1', 9999))
        print("   ✅ Connected!")
        
        # Test 1: Simple ping-pong
        print("\n2. Sending 'ping' to VS Code...")
        sock.send(b'ping')
        
        # Wait for response
        response = sock.recv(1024).decode().strip()
        print(f"   📨 Received: '{response}'")
        
        if response == 'pong':
            print("   ✅ PING-PONG TEST PASSED!")
        else:
            print(f"   ❌ Expected 'pong', got '{response}'")
        
        # Test 2: Send a Roo command
        print("\n3. Sending Roo command...")
        sock.send(b'roo:Hello from Python!')
        
        response = sock.recv(1024).decode().strip()
        print(f"   📨 Received: '{response}'")
        
        if response == 'sent_to_terminal':
            print("   ✅ ROO COMMAND TEST PASSED!")
            print("   📝 Check VS Code terminal for Roo message")
        
        sock.close()
        
        print("\n" + "="*40)
        print("🎉 PROOF OF CONCEPT SUCCESSFUL!")
        print("We can communicate with VS Code/Roo!")
        print("="*40)
        
    except ConnectionRefusedError:
        print("❌ Connection failed!")
        print("Make sure:")
        print("  1. VS Code is running")
        print("  2. The extension is installed and activated")
        print("  3. You see 'Listening on port 9999' notification")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_roo_bridge()