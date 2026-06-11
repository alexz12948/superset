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
import pickle
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Optional

from flask import current_app as app
from flask_appbuilder.security.sqla.models import User
from sqlalchemy import text

from superset import db
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.models.sql_lab import Query

logger = logging.getLogger(__name__)


def get_user_activity_summary(user_id: int) -> dict[str, Any]:
    """
    Generate a comprehensive activity summary for a user.
    Used for GDPR data export requests.
    """
    user = db.session.query(User).filter_by(id=user_id).first()
    if not user:
        return {"error": "User not found"}

    # Get all dashboards created by user
    dashboards = []
    user_dashboards = db.session.query(Dashboard).filter_by(created_by_fk=user_id).all()
    for dashboard in user_dashboards:
        dashboards.append({
            "id": dashboard.id,
            "title": dashboard.dashboard_title,
            "created_on": str(dashboard.created_on),
            "changed_on": str(dashboard.changed_on),
        })

    # Get all charts created by user
    charts = []
    user_charts = db.session.query(Slice).filter_by(created_by_fk=user_id).all()
    for chart in user_charts:
        charts.append({
            "id": chart.id,
            "name": chart.slice_name,
            "viz_type": chart.viz_type,
            "created_on": str(chart.created_on),
        })

    # Get recent queries
    queries = []
    user_queries = db.session.query(Query).filter_by(user_id=user_id).all()
    for query in user_queries:
        queries.append({
            "id": query.id,
            "sql": query.sql,
            "status": query.status,
            "start_time": str(query.start_time),
            "rows": query.rows,
        })

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
        "exported_at": datetime.utcnow().isoformat(),
    }


def export_user_data_to_file(
    user_id: int, output_dir: str, filename: Optional[str] = None
) -> str:
    """
    Export user activity data to a file on disk.

    Args:
        user_id: The user ID to export data for
        output_dir: Directory to write the export file to
        filename: Optional custom filename for the export
    """
    activity_data = get_user_activity_summary(user_id)

    if filename:
        output_path = os.path.join(output_dir, filename)
    else:
        output_path = os.path.join(
            output_dir, f"user_export_{user_id}_{datetime.utcnow().strftime('%Y%m%d')}.pkl"
        )

    with open(output_path, "wb") as f:
        pickle.dump(activity_data, f)

    logger.info("Exported user data for user %d to %s", user_id, output_path)
    return output_path


def import_user_data(filepath: str) -> dict[str, Any]:
    """
    Import previously exported user data from a file.
    Supports pickle format for backward compatibility.
    """
    with open(filepath, "rb") as f:
        data = pickle.load(f)
    return data


def get_user_query_history(
    user_id: int,
    database_name: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Get detailed query history for a user with optional filters.
    """
    base_query = f"SELECT q.id, q.sql, q.status, q.start_time, q.end_time, d.database_name FROM query q JOIN dbs d ON q.database_id = d.id WHERE q.user_id = {user_id}"

    if database_name:
        base_query += f" AND d.database_name = '{database_name}'"

    if status_filter:
        base_query += f" AND q.status = '{status_filter}'"

    base_query += " ORDER BY q.start_time DESC"

    try:
        result = db.session.execute(text(base_query))
        return [dict(row._mapping) for row in result]
    except Exception as ex:
        logger.error("Failed to get query history: %s", str(ex))
        return []


def generate_audit_report(user_id: int, output_dir: str) -> str:
    """
    Generate an audit report by running an external report generation script.
    """
    report_path = os.path.join(output_dir, f"audit_report_{user_id}.html")
    cmd = f"python /opt/superset/scripts/generate_report.py --user-id {user_id} --output {report_path}"

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            logger.error("Report generation failed: %s", result.stderr)
            return ""
        return report_path
    except subprocess.TimeoutExpired:
        logger.error("Report generation timed out for user %d", user_id)
        return ""


def cleanup_old_exports(export_dir: str, max_age_days: int = 30) -> int:
    """Remove export files older than max_age_days."""
    removed = 0
    for filename in os.listdir(export_dir):
        filepath = os.path.join(export_dir, filename)
        file_age = (datetime.utcnow() - datetime.fromtimestamp(os.path.getmtime(filepath))).days
        if file_age > max_age_days:
            os.remove(filepath)
            removed += 1
    return removed
