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
"""Tests for superset.utils.user_export."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from superset.utils import json
from superset.utils.user_export import (
    cleanup_old_exports,
    export_user_data_to_file,
    get_user_query_history,
    import_user_data,
    UserNotFoundError,
)

# ---------------------------------------------------------------------------
# import_user_data
# ---------------------------------------------------------------------------


def test_import_user_data_reads_json(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    payload = {"user": {"id": 1}, "dashboards": []}
    filepath = os.path.join(export_dir, "export.json")
    with open(filepath, "w") as f:
        f.write(json.dumps(payload))

    result = import_user_data("export.json", allowed_dir=export_dir)
    assert result == payload


def test_import_user_data_rejects_path_traversal(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    with pytest.raises(ValueError, match="inside the allowed export directory"):
        import_user_data("../../etc/passwd", allowed_dir=export_dir)


def test_import_user_data_rejects_absolute_escape(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    with pytest.raises(ValueError, match="inside the allowed export directory"):
        import_user_data("/etc/passwd", allowed_dir=export_dir)


def test_import_user_data_file_not_found(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    with pytest.raises(FileNotFoundError):
        import_user_data("nonexistent.json", allowed_dir=export_dir)


# ---------------------------------------------------------------------------
# export_user_data_to_file – writes JSON, not pickle
# ---------------------------------------------------------------------------


@patch("superset.utils.user_export.get_user_activity_summary")
def test_export_writes_json_not_pickle(
    mock_summary: MagicMock, tmp_path: object
) -> None:
    export_dir = str(tmp_path)
    mock_summary.return_value = {"user": {"id": 1}, "dashboards": []}

    path = export_user_data_to_file(1, export_dir)

    assert path.endswith(".json")
    with open(path) as f:
        data = json.loads(f.read())
    assert data["user"]["id"] == 1


# ---------------------------------------------------------------------------
# get_user_activity_summary – raises UserNotFoundError
# ---------------------------------------------------------------------------


@patch("superset.utils.user_export.db")
def test_get_user_activity_summary_not_found(mock_db: MagicMock) -> None:
    from superset.utils.user_export import get_user_activity_summary

    mock_db.session.query.return_value.filter_by.return_value.first.return_value = None
    with pytest.raises(UserNotFoundError):
        get_user_activity_summary(999)


# ---------------------------------------------------------------------------
# get_user_query_history – parameterised queries
# ---------------------------------------------------------------------------


@patch("superset.utils.user_export.db")
def test_get_user_query_history_rejects_invalid_status(mock_db: MagicMock) -> None:
    result = get_user_query_history(1, status_filter="'; DROP TABLE users;--")
    assert result == []
    mock_db.session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_old_exports – error handling
# ---------------------------------------------------------------------------


def test_cleanup_old_exports_skips_directories(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    subdir = os.path.join(export_dir, "subdir")
    os.makedirs(subdir)
    removed = cleanup_old_exports(export_dir, max_age_days=0)
    assert removed == 0
    assert os.path.isdir(subdir)


def test_cleanup_old_exports_removes_old_files(tmp_path: object) -> None:
    export_dir = str(tmp_path)
    old_file = os.path.join(export_dir, "old.json")
    with open(old_file, "w") as f:
        f.write("{}")
    old_ts = time.time() - 40 * 86400
    os.utime(old_file, (old_ts, old_ts))

    removed = cleanup_old_exports(export_dir, max_age_days=30)
    assert removed == 1
    assert not os.path.exists(old_file)


def test_cleanup_old_exports_nonexistent_dir() -> None:
    assert cleanup_old_exports("/nonexistent/dir/xyz") == 0
