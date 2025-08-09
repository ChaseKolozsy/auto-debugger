#!/usr/bin/env python3
"""Test script with functions for enhanced debugger."""

def greet(name):
    """Greet a person with their name."""
    message = f"Hello, {name}!"
    print(message)
    return message

def calculate(a, b):
    """Calculate sum and product of two numbers."""
    sum_result = a + b
    print(f"Sum: {sum_result}")
    
    product = a * b
    print(f"Product: {product}")
    
    return sum_result, product

class Calculator:
    """A simple calculator class."""
    
    def __init__(self, initial_value=0):
        self.value = initial_value
        print(f"Calculator initialized with {initial_value}")
    
    def add(self, amount):
        """Add an amount to the current value."""
        old_value = self.value
        self.value += amount
        print(f"Added {amount}: {old_value} -> {self.value}")
        return self.value
    
    def multiply(self, factor):
        """Multiply the current value by a factor."""
        old_value = self.value
        self.value *= factor
        print(f"Multiplied by {factor}: {old_value} -> {self.value}")
        return self.value

def main():
    """Main function to demonstrate the debugger."""
    print("Starting main function")
    
    # Test simple function
    greeting = greet("Alice")
    
    # Test function with multiple returns
    x = 5
    y = 3
    sum_val, prod_val = calculate(x, y)
    
    # Test class methods
    calc = Calculator(10)
    calc.add(5)
    calc.multiply(2)
    
    print("Main function complete")
    return calc.value

if __name__ == "__main__":
    result = main()
    print(f"Final result: {result}")