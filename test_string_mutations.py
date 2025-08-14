#!/usr/bin/env python3
"""Test script to demonstrate string mutation debugging needs."""

def process_user_input(text):
    """Simulate string processing with subtle bugs."""
    result = text.strip()
    
    # Bug 1: Wrong case handling
    if "admin" in result:  # Should be case-insensitive
        result = result.upper()
    
    # Bug 2: Incorrect string concatenation
    result = result + "processed"  # Missing space
    
    # Bug 3: Character encoding issue
    result = result.replace("é", "e")  # Loses accent
    
    # Bug 4: Off-by-one in slicing
    if len(result) > 10:
        result = result[:10]  # Should be [:11]
    
    return result

def parse_config_line(line):
    """Parse configuration with string manipulation bugs."""
    # Bug: Split on wrong delimiter
    parts = line.split(",")  # Config uses ";" as delimiter
    
    key = parts[0].strip() if parts else ""
    value = parts[1].strip() if len(parts) > 1 else ""
    
    # Bug: Wrong string interpolation
    formatted = f"Config: {key}={value}"  # Should escape special chars
    
    return formatted

def main():
    # Test string mutations
    test_cases = [
        "  Admin User  ",
        "café résumé",
        "short",
        "this is a longer string that will be truncated"
    ]
    
    for test in test_cases:
        result = process_user_input(test)
        print(f"Input: '{test}' -> Output: '{result}'")
    
    # Test config parsing
    config_lines = [
        "database;localhost:5432",
        "api_key;secret,with,commas",
        "timeout;30"
    ]
    
    for line in config_lines:
        parsed = parse_config_line(line)
        print(f"Config: '{line}' -> '{parsed}'")

if __name__ == "__main__":
    main()