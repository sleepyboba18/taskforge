"""Shared bounded pagination parsing and response metadata."""

from __future__ import annotations

import math
from typing import Any

from werkzeug.datastructures import MultiDict

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def parse_pagination(args: MultiDict[str, Any]) -> tuple[int, int, dict[str, str]]:
    errors: dict[str, str] = {}
    parsed: dict[str, int] = {}
    for field, default in (("page", 1), ("per_page", DEFAULT_PER_PAGE)):
        values = args.getlist(field)
        if len(values) > 1:
            errors[field] = f"{field.replace('_', ' ').capitalize()} must be supplied once."
            continue
        try:
            parsed[field] = int(values[0]) if values else default
        except (TypeError, ValueError):
            errors[field] = f"{field.replace('_', ' ').capitalize()} must be an integer."

    page = parsed.get("page", 0)
    per_page = parsed.get("per_page", 0)
    if "page" not in errors and page < 1:
        errors["page"] = "Page must be at least 1."
    if "per_page" not in errors and not 1 <= per_page <= MAX_PER_PAGE:
        errors["per_page"] = f"Per page must be between 1 and {MAX_PER_PAGE}."
    return page, per_page, errors


def validate_query_parameters(
    args: MultiDict[str, Any], allowed: set[str]
) -> dict[str, str]:
    errors: dict[str, str] = {}
    unknown = set(args) - allowed
    if unknown:
        errors["query"] = "Unsupported query parameters: " + ", ".join(sorted(unknown))
    repeated = sorted(key for key in args if len(args.getlist(key)) > 1)
    if repeated:
        errors["query"] = "Query parameters may be supplied only once: " + ", ".join(repeated)
    return errors


def pagination_metadata(page: int, per_page: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": math.ceil(total / per_page) if total else 0,
    }