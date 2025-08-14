#!/usr/bin/env python3
"""
MCP server for auto-debugger using FastMCP.
Provides tools to query debugging sessions stored in SQLite (review/autopsy).
Enhanced with specialized tools for debugging silent bugs and analyzing subtle issues.
"""

import json
import sqlite3
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("autodebug-mcp", version="0.4.0")

def get_db_path(db_path: Optional[str] = None) -> Path:
    """Get the database path, creating directories if needed."""
    if db_path and db_path.strip():
        path = Path(db_path)
    else:
        path = Path.cwd() / ".autodebug" / "line_reports.db"
    
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def parse_json_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON fields in database rows."""
    if not row:
        return row
    
    if isinstance(row.get('variables'), str):
        try:
            row['variables'] = json.loads(row['variables'])
        except (json.JSONDecodeError, TypeError):
            pass
    
    if isinstance(row.get('variables_delta'), str):
        try:
            row['variables_delta'] = json.loads(row['variables_delta'])
        except (json.JSONDecodeError, TypeError):
            pass
    
    return row

@mcp.tool
def list_sessions(db: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all debugging sessions with basic metadata."""
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT session_id, file, language, start_time, end_time,
                   total_lines_executed, successful_lines, lines_with_errors, 
                   total_crashes, updated_at
            FROM session_summaries 
            ORDER BY updated_at DESC
        """).fetchall()
        
        return [dict(row) for row in rows]

@mcp.tool
def get_session(session_id: str, db: Optional[str] = None) -> Dict[str, Any]:
    """Get detailed information about a specific session."""
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        row = cursor.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        
        return dict(row) if row else {}

@mcp.tool
def list_line_reports(
    session_id: str,
    offset: int = 0,
    limit: int = 25,
    status: Optional[str] = None,
    file: Optional[str] = None,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    List line reports for a session with pagination and filters.
    
    Args:
        session_id: The debugging session ID
        offset: Number of records to skip (for pagination)
        limit: Maximum number of records to return
        status: Filter by status ('success', 'error', 'warning')
        file: Filter by source file path
        db: Optional database path
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Build query with filters
        filters = ["session_id = ?"]
        params = [session_id]
        
        if status:
            filters.append("status = ?")
            params.append(status)
        
        if file:
            filters.append("file = ?")
            params.append(file)
        
        where_clause = " AND ".join(filters)
        
        query = f"""
            SELECT id, file, line_number, code, timestamp, variables, 
                   variables_delta, stack_depth, thread_id, status, 
                   error_type, error_message, observations
            FROM line_reports 
            WHERE {where_clause}
            ORDER BY id 
            LIMIT ? OFFSET ?
        """
        
        params.extend([limit, offset])
        rows = cursor.execute(query, params).fetchall()
        
        return [parse_json_fields(dict(row)) for row in rows]

@mcp.tool
def get_line_report(line_id: int, db: Optional[str] = None) -> Dict[str, Any]:
    """Get detailed information about a specific line report."""
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        row = cursor.execute("""
            SELECT id, session_id, file, line_number, code, timestamp, 
                   variables, variables_delta, stack_depth, thread_id, 
                   status, error_type, error_message, observations, stack_trace
            FROM line_reports 
            WHERE id = ?
        """, (line_id,)).fetchone()
        
        return parse_json_fields(dict(row)) if row else {}

@mcp.tool
def get_crashes(session_id: str, db: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all error/crash line reports for a session."""
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, file, line_number, code, timestamp, 
                   error_type, error_message, stack_trace
            FROM line_reports 
            WHERE session_id = ? AND status = 'error'
            ORDER BY id
        """, (session_id,)).fetchall()
        
        return [dict(row) for row in rows]

@mcp.tool
def add_note(
    line_report_id: int,
    note: str,
    source: str = "llm",
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Add a note/observation to a specific line report.
    
    Args:
        line_report_id: The ID of the line report to annotate
        note: The note/observation to add
        source: Source identifier (e.g., 'llm', 'agent', 'human')
        db: Optional database path
    
    Returns:
        Success status and the formatted note that was added
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Get current observations
        current = cursor.execute(
            "SELECT observations FROM line_reports WHERE id = ?",
            (line_report_id,)
        ).fetchone()
        
        if not current:
            return {
                "success": False,
                "error": f"Line report {line_report_id} not found"
            }
        
        # Format the new note with timestamp and source
        timestamp = datetime.now().isoformat()
        formatted_note = f"[{timestamp}] [{source}] {note}\n"
        
        # Append to existing observations
        current_obs = current[0] or ""
        new_observations = current_obs + formatted_note
        
        # Update the database
        cursor.execute(
            "UPDATE line_reports SET observations = ? WHERE id = ?",
            (new_observations, line_report_id)
        )
        conn.commit()
        
        return {
            "success": True,
            "line_report_id": line_report_id,
            "changes": cursor.rowcount,
            "note": formatted_note.strip()
        }

@mcp.tool
def get_function_context(
    session_id: str,
    file: str,
    line: int,
    mode: str = "sig",
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get the function context for a specific line.
    
    Args:
        session_id: The debugging session ID
        file: The source file path
        line: The line number
        mode: 'sig' for signature only, 'full' for signature and body
        db: Optional database path
    
    Returns:
        Function signature and optionally the full function body
    """
    # Import here to avoid circular dependencies
    import sys
    from pathlib import Path
    autodebugger_path = Path(__file__).parent.parent / "autodebugger"
    if str(autodebugger_path) not in sys.path:
        sys.path.insert(0, str(autodebugger_path))
    
    from common import extract_function_context
    
    try:
        context = extract_function_context(file, line)
        result = {
            "session_id": session_id,
            "file": file,
            "line": line,
            "function_name": context.get("name"),
            "function_signature": context.get("sig"),
        }
        if mode == "full":
            result["function_body"] = context.get("body")
        return result
    except Exception as e:
        return {
            "session_id": session_id,
            "file": file,
            "line": line,
            "error": str(e)
        }

