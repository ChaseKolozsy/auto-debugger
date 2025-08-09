from __future__ import annotations

import os
from typing import Optional, List, Dict, Any, Tuple
from flask import Flask, render_template, request, redirect, url_for

from .db import LineReportStore


def create_app(db_path: Optional[str] = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Use one connection per request to avoid cross-thread issues
    def get_store() -> LineReportStore:
        s = LineReportStore(db_path)
        s.open()
        return s

    @app.teardown_request
    def _close_db(_exc: Optional[BaseException]) -> None:
        st = getattr(app, '_req_store', None)
        if st:
            try:
                st.close()
            except Exception:
                pass

    @app.route("/")
    def index():
        # List sessions sorted by updated time desc
        store = get_store(); app._req_store = store  # type: ignore[attr-defined]
        conn = store.conn
        assert conn is not None
        cur = conn.cursor()
        q = "SELECT session_id, file, language, start_time, end_time, total_lines_executed, successful_lines, lines_with_errors, total_crashes, updated_at FROM session_summaries ORDER BY updated_at DESC"
        cur.execute(q)
        rows = cur.fetchall()
        sessions = [
            {
                "session_id": r[0],
                "file": r[1],
                "language": r[2],
                "start_time": r[3],
                "end_time": r[4],
                "total_lines_executed": r[5],
                "successful_lines": r[6],
                "lines_with_errors": r[7],
                "total_crashes": r[8],
                "updated_at": r[9],
            }
            for r in rows
        ]
        # Distinct script entry points for filter
        cur.execute("SELECT DISTINCT file FROM session_summaries ORDER BY file")
        entries = [r[0] for r in cur.fetchall()]
        return render_template("index.html", sessions=sessions, entries=entries)

    @app.route("/sessions")
    def sessions():
        entry = request.args.get("entry")
        where = ""
        params: List[Any] = []
        if entry:
            where = "WHERE file = ?"
            params.append(entry)
        q = (
            "SELECT session_id, file, language, start_time, end_time, total_lines_executed, successful_lines, lines_with_errors, total_crashes, updated_at "
            f"FROM session_summaries {where} ORDER BY updated_at DESC"
        )
        store = get_store(); app._req_store = store  # type: ignore[attr-defined]
        conn = store.conn
        assert conn is not None
        cur = conn.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
        sessions = [
            {
                "session_id": r[0],
                "file": r[1],
                "language": r[2],
                "start_time": r[3],
                "end_time": r[4],
                "total_lines_executed": r[5],
                "successful_lines": r[6],
                "lines_with_errors": r[7],
                "total_crashes": r[8],
                "updated_at": r[9],
            }
            for r in rows
        ]
        return render_template("sessions.html", sessions=sessions, selected_entry=entry)

    @app.route("/session/<session_id>")
    def session_detail(session_id: str):
        store = get_store(); app._req_store = store  # type: ignore[attr-defined]
        conn = store.conn
        assert conn is not None
        cur = conn.cursor()
        cur.execute("SELECT * FROM session_summaries WHERE session_id=?", (session_id,))
        summary_row = cur.fetchone()
        if not summary_row:
            return render_template("session_detail.html", session=None, reports=[])
        # Map columns
        columns = [d[0] for d in cur.description]
        summary = {columns[i]: summary_row[i] for i in range(len(columns))}

        # Optional filter by file/line/status
        file_filter = request.args.get("file")
        status = request.args.get("status")
        parts = ["session_id = ?"]
        params: List[Any] = [session_id]
        if file_filter:
            parts.append("file = ?")
            params.append(file_filter)
        if status in {"success", "error", "warning"}:
            parts.append("status = ?")
            params.append(status)
        where = " AND ".join(parts)
        cur.execute(
            "SELECT id, file, line_number, code, timestamp, variables, variables_delta, stack_depth, thread_id, status, error_type, error_message "
            f"FROM line_reports WHERE {where} ORDER BY id",
            tuple(params),
        )
        rows = cur.fetchall()
        import json as _json
        reports = []

        # Build a per-file function map: line -> qualified name
        # This avoids DB migrations and derives method/function context from source.
        import ast
        function_maps: Dict[str, List[Tuple[int, int, str]]] = {}

        def build_function_ranges(pyfile: str) -> List[Tuple[int, int, str]]:
            ranges: List[Tuple[int, int, str]] = []
            try:
                with open(pyfile, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=pyfile)
            except Exception:
                return ranges

            stack: List[str] = []

            class Visitor(ast.NodeVisitor):
                def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
                    stack.append(node.name)
                    self.generic_visit(node)
                    stack.pop()

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
                    qual = ".".join(stack + [node.name]) if stack else node.name
                    start = getattr(node, "lineno", None)
                    end = getattr(node, "end_lineno", None)
                    if isinstance(start, int) and isinstance(end, int):
                        ranges.append((start, end, qual))
                    self.generic_visit(node)

                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
                    qual = ".".join(stack + [node.name]) if stack else node.name
                    start = getattr(node, "lineno", None)
                    end = getattr(node, "end_lineno", None)
                    if isinstance(start, int) and isinstance(end, int):
                        ranges.append((start, end, qual))
                    self.generic_visit(node)

            Visitor().visit(tree)
            # Sort by range size descending to prefer the most specific (innermost)
            ranges.sort(key=lambda t: (t[1] - t[0], t[0]), reverse=True)
            return ranges

        def function_for_line(pyfile: str, line: int) -> Optional[str]:
            if not pyfile or not os.path.isfile(pyfile):
                return None
            if pyfile not in function_maps:
                function_maps[pyfile] = build_function_ranges(pyfile)
            for start, end, qual in function_maps.get(pyfile, []):
                if start <= line <= end:
                    return qual
            return None
        for r in rows:
            try:
                vars_obj = _json.loads(r[5] or '{}')
            except Exception:
                vars_obj = {}
            try:
                delta_obj = _json.loads(r[6] or '{}')
            except Exception:
                delta_obj = {}
            func_name = function_for_line(r[1], int(r[2]) if r[2] is not None else -1)
            reports.append({
                "id": r[0],
                "file": r[1],
                "line_number": r[2],
                "code": r[3],
                "timestamp": r[4],
                "variables": vars_obj,
                "variables_delta": delta_obj,
                "stack_depth": r[7],
                "thread_id": r[8],
                "status": r[9],
                "error_type": r[10],
                "error_message": r[11],
                "function": func_name,
            })
        # Distinct files for quick filtering
        cur.execute("SELECT DISTINCT file FROM line_reports WHERE session_id=? ORDER BY file", (session_id,))
        files = [r[0] for r in cur.fetchall()]
        return render_template("session_detail.html", session=summary, reports=reports, files=files, selected_file=file_filter, selected_status=status)

    return app
