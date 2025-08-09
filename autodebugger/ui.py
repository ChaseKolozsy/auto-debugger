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
        from typing import TypedDict, Optional as _Optional

        class FnInfo(TypedDict, total=False):
            start: int
            end: int
            qual: str
            sig: str
            body: str

        function_maps: Dict[str, List[FnInfo]] = {}

        def _extract_sig_and_body(source: str, node: ast.AST) -> Tuple[str, str]:
            try:
                segment = ast.get_source_segment(source, node) or ""
            except Exception:
                segment = ""
            lines = segment.splitlines()
            if not lines:
                return "", ""
            # Capture signature lines up to the first line ending with ':' (inclusive)
            sig_lines: List[str] = []
            body_lines: List[str] = []
            found_colon = False
            for ln in lines:
                sig_lines.append(ln)
                if ln.rstrip().endswith(":"):
                    found_colon = True
                    break
            if found_colon:
                body_lines = lines[len(sig_lines):]
            else:
                # Fallback: treat first line as signature, rest as body
                sig_lines = [lines[0]]
                body_lines = lines[1:]
            sig = "\n".join(sig_lines).strip()
            # Truncate body preview for UI friendliness
            body = "\n".join(body_lines).strip()
            max_chars = 1200
            if len(body) > max_chars:
                body = body[: max_chars - 1] + "\n…"
            return sig, body

        def build_function_ranges(pyfile: str) -> List[FnInfo]:
            infos: List[FnInfo] = []
            try:
                with open(pyfile, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=pyfile)
            except Exception:
                return infos

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
                        sig, body = _extract_sig_and_body(source, node)
                        infos.append({"start": start, "end": end, "qual": qual, "sig": sig, "body": body})
                    self.generic_visit(node)

                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
                    qual = ".".join(stack + [node.name]) if stack else node.name
                    start = getattr(node, "lineno", None)
                    end = getattr(node, "end_lineno", None)
                    if isinstance(start, int) and isinstance(end, int):
                        sig, body = _extract_sig_and_body(source, node)
                        infos.append({"start": start, "end": end, "qual": qual, "sig": sig, "body": body})
                    self.generic_visit(node)

            Visitor().visit(tree)
            # Sort by range size descending to prefer the most specific (innermost)
            infos.sort(key=lambda t: ((t["end"] - t["start"]) if ("end" in t and "start" in t) else 0, t.get("start", 0)), reverse=True)
            return infos

        def function_for_line(pyfile: str, line: int) -> Optional[str]:
            if not pyfile or not os.path.isfile(pyfile):
                return None
            if pyfile not in function_maps:
                function_maps[pyfile] = build_function_ranges(pyfile)
            for info in function_maps.get(pyfile, []):
                start = info.get("start")
                end = info.get("end")
                if isinstance(start, int) and isinstance(end, int) and start <= line <= end:
                    return info.get("qual")
            return None

        def function_detail_for_line(pyfile: str, line: int, repo_root: _Optional[str], commit: _Optional[str]) -> Tuple[_Optional[str], _Optional[str]]:
            # Prefer reading the file content from git at the recorded commit
            source: _Optional[str] = None
            if repo_root and commit and os.path.abspath(pyfile).startswith(os.path.abspath(repo_root)):
                rel = os.path.relpath(pyfile, repo_root)
                try:
                    import subprocess as _sp
                    show = _sp.run(["git", "show", f"{commit}:{rel}"], cwd=repo_root, capture_output=True)
                    if show.returncode == 0:
                        source = show.stdout.decode("utf-8", errors="replace")
                except Exception:
                    source = None
            # Fallback to disk
            if source is None:
                try:
                    with open(pyfile, "r", encoding="utf-8") as f:
                        source = f.read()
                except Exception:
                    source = None
            if not source:
                return None, None
            try:
                tree = ast.parse(source, filename=pyfile)
            except Exception:
                return None, None

            # Recompute ranges for this file based on the chosen source
            infos: List[FnInfo] = []
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
                        sig, body = _extract_sig_and_body(source or "", node)
                        infos.append({"start": start, "end": end, "qual": qual, "sig": sig, "body": body})
                    self.generic_visit(node)
                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
                    qual = ".".join(stack + [node.name]) if stack else node.name
                    start = getattr(node, "lineno", None)
                    end = getattr(node, "end_lineno", None)
                    if isinstance(start, int) and isinstance(end, int):
                        sig, body = _extract_sig_and_body(source or "", node)
                        infos.append({"start": start, "end": end, "qual": qual, "sig": sig, "body": body})
                    self.generic_visit(node)
            Visitor().visit(tree)
            infos.sort(key=lambda t: ((t["end"] - t["start"]) if ("end" in t and "start" in t) else 0, t.get("start", 0)), reverse=True)
            for info in infos:
                start = info.get("start")
                end = info.get("end")
                if isinstance(start, int) and isinstance(end, int) and start <= line <= end:
                    return info.get("sig"), info.get("body")
            return None, None
        # Load provenance for this session
        cur.execute("SELECT git_root, git_commit FROM session_summaries WHERE session_id=?", (session_id,))
        row = cur.fetchone()
        repo_root = row[0] if row else None
        repo_commit = row[1] if row else None

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
            func_sig, func_body = function_detail_for_line(r[1], int(r[2]) if r[2] is not None else -1, repo_root, repo_commit)
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
                "function_signature": func_sig,
                "function_body": func_body,
            })
        # Distinct files for quick filtering
        cur.execute("SELECT DISTINCT file FROM line_reports WHERE session_id=? ORDER BY file", (session_id,))
        files = [r[0] for r in cur.fetchall()]
        return render_template("session_detail.html", session=summary, reports=reports, files=files, selected_file=file_filter, selected_status=status)

    return app
