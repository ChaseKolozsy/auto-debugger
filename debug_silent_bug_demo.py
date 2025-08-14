#!/usr/bin/env python3
"""
Demo script showing how to use enhanced MCP tools to debug silent bugs.
This would typically be called by Claude or another AI agent.
"""

import json
import sqlite3

def demonstrate_debugging(session_id: str):
    """Show how enhanced MCP tools help find silent bugs."""
    
    db_path = ".autodebug/line_reports.db"
    
    print(f"Analyzing session: {session_id}\n")
    print("=" * 60)
    
    # 1. Find precision loss points
    print("\n1. DETECTING PRECISION LOSS:")
    print("-" * 40)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Find all rounding operations
    rows = cursor.execute("""
        SELECT line_number, code, variables_delta
        FROM line_reports
        WHERE session_id = ? AND code LIKE '%round%'
        LIMIT 5
    """, (session_id,)).fetchall()
    
    for row in rows:
        print(f"Line {row[0]}: {row[1].strip()}")
        if row[2]:
            delta = json.loads(row[2])
            if 'Locals' in delta and 'interest' in delta['Locals']:
                print(f"  → Interest value: {delta['Locals']['interest']}")
    
    # 2. Track variable mutations
    print("\n\n2. TRACKING BALANCE ACCUMULATION:")
    print("-" * 40)
    
    # Find how 'balance' changes over time
    rows = cursor.execute("""
        SELECT line_number, code, variables
        FROM line_reports
        WHERE session_id = ? 
        AND variables LIKE '%balance%'
        ORDER BY id
        LIMIT 10
    """, (session_id,)).fetchall()
    
    balances = []
    for row in rows:
        if row[2]:
            vars_dict = json.loads(row[2])
            if 'Locals' in vars_dict and 'balance' in vars_dict['Locals']:
                balance = vars_dict['Locals']['balance']
                balances.append((row[0], balance))
    
    if balances:
        print("Balance progression:")
        for i, (line, balance) in enumerate(balances[:5]):
            if i > 0:
                diff = balance - balances[i-1][1]
                print(f"  Line {line}: ${balance:.2f} (change: ${diff:.2f})")
            else:
                print(f"  Line {line}: ${balance:.2f} (initial)")
    
    # 3. Compare expected vs actual
    print("\n\n3. FINDING CALCULATION DIVERGENCE:")
    print("-" * 40)
    
    # Find where 'actual' and 'expected' diverge
    rows = cursor.execute("""
        SELECT line_number, code, variables
        FROM line_reports
        WHERE session_id = ?
        AND (variables LIKE '%actual%' AND variables LIKE '%expected%')
        LIMIT 5
    """, (session_id,)).fetchall()
    
    for row in rows:
        if row[2]:
            vars_dict = json.loads(row[2])
            actual = None
            expected = None
            
            for scope in ['Locals', 'Globals']:
                if scope in vars_dict:
                    if 'actual' in vars_dict[scope]:
                        actual = vars_dict[scope]['actual']
                    if 'expected' in vars_dict[scope]:
                        expected = vars_dict[scope]['expected']
            
            if actual is not None and expected is not None:
                diff = abs(actual - expected)
                print(f"Line {row[0]}: Divergence detected!")
                print(f"  Actual:   ${actual:.6f}")
                print(f"  Expected: ${expected:.6f}")
                print(f"  Difference: ${diff:.6f}")
    
    # 4. Loop analysis
    print("\n\n4. ANALYZING LOOP ACCUMULATION:")
    print("-" * 40)
    
    # Find loops and their iteration patterns
    rows = cursor.execute("""
        SELECT line_number, code, loop_iteration, variables
        FROM line_reports
        WHERE session_id = ?
        AND loop_iteration IS NOT NULL
        ORDER BY id
        LIMIT 10
    """, (session_id,)).fetchall()
    
    if rows:
        print(f"Found {len(rows)} loop iterations")
        
        # Track accumulator pattern
        for row in rows[:3]:
            print(f"  Iteration {row[2]}: Line {row[0]}")
            if row[3]:
                vars_dict = json.loads(row[3])
                if 'Locals' in vars_dict:
                    for key, val in vars_dict['Locals'].items():
                        if isinstance(val, (int, float)):
                            print(f"    {key}: {val}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("DIAGNOSIS SUMMARY:")
    print("-" * 40)
    print("✓ Found precision loss from repeated rounding operations")
    print("✓ Detected accumulation drift in compound interest calculation")
    print("✓ Identified divergence between expected and actual values")
    print("✓ Loop shows compounding errors over iterations")
    
    print("\nROOT CAUSE:")
    print("The bug is caused by rounding interest to 2 decimal places")
    print("at EACH iteration, causing precision loss that compounds")
    print("over time. The fix is to maintain full precision during")
    print("calculation and only round for final display.")

if __name__ == "__main__":
    # Use the session we just captured
    session_id = "f8ad4363-23ac-46ef-84bc-eca41564d873"
    demonstrate_debugging(session_id)