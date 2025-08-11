from __future__ import annotations

"""
Interactive nested value explorer for audio debugging.

Allows users to navigate through nested data structures (lists, dicts, tuples, objects)
with y/n prompts to explore deeper levels.
"""

import sys
import select
import time
from typing import Any, Dict, List, Optional, Tuple, Union


class NestedValueExplorer:
    """Interactive explorer for nested data structures during audio debugging."""
    
    def __init__(self, tts: Any, verbose: bool = False):
        """
        Initialize the explorer.
        
        Args:
            tts: Text-to-speech instance (MacSayTTS)
            verbose: Whether to print debug info
        """
        self.tts = tts
        self.verbose = verbose
        self.max_items_before_prompt = 3  # Show first 3 items before asking to continue
        self.max_depth = 5  # Maximum nesting depth to prevent infinite recursion
        
    def explore_value(self, name: str, value: Any, depth: int = 0) -> None:
        """
        Explore a value interactively, prompting for deeper exploration.
        
        Args:
            name: Variable name or path (e.g., "x" or "x.field")
            value: The value to explore
            depth: Current nesting depth
        """
        if depth >= self.max_depth:
            self.tts.speak(f"Maximum depth reached for {name}")
            return
            
        # Announce the variable and its type
        value_type = type(value).__name__
        
        # Handle None
        if value is None:
            self.tts.speak(f"{name} is None")
            return
            
        # Handle primitive types
        if isinstance(value, (int, float, str, bool)):
            self._announce_primitive(name, value, value_type)
            return
            
        # Handle collections
        if isinstance(value, (list, tuple)):
            self._explore_sequence(name, value, value_type, depth)
        elif isinstance(value, dict):
            self._explore_dict(name, value, depth)
        elif hasattr(value, '__dict__'):
            self._explore_object(name, value, depth)
        else:
            # Fallback for other types
            self.tts.speak(f"{name} is {value_type}: {str(value)[:100]}")
            
    def _announce_primitive(self, name: str, value: Any, value_type: str) -> None:
        """Announce a primitive value."""
        if isinstance(value, str) and len(value) > 50:
            self.tts.speak(f"{name} is string of length {len(value)}: {value[:50]}...")
        else:
            # The value representation might have brackets/braces, so it will be converted by TTS
            self.tts.speak(f"{name} is {value_type}: {value}")
            
    def _explore_sequence(self, name: str, sequence: Union[List, Tuple], seq_type: str, depth: int) -> None:
        """Explore a list or tuple."""
        length = len(sequence)
        
        if length == 0:
            self.tts.speak(f"{name} is empty {seq_type}")
            return
            
        self.tts.speak(f"{name} is {seq_type} with {length} items")
        self._wait_for_speech()
        
        # Show first few items
        items_to_show = min(self.max_items_before_prompt, length)
        for i in range(items_to_show):
            item_name = f"{name}[{i}]"
            item_value = sequence[i]
            
            # Give brief description
            if isinstance(item_value, (int, float, str, bool, type(None))):
                self._announce_primitive(item_name, item_value, type(item_value).__name__)
            else:
                item_type = type(item_value).__name__
                self.tts.speak(f"{item_name} is {item_type}")
            
            self._wait_for_speech()
            
            # Ask if user wants to explore this item deeper
            if not isinstance(item_value, (int, float, str, bool, type(None))):
                if self._prompt_explore_deeper(item_name):
                    self.explore_value(item_name, item_value, depth + 1)
                    
        # If there are more items, ask if user wants to continue
        if length > self.max_items_before_prompt:
            remaining = length - self.max_items_before_prompt
            self.tts.speak(f"{remaining} more items. Continue?")
            self._wait_for_speech()
            
            if self._get_yes_no_response():
                for i in range(self.max_items_before_prompt, length):
                    item_name = f"{name}[{i}]"
                    item_value = sequence[i]
                    
                    if isinstance(item_value, (int, float, str, bool, type(None))):
                        self._announce_primitive(item_name, item_value, type(item_value).__name__)
                    else:
                        item_type = type(item_value).__name__
                        self.tts.speak(f"{item_name} is {item_type}")
                        self._wait_for_speech()
                        
                        if self._prompt_explore_deeper(item_name):
                            self.explore_value(item_name, item_value, depth + 1)
                            
    def _explore_dict(self, name: str, dict_value: Dict, depth: int) -> None:
        """Explore a dictionary."""
        num_keys = len(dict_value)
        
        if num_keys == 0:
            self.tts.speak(f"{name} is empty dictionary")
            return
            
        self.tts.speak(f"{name} is dictionary with {num_keys} keys")
        self._wait_for_speech()
        
        keys = list(dict_value.keys())
        keys_to_show = min(self.max_items_before_prompt, num_keys)
        
        for i in range(keys_to_show):
            key = keys[i]
            value = dict_value[key]
            item_name = f"{name}[{repr(key)}]"
            
            # Give brief description
            if isinstance(value, (int, float, str, bool, type(None))):
                self._announce_primitive(item_name, value, type(value).__name__)
            else:
                value_type = type(value).__name__
                self.tts.speak(f"{item_name} is {value_type}")
            
            self._wait_for_speech()
            
            # Ask if user wants to explore deeper
            if not isinstance(value, (int, float, str, bool, type(None))):
                if self._prompt_explore_deeper(item_name):
                    self.explore_value(item_name, value, depth + 1)
                    
        # If there are more keys, ask if user wants to continue
        if num_keys > self.max_items_before_prompt:
            remaining = num_keys - self.max_items_before_prompt
            self.tts.speak(f"{remaining} more keys. Continue?")
            self._wait_for_speech()
            
            if self._get_yes_no_response():
                for i in range(self.max_items_before_prompt, num_keys):
                    key = keys[i]
                    value = dict_value[key]
                    item_name = f"{name}[{repr(key)}]"
                    
                    if isinstance(value, (int, float, str, bool, type(None))):
                        self._announce_primitive(item_name, value, type(value).__name__)
                    else:
                        value_type = type(value).__name__
                        self.tts.speak(f"{item_name} is {value_type}")
                        self._wait_for_speech()
                        
                        if self._prompt_explore_deeper(item_name):
                            self.explore_value(item_name, value, depth + 1)
                            
    def _explore_object(self, name: str, obj: Any, depth: int) -> None:
        """Explore an object with attributes."""
        obj_type = type(obj).__name__
        attrs = [attr for attr in dir(obj) if not attr.startswith('_')]
        
        if not attrs:
            self.tts.speak(f"{name} is {obj_type} with no public attributes")
            return
            
        self.tts.speak(f"{name} is {obj_type} with {len(attrs)} attributes")
        self._wait_for_speech()
        
        attrs_to_show = min(self.max_items_before_prompt, len(attrs))
        
        for i in range(attrs_to_show):
            attr = attrs[i]
            try:
                value = getattr(obj, attr)
                item_name = f"{name}.{attr}"
                
                # Skip methods
                if callable(value):
                    continue
                    
                # Give brief description
                if isinstance(value, (int, float, str, bool, type(None))):
                    self._announce_primitive(item_name, value, type(value).__name__)
                else:
                    value_type = type(value).__name__
                    self.tts.speak(f"{item_name} is {value_type}")
                
                self._wait_for_speech()
                
                # Ask if user wants to explore deeper
                if not isinstance(value, (int, float, str, bool, type(None))):
                    if self._prompt_explore_deeper(item_name):
                        self.explore_value(item_name, value, depth + 1)
            except Exception:
                # Skip attributes that can't be accessed
                continue
                
        # If there are more attributes, ask if user wants to continue
        if len(attrs) > self.max_items_before_prompt:
            remaining = len(attrs) - self.max_items_before_prompt
            self.tts.speak(f"{remaining} more attributes. Continue?")
            self._wait_for_speech()
            
            if self._get_yes_no_response():
                for i in range(self.max_items_before_prompt, len(attrs)):
                    attr = attrs[i]
                    try:
                        value = getattr(obj, attr)
                        item_name = f"{name}.{attr}"
                        
                        if callable(value):
                            continue
                            
                        if isinstance(value, (int, float, str, bool, type(None))):
                            self._announce_primitive(item_name, value, type(value).__name__)
                        else:
                            value_type = type(value).__name__
                            self.tts.speak(f"{item_name} is {value_type}")
                            self._wait_for_speech()
                            
                            if self._prompt_explore_deeper(item_name):
                                self.explore_value(item_name, value, depth + 1)
                    except Exception:
                        continue
                        
    def _prompt_explore_deeper(self, item_name: str) -> bool:
        """Ask if user wants to explore an item deeper."""
        self.tts.speak(f"Explore {item_name}? Press y for yes, n for no")
        self._wait_for_speech()
        return self._get_yes_no_response()
        
    def _get_yes_no_response(self) -> bool:
        """Get a yes/no response from the user."""
        while True:
            # Check for keyboard input
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline().strip().lower()
                if line in ['y', 'yes']:
                    if self.verbose:
                        print("[Explorer] User selected: Yes")
                    return True
                elif line in ['n', 'no']:
                    if self.verbose:
                        print("[Explorer] User selected: No")
                    return False
                else:
                    self.tts.speak("Please type y for yes or n for no")
                    self._wait_for_speech()
            time.sleep(0.05)
            
    def _wait_for_speech(self) -> None:
        """Wait for TTS to finish speaking."""
        while self.tts.is_speaking():
            time.sleep(0.05)
            
            
