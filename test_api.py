#!/usr/bin/env python3
"""Test the audio API endpoints."""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:63953"

def test_audio_toggle():
    """Test the audio toggle endpoint."""
    print("Testing audio toggle...")
    response = requests.post(f"{BASE_URL}/toggle-audio")
    if response.status_code == 200:
        data = response.json()
        print(f"  Audio state: {data.get('audio_enabled', 'unknown')}")
        return True
    else:
        print(f"  Failed: {response.status_code}")
        return False

def test_read_function():
    """Test the read function endpoint."""
    print("Testing read function...")
    response = requests.post(f"{BASE_URL}/read-function")
    if response.status_code == 200:
        data = response.json()
        print(f"  Response: {data}")
        return True
    else:
        print(f"  Failed: {response.status_code}")
        return False

def get_state():
    """Get the current debugger state."""
    response = requests.get(f"{BASE_URL}/state")
    if response.status_code == 200:
        return response.json()
    return None

if __name__ == "__main__":
    # Give the debugger a moment to initialize
    time.sleep(1)
    
    # Get initial state
    state = get_state()
    if state:
        print(f"Initial state:")
        print(f"  Audio enabled: {state.get('audio_enabled')}")
        print(f"  Audio available: {state.get('audio_available')}")
        print(f"  Function: {state.get('function_name', 'None')}")
    
    # Test toggle
    test_audio_toggle()
    
    # Get state after toggle
    time.sleep(0.5)
    state = get_state()
    if state:
        print(f"After toggle:")
        print(f"  Audio enabled: {state.get('audio_enabled')}")
    
    # Test read function
    test_read_function()
    
    print("\nAll tests completed!")