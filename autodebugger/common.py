"""Common utilities shared across different UI modules."""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional, Tuple


def extract_function_context(file_path: str, line: int, source: Optional[str] = None) -> Dict[str, Any]:
    """
    Extract function context for a given file and line.
    
    This unified implementation replaces duplicated logic across modules.
    
    Args:
        file_path: Path to the Python file
        line: Line number to find context for
        source: Optional source code (if already loaded)
    
    Returns:
        Dictionary with 'name', 'sig', and 'body' keys
    """
    if not file_path or not os.path.isfile(file_path):
        return {"name": None, "sig": None, "body": None}
    
    try:
        # Load source if not provided
        if source is None:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        
        # Try AST parsing first for accuracy
        try:
            tree = ast.parse(source, filename=file_path)
            return _extract_function_context_ast(tree, source, line)
        except:
            # Fall back to heuristic method if AST parsing fails
            return _extract_function_context_heuristic(source, line)
            
    except Exception:
        return {"name": None, "sig": None, "body": None}


def _extract_function_context_ast(tree: ast.AST, source: str, line: int) -> Dict[str, Any]:
    """Extract function context using AST parsing."""
    sig_out: Optional[str] = None
    body_out: Optional[str] = None
    name_out: Optional[str] = None
    stack: List[str] = []
    
    def extract_sig_and_body(node: ast.AST) -> Tuple[str, str]:
        try:
            segment = ast.get_source_segment(source, node) or ""
        except Exception:
            segment = ""
        
        lines = segment.splitlines()
        if not lines:
            return "", ""
        
        sig_lines: List[str] = []
        for ln in lines:
            sig_lines.append(ln)
            if ln.rstrip().endswith(":"):
                break
        
        body_lines = lines[len(sig_lines):]
        sig = "\n".join(sig_lines).strip()
        body = "\n".join(body_lines).strip()
        
        # Truncate body for UI friendliness (but keep reasonable amount)
        max_chars = 3000  # Increased from 1200 to allow fuller function bodies
        if len(body) > max_chars:
            body = body[:max_chars - 1] + "\n…"
        
        return sig, body
    
    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal stack
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()
        
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal sig_out, body_out, name_out
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if isinstance(start, int) and isinstance(end, int) and start <= line <= end:
                sig_out, body_out = extract_sig_and_body(node)
                if stack:
                    name_out = ".".join(stack) + "." + node.name
                else:
                    name_out = node.name
            self.generic_visit(node)
        
        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nonlocal sig_out, body_out, name_out
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if isinstance(start, int) and isinstance(end, int) and start <= line <= end:
                sig_out, body_out = extract_sig_and_body(node)
                if stack:
                    name_out = ".".join(stack) + "." + node.name
                else:
                    name_out = node.name
            self.generic_visit(node)
    
    Visitor().visit(tree)
    return {"name": name_out, "sig": sig_out, "body": body_out}


def _extract_function_context_heuristic(source: str, line: int) -> Dict[str, Any]:
    """Extract function context using heuristic method (fallback)."""
    lines = source.splitlines()
    
    if line <= 0 or line > len(lines):
        return {"name": None, "sig": None, "body": None}
    
    func_name = None
    func_sig = None
    func_body_lines = []
    
    # Look backwards for def/class
    for i in range(line - 1, max(-1, line - 100), -1):
        if i < len(lines):
            stripped = lines[i].strip()
            
            # Check indentation to see if we're still in the same scope
            if i < line - 1:
                if lines[i] and not lines[i][0].isspace() and not stripped.startswith('#'):
                    if not stripped.startswith('@'):
                        break
            
            if stripped.startswith(('def ', 'async def ')):
                func_sig = lines[i].rstrip()
                # Extract name
                if 'def ' in stripped:
                    name_part = stripped.split('def ', 1)[1]
                else:
                    name_part = stripped.split('async def ', 1)[1]
                func_name = name_part.split('(')[0].strip()
                
                # Get function body - collect until we hit the next function/class or end of indentation
                base_indent = len(lines[i]) - len(lines[i].lstrip()) if i < len(lines) else 0
                for j in range(i + 1, min(i + 100, len(lines))):  # Check up to 100 lines
                    if j < len(lines):
                        current_line = lines[j]
                        if current_line.strip():  # Non-empty line
                            current_indent = len(current_line) - len(current_line.lstrip())
                            # Stop if we hit a line with same or less indentation (except decorators/comments)
                            if current_indent <= base_indent and not current_line.strip().startswith(('@', '#')):
                                break
                            # Stop if we hit another function/class definition at the same level
                            stripped_line = current_line.strip()
                            if stripped_line.startswith(('def ', 'async def ', 'class ')) and current_indent <= base_indent + 4:
                                break
                        func_body_lines.append(lines[j].rstrip())
                break
            
            elif stripped.startswith('class '):
                func_sig = lines[i].rstrip()
                func_name = stripped.split('class ', 1)[1].split('(')[0].split(':')[0].strip()
                
                # Check for methods
                for j in range(i + 1, min(line, i + 50)):
                    if j < len(lines):
                        method_stripped = lines[j].strip()
                        if method_stripped.startswith(('def ', 'async def ')):
                            if j <= line - 1:
                                func_sig = lines[j].rstrip()
                                if 'def ' in method_stripped:
                                    method_name = method_stripped.split('def ', 1)[1].split('(')[0].strip()
                                else:
                                    method_name = method_stripped.split('async def ', 1)[1].split('(')[0].strip()
                                func_name = f"{func_name}.{method_name}"
                                func_body_lines = []
                                method_indent = len(lines[j]) - len(lines[j].lstrip()) if j < len(lines) else 0
                                for k in range(j + 1, min(j + 100, len(lines))):  # Check up to 100 lines
                                    if k < len(lines):
                                        current_line = lines[k]
                                        if current_line.strip():  # Non-empty line
                                            current_indent = len(current_line) - len(current_line.lstrip())
                                            # Stop if we hit a line with same or less indentation
                                            if current_indent <= method_indent and not current_line.strip().startswith(('@', '#')):
                                                break
                                            # Stop if we hit another function/class definition
                                            if current_line.strip().startswith(('def ', 'async def ', 'class ')):
                                                break
                                        func_body_lines.append(lines[k].rstrip())
                break
    
    # Join all body lines and apply the same truncation as AST method
    func_body = '\n'.join(func_body_lines) if func_body_lines else None
    if func_body and len(func_body) > 3000:
        func_body = func_body[:2999] + '\n…'
    return {"name": func_name, "sig": func_sig, "body": func_body}


