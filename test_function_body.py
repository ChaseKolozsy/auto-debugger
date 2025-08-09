#!/usr/bin/env python3
"""Test that function body is read aloud."""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:64903"

def send_action(action):
    """Send an action to the debugger."""
    response = requests.post(f"{BASE_URL}/command", 
                            json={"action": action})
    return response.status_code == 200

def get_state():
    """Get the current debugger state."""
    response = requests.get(f"{BASE_URL}/state")
    if response.status_code == 200:
        return response.json()
    return None

def toggle_audio_on():
    """Ensure audio is on."""
    state = get_state()
    if state and not state.get('audio_enabled'):
        requests.post(f"{BASE_URL}/toggle-audio")

def read_function():
    """Request function context to be read aloud."""
    response = requests.post(f"{BASE_URL}/read-function")
    if response.status_code == 200:
        return response.json()
    return None

if __name__ == "__main__":
    # Step a few times to get into a function
    print("Stepping to get into a function...")
    for i in range(10):
        send_action("step")
        time.sleep(0.3)
        state = get_state()
        if state:
            func = state.get('function_name')
            if func and func != 'None':
                print(f"\nFound function: {func}")
                print(f"Function signature: {state.get('function_sig', 'N/A')}")
                print(f"Function body preview: {state.get('function_body', 'N/A')[:100]}...")
                
                # Ensure audio is on
                toggle_audio_on()
                
                # Read the function context
                print("\nRequesting function to be read aloud...")
                result = read_function()
                if result and 'spoken' in result:
                    print(f"Text that was spoken:\n{result['spoken']}")
                else:
                    print(f"Response: {result}")
                break
    
    print("\nTest completed!")