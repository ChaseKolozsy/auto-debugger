#!/usr/bin/env python3
"""Test script for manual stepping feature."""

def calculate(a, b, operation):
    """Simple calculator function."""
    result = 0
    
    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    elif operation == "divide":
        if b != 0:
            result = a / b
        else:
            raise ValueError("Cannot divide by zero")
    else:
        raise ValueError(f"Unknown operation: {operation}")
    
    return result


def main():
    """Main function to test manual stepping."""
    print("Starting calculator test...")
    
    # Test addition
    x = 10
    y = 5
    op = "add"
    result = calculate(x, y, op)
    print(f"{x} {op} {y} = {result}")
    
    # Test subtraction
    op = "subtract"
    result = calculate(x, y, op)
    print(f"{x} {op} {y} = {result}")
    
    # Test multiplication
    op = "multiply"
    result = calculate(x, y, op)
    print(f"{x} {op} {y} = {result}")
    
    # Test division
    op = "divide"
    result = calculate(x, y, op)
    print(f"{x} {op} {y} = {result}")
    
    # Test with a loop
    numbers = [1, 2, 3, 4, 5]
    total = 0
    for num in numbers:
        total += num
        print(f"Adding {num}, total is now {total}")
    
    print(f"Final total: {total}")
    print("Calculator test completed!")


if __name__ == "__main__":
    main()