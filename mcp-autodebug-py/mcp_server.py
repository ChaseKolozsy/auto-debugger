#!/usr/bin/env python3
"""
MCP server for auto-debugger using FastMCP.
Provides tools to query debugging sessions stored in SQLite and control active debugging sessions.
"""

import json
import sqlite3
import subprocess
import shlex
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass

from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("autodebug-mcp", version="0.3.0")

# Store active debugging sessions
@dataclass
class ActiveSession:
    """Represents an active debugging session."""
    session_id: str
    process: subprocess.Popen
    controller: Any  # HttpStepController instance
    current_line: int = 0
    current_file: str = ""
    current_code: str = ""
    variables: Dict[str, Any] = None
    variables_delta: Dict[str, Any] = None
    is_waiting: bool = False
    mode: str = "manual"
    
    def __post_init__(self):
        if self.variables is None:
            self.variables = {}
        if self.variables_delta is None:
            self.variables_delta = {}

# Global storage for active sessions
active_sessions: Dict[str, ActiveSession] = {}
sessions_lock = threading.Lock()

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
    limit: int = 200,
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

@mcp.tool
def create_debug_session(
    script_path: str,
    script_args: Optional[str] = None,
    breakpoints: Optional[List[str]] = None,
    python_exe: Optional[str] = None,
    db: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new debugging session for an LLM agent.
    
    Args:
        script_path: Path to the Python script to debug
        script_args: Arguments to pass to the script
        breakpoints: List of breakpoints in 'file:line' format
        python_exe: Python executable to use (defaults to current)
        db: Optional database path
    
    Returns:
        Session information including session_id and status
    """
    import uuid
    
    session_id = f"agent_{uuid.uuid4().hex[:12]}"
    
    # Build command
    cmd = [python_exe or "python", "-m", "autodebugger", "run", "--manual"]
    
    # Add database path if specified
    if db:
        cmd.extend(["--db", db])
    
    # Add breakpoints
    if breakpoints:
        for bp in breakpoints:
            cmd.extend(["-b", bp])
    
    # Add script path
    cmd.append(script_path)
    
    # Add script arguments
    if script_args:
        cmd.extend(shlex.split(script_args))
    
    try:
        # Launch the debugger process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Wait a moment for the process to start
        time.sleep(0.5)
        
        # Create session object
        session = ActiveSession(
            session_id=session_id,
            process=process,
            controller=None,  # Will be set when we connect
            mode="manual"
        )
        
        with sessions_lock:
            active_sessions[session_id] = session
        
        return {
            "success": True,
            "session_id": session_id,
            "command": " ".join(cmd),
            "pid": process.pid,
            "status": "created"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@mcp.tool  
def step_debug_session(
    session_id: str,
    action: str = "step"
) -> Dict[str, Any]:
    """
    Control stepping through a debugging session.
    
    Args:
        session_id: The active session ID
        action: Action to perform ('step', 'continue', 'quit')
    
    Returns:
        Current state after the action
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
    
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}
    
    # Send action to the debugger
    # This would interact with the controller
    # For now, return current state
    return {
        "success": True,
        "session_id": session_id,
        "action": action,
        "current_line": session.current_line,
        "current_file": session.current_file,
        "current_code": session.current_code,
        "is_waiting": session.is_waiting
    }

@mcp.tool
def get_variables(
    session_id: str,
    scope: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get current variables in the debugging session.
    
    Args:
        session_id: The active session ID
        scope: Optional scope filter ('locals', 'globals')
    
    Returns:
        Current variables and their values
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
    
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}
    
    variables = session.variables or {}
    
    if scope:
        variables = {k: v for k, v in variables.items() if k.lower() == scope.lower()}
    
    return {
        "success": True,
        "session_id": session_id,
        "variables": variables,
        "line": session.current_line,
        "file": session.current_file
    }

