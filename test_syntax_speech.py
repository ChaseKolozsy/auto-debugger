#!/usr/bin/env python3
"""
Test script to verify syntax-to-speech conversion.
Run this directly to test the conversion without debugging.
"""

from autodebugger.syntax_to_speech import syntax_to_speech_code, syntax_to_speech_value

def test_syntax_conversion():
    print("Testing Syntax-to-Speech Conversion")
    print("=" * 50)
    
    # Test code examples
    code_examples = [
        "def foo(x, y):",
        "result = calculate(a[0], b[1])",
        "data = {'key': [1, 2, 3]}",
        "if (x > 0) and (y < 10):",
        "matrix[i][j] = value",
        "Calculator(Add(), Subtract())",
    ]
    
    print("\nCode Conversions:")
    print("-" * 30)
    for code in code_examples:
        converted = syntax_to_speech_code(code)
        print(f"Original:  {code}")
        print(f"Converted: {converted}")
        print()
    
    # Test value examples
    value_examples = [
        "[1, 2, 3]",
        "{'name': 'John', 'age': 30}",
        "(1, 2, 3)",
        "[[1, 2], [3, 4]]",
        "{('a', 'b'): [1, 2, 3]}",
        "Calculator(add=<Add>, subtract=<Subtract>)",
    ]
    
    print("\nValue Conversions:")
    print("-" * 30)
    for value in value_examples:
        converted = syntax_to_speech_value(value)
        print(f"Original:  {value}")
        print(f"Converted: {converted}")
        print()
    
    # Test with prefixed messages
    messages = [
        "Line 42: calc = Calculator()",
        "Scope: a=[1, 2, 3], b={'x': 10}",
        "Changes: result changed from [] to [1, 2]",
    ]
    
    print("\nMessage Conversions:")
    print("-" * 30)
    for msg in messages:
        # Simulate what the TTS would do
        if any(char in msg for char in ["[", "]", "{", "}", "(", ")"]):
            if msg.strip().startswith(("Line ", "Scope:", "Changes:")):
                parts = msg.split(":", 1)
                if len(parts) == 2:
                    prefix, value = parts
                    converted = f"{prefix}: {syntax_to_speech_value(value.strip())}"
                else:
                    converted = syntax_to_speech_value(msg)
            else:
                converted = syntax_to_speech_value(msg)
        else:
            converted = msg
        
        print(f"Original:  {msg}")
        print(f"Converted: {converted}")
        print()

if __name__ == "__main__":
    test_syntax_conversion()