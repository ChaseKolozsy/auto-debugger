#!/usr/bin/env python3
"""Simple multi-file test for skip-to-next-file feature."""

def helper_function():
    """This is in the same file, so 't' won't stop here."""
    x = 1
    y = 2
    z = x + y
    print(f"Helper result: {z}")
    return z

def another_helper():
    """Also in the same file."""
    a = 10
    b = 20
    c = a * b
    print(f"Another helper: {c}")
    return c

def main():
    """Main function that calls helpers."""
    print("Starting main...")
    
    # Call first helper
    result1 = helper_function()
    print(f"Got result1: {result1}")
    
    # Call second helper
    result2 = another_helper()
    print(f"Got result2: {result2}")
    
    # Do some more work
    for i in range(3):
        print(f"Loop iteration {i}")
        temp = i * 2
        print(f"Temp value: {temp}")
    
    # Import and use another module (this WILL trigger 't' to stop)
    import math
    angle = math.pi / 4
    sine = math.sin(angle)
    print(f"Sin of pi/4: {sine}")
    
    # Import os module
    import os
    cwd = os.getcwd()
    print(f"Current directory: {cwd}")
    
    print("Done!")
    return result1 + result2

if __name__ == "__main__":
    final = main()
    print(f"Final result: {final}")