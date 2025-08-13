#!/usr/bin/env python3
"""Test both loop iteration and memory limits."""

def allocate_memory(size_mb):
    """Allocate some memory."""
    return [0] * (size_mb * 1024 * 1024 // 8)

def main():
    print("Testing combined resource limits", flush=True)
    
    memory_blocks = []
    counter = 0
    
    # Loop that both iterates and consumes memory
    while True:
        counter += 1
        print(f"Iteration {counter}", flush=True)
        
        # Allocate 10 MB per iteration
        block = allocate_memory(10)
        memory_blocks.append(block)
        
        print(f"  Allocated 10 MB (total: {counter * 10} MB)", flush=True)
        
        if counter > 100:
            break
    
    print("Done", flush=True)

if __name__ == "__main__":
    main()