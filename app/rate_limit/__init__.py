"""PostgreSQL-backed API rate limiting."""

from app.rate_limit.decorators import rate_limit

__all__ = ["rate_limit"]
