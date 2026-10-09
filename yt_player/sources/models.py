from typing import Optional

from pydantic import BaseModel, Field

from yt_player.player.models import Track


class SearchRequest(BaseModel):
    query: str
    count: int = Field(default=10, ge=1, le=50)
    bluetooth_device_id: Optional[str] = None
    requested_by: Optional[str] = None


class SearchResponse(BaseModel):
    results: list[Track]
