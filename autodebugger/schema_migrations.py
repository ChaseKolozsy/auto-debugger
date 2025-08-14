#!/usr/bin/env python3
"""
Schema migrations for autodebugger database.
Adds tables for storing dependency analysis results.
"""

import sqlite3
from pathlib import Path
from typing import Optional

def get_db_path(db_path: Optional[str] = None) -> Path:
    """Get the database path."""
    if db_path and db_path.strip():
        return Path(db_path)
    return Path.cwd() / ".autodebug" / "line_reports.db"

def migrate_add_dependency_tables(db_path: Optional[str] = None):
    """Add tables for storing dependency graph data."""
    
    db = get_db_path(db_path)
    
    with sqlite3.connect(db) as conn:
        cursor = conn.cursor()
        
        # Table 1: Dependency analysis summary for each session
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS dependency_summaries (
            session_id TEXT PRIMARY KEY,
            total_variables INTEGER,
            root_variables TEXT,  -- JSON array
            leaf_variables TEXT,  -- JSON array
            circular_dependencies TEXT,  -- JSON array of cycles
            most_depended_on TEXT,  -- JSON array of [var, count] pairs
            most_dependent TEXT,  -- JSON array of [var, count] pairs
            analysis_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES session_summaries(session_id)
        )
        """)
        
        # Table 2: Variable dependencies (edges in the graph)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS variable_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            target_variable TEXT NOT NULL,  -- Variable being assigned
            source_variable TEXT NOT NULL,  -- Variable it depends on
            first_line INTEGER,  -- First line where this dependency appears
            occurrence_count INTEGER DEFAULT 1,  -- How many times this dependency occurs
            FOREIGN KEY (session_id) REFERENCES session_summaries(session_id)
        )
        """)
        
        # Table 3: Variable metadata
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS variable_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            variable_name TEXT NOT NULL,
            first_appearance_line INTEGER,
            last_modified_line INTEGER,
            total_mutations INTEGER,
            dependency_depth INTEGER,  -- How deep in the dependency tree
            depends_on_count INTEGER,  -- Number of variables this depends on
            depended_by_count INTEGER,  -- Number of variables that depend on this
            is_root BOOLEAN DEFAULT 0,  -- No dependencies
            is_leaf BOOLEAN DEFAULT 0,  -- Nothing depends on it
            UNIQUE(session_id, variable_name),
            FOREIGN KEY (session_id) REFERENCES session_summaries(session_id)
        )
        """)
        
        # Create indexes for efficient querying
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_var_deps_session 
        ON variable_dependencies(session_id)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_var_deps_target 
        ON variable_dependencies(session_id, target_variable)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_var_deps_source 
        ON variable_dependencies(session_id, source_variable)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_var_meta_session 
        ON variable_metadata(session_id)
        """)
        
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_var_meta_name 
        ON variable_metadata(session_id, variable_name)
        """)
        
        conn.commit()
        print(f"✓ Migration complete: Added dependency tables to {db}")
        
        # Check the migration
        cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name LIKE '%depend%'
        """)
        tables = cursor.fetchall()
        print(f"  Created tables: {[t[0] for t in tables]}")

def check_migration_status(db_path: Optional[str] = None):
    """Check if dependency tables exist."""
    
    db = get_db_path(db_path)
    
    with sqlite3.connect(db) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name IN (
            'dependency_summaries', 
            'variable_dependencies', 
            'variable_metadata'
        )
        """)
        
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        required_tables = [
            'dependency_summaries', 
            'variable_dependencies', 
            'variable_metadata'
        ]
        
        missing = set(required_tables) - set(existing_tables)
        
        if missing:
            print(f"Missing tables: {missing}")
            return False
        else:
            print("All dependency tables exist")
            return True

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check_migration_status()
    else:
        migrate_add_dependency_tables()
        check_migration_status()