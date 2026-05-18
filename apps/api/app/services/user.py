import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE_FIELDS = {"full_name", "settings", "notification_preferences"}


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def update_user(db: AsyncSession, user: User, **kwargs) -> User:
    for key, value in kwargs.items():
        if key not in _ALLOWED_UPDATE_FIELDS:
            logger.warning(
                "Rejected disallowed field %r in update_user for user %s",
                key,
                user.id,
            )
            continue
        if value is not None:
            setattr(user, key, value)
    await db.flush()
    await db.refresh(user)
    return user
