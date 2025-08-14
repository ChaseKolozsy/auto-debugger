#!/usr/bin/env python3
"""
Post-processing script to analyze variable dependencies from autodebugger data.
Builds a dependency graph by analyzing how variables reference each other.
"""

import ast
import json
import sqlite3
from typing import Dict, List, Set, Tuple
from pathlib import Path

class DependencyAnalyzer:
    """Analyze variable dependencies from line reports."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.dependencies = {}  # var_name -> set of variables it depends on
        self.reverse_deps = {}  # var_name -> set of variables that depend on it
        
    def analyze_session(self, session_id: str, save_to_db: bool = True) -> Dict:
        """Analyze dependencies for a debugging session."""
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if analysis already exists
            existing = cursor.execute("""
                SELECT COUNT(*) FROM dependency_summaries 
                WHERE session_id = ?
            """, (session_id,)).fetchone()
            
            if existing and existing[0] > 0:
                print(f"Dependency analysis already exists for session {session_id}")
                # Could optionally delete and re-analyze
            
            # Get all line reports for the session
            rows = cursor.execute("""
                SELECT line_number, code, variables, variables_delta
                FROM line_reports
                WHERE session_id = ?
                ORDER BY id
            """, (session_id,)).fetchall()
            
            # Track current variables in scope
            current_vars = set()
            variable_first_seen = {}  # Track first appearance
            variable_last_modified = {}  # Track last modification
            dependency_lines = {}  # Track where dependencies occur
            
            for line_num, code, vars_json, delta_json in rows:
                if vars_json:
                    vars_dict = json.loads(vars_json)
                    
                    # Update current variables
                    if 'Locals' in vars_dict:
                        current_vars = set(vars_dict['Locals'].keys())
                        
                        # Track first appearance and modifications
                        for var in current_vars:
                            if var not in variable_first_seen:
                                variable_first_seen[var] = line_num
                            if delta_json:
                                delta = json.loads(delta_json)
                                if 'Locals' in delta and var in delta['Locals']:
                                    variable_last_modified[var] = line_num
                
                # Analyze the code to find dependencies
                dependencies = self._extract_dependencies(code, current_vars)
                
                # Update dependency graph
                for target, sources in dependencies.items():
                    if target not in self.dependencies:
                        self.dependencies[target] = set()
                    self.dependencies[target].update(sources)
                    
                    # Track where this dependency first appears
                    for source in sources:
                        dep_key = (target, source)
                        if dep_key not in dependency_lines:
                            dependency_lines[dep_key] = line_num
                    
                    # Update reverse dependencies
                    for source in sources:
                        if source not in self.reverse_deps:
                            self.reverse_deps[source] = set()
                        self.reverse_deps[source].add(target)
        
        report = self._build_report()
        
        if save_to_db:
            self._save_to_database(
                session_id, 
                report, 
                variable_first_seen, 
                variable_last_modified,
                dependency_lines
            )
        
        return report
    
    def _extract_dependencies(self, code: str, available_vars: Set[str]) -> Dict[str, Set[str]]:
        """Extract variable dependencies from a line of code."""
        dependencies = {}
        
        # Skip non-assignment lines
        if '=' not in code or '==' in code or '!=' in code:
            return dependencies
        
        try:
            # Parse the code line
            tree = ast.parse(code.strip())
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    # Get target variable(s)
                    targets = []
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            targets.append(target.id)
                    
                    # Get source variables
                    sources = set()
                    for sub_node in ast.walk(node.value):
                        if isinstance(sub_node, ast.Name):
                            if sub_node.id in available_vars:
                                sources.add(sub_node.id)
                    
                    # Record dependencies
                    for target in targets:
                        if sources:
                            dependencies[target] = sources
                
                elif isinstance(node, ast.AugAssign):
                    # Handle += -= etc
                    if isinstance(node.target, ast.Name):
                        target = node.target.id
                        sources = {target}  # Depends on itself
                        
                        for sub_node in ast.walk(node.value):
                            if isinstance(sub_node, ast.Name):
                                if sub_node.id in available_vars:
                                    sources.add(sub_node.id)
                        
                        dependencies[target] = sources
        
        except SyntaxError:
            # Could not parse, skip
            pass
        
        return dependencies
    
    def _build_report(self) -> Dict:
        """Build a comprehensive dependency report."""
        
        # Find root variables (no dependencies)
        roots = []
        for var in set(self.dependencies.keys()) | set(self.reverse_deps.keys()):
            if var not in self.dependencies or not self.dependencies.get(var):
                roots.append(var)
        
        # Find leaf variables (nothing depends on them)
        leaves = []
        for var in self.dependencies.keys():
            if var not in self.reverse_deps or not self.reverse_deps.get(var):
                leaves.append(var)
        
        # Calculate dependency depth for each variable
        depths = {}
        for var in self.dependencies:
            depths[var] = self._calculate_depth(var)
        
        # Find circular dependencies
        circles = self._find_circular_deps()
        
        return {
            'total_variables': len(set(self.dependencies.keys()) | set(self.reverse_deps.keys())),
            'root_variables': roots,
            'leaf_variables': leaves,
            'dependency_graph': {k: list(v) for k, v in self.dependencies.items()},
            'reverse_dependencies': {k: list(v) for k, v in self.reverse_deps.items()},
            'dependency_depths': depths,
            'circular_dependencies': circles,
            'most_depended_on': self._get_most_depended_on(5),
            'most_dependent': self._get_most_dependent(5)
        }
    
    def _calculate_depth(self, var: str, visited: Set[str] = None) -> int:
        """Calculate the dependency depth of a variable."""
        if visited is None:
            visited = set()
        
        if var in visited:
            return 0  # Circular dependency
        
        if var not in self.dependencies or not self.dependencies[var]:
            return 0
        
        visited.add(var)
        max_depth = 0
        
        for dep in self.dependencies[var]:
            depth = self._calculate_depth(dep, visited.copy())
            max_depth = max(max_depth, depth)
        
        return max_depth + 1
    
    def _find_circular_deps(self) -> List[List[str]]:
        """Find circular dependencies."""
        circles = []
        visited = set()
        
        def dfs(var, path):
            if var in path:
                # Found a circle
                circle_start = path.index(var)
                circle = path[circle_start:] + [var]
                if tuple(sorted(circle)) not in visited:
                    circles.append(circle)
                    visited.add(tuple(sorted(circle)))
                return
            
            if var not in self.dependencies:
                return
            
            for dep in self.dependencies[var]:
                dfs(dep, path + [var])
        
        for var in self.dependencies:
            dfs(var, [])
        
        return circles
    
    def _get_most_depended_on(self, n: int) -> List[Tuple[str, int]]:
        """Get the n most depended-on variables."""
        counts = [(var, len(deps)) for var, deps in self.reverse_deps.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        return counts[:n]
    
    def _get_most_dependent(self, n: int) -> List[Tuple[str, int]]:
        """Get the n most dependent variables."""
        counts = [(var, len(deps)) for var, deps in self.dependencies.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        return counts[:n]
    
    def _save_to_database(self, session_id: str, report: Dict, 
                         first_seen: Dict, last_modified: Dict,
                         dependency_lines: Dict):
        """Save analysis results to database."""
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Save summary
            cursor.execute("""
                INSERT OR REPLACE INTO dependency_summaries 
                (session_id, total_variables, root_variables, leaf_variables,
                 circular_dependencies, most_depended_on, most_dependent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                report['total_variables'],
                json.dumps(report['root_variables']),
                json.dumps(report['leaf_variables']),
                json.dumps(report['circular_dependencies']),
                json.dumps(report['most_depended_on']),
                json.dumps(report['most_dependent'])
            ))
            
            # Save individual dependencies
            for target, sources in self.dependencies.items():
                for source in sources:
                    first_line = dependency_lines.get((target, source), 0)
                    cursor.execute("""
                        INSERT OR REPLACE INTO variable_dependencies
                        (session_id, target_variable, source_variable, first_line)
                        VALUES (?, ?, ?, ?)
                    """, (session_id, target, source, first_line))
            
            # Save variable metadata
            all_vars = set(self.dependencies.keys()) | set(self.reverse_deps.keys())
            
            for var in all_vars:
                depth = self._calculate_depth(var)
                depends_on = len(self.dependencies.get(var, []))
                depended_by = len(self.reverse_deps.get(var, []))
                is_root = var in report['root_variables']
                is_leaf = var in report['leaf_variables']
                
                cursor.execute("""
                    INSERT OR REPLACE INTO variable_metadata
                    (session_id, variable_name, first_appearance_line, 
                     last_modified_line, dependency_depth, depends_on_count,
                     depended_by_count, is_root, is_leaf)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id, var,
                    first_seen.get(var, 0),
                    last_modified.get(var, 0),
                    depth, depends_on, depended_by,
                    1 if is_root else 0,
                    1 if is_leaf else 0
                ))
            
            conn.commit()
            print(f"✓ Saved dependency analysis for session {session_id}")


def main():
    """Example usage."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python dependency_analyzer.py <session_id>")
        sys.exit(1)
    
    session_id = sys.argv[1]
    db_path = ".autodebug/line_reports.db"
    
    analyzer = DependencyAnalyzer(db_path)
    report = analyzer.analyze_session(session_id)
    
    print("Variable Dependency Analysis")
    print("=" * 50)
    print(f"Total variables: {report['total_variables']}")
    print(f"Root variables: {report['root_variables'][:10]}")
    print(f"Leaf variables: {report['leaf_variables'][:10]}")
    
    print("\nMost depended-on variables:")
    for var, count in report['most_depended_on']:
        print(f"  {var}: {count} dependencies")
    
    print("\nMost dependent variables:")
    for var, count in report['most_dependent']:
        print(f"  {var}: depends on {count} variables")
    
    if report['circular_dependencies']:
        print("\n⚠️ Circular dependencies detected:")
        for circle in report['circular_dependencies']:
            print(f"  {' -> '.join(circle)}")
    
    print("\nSample dependency chains:")
    for var, deps in list(report['dependency_graph'].items())[:5]:
        if deps:
            print(f"  {var} ← {', '.join(deps)}")


if __name__ == "__main__":
    main()