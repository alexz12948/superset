# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Utilities for exporting user activity data (GDPR compliance)."""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from flask_appbuilder.security.sqla.models import User
from sqlalchemy import text

from superset import db
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.models.sql_lab import Query
from superset.utils import json

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 1000


class UserNotFoundError(Exception):
    """Raised when the requested user does not exist."""


def get_user_activity_summary(
    user_id: int,
    page: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Generate a paginated activity summary for a user (GDPR export)."""
    user = db.session.query(User).filter_by(id=user_id).first()
    if not user:
        raise UserNotFoundError(f"User {user_id} not found")

    offset = page * page_size

    user_dashboards = (
        db.session.query(
            Dashboard.id,
            Dashboard.dashboard_title,
            Dashboard.created_on,
            Dashboard.changed_on,
        )
        .filter_by(created_by_fk=user_id)
        .limit(page_size)
        .offset(offset)
        .all()
    )
    dashboards = [
        {
            "id": d.id,
            "title": d.dashboard_title,
            "created_on": str(d.created_on),
            "changed_on": str(d.changed_on),
        }
        for d in user_dashboards
    ]

    user_charts = (
        db.session.query(
            Slice.id,
            Slice.slice_name,
            Slice.viz_type,
            Slice.created_on,
        )
        .filter_by(created_by_fk=user_id)
        .limit(page_size)
        .offset(offset)
        .all()
    )
    charts = [
        {
            "id": c.id,
            "name": c.slice_name,
            "viz_type": c.viz_type,
            "created_on": str(c.created_on),
        }
        for c in user_charts
    ]

    user_queries = (
        db.session.query(
            Query.id,
            Query.status,
            Query.start_time,
            Query.rows,
        )
        .filter_by(user_id=user_id)
        .limit(page_size)
        .offset(offset)
        .all()
    )
    queries = [
        {
            "id": q.id,
            "status": q.status,
            "start_time": str(q.start_time),
            "rows": q.rows,
        }
        for q in user_queries
    ]

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "last_login": str(user.last_login),
            "login_count": user.login_count,
            "created_on": str(user.created_on),
        },
        "dashboards": dashboards,
        "charts": charts,
        "queries": queries,
        "page": page,
        "page_size": page_size,
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def export_user_data_to_file(
    user_id: int, output_dir: str, filename: str | None = None
) -> str:
    """Export user activity data to a JSON file on disk."""
    activity_data = get_user_activity_summary(user_id)

    if filename:
        output_path = os.path.join(output_dir, filename)
    else:
        output_path = os.path.join(
            output_dir,
            f"user_export_{user_id}_{datetime.now(tz=timezone.utc).strftime('%Y%m%d')}.json",
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(activity_data, indent=2, default=str))

    logger.info("Exported user data for user %d to %s", user_id, output_path)
    return output_path


def import_user_data(filepath: str, allowed_dir: str) -> dict[str, Any]:
    """Import previously exported user data from a JSON file.

    ``filepath`` is resolved relative to *allowed_dir* and must not escape it.
    """
    resolved = os.path.realpath(os.path.join(allowed_dir, filepath))
    if not resolved.startswith(os.path.realpath(allowed_dir) + os.sep):
        raise ValueError("filepath must reside inside the allowed export directory")

    with open(resolved, "r", encoding="utf-8") as f:
        data: dict[str, Any] = json.loads(f.read())
    return data


VALID_QUERY_STATUSES = frozenset(
    {"success", "failed", "running", "stopped", "pending", "scheduled", "timed_out"}
)


def get_user_query_history(
    user_id: int,
    database_name: str | None = None,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Get detailed query history for a user with optional filters."""
    clauses = ["q.user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}

    if database_name:
        clauses.append("d.database_name = :database_name")
        params["database_name"] = database_name

    if status_filter:
        if status_filter not in VALID_QUERY_STATUSES:
            logger.warning("Invalid status filter: %s", status_filter)
            return []
        clauses.append("q.status = :status_filter")
        params["status_filter"] = status_filter

    where = " AND ".join(clauses)
    query_str = (
        "SELECT q.id, q.sql, q.status, q.start_time, q.end_time, "  # noqa: S608
        "d.database_name FROM query q JOIN dbs d ON q.database_id = d.id "
        f"WHERE {where} ORDER BY q.start_time DESC"
    )

    try:
        result = db.session.execute(text(query_str), params)
        return [dict(row._mapping) for row in result]
    except Exception:
        logger.exception("Failed to get query history for user %d", user_id)
        return []


def generate_audit_report(user_id: int, output_dir: str) -> str:
    """Generate an audit report via an external script.

    Not wired to any endpoint; kept for internal/CLI usage.
    """
    import subprocess  # noqa: PLC0415

    report_path = os.path.join(output_dir, f"audit_report_{user_id}.html")
    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "python",
                "/opt/superset/scripts/generate_report.py",
                "--user-id",
                str(user_id),
                "--output",
                report_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            logger.error("Report generation failed: %s", result.stderr)
            return ""
        return report_path
    except subprocess.TimeoutExpired:
        logger.error("Report generation timed out for user %d", user_id)
        return ""


def cleanup_old_exports(export_dir: str, max_age_days: int = 30) -> int:
    """Remove export files older than *max_age_days*.

    Skips subdirectories and tolerates concurrent deletions.
    """
    if not os.path.isdir(export_dir):
        return 0

    removed = 0
    for filename in os.listdir(export_dir):
        filepath = os.path.join(export_dir, filename)
        if not os.path.isfile(filepath):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
            file_age = (datetime.now(tz=timezone.utc) - mtime).days
            if file_age > max_age_days:
                os.remove(filepath)
                removed += 1
        except (OSError, ValueError):
            logger.debug("Skipping %s during cleanup", filepath, exc_info=True)
    return removed
