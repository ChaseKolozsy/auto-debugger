#!/usr/bin/env python3
"""Test debugger interaction."""

import requests
import time
import json

BASE_URL = "http://127.0.0.1:51309"

def get_state():
    """Get current state."""
    r = requests.get(f"{BASE_URL}/state")
    return r.json()

def send_action(action):
    """Send an action."""
    r = requests.post(f"{BASE_URL}/command", 
                      json={"action": action})
    return r.status_code == 200

print("Initial state:")
state = get_state()
print(f"  Line: {state.get('line')}")
print(f"  Function: {state.get('function_name')}")
print(f"  Waiting: {state.get('waiting')}")

print("\nStepping 3 times...")
for i in range(3):
    if send_action("step"):
        time.sleep(0.5)
        state = get_state()
        print(f"  Step {i+1}: Line {state.get('line')}, Function: {state.get('function_name')}")
    else:
        print(f"  Step {i+1}: Failed")

print("\nFinal state:")
state = get_state()
print(f"  Line: {state.get('line')}")
print(f"  Function: {state.get('function_name')}")
print(f"  Code: {state.get('code')}")