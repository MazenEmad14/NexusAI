from pydantic import BaseModel, Field
from typing import Optional
class PushRequest(BaseModel):
    do_reset: Optional[bool] = False
    background: bool = False   # true: queue a job and answer 202 at once; follow it at /api/v1/jobs/{id}
    
class SearchRequest(BaseModel):
    text: str
    limit: Optional[int] = Field(default=5, gt=0)
