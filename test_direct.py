#!/usr/bin/env python3
"""Test running debugger directly."""

from autodebugger.runner import AutoDebugger

print("[TEST] Creating AutoDebugger")
dbg = AutoDebugger()

print("[TEST] Calling run()")
session_id = dbg.run("test_simple.py", just_my_code=True, stop_on_entry=True)

print(f"[TEST] Session ID: {session_id}")