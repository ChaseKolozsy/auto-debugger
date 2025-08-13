#!/usr/bin/env python3
"""Quick test that writes 25MB file immediately."""

def main():
    # Write 25 MB immediately (exceeds 20 MB limit)
    with open("test_25mb.dat", 'w') as f:
        f.write("X" * (25 * 1024 * 1024))
    print("Wrote 25 MB")
    
    # This shouldn't be reached if limit works
    print("Still running after write")

if __name__ == "__main__":
    main()