# ============= ENHANCED TOOLS FOR SILENT BUG DEBUGGING =============

@mcp.tool
def find_variable_mutations(
    session_id: str,
    variable_name: str,
    scope: str = "Locals",
    limit: int = 20,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Track how a specific variable changes throughout execution.
    Perfect for debugging unexpected value changes in silent bugs.
    
    Args:
        session_id: The debugging session ID
        variable_name: Name of the variable to track
        scope: 'Locals' or 'Globals'
        limit: Maximum number of changes to return
        db: Optional database path
    
    Returns:
        List of line reports where the variable changed, with before/after values
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables_delta, timestamp
            FROM line_reports
            WHERE session_id = ? AND variables_delta IS NOT NULL
            ORDER BY id
        """, (session_id,)).fetchall()
        
        changes = []
        previous_value = None
        
        for row in rows:
            delta = json.loads(row['variables_delta']) if isinstance(row['variables_delta'], str) else row['variables_delta']
            if not delta or scope not in delta:
                continue
            
            if variable_name in delta[scope]:
                current_value = delta[scope][variable_name]
                
                changes.append({
                    'line_number': row['line_number'],
                    'code': row['code'].strip(),
                    'previous_value': previous_value,
                    'new_value': current_value,
                    'timestamp': row['timestamp']
                })
                
                previous_value = current_value
                
                if len(changes) >= limit:
                    break
        
        return changes

@mcp.tool
def find_precision_loss(
    session_id: str,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Detect floating point precision loss and rounding operations.
    Identifies where calculations lose precision.
    
    Args:
        session_id: The debugging session ID
        db: Optional database path
    
    Returns:
        Lines where precision loss was detected
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables_delta
            FROM line_reports
            WHERE session_id = ? 
            AND (code LIKE '%round%' OR code LIKE '%float%')
            ORDER BY id
            LIMIT 50
        """, (session_id,)).fetchall()
        
        precision_issues = []
        
        for row in rows:
            delta = json.loads(row['variables_delta']) if isinstance(row['variables_delta'], str) else row['variables_delta']
            
            issue = {
                'line_number': row['line_number'],
                'code': row['code'].strip(),
                'issue_type': 'rounding' if 'round' in row['code'] else 'float_operation'
            }
            
            if delta:
                # Look for float values that might have precision issues
                for scope in ['Locals', 'Globals']:
                    if scope in delta:
                        float_vars = {k: v for k, v in delta[scope].items() 
                                     if isinstance(v, float)}
                        if float_vars:
                            issue['affected_variables'] = float_vars
            
            precision_issues.append(issue)
        
        return precision_issues

