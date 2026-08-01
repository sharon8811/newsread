"""Set a user's instance role (owner/admin/user) from the server shell.

The bootstrap path for hosted deployments and installations that predate
roles: public signup never mints an owner there, so the operator promotes
their own account once, from a shell they already control:

    cd backend && PYTHONPATH=. .venv/bin/python scripts/set_role.py \
        --user you@example.com --role owner

Demoting the only active owner is refused; promote a replacement first.
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, or_, select

from app.db import SessionLocal, init_db
from app.models import User
from app.roles import ROLES, FinalOwnerError, change_role


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="email or username of the account to change")
    parser.add_argument("--role", choices=ROLES, help="role to assign")
    parser.add_argument("--list", action="store_true", help="list owner/admin accounts and exit")
    args = parser.parse_args()
    if not args.list and not (args.user and args.role):
        parser.error("--user and --role are required (or use --list)")
    return args


async def run(args: argparse.Namespace) -> int:
    await init_db()
    async with SessionLocal() as session:
        if args.list:
            admins = (
                await session.scalars(
                    select(User).where(User.role != "user").order_by(User.role, User.id)
                )
            ).all()
            if not admins:
                print("no owner or admin accounts yet")
            for user in admins:
                print(f"{user.role:<5}  {user.username} <{user.email}>  status={user.status}")
            return 0

        identifier = args.user.strip().lower()
        user = await session.scalar(
            select(User).where(
                or_(
                    func.lower(User.email) == identifier,
                    func.lower(User.username) == identifier,
                )
            )
        )
        if user is None:
            print(f"no user matches {args.user!r}", file=sys.stderr)
            return 1
        if user.role == args.role:
            print(f"{user.username} already has role {args.role}")
            return 0
        try:
            await change_role(session, user, args.role)
        except FinalOwnerError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        await session.commit()
        print(f"{user.username} <{user.email}> is now {args.role}")
        return 0


async def main() -> None:
    sys.exit(await run(parse_args()))


if __name__ == "__main__":
    asyncio.run(main())
