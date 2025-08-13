#!/usr/bin/env python3
"""
MCP server for auto-debugger using FastMCP.
Provides tools to query debugging sessions stored in SQLite (review/autopsy).
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("autodebug-mcp", version="0.3.0")

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



if __name__ == "__main__":
    # Run the MCP server
    mcp.run()