#!/usr/bin/env python3
"""Test the function extraction logic."""

from autodebugger.enhanced_control import extract_function_context

# Test extraction for various lines in test_functions.py
test_file = "test_functions.py"

test_lines = [
    5,   # Inside greet function
    12,  # Inside calculate function  
    24,  # Inside Calculator.__init__
    29,  # Inside Calculator.add
    43,  # Inside main function
]

for line in test_lines:
    result = extract_function_context(test_file, line)
    print(f"Line {line}: {result['name'] or 'Not in function'}")
    if result['sig']:
        print(f"  Signature: {result['sig'][:50]}...")
    print()