from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from yt_player.core.time import utc_now


class EventMessage(BaseModel):
    type: str
    data: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)
