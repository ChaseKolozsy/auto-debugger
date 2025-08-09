# AutoDebug MCP Server (Python)

A Model Context Protocol (MCP) server for querying and annotating auto-debugger sessions stored in SQLite.

## Features

- Query debugging sessions and line reports
- Filter by status, file, with pagination
- Add collaborative notes/observations to line reports
- Retrieve crash information and error traces
- Built with FastMCP for clean, efficient implementation

## Installation

```bash
# Install with uv
cd mcp-autodebug-py
uv pip install fastmcp
```

## Usage

### As MCP Server (stdio)
```bash
python mcp_server.py
```

### Available Tools

1. **list_sessions** - List all debugging sessions with metadata
2. **get_session** - Get detailed information about a specific session
3. **list_line_reports** - List line reports with pagination and filters
4. **get_line_report** - Get detailed information about a specific line
5. **get_crashes** - List all error/crash reports for a session
6. **add_note** - Add timestamped notes/observations to line reports (for LLM collaboration)
7. **get_function_context** - Get function context for a specific line (placeholder)

### Example: Adding a Note

```python
# Add an LLM observation to line report #42
add_note(
    line_report_id=42,
    note="Variable 'user_id' is None, causing downstream NullPointerException",
    source="llm"
)
```

## Database Schema

The server expects an SQLite database with the following tables:
- `session_summaries` - Overview of debugging sessions
- `line_reports` - Detailed line-by-line execution records
- `file_snapshots` - Source file snapshots for dirty/uncommitted code

## Benefits Over Node.js Version

- No build compilation issues
- Managed with uv for consistent Python environments
- Native Python integration with auto-debugger
- Simpler dependency management