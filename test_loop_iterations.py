#!/usr/bin/env python3
"""
Test script to demonstrate loop iteration tracking with function calls.
This script has a loop that calls a function multiple times.
"""

def process_item(item, multiplier=2):
    """Process a single item by multiplying it."""
    result = item * multiplier
    print(f"Processing item {item}: {item} * {multiplier} = {result}")
    return result

def nested_function(value):
    """A nested function to show tracking works even with function calls."""
    temp = value + 10
    print(f"  Nested processing: {value} + 10 = {temp}")
    return temp

def main():
    """Main function with a loop that will exceed iteration limit."""
    print("Starting loop iteration test...", flush=True)
    
    # This loop will run many times and call functions
    counter = 0
    results = []
    
    # Infinite loop that will be stopped by max iterations
    while True:
        counter += 1
        print(f"\n--- Iteration {counter} ---")
        
        # Call a function inside the loop
        processed = process_item(counter)
        
        # Call another nested function
        nested_result = nested_function(processed)
        
        results.append(nested_result)
        
        # Show accumulated results every 5 iterations
        if counter % 5 == 0:
            print(f"Results so far: {results}")
        
        # This would normally be an exit condition, but we'll let
        # the debugger stop us based on max iterations
        if counter > 1000:  # This won't be reached if max iterations is lower
            break
    
    print(f"Loop completed with {counter} iterations")
    print(f"Final results: {results}")

if __name__ == "__main__":
    main()