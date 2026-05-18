from pydantic import BaseModel, Field


class PaginationMeta(BaseModel):
    cursor: str | None = None
    has_more: bool = False


class PaginationParams(BaseModel):
    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
