"""Named authorization groups for API routes."""

from app.models import UserRole

OPERATORS = (UserRole.ADMIN, UserRole.OPERATOR)
AUTHENTICATED = (UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER)
ADMINS = (UserRole.ADMIN,)
