#!/usr/bin/env python3
"""Test combined limits with a breakpoint approach."""

import time

def main():
    print("Testing combined resource limits", flush=True)
    
    memory_blocks = []
    counter = 0
    
    # Set a breakpoint here or use manual stepping
    breakpoint()  # This will cause the debugger to stop
    
    # Loop that both iterates and consumes memory
    while counter < 10:
        counter += 1
        print(f"Iteration {counter}", flush=True)
        
        # Allocate 5 MB per iteration
        block = [0] * (5 * 1024 * 1024 // 8)
        memory_blocks.append(block)
        
        print(f"  Allocated 5 MB (total: {counter * 5} MB)", flush=True)
        time.sleep(0.1)  # Small delay
    
    print("Done", flush=True)

if __name__ == "__main__":
    main()