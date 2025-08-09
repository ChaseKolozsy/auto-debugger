#!/usr/bin/env python3
"""Test stepping through code and reading function context."""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:63953"

def send_action(action):
    """Send an action to the debugger."""
    response = requests.post(f"{BASE_URL}/command", 
                            json={"action": action})
    return response.status_code == 200

def toggle_audio():
    """Toggle audio on/off."""
    response = requests.post(f"{BASE_URL}/toggle-audio")
    if response.status_code == 200:
        return response.json().get('audio_enabled')
    return None

def read_function():
    """Request function context to be read aloud."""
    response = requests.post(f"{BASE_URL}/read-function")
    if response.status_code == 200:
        return response.json()
    return None

def get_state():
    """Get the current debugger state."""
    response = requests.get(f"{BASE_URL}/state")
    if response.status_code == 200:
        return response.json()
    return None

if __name__ == "__main__":
    # Step a few times to get into a function
    print("Stepping through code...")
    for i in range(5):
        send_action("step")
        time.sleep(0.5)
        state = get_state()
        if state:
            print(f"  Line {state.get('line')}: {state.get('function_name', 'not in function')}")
    
    # Enable audio
    print("\nEnabling audio...")
    audio_state = toggle_audio()
    if audio_state is False:  # Was off, toggle again to turn on
        audio_state = toggle_audio()
    print(f"  Audio is now: {audio_state}")
    
    # Try to read function context
    print("\nReading function context...")
    result = read_function()
    print(f"  Result: {result}")
    
    print("\nTest completed!")