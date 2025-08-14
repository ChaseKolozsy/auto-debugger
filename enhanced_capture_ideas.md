# Enhanced Capture for Variable Dependencies

## Current Capture
The debugger currently captures:
- All variables and their values at each line
- Variable deltas (what changed)
- Code being executed

## Enhancement Options

### Option 1: AST Analysis During Stepping
```python
# In the debugger's line handler:
def on_line_executed(frame, line_code):
    # Current capture
    variables = capture_variables(frame)
    
    # NEW: Analyze the line for dependencies
    dependencies = extract_dependencies(line_code, variables)
    
    # Store in new column: dependencies JSON
    store_line_report(
        variables=variables,
        dependencies=dependencies  # NEW
    )
```

### Option 2: Bytecode Analysis
```python
import dis

def analyze_dependencies(frame):
    """Analyze Python bytecode to find dependencies."""
    code = frame.f_code
    bytecode = dis.Bytecode(code)
    
    # Track LOAD_NAME/LOAD_FAST (read) and STORE_NAME/STORE_FAST (write)
    reads = []
    writes = []
    
    for instr in bytecode:
        if instr.opname in ['LOAD_NAME', 'LOAD_FAST', 'LOAD_GLOBAL']:
            reads.append(instr.argval)
        elif instr.opname in ['STORE_NAME', 'STORE_FAST']:
            writes.append(instr.argval)
    
    return {
        'reads': reads,
        'writes': writes
    }
```

### Option 3: Frame Inspection
```python
def track_variable_access(frame, event, arg):
    """Track which variables are accessed."""
    if event == 'line':
        # Get the source line
        line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
        
        # Track what variables are read
        local_vars = frame.f_locals
        global_vars = frame.f_globals
        
        # Use AST to find variable references
        try:
            tree = ast.parse(line)
            reader = VariableReader()
            reader.visit(tree)
            
            dependencies = {
                'line': frame.f_lineno,
                'reads': reader.reads,
                'writes': reader.writes,
                'available': list(local_vars.keys())
            }
            
            # Store in database
            store_dependencies(dependencies)
        except:
            pass
```

## Database Schema Changes

Add new columns to line_reports:
```sql
ALTER TABLE line_reports ADD COLUMN dependencies TEXT;  -- JSON
ALTER TABLE line_reports ADD COLUMN reads_vars TEXT;    -- JSON array
ALTER TABLE line_reports ADD COLUMN writes_vars TEXT;   -- JSON array
```

## Implementation Complexity

1. **Post-processing** (EASIEST) ✅
   - Works with existing data
   - No debugger changes needed
   - Can be run anytime
   - May miss some dynamic dependencies

2. **AST during capture** (MODERATE)
   - Requires modifying debugger
   - Good accuracy
   - Small performance impact
   - Works for most cases

3. **Bytecode analysis** (HARD)
   - Most accurate
   - Higher performance impact
   - Complex implementation
   - Handles all Python constructs

## Recommendation

**Start with post-processing** (already implemented above) because:
1. No debugger modifications needed
2. Can analyze existing captured data
3. Good enough for 90% of use cases
4. Can be enhanced later if needed

The post-processing script can be run as:
```bash
python dependency_analyzer.py <session_id>
```

And integrated into MCP as a tool:
```python
@mcp.tool
def analyze_dependencies(session_id: str):
    """Analyze variable dependencies for a session."""
    analyzer = DependencyAnalyzer(db_path)
    return analyzer.analyze_session(session_id)
```