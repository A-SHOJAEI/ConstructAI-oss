from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.models.user import User
from app.schemas.user import NotificationPreferences, UserResponse, UserUpdate
from app.services.user import update_user

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_endpoint(
    current_user: User = Depends(require_permission("users", "read")),
):
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    update_data: UserUpdate,
    current_user: User = Depends(get_current_user),  # Any authenticated user can update own profile
    db: AsyncSession = Depends(get_db),
):
    updated = await update_user(
        db,
        current_user,
        full_name=update_data.full_name,
        settings=update_data.settings,
    )
    return updated


@router.get("/me/notification-preferences", response_model=NotificationPreferences)
async def get_notification_preferences(
    current_user: User = Depends(require_permission("users", "read")),
):
    """Get current user's notification preferences from their settings."""
    prefs = (current_user.settings or {}).get("notifications", {})
    return NotificationPreferences(**prefs)


@router.patch("/me/notification-preferences", response_model=NotificationPreferences)
async def update_notification_preferences(
    prefs: NotificationPreferences,
    current_user: User = Depends(get_current_user),  # Any user can update own prefs
    db: AsyncSession = Depends(get_db),
):
    """Update current user's notification preferences."""
    settings = dict(current_user.settings or {})
    settings["notifications"] = prefs.model_dump()
    updated = await update_user(db, current_user, settings=settings)
    return NotificationPreferences(**updated.settings.get("notifications", {}))
