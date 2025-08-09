#!/usr/bin/env python3
"""Test async function extraction."""

import requests
import time
import json

BASE_URL = "http://127.0.0.1:52525"

def get_state():
    """Get current state."""
    r = requests.get(f"{BASE_URL}/state")
    return r.json()

def send_action(action):
    """Send an action."""
    r = requests.post(f"{BASE_URL}/command", 
                      json={"action": action})
    return r.status_code == 200

print("Testing async function extraction...")
print("Stepping rapidly and checking function context...\n")

for i in range(5):
    if send_action("step"):
        # Immediately get state (function might not be extracted yet)
        state1 = get_state()
        line = state1.get('line')
        func1 = state1.get('function_name')
        
        # Wait a bit for async extraction
        time.sleep(0.2)
        
        # Get state again (function should be extracted now)
        state2 = get_state()
        func2 = state2.get('function_name')
        
        print(f"Step {i+1}: Line {line}")
        print(f"  Immediate: {func1}")
        print(f"  After 200ms: {func2}")
        
        if func1 != func2:
            print("  ✓ Async extraction working!")
    else:
        print(f"Step {i+1}: Failed")

print("\nTest completed!")