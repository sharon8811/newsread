"""Shared FastAPI dependency aliases.

Every protected endpoint used to spell out the same two defaulted params;
these Annotated aliases replace that pair wholesale:

    async def endpoint(user: CurrentUser, session: DbSession): ...

Note Annotated params carry no default, so they must precede any defaulted
params (Query/Body/etc.) in endpoint signatures.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import User
from .security import get_current_user

DbSession = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
