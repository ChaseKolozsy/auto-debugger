#!/usr/bin/env python3
"""Test script that writes data to disk gradually to test disk usage limits."""

import os
import time

def write_chunk(filename, chunk_size_mb, chunk_num):
    """Write a chunk of data to file."""
    data = "X" * (chunk_size_mb * 1024 * 1024)  # Create MB of data
    mode = 'w' if chunk_num == 0 else 'a'
    with open(filename, mode) as f:
        f.write(data)
    print(f"Wrote chunk {chunk_num + 1}: {chunk_size_mb} MB")

def main():
    output_file = "test_output.dat"
    chunk_size_mb = 5  # Write 5 MB at a time
    num_chunks = 20  # Total 100 MB
    
    print(f"Starting disk usage test - will write {chunk_size_mb * num_chunks} MB total")
    
    # Clean up any previous test file
    if os.path.exists(output_file):
        os.remove(output_file)
    
    for i in range(num_chunks):
        write_chunk(output_file, chunk_size_mb, i)
        time.sleep(0.1)  # Small delay between writes
    
    print(f"Finished writing {chunk_size_mb * num_chunks} MB to {output_file}")
    
    # Clean up
    if os.path.exists(output_file):
        os.remove(output_file)
        print("Cleaned up test file")

if __name__ == "__main__":
    main()