#!/usr/bin/env python3
"""Direct test of manual-from feature."""

from autodebugger.runner import AutoDebugger

print("Creating debugger...")
dbg = AutoDebugger()

print("Starting run with manual-from...")
session_id = dbg.run(
    "test_simple.py",
    manual=True,
    manual_from="test_simple.py:8",
    manual_web=True,
    stop_on_entry=False
)

print(f"Session completed: {session_id}")