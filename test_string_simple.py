#!/usr/bin/env python3
"""Simple test for string mutations."""

def main():
    # Test string mutations
    text = "hello"
    text = text.upper()
    text = text + " world"
    text = text.strip()
    text = text.replace("HELLO", "Hi")
    
    # Test with instance variables
    class Processor:
        def __init__(self):
            self.message = "initial"
        
        def process(self):
            self.message = self.message.upper()
            self.message = self.message + "_PROCESSED"
            return self.message
    
    proc = Processor()
    result = proc.process()
    
    print(f"Final text: {text}")
    print(f"Final message: {result}")
    
    return text, result

if __name__ == "__main__":
    main()