@mcp.tool
def get_variable_changes(
    session_id: str
) -> Dict[str, Any]:
    """
    Get variables that changed in the last step.
    
    Args:
        session_id: The active session ID
    
    Returns:
        Variables that changed and their new values
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
    
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}
    
    return {
        "success": True,
        "session_id": session_id,
        "changes": session.variables_delta or {},
        "line": session.current_line,
        "file": session.current_file
    }

@mcp.tool
def explore_variable(
    session_id: str,
    variable_name: str,
    path: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Explore a specific variable in detail (nested structures).
    
    Args:
        session_id: The active session ID
        variable_name: Name of the variable to explore
        path: Optional path into nested structure (e.g., ['key1', 'subkey'])
    
    Returns:
        Detailed information about the variable
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
    
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}
    
    # Get the variable
    variables = session.variables or {}
    
    # Find the variable in any scope
    var_value = None
    for scope_name, scope_vars in variables.items():
        if isinstance(scope_vars, dict) and variable_name in scope_vars:
            var_value = scope_vars[variable_name]
            break
    
    if var_value is None:
        return {
            "success": False,
            "error": f"Variable '{variable_name}' not found"
        }
    
    # Navigate path if provided
    if path:
        try:
            for key in path:
                if isinstance(var_value, dict):
                    var_value = var_value[key]
                elif isinstance(var_value, (list, tuple)):
                    var_value = var_value[int(key)]
                else:
                    return {
                        "success": False,
                        "error": f"Cannot navigate into {type(var_value).__name__}"
                    }
        except (KeyError, IndexError, ValueError) as e:
            return {
                "success": False,
                "error": f"Path navigation failed: {e}"
            }
    
    # Prepare response with type information
    result = {
        "success": True,
        "session_id": session_id,
        "variable_name": variable_name,
        "path": path or [],
        "type": type(var_value).__name__,
        "value": var_value
    }
    
    # Add collection-specific info
    if isinstance(var_value, dict):
        result["keys"] = list(var_value.keys())
        result["size"] = len(var_value)
    elif isinstance(var_value, (list, tuple)):
        result["length"] = len(var_value)
        result["preview"] = var_value[:10] if len(var_value) > 10 else var_value
    elif isinstance(var_value, str):
        result["length"] = len(var_value)
        if len(var_value) > 100:
            result["preview"] = var_value[:100] + "..."
    
    return result

@mcp.tool
def list_active_sessions() -> List[Dict[str, Any]]:
    """
    List all currently active debugging sessions.
    
    Returns:
        List of active session information
    """
    with sessions_lock:
        sessions = []
        for sid, session in active_sessions.items():
            sessions.append({
                "session_id": sid,
                "pid": session.process.pid if session.process else None,
                "current_file": session.current_file,
                "current_line": session.current_line,
                "is_waiting": session.is_waiting,
                "mode": session.mode
            })
    return sessions

@mcp.tool
def terminate_debug_session(
    session_id: str
) -> Dict[str, Any]:
    """
    Terminate an active debugging session.
    
    Args:
        session_id: The session ID to terminate
    
    Returns:
        Success status
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
        
        if not session:
            return {"success": False, "error": f"Session {session_id} not found"}
        
        try:
            # Terminate the process
            if session.process:
                session.process.terminate()
                session.process.wait(timeout=5)
            
            # Remove from active sessions
            del active_sessions[session_id]
            
            return {
                "success": True,
                "session_id": session_id,
                "status": "terminated"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

@mcp.tool
def get_current_state(
    session_id: str
) -> Dict[str, Any]:
    """
    Get the complete current state of a debugging session.
    
    Args:
        session_id: The active session ID
    
    Returns:
        Complete state including line, code, variables, and changes
    """
    with sessions_lock:
        session = active_sessions.get(session_id)
    
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}
    
    # Import here to get summarization functions
    import sys
    from pathlib import Path
    autodebugger_path = Path(__file__).parent.parent / "autodebugger"
    if str(autodebugger_path) not in sys.path:
        sys.path.insert(0, str(autodebugger_path))
    
    from common import summarize_value, summarize_delta
    
    # Summarize variables
    var_summary = {}
    for scope, vars_dict in (session.variables or {}).items():
        if isinstance(vars_dict, dict):
            var_summary[scope] = {
                name: summarize_value(value, 60)
                for name, value in vars_dict.items()
            }
    
    # Summarize changes
    changes_summary = summarize_delta(session.variables_delta or {})
    
    return {
        "success": True,
        "session_id": session_id,
        "current_file": session.current_file,
        "current_line": session.current_line,
        "current_code": session.current_code,
        "is_waiting": session.is_waiting,
        "variables_summary": var_summary,
        "changes_summary": changes_summary,
        "full_variables": session.variables,
        "full_changes": session.variables_delta
    }

if __name__ == "__main__":
    # Run the MCP server
    mcp.run()