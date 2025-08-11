#!/usr/bin/env python3
"""
Test script for demonstrating nested structure exploration in the auto-debugger.
This creates various nested data structures to test the interactive exploration feature.
"""

def test_nested_structures():
    # Simple values
    simple_int = 42
    simple_str = "Hello, World!"
    simple_float = 3.14159
    
    # List with nested structures
    nested_list = [
        1,
        "string",
        [2, 3, [4, 5]],
        {"key": "value", "nested": {"deep": "value"}},
    ]
    
    # Dictionary with various types
    nested_dict = {
        "name": "Test Object",
        "count": 10,
        "items": ["apple", "banana", "cherry"],
        "metadata": {
            "created": "2024-01-01",
            "tags": ["test", "demo", "nested"],
            "config": {
                "enabled": True,
                "level": 3,
                "options": ["opt1", "opt2", "opt3"]
            }
        }
    }
    
    # Tuple with mixed types
    mixed_tuple = (
        "first",
        42,
        [1, 2, 3],
        {"a": 1, "b": 2},
        (5, 6, 7)
    )
    
    # Custom object with attributes
    class TestObject:
        def __init__(self):
            self.name = "TestObject"
            self.value = 100
            self.children = [1, 2, 3]
            self.data = {"x": 10, "y": 20}
            self.nested = NestedObject()
    
    class NestedObject:
        def __init__(self):
            self.level = 2
            self.message = "I am nested"
            self.items = ["a", "b", "c"]
    
    custom_obj = TestObject()
    
    # Large list (to test pagination)
    large_list = list(range(20))
    
    # Deep nesting
    deep_structure = {
        "level1": {
            "level2": {
                "level3": {
                    "level4": {
                        "level5": "deep value",
                        "siblings": [1, 2, 3]
                    }
                }
            }
        }
    }
    
    # Let's do some operations to trigger changes
    nested_list.append("new item")
    nested_dict["new_key"] = "new_value"
    custom_obj.value = 200
    
    # Final computation
    result = simple_int + simple_float + custom_obj.value
    
    print(f"Result: {result}")
    print(f"Nested list length: {len(nested_list)}")
    print(f"Dict keys: {list(nested_dict.keys())}")
    
    return result

if __name__ == "__main__":
    test_nested_structures()