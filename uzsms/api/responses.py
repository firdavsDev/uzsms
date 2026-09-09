"""The uzsms API's uniform response envelope.

Owner decision D2 (binding): every response body this API produces —
success or failure — has exactly the keys ``success``, ``data``, and
``error``. This module is the ONE place that envelope is constructed; no
view or exception handler may build that dict inline.
"""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response


def success_response(data: dict[str, Any], status_code: int) -> Response:
    """Build a success envelope carrying ``data``, with ``error`` set to ``None``."""
    return Response({"success": True, "data": data, "error": None}, status=status_code)


def error_response(
    code: str,
    message: str,
    detail: Any = None,
    *,
    status_code: int,
) -> Response:
    """Build a failure envelope carrying an ``error`` object, with ``data`` set to ``None``."""
    return Response(
        {
            "success": False,
            "data": None,
            "error": {"code": code, "message": message, "detail": detail},
        },
        status=status_code,
    )