def summarize_value(value: Any, max_len: int = 120) -> str:
    """
    Summarize a value for speech output.
    
    Handles lists, dicts, and other types with smart truncation.
    
    Args:
        value: The value to summarize
        max_len: Maximum length of the summary
    
    Returns:
        Human-readable summary string
    """
    if value is None:
        return "None"
    
    if isinstance(value, (list, tuple)):
        type_name = "list" if isinstance(value, list) else "tuple"
        if not value:
            return f"empty {type_name}"
        return f"{type_name} of {len(value)} items"
    
    if isinstance(value, dict):
        if not value:
            return "empty dict"
        # Check if it's a DAP structure
        if "value" in value and isinstance(value.get("value"), str):
            # DAP format, extract the actual value
            actual_value = value.get("value", "")
            if len(actual_value) > max_len:
                return actual_value[:max_len - 3] + "..."
            return actual_value
        return f"dict with {len(value)} keys"
    
    if isinstance(value, set):
        if not value:
            return "empty set"
        return f"set with {len(value)} items"
    
    # For strings and other simple types
    s = str(value)
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


def summarize_delta(delta: Dict[str, Any], max_len: int = 120) -> str:
    """
    Summarize variable changes for TTS announcement.
    
    Handles both raw DAP format and clean parsed values.
    
    Args:
        delta: Dictionary of variable changes
        max_len: Maximum length of the summary
    
    Returns:
        Human-readable summary of changes
    """
    parts: List[str] = []
    
    for key, value in delta.items():
        # Skip internal keys
        if key.startswith('_'):
            continue
        
        # Handle scope dictionaries (Locals, Globals, etc.)
        if isinstance(value, dict):
            # Check if this is a scope with multiple variables
            if all(not k.startswith('_') for k in value.keys()):
                # This is a scope, summarize its contents
                for var_name, var_value in value.items():
                    summary = summarize_value(var_value, 40)
                    parts.append(f"{var_name} = {summary}")
            else:
                # Single variable or DAP structure
                summary = summarize_value(value, 40)
                parts.append(f"{key} = {summary}")
        elif value is None:
            parts.append(f"{key} removed")
        else:
            summary = summarize_value(value, 40)
            parts.append(f"{key} = {summary}")
    
    if not parts:
        return "no changes"
    
    text = "; ".join(parts)
    if len(text) > max_len:
        text = text[:max_len - 3] + "..."
    return text


def parse_dap_variables(variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse DAP format variables into clean Python values.
    
    Args:
        variables: DAP format variables dictionary
    
    Returns:
        Cleaned dictionary with actual Python values
    """
    result = {}
    
    for scope_name, scope_data in variables.items():
        if scope_name.startswith('_'):
            continue
        
        if isinstance(scope_data, dict):
            cleaned_scope = {}
            for var_name, var_data in scope_data.items():
                if var_name.startswith('_'):
                    continue
                
                # Extract actual value from DAP structure
                if isinstance(var_data, dict) and "value" in var_data:
                    value_str = var_data["value"]
                    # Try to parse the string representation back to Python object
                    try:
                        # Handle common cases
                        if value_str == "None":
                            cleaned_scope[var_name] = None
                        elif value_str == "True":
                            cleaned_scope[var_name] = True
                        elif value_str == "False":
                            cleaned_scope[var_name] = False
                        elif value_str.startswith("[") and value_str.endswith("]"):
                            # Try to parse as list
                            try:
                                import ast
                                cleaned_scope[var_name] = ast.literal_eval(value_str)
                            except:
                                cleaned_scope[var_name] = value_str
                        elif value_str.startswith("{") and value_str.endswith("}"):
                            # Try to parse as dict
                            try:
                                import ast
                                cleaned_scope[var_name] = ast.literal_eval(value_str)
                            except:
                                cleaned_scope[var_name] = value_str
                        else:
                            # Try numeric conversion
                            try:
                                if "." in value_str:
                                    cleaned_scope[var_name] = float(value_str)
                                else:
                                    cleaned_scope[var_name] = int(value_str)
                            except:
                                cleaned_scope[var_name] = value_str
                    except:
                        cleaned_scope[var_name] = value_str
                else:
                    cleaned_scope[var_name] = var_data
            
            result[scope_name] = cleaned_scope
        else:
            result[scope_name] = scope_data
    
    return result