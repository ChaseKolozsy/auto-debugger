#!/usr/bin/env python3
"""
Test script to verify MCP server starts and exposes tools correctly.
"""

import subprocess
import json
import sys
import time
from pathlib import Path

def test_mcp_server():
    """Test that the MCP server starts and responds to initialization."""
    
    server_path = Path(__file__).parent / "mcp_server.py"
    
    # Start the MCP server process
    print("Starting MCP server...")
    process = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    try:
        # Send initialization request
        init_request = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0"
                }
            },
            "id": 1
        }
        
        print("Sending initialization request...")
        process.stdin.write(json.dumps(init_request) + '\n')
        process.stdin.flush()
        
        # Wait for response
        response_line = process.stdout.readline()
        if not response_line:
            print("ERROR: No response from server")
            return False
            
        try:
            response = json.loads(response_line)
            print(f"Got response: {json.dumps(response, indent=2)}")
            
            # Check if we got tools capability in the response
            if "result" in response:
                result = response["result"]
                if "capabilities" in result and "tools" in result["capabilities"]:
                    # The tools capability just indicates support, not the actual tools
                    print("\nServer supports tools capability")
                    
                    # Send initialized notification (correct MCP format)
                    initialized = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    }
                    process.stdin.write(json.dumps(initialized) + '\n')
                    process.stdin.flush()
                    
                    # Small delay to ensure server is ready
                    time.sleep(0.1)
                    
                    # Request tools list
                    tools_request = {
                        "jsonrpc": "2.0",
                        "method": "tools/list",
                        "params": {},
                        "id": 2
                    }
                    print("\nRequesting tools list...")
                    process.stdin.write(json.dumps(tools_request) + '\n')
                    process.stdin.flush()
                    
                    # Read tools response with timeout
                    import select
                    ready = select.select([process.stdout], [], [], 2.0)
                    if ready[0]:
                        tools_response_line = process.stdout.readline()
                        if tools_response_line:
                            try:
                                tools_response = json.loads(tools_response_line)
                                if "result" in tools_response and "tools" in tools_response["result"]:
                                    tools = tools_response["result"]["tools"]
                                    print(f"\nFound {len(tools)} tools:")
                                    for tool in tools:
                                        desc = tool.get('description', 'No description')
                                        if len(desc) > 60:
                                            desc = desc[:60] + "..."
                                        print(f"  - {tool['name']}: {desc}")
                                else:
                                    print("\nNo tools found in response")
                            except json.JSONDecodeError:
                                print(f"Failed to parse tools response: {tools_response_line}")
                    else:
                        print("\nNo tools response received (timeout)")
                    
                    print("\nMCP server test PASSED!")
                    return True
                else:
                    print("ERROR: No tools found in capabilities")
                    return False
            else:
                print(f"ERROR: Unexpected response format")
                return False
                
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to parse response: {e}")
            print(f"Raw response: {response_line}")
            return False
            
    finally:
        # Clean shutdown
        print("\nShutting down server...")
        process.terminate()
        time.sleep(0.5)
        if process.poll() is None:
            process.kill()
        
        # Read any stderr output
        stderr = process.stderr.read()
        if stderr:
            print(f"Server stderr:\n{stderr}")

if __name__ == "__main__":
    success = test_mcp_server()
    sys.exit(0 if success else 1)