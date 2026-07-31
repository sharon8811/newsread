"""Instance-level roles shared by the API and management scripts.

Roles live on users.role and are the only authorization input for the
administration surface — the deployment mode picks feature defaults but never
grants access. 'owner' manages admins and instance settings, 'admin' accesses
administration features and manages regular users, 'user' is normal
application access.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_USER)
ADMIN_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})

STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUSES = (STATUS_ACTIVE, STATUS_SUSPENDED)


class FinalOwnerError(Exception):
    """Raised when a change would leave the instance without an active owner."""


async def _other_active_owners(session: AsyncSession, user: User) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == ROLE_OWNER,
                User.status == STATUS_ACTIVE,
                User.id != user.id,
            )
        )
    ) or 0


async def change_role(session: AsyncSession, user: User, role: str) -> None:
    """Set a user's role, refusing to demote the final active owner. The
    caller commits."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r} (expected one of {', '.join(ROLES)})")
    if (
        user.role == ROLE_OWNER
        and role != ROLE_OWNER
        and await _other_active_owners(session, user) == 0
    ):
        raise FinalOwnerError("cannot demote the only owner; promote another owner first")
    user.role = role


async def change_status(session: AsyncSession, user: User, status: str) -> None:
    """Set a user's account status, refusing to suspend the final active
    owner. The caller commits."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} (expected one of {', '.join(STATUSES)})")
    if (
        status == STATUS_SUSPENDED
        and user.role == ROLE_OWNER
        and await _other_active_owners(session, user) == 0
    ):
        raise FinalOwnerError("cannot suspend the only owner; promote another owner first")
    user.status = status
