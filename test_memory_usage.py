#!/usr/bin/env python3
"""
Test script to demonstrate memory usage tracking.
This script gradually consumes more memory by creating large data structures.
"""

import time

def consume_memory(size_mb):
    """Create a list that consumes approximately size_mb of memory."""
    # Each integer in Python takes about 28 bytes
    # So to consume 1 MB, we need about 37,000 integers
    elements_per_mb = 37000
    data = list(range(elements_per_mb * size_mb))
    return data

def process_data(data):
    """Do some processing on the data to prevent optimization."""
    total = sum(data[:1000])  # Sum first 1000 elements
    print(f"  Processed data, sample sum: {total}")
    return total

def main():
    """Main function that gradually increases memory usage."""
    print("Starting memory consumption test...", flush=True)
    
    memory_blocks = []  # Keep references to prevent garbage collection
    iteration = 0
    
    while True:
        iteration += 1
        print(f"\n--- Iteration {iteration} ---", flush=True)
        
        # Consume 5 MB per iteration
        size_mb = 5
        print(f"Allocating {size_mb} MB of memory...", flush=True)
        
        # Create data that consumes memory
        data = consume_memory(size_mb)
        memory_blocks.append(data)  # Keep reference
        
        # Process the data (function call to test tracking works)
        result = process_data(data)
        
        # Show total allocated
        total_mb = iteration * size_mb
        print(f"Total memory allocated so far: ~{total_mb} MB", flush=True)
        
        # Small delay to make it observable
        time.sleep(0.1)
        
        # This would normally run forever, but the debugger
        # should stop it when memory limit is exceeded
        if iteration > 1000:
            break
    
    print(f"Completed {iteration} iterations", flush=True)
    print(f"Total memory blocks kept: {len(memory_blocks)}", flush=True)

if __name__ == "__main__":
    main()