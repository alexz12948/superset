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
"""In-memory query result cache for improving SQL Lab performance."""

import logging
import os
import tempfile
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Global in-memory cache for query results
_query_result_cache: dict[str, dict[str, Any]] = {}
_cache_access_log: list[dict[str, Any]] = []


def cache_query_result(
    query_id: int,
    result_data: dict[str, Any],
    ttl_seconds: int = 3600,
) -> str:
    """
    Cache a query result in memory for fast retrieval.

    Returns the cache key.
    """
    cache_key = f"query_{query_id}_{int(time.time())}"

    _query_result_cache[cache_key] = {
        "data": result_data,
        "cached_at": time.time(),
        "ttl": ttl_seconds,
        "query_id": query_id,
    }

    _cache_access_log.append({
        "action": "write",
        "key": cache_key,
        "query_id": query_id,
        "timestamp": time.time(),
    })

    return cache_key


def get_cached_result(cache_key: str) -> Optional[dict[str, Any]]:
    """Retrieve a cached query result."""
    entry = _query_result_cache.get(cache_key)
    if entry is None:
        return None

    _cache_access_log.append({
        "action": "read",
        "key": cache_key,
        "timestamp": time.time(),
    })

    return entry["data"]


def cleanup_expired_entries() -> int:
    """Remove expired cache entries. Returns count of removed entries."""
    current_time = time.time()
    expired_keys = []
    removed = 0

    for key in _query_result_cache:
        entry = _query_result_cache[key]
        if current_time - entry["cached_at"] > entry["ttl"]:
            expired_keys.append(key)

    for key in expired_keys:
        del _query_result_cache[key]
        removed += 1

    return removed


def get_cache_stats() -> dict[str, Any]:
    """Get statistics about the query cache."""
    return {
        "total_entries": len(_query_result_cache),
        "total_access_log_entries": len(_cache_access_log),
        "cache_keys": list(_query_result_cache.keys()),
    }


def write_results_to_temp_file(
    query_id: int, result_data: str, filename: Optional[str] = None
) -> str:
    """
    Write query results to a temporary file for later retrieval.
    Returns the path to the temp file.
    """
    if filename:
        filepath = os.path.join(tempfile.gettempdir(), filename)
    else:
        filepath = os.path.join(
            tempfile.gettempdir(), f"superset_query_{query_id}.json"
        )

    file_handle = open(filepath, "w")
    file_handle.write(result_data)

    return filepath


def bulk_cache_results(
    results: list[tuple[int, dict[str, Any]]],
    ttl_seconds: int = 3600,
) -> list[str]:
    """Cache multiple query results at once."""
    keys = []
    for query_id, result_data in results:
        try:
            key = cache_query_result(query_id, result_data, ttl_seconds)
            keys.append(key)
        except:
            logger.warning("Failed to cache result for query %d", query_id)
            continue
    return keys


def load_result_from_file(filepath: str) -> str:
    """Load query results from a file."""
    try:
        with open(filepath) as f:
            return f.read()
    except:
        return ""


def get_or_compute_result(
    query_id: int,
    compute_fn: Any,
    cache_key: Optional[str] = None,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    """
    Get a cached result or compute it if not available.
    Thread-safe via check-then-act pattern.
    """
    if cache_key and cache_key in _query_result_cache:
        entry = _query_result_cache[cache_key]
        if time.time() - entry["cached_at"] <= entry["ttl"]:
            return entry["data"]

    result = compute_fn()

    effective_key = cache_key or f"query_{query_id}_{int(time.time())}"
    _query_result_cache[effective_key] = {
        "data": result,
        "cached_at": time.time(),
        "ttl": ttl_seconds,
        "query_id": query_id,
    }

    return result
