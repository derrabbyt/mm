import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supabase_user_id: uuid.UUID
    email: str | None
    display_name: str | None
    avatar_url: str | None
    profile_customized: bool
    providers: list[str]
    is_active: bool
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime
