from __future__ import annotations

"""
Syntax-to-speech mappings for converting programming syntax to natural language.

This module provides mappings and utilities to convert syntax characters that
text-to-speech engines typically ignore into their spoken equivalents.
"""

import re
from typing import Dict, List, Tuple

# Mapping of syntax characters to their spoken representations
SYNTAX_TO_SPEECH_MAP: Dict[str, str] = {
    # Parentheses
    "(": " open paren ",
    ")": " close paren ",
    
    # Square brackets
    "[": " open bracket ",
    "]": " close bracket ",
    
    # Curly braces
    "{": " open brace ",
    "}": " close brace ",
    
    # Common operators that might be helpful
    "==": " equals equals ",
    "!=": " not equals ",
    "<=": " less than or equal to ",
    ">=": " greater than or equal to ",
    "<": " less than ",
    ">": " greater than ",
    "=": " equals ",
    "+": " plus ",
    "-": " minus ",
    "*": " times ",
    "/": " divide ",
    "%": " modulo ",
    "**": " power ",
    "//": " floor divide ",
    
    # Logical operators
    "&&": " and ",
    "||": " or ",
    "!": " not ",
    
    # Other common syntax
    ":": " colon ",
    ";": " semicolon ",
    ",": " comma ",
    ".": " dot ",
    "->": " arrow ",
    "=>": " fat arrow ",
}

# Regex patterns for more complex replacements
REGEX_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    # Handle multiple spaces
    (re.compile(r'\s+'), ' '),
]


def syntax_to_speech(text: str, include_operators: bool = True) -> str:
    """
    Convert programming syntax to natural language for text-to-speech.
    
    Args:
        text: The text containing programming syntax
        include_operators: Whether to include operator replacements
        
    Returns:
        Text with syntax converted to speakable format
    """
    if not text:
        return text
        
    result = text
    
    # Apply basic syntax replacements
    for syntax, speech in SYNTAX_TO_SPEECH_MAP.items():
        # Skip operators if not requested
        if not include_operators and syntax in ["==", "!=", "<=", ">=", "<", ">", "=", "+", "-", "*", "/", "%", "**", "//", "&&", "||", "!"]:
            continue
            
        # Only replace brackets, braces, and parentheses for now
        if syntax not in ["(", ")", "[", "]", "{", "}"]:
            continue
            
        result = result.replace(syntax, speech)
    
    # Apply regex replacements
    for pattern, replacement in REGEX_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    
    # Clean up multiple spaces that may have been introduced
    result = ' '.join(result.split())
    
    return result


def syntax_to_speech_code(code: str) -> str:
    """
    Convert code syntax to natural language, optimized for code reading.
    
    This version is specifically for reading code lines and includes
    more contextual processing.
    
    Args:
        code: A line or block of code
        
    Returns:
        Code with syntax converted to speakable format
    """
    if not code:
        return code
        
    # For code, we want to be more selective about what we convert
    result = code
    
    # Replace brackets, braces, and parentheses
    replacements = {
        "(": " open paren ",
        ")": " close paren ",
        "[": " open bracket ",
        "]": " close bracket ",
        "{": " open brace ",
        "}": " close brace ",
    }
    
    for syntax, speech in replacements.items():
        result = result.replace(syntax, speech)
    
    # Clean up multiple spaces
    result = ' '.join(result.split())
    
    return result


def syntax_to_speech_value(value_str: str) -> str:
    """
    Convert value representations to natural language.
    
    This version is optimized for reading variable values and data structures.
    
    Args:
        value_str: String representation of a value
        
    Returns:
        Value with syntax converted to speakable format
    """
    if not value_str:
        return value_str
        
    result = value_str
    
    # For values, we want clear structure announcements
    replacements = {
        "[": " list open bracket ",
        "]": " close bracket ",
        "{": " dict open brace ",
        "}": " close brace ",
        "(": " tuple open paren ",
        ")": " close paren ",
    }
    
    # Check if it looks like a dict (has colons) or a list/tuple
    if ":" in value_str and "{" in value_str:
        # It's likely a dict, use dict-specific language
        replacements["{"] = " dictionary open brace "
    elif "[" in value_str:
        # It's likely a list
        replacements["["] = " list open bracket "
    elif "(" in value_str and "," in value_str:
        # It's likely a tuple
        replacements["("] = " tuple open paren "
    
    for syntax, speech in replacements.items():
        result = result.replace(syntax, speech)
    
    # Clean up multiple spaces
    result = ' '.join(result.split())
    
    return result


def test_conversions():
    """Test the syntax-to-speech conversions."""
    
    # Test code examples
    code_examples = [
        "def foo(x, y):",
        "result = calculate(a[0], b[1])",
        "data = {'key': [1, 2, 3]}",
        "if (x > 0) and (y < 10):",
        "matrix[i][j] = value",
    ]
    
    print("Code syntax conversions:")
    for code in code_examples:
        converted = syntax_to_speech_code(code)
        print(f"  {code}")
        print(f"  → {converted}")
        print()
    
    # Test value examples
    value_examples = [
        "[1, 2, 3]",
        "{'name': 'John', 'age': 30}",
        "(1, 2, 3)",
        "[[1, 2], [3, 4]]",
        "{('a', 'b'): [1, 2, 3]}",
    ]
    
    print("Value syntax conversions:")
    for value in value_examples:
        converted = syntax_to_speech_value(value)
        print(f"  {value}")
        print(f"  → {converted}")
        print()


if __name__ == "__main__":
    test_conversions()