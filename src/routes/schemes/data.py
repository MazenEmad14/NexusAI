from pydantic import BaseModel
from typing import Optional

class ProcessRequest(BaseModel):
    file_id: str = None
    chunk_size: Optional[int] = 300
    overlap_size: Optional[int] = 50
    do_reset: Optional[bool] = False
