#!/usr/bin/env python3
"""Test the fixed manual-from feature."""

from autodebugger.runner import AutoDebugger
import time

print("Creating debugger...")
dbg = AutoDebugger()

print("Starting run with manual-from at line 8...")
try:
    session_id = dbg.run(
        "test_simple.py",
        manual=True,
        manual_from="test_simple.py:8",
        manual_web=True,
        stop_on_entry=False
    )
    print(f"Session completed: {session_id}")
except KeyboardInterrupt:
    print("\nInterrupted by user")
except Exception as e:
    print(f"Error: {e}")