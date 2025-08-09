# Test Instructions for 'f' Key Fix

## Setup
The debugger is currently running at http://127.0.0.1:52525

## Test Procedure

1. **Open the debugger interface** in your browser at the URL above

2. **Step a few times** (press Enter) until you're inside a function
   - You should see a function name appear in the "Function:" field

3. **Press 'f' key** to toggle the function context display
   - The function context panel should appear/disappear
   - The interface should NOT hang or freeze
   - You should be able to immediately continue stepping

4. **Continue stepping** with Enter key after pressing 'f'
   - This should work without any delays or hanging

## What Was Fixed

The hanging issue was caused by:
- Function extraction happening synchronously while holding a lock
- This blocked the UI thread when pressing 'f'

The fix:
- Function extraction now happens asynchronously in a background thread
- Results are cached to avoid re-parsing
- The lock is not held during AST parsing
- Quick heuristics skip unnecessary parsing

## Expected Behavior

- Pressing 'f' toggles the function display instantly
- Function context may appear with slight delay (async extraction)
- No hanging or freezing at any point
- All other controls remain responsive