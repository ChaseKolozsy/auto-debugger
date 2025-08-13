#!/usr/bin/env python3
"""Simple test that quickly writes a large file to disk."""

import os

def main():
    filename = "large_file.dat"
    size_mb = 50
    
    print(f"Writing {size_mb} MB file...")
    
    # Write 50 MB at once
    data = "A" * (size_mb * 1024 * 1024)
    with open(filename, 'w') as f:
        f.write(data)
    
    print(f"Wrote {size_mb} MB to {filename}")
    
    # Keep file around to maintain disk usage
    input("Press Enter to clean up...")
    
    if os.path.exists(filename):
        os.remove(filename)
        print("Cleaned up")

if __name__ == "__main__":
    main()