def format_nested_value_summary(value: Any, max_depth: int = 2) -> str:
    """
    Format a summary of a nested value for quick audio presentation.
    
    Args:
        value: The value to summarize
        max_depth: Maximum depth to traverse
        
    Returns:
        A string summary suitable for TTS
    """
    def _summarize(v: Any, depth: int = 0) -> str:
        if depth >= max_depth:
            return "..."
            
        if v is None:
            return "None"
        elif isinstance(v, bool):
            return str(v)
        elif isinstance(v, (int, float)):
            return str(v)
        elif isinstance(v, str):
            if len(v) > 20:
                return f'"{v[:20]}..."'
            return f'"{v}"'
        elif isinstance(v, (list, tuple)):
            type_name = "list" if isinstance(v, list) else "tuple"
            if not v:
                return f"empty {type_name}"
            if len(v) <= 3:
                items = [_summarize(item, depth + 1) for item in v]
                return f"[{', '.join(items)}]"
            else:
                return f"{type_name} with {len(v)} items"
        elif isinstance(v, dict):
            if not v:
                return "empty dict"
            if len(v) <= 2:
                items = [f"{k}: {_summarize(val, depth + 1)}" for k, val in list(v.items())[:2]]
                return f"{{{', '.join(items)}}}"
            else:
                return f"dict with {len(v)} keys"
        elif hasattr(v, '__dict__'):
            return f"{type(v).__name__} object"
        else:
            s = str(v)
            if len(s) > 30:
                return s[:30] + "..."
            return s
            
    return _summarize(value)