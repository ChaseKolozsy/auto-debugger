#!/usr/bin/env python3
"""Test how long function extraction takes."""

import time
from autodebugger.enhanced_control import extract_function_context

# Test with test_functions.py
test_file = "test_functions.py"
test_lines = [5, 12, 24, 29, 43]

print("Testing function extraction timing...")
for line in test_lines:
    start = time.time()
    result = extract_function_context(test_file, line)
    elapsed = time.time() - start
    print(f"Line {line}: {elapsed:.4f}s - {result['name'] or 'None'}")

# Test multiple rapid calls
print("\nTesting 100 rapid calls...")
start = time.time()
for _ in range(100):
    extract_function_context(test_file, 29)
elapsed = time.time() - start
print(f"100 calls took {elapsed:.4f}s ({elapsed/100:.4f}s per call)")