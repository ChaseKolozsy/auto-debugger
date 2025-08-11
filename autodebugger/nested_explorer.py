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
    
    def __init__(
        self,
        tts: Any,
        verbose: bool = False,
        action_provider: Optional[Callable[[], Optional[str]]] = None,
        children_provider: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        data_fetcher: Optional[Callable[[int], Any]] = None,
    ):
        """
        Initialize the explorer.
        
        Args:
            tts: Text-to-speech instance (MacSayTTS)
            verbose: Whether to print debug info
            action_provider: Callback to get user actions
            children_provider: Callback to fetch DAP children
            data_fetcher: Callback to fetch complete data from debugpy
        """
        self.tts = tts
        self.verbose = verbose
        self.max_items_before_prompt = 3  # Show first 3 items before asking to continue
        self.max_depth = 10  # Maximum nesting depth
        # Optional callback to retrieve user actions (for web UI). Should return an action string like 'y'/'n'.
        self._action_provider = action_provider
        # Optional callback to resolve DAP children lazily when a node has a 'ref'
        self._children_provider = children_provider
        # Optional callback to fetch complete data when ellipsis is encountered
        self._data_fetcher = data_fetcher
        
        # Navigation state for interactive exploration
        self._navigation_stack = []  # Stack of (container, index, name) tuples
        self._current_container = None
        self._current_index = 0
        self._current_path = []
        
    def explore_interactive(self, name: str, value: Any) -> None:
        """
        Explore a value with interactive navigation (i=into, o=out, n=next).
        
        Handles ellipsis by fetching data only when user steps into it.
        """
        # Initialize navigation
        self._navigation_stack = []
        self._current_container = value
        self._current_index = 0
        self._current_path = [name]
        
        # Check if value needs initial parsing
        if isinstance(value, dict) and "_parsed" in value:
            self._current_container = value["_parsed"]
        
        # Start exploration
        exploring = True
        while exploring:
            # Announce current position
            path_str = ".".join(self._current_path) if len(self._current_path) > 1 else self._current_path[0]
            
            # Check what we're looking at
            if self._is_ellipsis(self._current_container):
                # We have ellipsis - offer to fetch
                announcement = f"At {path_str}: Data is truncated (ellipsis). Press I to fetch complete data, N to skip, O to go back"
                self.tts.speak(announcement)
                print(f"[TTS] {announcement}")
                self._wait_for_speech()
                
                action = self._get_navigation_action()
                if action == 'i':
                    # Fetch the complete data
                    if self._data_fetcher and hasattr(self._current_container, '__ref__'):
                        ref = self._current_container.__ref__
                        announcement = f"Fetching complete data for {path_str}..."
                        self.tts.speak(announcement)
                        print(f"[TTS] {announcement}")
                        self._wait_for_speech()
                        
                        fetched_data = self._data_fetcher(ref)
                        if fetched_data is not None:
                            self._current_container = fetched_data
                            # Continue to explore the fetched data
                        else:
                            self.tts.speak("Could not fetch data")
                            print("[TTS] Could not fetch data")
                    else:
                        self.tts.speak("No reference available to fetch data")
                        print("[TTS] No reference available to fetch data")
                        
                elif action == 'o':
                    # Step out
                    if self._navigation_stack:
                        parent_container, parent_index, parent_name = self._navigation_stack.pop()
                        self._current_container = parent_container
                        self._current_index = parent_index
                        self._current_path.pop()
                    else:
                        self.tts.speak("Already at top level")
                        print("[TTS] Already at top level")
                        
                elif action == 'n':
                    # Skip to next at current level
                    self._move_to_next()
                    
                elif action == 'q' or action == 'quit':
                    exploring = False
                    
            elif isinstance(self._current_container, (list, tuple)):
                # Navigate list/tuple
                self._navigate_sequence(path_str)
                
            elif isinstance(self._current_container, dict):
                # Navigate dictionary
                self._navigate_dict(path_str)
                
            else:
                # Simple value - announce and offer navigation
                self._announce_current_value(path_str)
                action = self._get_navigation_action()
                
                if action == 'n':
                    self._move_to_next()
                elif action == 'o':
                    if self._navigation_stack:
                        parent_container, parent_index, parent_name = self._navigation_stack.pop()
                        self._current_container = parent_container
                        self._current_index = parent_index
                        self._current_path.pop()
                    else:
                        exploring = False
                elif action == 'q':
                    exploring = False
    
    def _is_ellipsis(self, value: Any) -> bool:
        """Check if a value contains or is ellipsis."""
        if value is Ellipsis:
            return True
        if isinstance(value, str) and ('[...]' in value or '{...}' in value):
            return True
        if isinstance(value, dict) and value.get("_needs_fetch"):
            return True
        return False
    
    def _get_navigation_action(self) -> str:
        """Get navigation action from user (i/o/n/q)."""
        self.tts.speak("Press I to step into, O to step out, N for next, Q to quit")
        print("[TTS] Press I to step into, O to step out, N for next, Q to quit")
        self._wait_for_speech()
        
        if self._action_provider:
            while True:
                action = self._action_provider()
                if action:
                    return action.strip().lower()
                time.sleep(0.1)
        else:
            return input().strip().lower()
    
    def _navigate_sequence(self, path_str: str) -> None:
        """Navigate through a list or tuple."""
        container = self._current_container
        if self._current_index >= len(container):
            self._current_index = 0
            
        item = container[self._current_index]
        item_path = f"{path_str}[{self._current_index}]"
        
        # Check if item is ellipsis or complex
        if self._is_ellipsis(item):
            announcement = f"{item_path} contains truncated data. Press I to fetch, N for next item"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        elif isinstance(item, (list, tuple, dict)):
            announcement = f"{item_path} is {type(item).__name__}. Press I to explore, N for next"
            self.tts.speak(announcement)  
            print(f"[TTS] {announcement}")
        else:
            announcement = f"{item_path} = {item}"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        
        self._wait_for_speech()
        action = self._get_navigation_action()
        
        if action == 'i':
            # Step into this item
            self._navigation_stack.append((container, self._current_index, path_str))
            self._current_container = item
            self._current_index = 0
            self._current_path.append(f"[{self._current_index}]")
        elif action == 'n':
            # Move to next item
            self._current_index += 1
            if self._current_index >= len(container):
                self.tts.speak("End of list. Wrapping to beginning.")
                print("[TTS] End of list. Wrapping to beginning.")
                self._current_index = 0
        elif action == 'o':
            # Step out
            if self._navigation_stack:
                parent_container, parent_index, parent_name = self._navigation_stack.pop()
                self._current_container = parent_container
                self._current_index = parent_index
                self._current_path.pop()
    
    def _navigate_dict(self, path_str: str) -> None:
        """Navigate through a dictionary."""
        container = self._current_container
        keys = list(container.keys())
        
        if not keys:
            self.tts.speak(f"{path_str} is empty dictionary")
            print(f"[TTS] {path_str} is empty dictionary")
            return
            
        if self._current_index >= len(keys):
            self._current_index = 0
            
        key = keys[self._current_index]
        value = container[key]
        item_path = f"{path_str}.{key}" if not key.startswith('[') else f"{path_str}{key}"
        
        # Check if value is ellipsis or complex
        if self._is_ellipsis(value):
            announcement = f"{item_path} contains truncated data. Press I to fetch, N for next"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        elif isinstance(value, (list, tuple, dict)):
            announcement = f"{item_path} is {type(value).__name__}. Press I to explore, N for next"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        else:
            announcement = f"{item_path} = {value}"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        
        self._wait_for_speech()
        action = self._get_navigation_action()
        
        if action == 'i':
            # Step into this value
            self._navigation_stack.append((container, self._current_index, path_str))
            self._current_container = value
            self._current_index = 0
            self._current_path.append(f".{key}")
        elif action == 'n':
            # Move to next key
            self._current_index += 1
            if self._current_index >= len(keys):
                self.tts.speak("End of dictionary. Wrapping to beginning.")
                print("[TTS] End of dictionary. Wrapping to beginning.")
                self._current_index = 0
        elif action == 'o':
            # Step out
            if self._navigation_stack:
                parent_container, parent_index, parent_name = self._navigation_stack.pop()
                self._current_container = parent_container
                self._current_index = parent_index
                self._current_path.pop()
    
    def _announce_current_value(self, path_str: str) -> None:
        """Announce the current simple value."""
        value = self._current_container
        announcement = f"{path_str} = {value}"
        self.tts.speak(announcement)
        print(f"[TTS] {announcement}")
        self._wait_for_speech()
    
    def _move_to_next(self) -> None:
        """Move to next item at current level or step out if at end."""
        if self._navigation_stack:
            # We're inside something, try to move to next in parent
            parent_container, parent_index, parent_name = self._navigation_stack[-1]
            if isinstance(parent_container, (list, tuple)):
                if parent_index + 1 < len(parent_container):
                    # Move to next in parent
                    self._navigation_stack.pop()
                    self._current_container = parent_container
                    self._current_index = parent_index + 1
                    self._current_path.pop()
                else:
                    # At end of parent, step out
                    self._navigation_stack.pop()
                    self._current_container = parent_container
                    self._current_index = parent_index
                    self._current_path.pop()
            elif isinstance(parent_container, dict):
                keys = list(parent_container.keys())
                if parent_index + 1 < len(keys):
                    # Move to next in parent
                    self._navigation_stack.pop()
                    self._current_container = parent_container
                    self._current_index = parent_index + 1
                    self._current_path.pop()
                else:
                    # At end of parent, step out
                    self._navigation_stack.pop()
                    self._current_container = parent_container
                    self._current_index = parent_index
                    self._current_path.pop()
        else:
            self.tts.speak("No next item at top level")
            print("[TTS] No next item at top level")
    
    def read_complete_structure(self, name: str, value: Any) -> None:
        """Read out a complete data structure naturally, like reading code.
        
        For lists: "1 comma 2 comma 3"
        For dicts: "key1 colon value1 comma key2 colon value2"
        For nested: reads the entire structure recursively
        """
        announcement = f"{name} equals {self._format_for_speech(value)}"
        self.tts.speak(announcement)
        print(f"[TTS] {announcement}")
        self._wait_for_speech()
    
    def _format_for_speech(self, value: Any, depth: int = 0) -> str:
        """Format a value for natural speech reading.
        
        Lists: items separated by 'comma'
        Dicts: key colon value pairs separated by 'comma'
        """
        if depth > 10:  # Safety limit
            return "deeply nested structure"
            
        if value is None:
            return "None"
        elif isinstance(value, bool):
            return str(value)
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # For short strings, read them directly
            if len(value) < 50:
                return f"string {value}"
            else:
                return f"string of {len(value)} characters"
        elif isinstance(value, list):
            if not value:
                return "empty list"
            # Read items separated by comma
            items = []
            for item in value:
                items.append(self._format_for_speech(item, depth + 1))
            return f"list of {', '.join(items)}"
        elif isinstance(value, tuple):
            if not value:
                return "empty tuple"
            items = []
            for item in value:
                items.append(self._format_for_speech(item, depth + 1))
            return f"tuple of {', '.join(items)}"
        elif isinstance(value, dict):
            if not value:
                return "empty dict"
            # Skip private attributes for cleaner reading
            items = []
            for k, v in value.items():
                if not (isinstance(k, str) and k.startswith('_')):
                    key_str = str(k)
                    val_str = self._format_for_speech(v, depth + 1)
                    items.append(f"{key_str} colon {val_str}")
            if items:
                return f"dict with {', '.join(items)}"
            else:
                return "dict with private attributes"
        else:
            return f"{type(value).__name__} object"
    
    def explore_value(self, name: str, value: Any, depth: int = 0) -> None:
        """
        Explore a value interactively, prompting for deeper exploration.
        
        Args:
            name: Variable name or path (e.g., "x" or "x.field")
            value: The value to explore (may be a dict with "_parsed" key for DAP values)
            depth: Current nesting depth
        """
        print(f"\n[Explorer] === Exploring '{name}' at depth {depth} ===")
        print(f"[Explorer] Raw value type: {type(value).__name__}")
        if isinstance(value, dict) and len(str(value)) < 200:
            print(f"[Explorer] Raw value: {value}")
        
        # Check if this is a structured DAP value with parsed content
        if isinstance(value, dict) and "_parsed" in value:
            # Use the parsed value for exploration
            actual_value = value["_parsed"]
            print(f"[Explorer] Found _parsed value, type: {type(actual_value).__name__}")
            print(f"[Explorer] Parsed value: {actual_value}")
            # Keep reference info for lazy loading if needed
            ref_info = {"ref": value.get("_ref"), "children": value.get("_children")}
        else:
            actual_value = value
            ref_info = None
            
        # Continue with exploration using the actual value
        if depth >= self.max_depth:
            announcement = f"Maximum depth reached for {name}"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
            return
            
        # Announce the variable and its type
        value_type = type(actual_value).__name__
        
        # Handle None
        if actual_value is None:
            self.tts.speak(f"{name} is None")
            return
            
        # Handle primitive types
        if isinstance(actual_value, (int, float, str, bool)):
            self._announce_primitive(name, actual_value, value_type)
            return
            
        # Handle collections - use actual_value for real Python objects
        if isinstance(actual_value, (list, tuple)):
            self._explore_sequence(name, actual_value, value_type, depth)
        elif isinstance(actual_value, dict):
            # Check if this is still a DAP node structure (shouldn't be after parsing)
            if "children" in actual_value or "ref" in actual_value or "value" in actual_value:
                self._explore_dap_node(name, actual_value, depth)
            else:
                self._explore_dict(name, actual_value, depth)
        elif hasattr(actual_value, '__dict__'):
            self._explore_object(name, actual_value, depth)
        else:
            # Fallback for other types
            self.tts.speak(f"{name} is {value_type}: {str(actual_value)[:100]}")
            
    def _announce_primitive(self, name: str, value: Any, value_type: str) -> None:
        """Announce a primitive value."""
        if isinstance(value, str) and len(value) > 50:
            announcement = f"{name} is string of length {len(value)}: {value[:50]}..."
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
        else:
            # The value representation might have brackets/braces, so it will be converted by TTS
            announcement = f"{name} is {value_type}: {value}"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
            
    def _explore_sequence(self, name: str, sequence: Union[List, Tuple], seq_type: str, depth: int) -> None:
        """Explore a list or tuple."""
        length = len(sequence)
        
        if length == 0:
            announcement = f"{name} is empty {seq_type}"
            self.tts.speak(announcement)
            print(f"[TTS] {announcement}")
            return
            
        # Announce with actual contents preview
        announcement = f"{name} is {seq_type} with {length} items"
        self.tts.speak(announcement)
        print(f"[TTS] {announcement}")
        self._wait_for_speech()
        
        # Show preview of contents
        if self.verbose or True:  # Always show for debugging
            print(f"[Explorer] Contents: {sequence}")
        
        # Process items one at a time, asking for each
        i = 0
        while i < length:
            item_name = f"{name}[{i}]"
            item_value = sequence[i]
            
            # Give brief description with actual value
            if isinstance(item_value, (int, float, str, bool, type(None))):
                self._announce_primitive(item_name, item_value, type(item_value).__name__)
                self._wait_for_speech()
            else:
                item_type = type(item_value).__name__
                # Try to give more detail about the item
                if isinstance(item_value, (list, tuple)):
                    detail = f"{item_name} is {item_type} with {len(item_value)} items"
                elif isinstance(item_value, dict):
                    detail = f"{item_name} is {item_type} with {len(item_value)} keys"
                else:
                    detail = f"{item_name} is {item_type}"
                self.tts.speak(detail)
                print(f"[TTS] {detail}")
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
            
            
    def _resolve_dap_children(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve children for a DAP variable node using provider if needed."""
        children = node.get("children")
        if isinstance(children, dict) and children:
            return children
        ref = node.get("ref")
        if self._children_provider and isinstance(ref, int) and ref > 0:
            try:
                children = self._children_provider(node)
                if isinstance(children, dict):
                    node["children"] = children
                    return children
            except Exception:
                pass
        return children or {}

    def _explore_dap_node(self, name: str, node: Dict[str, Any], depth: int) -> None:
        """Explore a DAP variable node that may have children and a variablesReference."""
        # Announce scalar if no children path
        children = self._resolve_dap_children(node)
        value_repr = node.get("value")
        if not children:
            self._announce_primitive(name, value_repr, type(value_repr).__name__ if value_repr is not None else 'str')
            return

        # Identify if sequence-like: keys all digits and contiguous
        keys = list(children.keys())
        is_sequence = False
        try:
            idxs = sorted(int(k) for k in keys if str(k).isdigit())
            is_sequence = (len(idxs) == len(keys)) and (idxs == list(range(len(keys))))
        except Exception:
            is_sequence = False

        if is_sequence:
            length = len(keys)
            self.tts.speak(f"{name} is list with {length} items")
            self._wait_for_speech()
            i = 0
            while i < length:
                item_name = f"{name}[{i}]"
                item_node = children.get(str(i))
                # Brief
                brief = item_node.get("value") if isinstance(item_node, dict) else str(item_node)
                self.tts.speak(f"{item_name} is {brief[:100] if isinstance(brief, str) else brief}")
                self._wait_for_speech()
                # Ask to go deeper if child potentially has children
                can_deeper = isinstance(item_node, dict) and (item_node.get("children") or (item_node.get("ref") and self._children_provider))
                if can_deeper:
                    self.tts.speak(f"{item_name} has nested values. Press Y to explore deeper, N to skip")
                    self._wait_for_speech()
                    if self._get_yes_no_response():
                        self.explore_value(item_name, item_node, depth + 1)
                if i < length - 1:
                    self.tts.speak("Continue to next item? Y for yes, N to stop")
                    self._wait_for_speech()
                    if not self._get_yes_no_response():
                        break
                i += 1
            return

        # Otherwise treat as mapping
        self.tts.speak(f"{name} is dictionary with {len(keys)} keys")
        self._wait_for_speech()
        i = 0
        while i < len(keys):
            key = keys[i]
            item_name = f"{name}[{repr(key)}]"
            item_node = children.get(key)
            brief = item_node.get("value") if isinstance(item_node, dict) else str(item_node)
            self.tts.speak(f"{item_name} is {brief[:100] if isinstance(brief, str) else brief}")
            self._wait_for_speech()
            can_deeper = isinstance(item_node, dict) and (item_node.get("children") or (item_node.get("ref") and self._children_provider))
            if can_deeper:
                self.tts.speak(f"{item_name} has nested values. Press Y to explore deeper, N to skip")
                self._wait_for_speech()
                if self._get_yes_no_response():
                    self.explore_value(item_name, item_node, depth + 1)
            if i < len(keys) - 1:
                self.tts.speak("Continue to next key? Y for yes, N to stop")
                self._wait_for_speech()
                if not self._get_yes_no_response():
                    break
            i += 1

def format_nested_value_summary(value: Any, max_depth: int = 2) -> str:
    """
    Format a summary of a nested value for quick audio presentation.
    
    Args:
        value: The value to summarize (may be a dict with "_parsed" key for DAP values)
        max_depth: Maximum depth to traverse
        
    Returns:
        A string summary suitable for TTS
    """
    # Check if this is a structured DAP value with parsed content
    if isinstance(value, dict) and "_parsed" in value:
        # Use the parsed value for summary
        value = value["_parsed"]
    
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