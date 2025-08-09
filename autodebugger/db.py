from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

DEFAULT_DB_PATH = os.path.join(os.getcwd(), ".autodebug", "line_reports.db")


@dataclass
class LineReport:
    session_id: str
    file: str
    line_number: int
    code: str
    timestamp: str
    variables: Dict[str, Any]
    stack_depth: int
    thread_id: int
    variables_delta: Optional[Dict[str, Any]] = None
    observations: Optional[str] = None
    status: str = "success"  # success | error | warning
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    stack_trace: Optional[str] = None


@dataclass
class SessionSummary:
    session_id: str
    file: str
    language: str
    start_time: str
    end_time: Optional[str] = None
    total_lines_executed: int = 0
    successful_lines: int = 0
    lines_with_errors: int = 0
    total_crashes: int = 0
    # Provenance
    git_root: Optional[str] = None
    git_commit: Optional[str] = None
    git_dirty: int = 0


class LineReportStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._create_tables()
        self._ensure_delta_column()
        self._ensure_git_columns()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _create_tables(self) -> None:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS line_reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              file TEXT NOT NULL,
              line_number INTEGER NOT NULL,
              code TEXT NOT NULL,
              timestamp TEXT NOT NULL,
              variables TEXT,
              variables_delta TEXT,
              stack_depth INTEGER,
              thread_id INTEGER,
              observations TEXT,
              status TEXT CHECK(status IN ('success','error','warning')),
              error_message TEXT,
              error_type TEXT,
              stack_trace TEXT,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_id ON line_reports(session_id);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_file_line ON line_reports(file, line_number);
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summaries (
              session_id TEXT PRIMARY KEY,
              file TEXT NOT NULL,
              language TEXT NOT NULL,
              start_time TEXT NOT NULL,
              end_time TEXT,
              total_lines_executed INTEGER DEFAULT 0,
              successful_lines INTEGER DEFAULT 0,
              lines_with_errors INTEGER DEFAULT 0,
              total_crashes INTEGER DEFAULT 0,
              git_root TEXT,
              git_commit TEXT,
              git_dirty INTEGER DEFAULT 0,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()

    def _ensure_delta_column(self) -> None:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(line_reports)")
        cols = [r[1] for r in cur.fetchall()]
        if "variables_delta" not in cols:
            try:
                cur.execute("ALTER TABLE line_reports ADD COLUMN variables_delta TEXT")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass

    def _ensure_git_columns(self) -> None:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(session_summaries)")
        cols = [r[1] for r in cur.fetchall()]
        to_add = []
        if "git_root" not in cols:
            to_add.append(("git_root", "TEXT"))
        if "git_commit" not in cols:
            to_add.append(("git_commit", "TEXT"))
        if "git_dirty" not in cols:
            to_add.append(("git_dirty", "INTEGER DEFAULT 0"))
        for name, coltype in to_add:
            try:
                cur.execute(f"ALTER TABLE session_summaries ADD COLUMN {name} {coltype}")
            except sqlite3.OperationalError:
                pass
        if to_add:
            self.conn.commit()

    def create_session(self, summary: SessionSummary) -> None:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO session_summaries(
              session_id, file, language, start_time, end_time,
              total_lines_executed, successful_lines, lines_with_errors, total_crashes,
              git_root, git_commit, git_dirty
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                summary.session_id,
                summary.file,
                summary.language,
                summary.start_time,
                summary.end_time,
                summary.total_lines_executed,
                summary.successful_lines,
                summary.lines_with_errors,
                summary.total_crashes,
                summary.git_root,
                summary.git_commit,
                int(summary.git_dirty or 0),
            ),
        )
        self.conn.commit()

    def end_session(self, session_id: str, end_time: str) -> None:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE session_summaries
            SET end_time = ?, updated_at=CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (end_time, session_id),
        )
        self.conn.commit()

    def add_line_report(self, report: LineReport) -> int:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO line_reports(
              session_id,file,line_number,code,timestamp,
              variables,variables_delta,stack_depth,thread_id,observations,
              status,error_message,error_type,stack_trace
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                report.session_id,
                report.file,
                report.line_number,
                report.code,
                report.timestamp,
                json.dumps(report.variables or {}),
                json.dumps(report.variables_delta or {}),
                report.stack_depth,
                report.thread_id,
                report.observations,
                report.status,
                report.error_message,
                report.error_type,
                report.stack_trace,
            ),
        )
        last_id = cur.lastrowid
        # Update summary counters
        updates = ["total_lines_executed = total_lines_executed + 1"]
        if report.status == "success":
            updates.append("successful_lines = successful_lines + 1")
        elif report.status == "error":
            updates.append("lines_with_errors = lines_with_errors + 1")
            updates.append("total_crashes = total_crashes + 1")
        cur.execute(
            f"UPDATE session_summaries SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
            (report.session_id,),
        )
        self.conn.commit()
        return int(last_id)

    def export_session_json(self, session_id: str) -> str:
        assert self.conn is not None
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM session_summaries WHERE session_id=?", (session_id,))
        summary = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else []
        summary_obj = dict(zip(columns, summary)) if summary else None

        cur.execute(
            "SELECT id, session_id, file, line_number, code, timestamp, variables, stack_depth, thread_id, observations, status, error_message, error_type, stack_trace, created_at FROM line_reports WHERE session_id=? ORDER BY id",
            (session_id,),
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description] if cur.description else []
        reports = [dict(zip(colnames, row)) for row in rows]
        for r in reports:
            try:
                r["variables"] = json.loads(r.get("variables") or "{}")
            except Exception:
                r["variables"] = {}

        crashes = [
            {
                "line_number": r["line_number"],
                "error_type": r.get("error_type"),
                "error_message": r.get("error_message"),
                "stack_trace": r.get("stack_trace"),
                "timestamp": r.get("timestamp"),
            }
            for r in reports
            if r.get("status") == "error"
        ]
        export_payload = {
            "session_info": summary_obj,
            "line_reports": reports,
            "crashes": crashes,
            "summary": {
                "total_lines_executed": (summary_obj or {}).get("total_lines_executed", 0),
                "successful_lines": (summary_obj or {}).get("successful_lines", 0),
                "lines_with_errors": (summary_obj or {}).get("lines_with_errors", 0),
                "total_crashes": (summary_obj or {}).get("total_crashes", 0),
                "start_time": (summary_obj or {}).get("start_time"),
                "end_time": (summary_obj or {}).get("end_time"),
            },
        }
        return json.dumps(export_payload, indent=2)
