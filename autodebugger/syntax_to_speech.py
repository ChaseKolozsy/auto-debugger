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
    Convert code syntax to natural language with depth tracking.
    
    This version is specifically for reading code lines and includes
    depth tracking for nested structures.
    
    Args:
        code: A line or block of code
        
    Returns:
        Code with syntax converted to speakable format including depth levels
    """
    if not code:
        return code
    
    # Process line by line for multi-line code
    lines = code.split('\n')
    processed_lines = []
    
    # Track depth across lines (for multi-line structures)
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    
    for line in lines:
        result = ""
        in_string = False
        string_char = None
        i = 0
        
        while i < len(line):
            char = line[i]
            
            # Handle string detection to avoid replacing # inside strings
            if char in ['"', "'"] and (i == 0 or line[i-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                    result += char
                elif char == string_char:
                    in_string = False
                    string_char = None
                    result += char
                else:
                    result += char
                i += 1
                continue
            
            # Handle comment detection (# not in string)
            if char == '#' and not in_string:
                # Found a comment - replace # with "comment" and include the rest of the line
                comment_text = line[i+1:].strip()  # Get comment text after #
                if comment_text:
                    result += " comment " + comment_text
                else:
                    result += " comment"
                break  # Stop processing this line after comment
            
            # Handle brackets, braces, and parentheses with depth tracking
            if not in_string:
                if char == '(':
                    paren_depth += 1
                    result += f" open paren level {paren_depth} "
                elif char == ')':
                    if paren_depth > 0:
                        result += f" close paren level {paren_depth} "
                        paren_depth -= 1
                    else:
                        result += " close paren "
                elif char == '[':
                    bracket_depth += 1
                    result += f" open bracket level {bracket_depth} "
                elif char == ']':
                    if bracket_depth > 0:
                        result += f" close bracket level {bracket_depth} "
                        bracket_depth -= 1
                    else:
                        result += " close bracket "
                elif char == '{':
                    brace_depth += 1
                    result += f" open brace level {brace_depth} "
                elif char == '}':
                    if brace_depth > 0:
                        result += f" close brace level {brace_depth} "
                        brace_depth -= 1
                    else:
                        result += " close brace "
                else:
                    result += char
            else:
                result += char
            
            i += 1
        
        # Clean up multiple spaces for this line
        result = ' '.join(result.split())
        processed_lines.append(result)
    
    # Join all processed lines back together
    return '\n'.join(processed_lines)


def syntax_to_speech_value(value_str: str) -> str:
    """
    Convert value representations to natural language with depth tracking.
    
    This version is optimized for reading variable values and data structures,
    and tracks nesting depth for brackets, braces, and parentheses.
    
    Args:
        value_str: String representation of a value
        
    Returns:
        Value with syntax converted to speakable format including depth levels
    """
    if not value_str:
        return value_str
    
    result = ""
    in_string = False
    string_char = None
    
    # Track depth for each type of bracket
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    
    # Determine primary structure type
    is_dict = ":" in value_str and "{" in value_str
    is_list = "[" in value_str
    is_tuple = "(" in value_str and "," in value_str
    
    i = 0
    while i < len(value_str):
        char = value_str[i]
        
        # Handle string detection to avoid counting brackets inside strings
        if char in ['"', "'"] and (i == 0 or value_str[i-1] != '\\'):
            if not in_string:
                in_string = True
                string_char = char
                result += char
            elif char == string_char:
                in_string = False
                string_char = None
                result += char
            else:
                result += char
            i += 1
            continue
        
        # Handle brackets with depth tracking when not in string
        if not in_string:
            if char == '(':
                paren_depth += 1
                if is_tuple and paren_depth == 1:
                    result += f" tuple open paren level {paren_depth} "
                else:
                    result += f" open paren level {paren_depth} "
            elif char == ')':
                if paren_depth > 0:
                    result += f" close paren level {paren_depth} "
                    paren_depth -= 1
                else:
                    result += " close paren "
            elif char == '[':
                bracket_depth += 1
                if is_list and bracket_depth == 1:
                    result += f" list open bracket level {bracket_depth} "
                else:
                    result += f" open bracket level {bracket_depth} "
            elif char == ']':
                if bracket_depth > 0:
                    result += f" close bracket level {bracket_depth} "
                    bracket_depth -= 1
                else:
                    result += " close bracket "
            elif char == '{':
                brace_depth += 1
                if is_dict and brace_depth == 1:
                    result += f" dictionary open brace level {brace_depth} "
                else:
                    result += f" open brace level {brace_depth} "
            elif char == '}':
                if brace_depth > 0:
                    result += f" close brace level {brace_depth} "
                    brace_depth -= 1
                else:
                    result += " close brace "
            else:
                result += char
        else:
            result += char
        
        i += 1
    
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
        "x = 5  # Initialize x",
        "# This is a comment",
        "url = 'http://example.com#anchor'  # URL with # in string",
        "print('#' * 10)  # Print hash symbols",
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