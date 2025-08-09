from __future__ import annotations

import os
from typing import Optional, List, Dict, Any
from flask import Flask, render_template, request, redirect, url_for

from .db import LineReportStore


def create_app(db_path: Optional[str] = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    store = LineReportStore(db_path)
    store.open()

    @app.teardown_appcontext
    def _close_db(_exc: Optional[BaseException]) -> None:
        try:
            store.close()
        except Exception:
            pass

    @app.route("/")
    def index():
        # List sessions sorted by updated time desc
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
            "SELECT id, file, line_number, code, timestamp, variables, stack_depth, thread_id, status, error_type, error_message "
            f"FROM line_reports WHERE {where} ORDER BY id",
            tuple(params),
        )
        rows = cur.fetchall()
        reports = [
            {
                "id": r[0],
                "file": r[1],
                "line_number": r[2],
                "code": r[3],
                "timestamp": r[4],
                "stack_depth": r[6],
                "thread_id": r[7],
                "status": r[8],
                "error_type": r[9],
                "error_message": r[10],
            }
            for r in rows
        ]
        # Distinct files for quick filtering
        cur.execute("SELECT DISTINCT file FROM line_reports WHERE session_id=? ORDER BY file", (session_id,))
        files = [r[0] for r in cur.fetchall()]
        return render_template("session_detail.html", session=summary, reports=reports, files=files, selected_file=file_filter, selected_status=status)

    return app
