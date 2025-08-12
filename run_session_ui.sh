#!/bin/bash

# Run the unified UI for auto-debugger session management
# This provides a web interface to:
# - View all debugging sessions
# - Delete individual or multiple sessions  
# - Start new debugging sessions
# - Explore session details and variables

cd "$(dirname "$0")"

echo "Starting Auto-Debugger Session Management UI..."
echo "Opening browser at http://127.0.0.1:55001"
echo ""
echo "Features available:"
echo "  - View and search all debugging sessions"
echo "  - Delete individual sessions or bulk delete"
echo "  - Explore line-by-line execution details"
echo "  - View variable changes and scope"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the unified UI with enhanced features
python -m autodebugger ui --unified --host 127.0.0.1 --port 55001 --open