from pydantic import BaseModel, Field
from typing import Optional

class ProcessRequest(BaseModel):
    file_id: str = None
    chunk_size: Optional[int] = Field(default=300, gt=0)
    overlap_size: Optional[int] = Field(default=50, ge=0)
    do_reset: Optional[bool] = False
