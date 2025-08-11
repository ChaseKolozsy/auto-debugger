from __future__ import annotations

"""
Interactive nested value explorer for audio debugging.

Allows users to navigate through nested data structures (lists, dicts, tuples, objects)
with y/n prompts to explore deeper levels.
"""

import sys
import select
import time
from typing import Any, Dict, List, Optional, Tuple, Union, Callable


class NestedValueExplorer:
    """Interactive explorer for nested data structures during audio debugging."""
    
    def __init__(self, tts: Any, verbose: bool = False, action_provider: Optional[Callable[[], Optional[str]]] = None):
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
        # Optional callback to retrieve user actions (for web UI). Should return an action string like 'y'/'n'.
        self._action_provider = action_provider
        
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
        
        # Process items one at a time, asking for each
        i = 0
        while i < length:
            item_name = f"{name}[{i}]"
            item_value = sequence[i]
            
            # Give brief description
            if isinstance(item_value, (int, float, str, bool, type(None))):
                self._announce_primitive(item_name, item_value, type(item_value).__name__)
                self._wait_for_speech()
            else:
                item_type = type(item_value).__name__
                self.tts.speak(f"{item_name} is {item_type}")
                self._wait_for_speech()
                
                # For complex items, ask what to do
                self.tts.speak(f"{item_name} has nested values. Press Y to explore deeper, N to skip")
                self._wait_for_speech()
                
                if self._get_yes_no_response():
                    self.explore_value(item_name, item_value, depth + 1)
            
            # Ask if user wants to continue to next item
            if i < length - 1:
                self.tts.speak(f"Continue to next item? Y for yes, N to stop")
                self._wait_for_speech()
                if not self._get_yes_no_response():
                    break
            
            i += 1
                            
    def _explore_dict(self, name: str, dict_value: Dict, depth: int) -> None:
        """Explore a dictionary."""
        num_keys = len(dict_value)
        
        if num_keys == 0:
            self.tts.speak(f"{name} is empty dictionary")
            return
            
        self.tts.speak(f"{name} is dictionary with {num_keys} keys")
        self._wait_for_speech()
        
        keys = list(dict_value.keys())
        
        # Process keys one at a time, asking for each
        i = 0
        while i < num_keys:
            key = keys[i]
            value = dict_value[key]
            item_name = f"{name}[{repr(key)}]"
            
            # Give brief description
            if isinstance(value, (int, float, str, bool, type(None))):
                self._announce_primitive(item_name, value, type(value).__name__)
                self._wait_for_speech()
            else:
                value_type = type(value).__name__
                self.tts.speak(f"{item_name} is {value_type}")
                self._wait_for_speech()
                
                # For complex values, ask what to do
                self.tts.speak(f"{item_name} has nested values. Press Y to explore deeper, N to skip")
                self._wait_for_speech()
                
                if self._get_yes_no_response():
                    self.explore_value(item_name, value, depth + 1)
            
            # Ask if user wants to continue to next key
            if i < num_keys - 1:
                self.tts.speak(f"Continue to next key? Y for yes, N to stop")
                self._wait_for_speech()
                if not self._get_yes_no_response():
                    break
            
            i += 1
                            
    def _explore_object(self, name: str, obj: Any, depth: int) -> None:
        """Explore an object with attributes."""
        obj_type = type(obj).__name__
        attrs = [attr for attr in dir(obj) if not attr.startswith('_')]
        
        # Filter out callable attributes
        non_callable_attrs = []
        for attr in attrs:
            try:
                value = getattr(obj, attr)
                if not callable(value):
                    non_callable_attrs.append(attr)
            except Exception:
                continue
        
        if not non_callable_attrs:
            self.tts.speak(f"{name} is {obj_type} with no public attributes")
            return
            
        self.tts.speak(f"{name} is {obj_type} with {len(non_callable_attrs)} attributes")
        self._wait_for_speech()
        
        # Process attributes one at a time
        i = 0
        while i < len(non_callable_attrs):
            attr = non_callable_attrs[i]
            try:
                value = getattr(obj, attr)
                item_name = f"{name}.{attr}"
                
                # Give brief description
                if isinstance(value, (int, float, str, bool, type(None))):
                    self._announce_primitive(item_name, value, type(value).__name__)
                    self._wait_for_speech()
                else:
                    value_type = type(value).__name__
                    self.tts.speak(f"{item_name} is {value_type}")
                    self._wait_for_speech()
                    
                    # For complex values, ask what to do
                    self.tts.speak(f"{item_name} has nested values. Press Y to explore deeper, N to skip")
                    self._wait_for_speech()
                    
                    if self._get_yes_no_response():
                        self.explore_value(item_name, value, depth + 1)
                
                # Ask if user wants to continue to next attribute
                if i < len(non_callable_attrs) - 1:
                    self.tts.speak(f"Continue to next attribute? Y for yes, N to stop")
                    self._wait_for_speech()
                    if not self._get_yes_no_response():
                        break
                
                i += 1
            except Exception:
                # Skip attributes that can't be accessed
                i += 1
                continue
        
    def _get_yes_no_response(self) -> bool:
        """Get a yes/no response from the user."""
        print("\n[Explorer] Waiting for response: y/yes or n/no: ", end='', flush=True)
        while True:
            # Prefer external action provider if available (e.g., web UI)
            if self._action_provider is not None:
                action = self._action_provider()
                if action is None:
                    time.sleep(0.05)
                    continue
                act = str(action).strip().lower()
                if act in ('y', 'yes'):
                    if self.verbose:
                        print("[Explorer] User selected: Yes")
                    return True
                if act in ('n', 'no'):
                    if self.verbose:
                        print("[Explorer] User selected: No")
                    return False
                # Ignore unrelated actions
                continue

            # Fallback to stdin for terminal mode
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
                    print("\n[Explorer] Please enter y/yes or n/no: ", end='', flush=True)
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