@mcp.tool
def find_calculation_divergence(
    session_id: str,
    pattern1: str,
    pattern2: str,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Find where two variables or calculations diverge.
    Perfect for debugging discrepancies between expected and actual values.
    
    Args:
        session_id: The debugging session ID
        pattern1: First variable name or pattern to track
        pattern2: Second variable name or pattern to track
        db: Optional database path
    
    Returns:
        Analysis of where and how the values diverged
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables
            FROM line_reports
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()
        
        divergence_points = []
        
        for row in rows:
            vars_dict = json.loads(row['variables']) if isinstance(row['variables'], str) else row['variables']
            if not vars_dict:
                continue
            
            val1 = None
            val2 = None
            
            # Search for both patterns in all scopes
            for scope in ['Locals', 'Globals']:
                if scope in vars_dict:
                    for var_name, value in vars_dict[scope].items():
                        if pattern1 in var_name:
                            val1 = value
                        if pattern2 in var_name:
                            val2 = value
            
            # Check for divergence if both found and are numeric
            if val1 is not None and val2 is not None:
                if isinstance(val1, (int, float)) and isinstance(val2, (int, float)):
                    diff = abs(val1 - val2)
                    if diff > 0.0000001:
                        divergence_points.append({
                            'line_number': row['line_number'],
                            'code': row['code'].strip(),
                            f'{pattern1}_value': val1,
                            f'{pattern2}_value': val2,
                            'difference': diff,
                            'relative_error': diff / abs(val1) if val1 != 0 else float('inf')
                        })
        
        if divergence_points:
            return {
                'first_divergence': divergence_points[0] if divergence_points else None,
                'max_divergence': max(divergence_points, key=lambda x: x['difference']) if divergence_points else None,
                'total_divergence_points': len(divergence_points),
                'sample_points': divergence_points[:10]
            }
        
        return {'message': f'No divergence found between {pattern1} and {pattern2}'}

@mcp.tool
def analyze_loop_behavior(
    session_id: str,
    loop_line: int,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze how variables change across loop iterations.
    Detects accumulation patterns, drift, and unexpected growth.
    
    Args:
        session_id: The debugging session ID
        loop_line: Line number where the loop starts
        db: Optional database path
    
    Returns:
        Analysis of variable changes across iterations
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get executions around the loop line
        rows = cursor.execute("""
            SELECT id, line_number, code, variables, loop_iteration
            FROM line_reports
            WHERE session_id = ? 
            AND line_number BETWEEN ? AND ?
            ORDER BY id
            LIMIT 200
        """, (session_id, loop_line - 5, loop_line + 20)).fetchall()
        
        if not rows:
            return {'error': f'No code found around line {loop_line}'}
        
        # Track variables across iterations
        iterations = {}
        current_iteration = 0
        
        for row in rows:
            # Use loop_iteration if available, otherwise track manually
            iteration = row['loop_iteration'] if row['loop_iteration'] is not None else current_iteration
            
            if row['line_number'] == loop_line:
                current_iteration += 1
            
            vars_dict = json.loads(row['variables']) if isinstance(row['variables'], str) else row['variables']
            
            if vars_dict and 'Locals' in vars_dict:
                if iteration not in iterations:
                    iterations[iteration] = {}
                
                for var_name, value in vars_dict['Locals'].items():
                    if isinstance(value, (int, float)):
                        if var_name not in iterations[iteration]:
                            iterations[iteration][var_name] = value
        
        # Analyze patterns
        analysis = {}
        
        for var_name in set(var for iter_vars in iterations.values() for var in iter_vars):
            values = []
            for i in sorted(iterations.keys()):
                if var_name in iterations[i]:
                    values.append(iterations[i][var_name])
            
            if len(values) > 1:
                # Calculate growth pattern
                diffs = [values[i+1] - values[i] for i in range(len(values)-1) if i+1 < len(values)]
                
                if diffs:
                    avg_diff = sum(diffs) / len(diffs)
                    
                    # Determine pattern type
                    if all(abs(d - avg_diff) < 0.0001 for d in diffs):
                        pattern = 'linear'
                    elif all(d > 0 for d in diffs):
                        pattern = 'increasing'
                    elif all(d < 0 for d in diffs):
                        pattern = 'decreasing'
                    else:
                        pattern = 'irregular'
                    
                    analysis[var_name] = {
                        'initial': values[0],
                        'final': values[-1],
                        'iterations_tracked': len(values),
                        'total_change': values[-1] - values[0],
                        'average_change': avg_diff,
                        'pattern': pattern,
                        'first_5_values': values[:5]
                    }
        
        return {
            'loop_line': loop_line,
            'iterations_found': len(iterations),
            'variable_analysis': analysis
        }

@mcp.tool
def search_code_execution(
    session_id: str,
    pattern: str,
    context_lines: int = 2,
    limit: int = 20,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for code patterns and get their execution context.
    Useful for finding specific operations and their runtime state.
    
    Args:
        session_id: The debugging session ID
        pattern: Regex pattern to search for in code
        context_lines: Number of lines before/after to include
        limit: Maximum results to return
        db: Optional database path
    
    Returns:
        Matching lines with execution context
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Compile regex pattern
        try:
            pattern_re = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return [{'error': f'Invalid regex pattern: {e}'}]
        
        # Get all lines for the session
        all_rows = cursor.execute("""
            SELECT id, line_number, code, variables_delta
            FROM line_reports
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()
        
        matches = []
        
        for i, row in enumerate(all_rows):
            if pattern_re.search(row['code']):
                # Get context
                start = max(0, i - context_lines)
                end = min(len(all_rows), i + context_lines + 1)
                
                context = []
                for j in range(start, end):
                    context.append({
                        'line': all_rows[j]['line_number'],
                        'code': all_rows[j]['code'].strip(),
                        'is_match': j == i
                    })
                
                delta = json.loads(row['variables_delta']) if row['variables_delta'] and isinstance(row['variables_delta'], str) else row['variables_delta']
                
                matches.append({
                    'line_number': row['line_number'],
                    'matched_code': row['code'].strip(),
                    'variables_changed': delta,
                    'context': context
                })
                
                if len(matches) >= limit:
                    break
        
        return matches

@mcp.tool
def analyze_call_frequency(
    session_id: str,
    function_pattern: Optional[str] = None,
    min_calls: int = 1,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Analyze function call frequency to identify hot paths and performance bottlenecks.
    
    Args:
        session_id: The debugging session ID
        function_pattern: Optional regex pattern to filter functions
        min_calls: Minimum number of calls to include in results
        db: Optional database path
    
    Returns:
        List of functions with their call counts and line numbers
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Find all function definitions and their call counts
        query = """
            SELECT code, line_number, COUNT(*) as call_count
            FROM line_reports
            WHERE session_id = ?
            AND (code LIKE '%def %' OR code LIKE '%class %')
            GROUP BY code, line_number
            HAVING COUNT(*) >= ?
            ORDER BY call_count DESC
        """
        
        rows = cursor.execute(query, (session_id, min_calls)).fetchall()
        
        results = []
        for row in rows:
            code = row['code'].strip()
            
            # Apply pattern filter if provided
            if function_pattern:
                if not re.search(function_pattern, code):
                    continue
            
            # Extract function/class name
            if 'def ' in code:
                match = re.search(r'def\s+(\w+)', code)
                name = match.group(1) if match else 'unknown'
                type_ = 'function'
            else:
                match = re.search(r'class\s+(\w+)', code)
                name = match.group(1) if match else 'unknown'
                type_ = 'class'
            
            results.append({
                'name': name,
                'type': type_,
                'line_number': row['line_number'],
                'call_count': row['call_count'],
                'code': code
            })
        
        return results

@mcp.tool
def find_string_mutations(
    session_id: str,
    variable_name: str,
    scope: str = "Locals",
    show_diff: bool = True,
    limit: int = 20,
    db: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Track string variable changes with character-level diffs.
    Shows exactly what changed in string values, not just the full before/after.
    
    Args:
        session_id: The debugging session ID
        variable_name: Name of the string variable to track
        scope: 'Locals' or 'Globals'
        show_diff: Whether to include character-level diff analysis
        limit: Maximum number of changes to return
        db: Optional database path
    
    Returns:
        List of string mutations with detailed diff information
    """
    import difflib
    
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables_delta, timestamp
            FROM line_reports
            WHERE session_id = ? AND variables_delta IS NOT NULL
            ORDER BY id
        """, (session_id,)).fetchall()
        
        changes = []
        previous_value = None
        
        for row in rows:
            delta = json.loads(row['variables_delta']) if isinstance(row['variables_delta'], str) else row['variables_delta']
            if not delta or scope not in delta:
                continue
            
            # Check for direct variable or instance variable (self.variable)
            current_value = None
            if variable_name in delta[scope]:
                current_value = delta[scope][variable_name]
            elif 'self' in delta[scope] and isinstance(delta[scope]['self'], dict):
                # Check for instance variables
                if variable_name in delta[scope]['self']:
                    current_value = delta[scope]['self'][variable_name]
                # Also check if user passed "self.variable"
                elif '.' in variable_name:
                    parts = variable_name.split('.', 1)
                    if parts[0] == 'self' and parts[1] in delta[scope]['self']:
                        current_value = delta[scope]['self'][parts[1]]
            
            if current_value is None:
                continue
                
            # Only track string values
            if not isinstance(current_value, str):
                previous_value = current_value
                continue
            
            change_info = {
                'line_number': row['line_number'],
                'code': row['code'].strip(),
                'previous_value': previous_value,
                'new_value': current_value,
                'timestamp': row['timestamp']
            }
            
            # Add diff analysis if requested and we have a previous string value
            if show_diff and isinstance(previous_value, str):
                # Character-level diff
                diff = difflib.unified_diff(
                    previous_value.splitlines(keepends=True),
                    current_value.splitlines(keepends=True),
                    lineterm=''
                )
                diff_text = ''.join(diff)
                
                # Analyze the type of change
                if previous_value.strip() == current_value:
                    operation = 'whitespace_change'
                elif previous_value.lower() == current_value.lower():
                    operation = 'case_change'
                elif current_value.startswith(previous_value):
                    operation = 'append'
                    change_info['appended'] = current_value[len(previous_value):]
                elif current_value.endswith(previous_value):
                    operation = 'prepend'
                    change_info['prepended'] = current_value[:-len(previous_value)]
                elif len(current_value) < len(previous_value):
                    operation = 'truncation'
                elif len(current_value) > len(previous_value):
                    operation = 'expansion'
                else:
                    operation = 'replacement'
                
                change_info['operation'] = operation
                change_info['length_change'] = len(current_value) - len(previous_value)
                
                # Find common sequences
                matcher = difflib.SequenceMatcher(None, previous_value, current_value)
                change_info['similarity_ratio'] = matcher.ratio()
                
                if diff_text:
                    change_info['diff'] = diff_text
            
            changes.append(change_info)
            previous_value = current_value
            
            if len(changes) >= limit:
                break
        
        return changes

@mcp.tool
def find_state_transitions(
    session_id: str,
    state_variable: str,
    scope: str = "Locals",
    valid_states: Optional[List[str]] = None,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Track state machine transitions and identify invalid state changes.
    Perfect for debugging FSMs, workflow engines, and protocol implementations.
    
    Args:
        session_id: The debugging session ID
        state_variable: Name of the state variable to track
        scope: 'Locals' or 'Globals'
        valid_states: Optional list of valid state values
        db: Optional database path
    
    Returns:
        State transition analysis including transition graph and anomalies
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables_delta, timestamp
            FROM line_reports
            WHERE session_id = ? AND variables_delta IS NOT NULL
            ORDER BY id
        """, (session_id,)).fetchall()
        
        transitions = []
        state_counts = {}
        transition_graph = {}
        previous_state = None
        
        for row in rows:
            delta = json.loads(row['variables_delta']) if isinstance(row['variables_delta'], str) else row['variables_delta']
            if not delta or scope not in delta:
                continue
            
            # Check for direct variable or instance variable (self.variable)
            current_state = None
            if state_variable in delta[scope]:
                current_state = delta[scope][state_variable]
            elif 'self' in delta[scope] and isinstance(delta[scope]['self'], dict):
                # Check for instance variables
                if state_variable in delta[scope]['self']:
                    current_state = delta[scope]['self'][state_variable]
                # Also check if user passed "self.variable"
                elif '.' in state_variable:
                    parts = state_variable.split('.', 1)
                    if parts[0] == 'self' and parts[1] in delta[scope]['self']:
                        current_state = delta[scope]['self'][parts[1]]
            
            if current_state is None:
                continue
            
            # Track state occurrence
            state_counts[current_state] = state_counts.get(current_state, 0) + 1
            
            # Track transition
            if previous_state is not None:
                transition_key = f"{previous_state} -> {current_state}"
                
                if transition_key not in transition_graph:
                    transition_graph[transition_key] = {
                        'count': 0,
                        'lines': []
                    }
                
                transition_graph[transition_key]['count'] += 1
                transition_graph[transition_key]['lines'].append(row['line_number'])
                
                transition_info = {
                    'line_number': row['line_number'],
                    'code': row['code'].strip(),
                    'from_state': previous_state,
                    'to_state': current_state,
                    'timestamp': row['timestamp']
                }
                
                # Check if transition is valid
                if valid_states:
                    if current_state not in valid_states:
                        transition_info['invalid'] = True
                        transition_info['reason'] = 'unknown_state'
                    elif previous_state not in valid_states:
                        transition_info['invalid'] = True
                        transition_info['reason'] = 'invalid_source_state'
                
                transitions.append(transition_info)
            
            previous_state = current_state
        
        # Identify cycles
        cycles = []
        for trans_key, trans_data in transition_graph.items():
            states = trans_key.split(' -> ')
            if len(states) == 2 and states[0] == states[1]:
                cycles.append({
                    'state': states[0],
                    'count': trans_data['count'],
                    'lines': trans_data['lines']
                })
        
        # Find dead ends (states with no outgoing transitions)
        all_from_states = set()
        all_to_states = set()
        for trans_key in transition_graph.keys():
            from_state, to_state = trans_key.split(' -> ')
            all_from_states.add(from_state)
            all_to_states.add(to_state)
        
        dead_ends = all_to_states - all_from_states
        
        return {
            'total_transitions': len(transitions),
            'unique_states': list(state_counts.keys()),
            'state_counts': state_counts,
            'transition_graph': transition_graph,
            'cycles': cycles,
            'dead_end_states': list(dead_ends),
            'transitions': transitions[:20],  # First 20 transitions
            'invalid_transitions': [t for t in transitions if t.get('invalid', False)][:10]
        }

@mcp.tool
def analyze_string_patterns(
    session_id: str,
    variable_pattern: str,
    regex_pattern: Optional[str] = None,
    check_format: Optional[str] = None,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze string variables for pattern compliance and format violations.
    Useful for validating emails, URLs, phone numbers, or custom formats.
    
    Args:
        session_id: The debugging session ID
        variable_pattern: Pattern to match variable names (e.g., "email", ".*_url")
        regex_pattern: Optional regex to validate string values against
        check_format: Optional format type ('email', 'url', 'ipv4', 'ipv6', 'uuid')
        db: Optional database path
    
    Returns:
        Analysis of string patterns including violations and encoding issues
    """
    db_path = get_db_path(db)
    
    # Predefined format patterns
    format_patterns = {
        'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
        'url': r'^https?://[^\s/$.?#].[^\s]*$',
        'ipv4': r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$',
        'ipv6': r'^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))$',
        'uuid': r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    }
    
    # Use provided pattern or get from format type
    if check_format and check_format in format_patterns:
        validation_pattern = format_patterns[check_format]
    elif regex_pattern:
        validation_pattern = regex_pattern
    else:
        validation_pattern = None
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, line_number, code, variables
            FROM line_reports
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()
        
        pattern_matches = []
        violations = []
        encoding_issues = []
        variable_re = re.compile(variable_pattern)
        validation_re = re.compile(validation_pattern) if validation_pattern else None
        
        for row in rows:
            vars_dict = json.loads(row['variables']) if isinstance(row['variables'], str) else row['variables']
            if not vars_dict:
                continue
            
            for scope in ['Locals', 'Globals']:
                if scope not in vars_dict:
                    continue
                
                for var_name, value in vars_dict[scope].items():
                    # Check if variable name matches pattern
                    if not variable_re.search(var_name):
                        continue
                    
                    # Only analyze string values
                    if not isinstance(value, str):
                        continue
                    
                    match_info = {
                        'line_number': row['line_number'],
                        'variable': var_name,
                        'value': value,
                        'scope': scope
                    }
                    
                    # Check for validation pattern
                    if validation_re:
                        if validation_re.match(value):
                            match_info['valid'] = True
                            pattern_matches.append(match_info)
                        else:
                            match_info['valid'] = False
                            match_info['format'] = check_format or 'custom'
                            violations.append(match_info)
                    
                    # Check for encoding issues
                    try:
                        # Check if string contains non-ASCII
                        value.encode('ascii')
                    except UnicodeEncodeError:
                        encoding_info = match_info.copy()
                        encoding_info['issue'] = 'non_ascii'
                        encoding_info['chars'] = [c for c in value if ord(c) > 127]
                        encoding_issues.append(encoding_info)
                    
                    # Check for control characters
                    control_chars = [c for c in value if ord(c) < 32 and c not in '\n\r\t']
                    if control_chars:
                        encoding_info = match_info.copy()
                        encoding_info['issue'] = 'control_characters'
                        encoding_info['chars'] = [f'\\x{ord(c):02x}' for c in control_chars]
                        encoding_issues.append(encoding_info)
        
        # Analyze common issues
        issue_summary = {}
        for violation in violations:
            issue_type = violation.get('format', 'unknown')
            if issue_type not in issue_summary:
                issue_summary[issue_type] = 0
            issue_summary[issue_type] += 1
        
        return {
            'total_matches': len(pattern_matches) + len(violations),
            'valid_count': len(pattern_matches),
            'violation_count': len(violations),
            'violations': violations[:20],  # First 20 violations
            'encoding_issues': encoding_issues[:10],
            'issue_summary': issue_summary,
            'sample_valid': pattern_matches[:5] if pattern_matches else [],
            'validation_pattern': validation_pattern if validation_pattern else 'none'
        }

@mcp.tool
def analyze_variable_dependencies(
    session_id: str,
    force_reanalyze: bool = False,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze and store variable dependencies for a debugging session.
    Builds a complete dependency graph showing how variables influence each other.
    
    Args:
        session_id: The debugging session ID to analyze
        force_reanalyze: Whether to rerun analysis even if it exists
        db: Optional database path
    
    Returns:
        Dependency analysis report including graph, cycles, and key variables
    """
    db_path = get_db_path(db)
    
    # First ensure the dependency tables exist
    import sys
    from pathlib import Path
    autodebugger_path = Path(__file__).parent.parent / "autodebugger"
    if str(autodebugger_path) not in sys.path:
        sys.path.insert(0, str(autodebugger_path))
    
    try:
        from schema_migrations import migrate_add_dependency_tables
        migrate_add_dependency_tables(str(db_path))
    except Exception as e:
        return {'error': f'Failed to create dependency tables: {e}'}
    
    # Check if analysis already exists
    if not force_reanalyze:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            existing = cursor.execute("""
                SELECT total_variables FROM dependency_summaries 
                WHERE session_id = ?
            """, (session_id,)).fetchone()
            
            if existing:
                return get_dependency_graph(session_id, db)
    
    # Run the analysis
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from dependency_analyzer import DependencyAnalyzer
        analyzer = DependencyAnalyzer(str(db_path))
        report = analyzer.analyze_session(session_id, save_to_db=True)
        
        return {
            'status': 'analyzed',
            'session_id': session_id,
            'report': report
        }
    except Exception as e:
        return {'error': f'Failed to analyze dependencies: {e}'}

@mcp.tool
def get_dependency_graph(
    session_id: str,
    variable_name: Optional[str] = None,
    depth: int = 2,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Query stored dependency graph data for a session.
    Can get full graph or dependencies for a specific variable.
    
    Args:
        session_id: The debugging session ID
        variable_name: Optional - get dependencies for specific variable
        depth: How many levels of dependencies to include (for specific variable)
        db: Optional database path
    
    Returns:
        Dependency graph data from the database
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get summary if it exists
        summary = cursor.execute("""
            SELECT * FROM dependency_summaries 
            WHERE session_id = ?
        """, (session_id,)).fetchone()
        
        if not summary:
            return {
                'error': 'No dependency analysis found for this session',
                'hint': 'Run analyze_variable_dependencies first'
            }
        
        result = {
            'session_id': session_id,
            'total_variables': summary['total_variables'],
            'root_variables': json.loads(summary['root_variables']),
            'leaf_variables': json.loads(summary['leaf_variables']),
            'circular_dependencies': json.loads(summary['circular_dependencies']),
            'most_depended_on': json.loads(summary['most_depended_on']),
            'most_dependent': json.loads(summary['most_dependent'])
        }
        
        if variable_name:
            # Get specific variable info
            var_meta = cursor.execute("""
                SELECT * FROM variable_metadata 
                WHERE session_id = ? AND variable_name = ?
            """, (session_id, variable_name)).fetchone()
            
            if var_meta:
                result['variable_info'] = dict(var_meta)
                
                # Get what this variable depends on
                depends_on = cursor.execute("""
                    SELECT source_variable, first_line 
                    FROM variable_dependencies
                    WHERE session_id = ? AND target_variable = ?
                """, (session_id, variable_name)).fetchall()
                
                result['depends_on'] = [
                    {'variable': row['source_variable'], 'first_line': row['first_line']}
                    for row in depends_on
                ]
                
                # Get what depends on this variable
                depended_by = cursor.execute("""
                    SELECT target_variable, first_line 
                    FROM variable_dependencies
                    WHERE session_id = ? AND source_variable = ?
                """, (session_id, variable_name)).fetchall()
                
                result['depended_by'] = [
                    {'variable': row['target_variable'], 'first_line': row['first_line']}
                    for row in depended_by
                ]
                
                # Get dependency chain if depth > 1
                if depth > 1:
                    result['dependency_chain'] = _get_dependency_chain(
                        cursor, session_id, variable_name, depth
                    )
        else:
            # Get full dependency graph (limited to avoid huge response)
            all_deps = cursor.execute("""
                SELECT target_variable, source_variable, first_line
                FROM variable_dependencies
                WHERE session_id = ?
                LIMIT 100
            """, (session_id,)).fetchall()
            
            graph = {}
            for row in all_deps:
                target = row['target_variable']
                if target not in graph:
                    graph[target] = []
                graph[target].append({
                    'source': row['source_variable'],
                    'line': row['first_line']
                })
            
            result['dependency_graph'] = graph
        
        return result

def _get_dependency_chain(cursor, session_id: str, variable: str, depth: int, visited=None):
    """Recursively get dependency chain for a variable."""
    if visited is None:
        visited = set()
    
    if variable in visited or depth <= 0:
        return None
    
    visited.add(variable)
    
    # Get direct dependencies
    deps = cursor.execute("""
        SELECT source_variable FROM variable_dependencies
        WHERE session_id = ? AND target_variable = ?
    """, (session_id, variable)).fetchall()
    
    chain = {
        'variable': variable,
        'depends_on': []
    }
    
    for dep in deps:
        source = dep['source_variable']
        sub_chain = _get_dependency_chain(
            cursor, session_id, source, depth - 1, visited.copy()
        )
        if sub_chain:
            chain['depends_on'].append(sub_chain)
        else:
            chain['depends_on'].append({'variable': source})
    
    return chain

@mcp.tool
def find_variable_influence(
    session_id: str,
    source_variable: str,
    target_variable: str,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Check if and how one variable influences another through dependency chains.
    Useful for understanding causality in bugs.
    
    Args:
        session_id: The debugging session ID
        source_variable: The variable that might influence
        target_variable: The variable that might be influenced
        db: Optional database path
    
    Returns:
        Influence path if exists, or indication of no influence
    """
    db_path = get_db_path(db)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row  # Fix: Set row_factory
        cursor = conn.cursor()
        
        # Check if dependency data exists
        summary = cursor.execute("""
            SELECT COUNT(*) FROM dependency_summaries 
            WHERE session_id = ?
        """, (session_id,)).fetchone()
        
        if not summary or summary[0] == 0:
            return {
                'error': 'No dependency analysis found',
                'hint': 'Run analyze_variable_dependencies first'
            }
        
        # BFS to find path from source to target
        visited = set()
        queue = [(source_variable, [source_variable])]
        paths = []
        
        while queue:
            current, path = queue.pop(0)
            
            if current == target_variable:
                paths.append(path)
                continue
            
            if current in visited:
                continue
            
            visited.add(current)
            
            # Find variables that depend on current
            dependents = cursor.execute("""
                SELECT DISTINCT target_variable 
                FROM variable_dependencies
                WHERE session_id = ? AND source_variable = ?
            """, (session_id, current)).fetchall()
            
            for dep in dependents:
                next_var = dep['target_variable']
                if next_var not in visited:
                    queue.append((next_var, path + [next_var]))
        
        if paths:
            # Get line numbers for the shortest path
            shortest_path = min(paths, key=len)
            path_with_lines = []
            
            for i in range(len(shortest_path) - 1):
                source = shortest_path[i]
                target = shortest_path[i + 1]
                
                line_info = cursor.execute("""
                    SELECT first_line FROM variable_dependencies
                    WHERE session_id = ? 
                    AND source_variable = ? 
                    AND target_variable = ?
                    LIMIT 1
                """, (session_id, source, target)).fetchone()
                
                path_with_lines.append({
                    'from': source,
                    'to': target,
                    'line': line_info['first_line'] if line_info else None
                })
            
            return {
                'has_influence': True,
                'shortest_path': shortest_path,
                'path_length': len(shortest_path) - 1,
                'path_details': path_with_lines,
                'all_paths': paths[:5]  # Limit to 5 paths
            }
        else:
            return {
                'has_influence': False,
                'source': source_variable,
                'target': target_variable,
                'reason': 'No dependency path found'
            }


if __name__ == "__main__":
    # Run the MCP server
    mcp.run()