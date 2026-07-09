from pydantic import BaseModel, Field
from typing import Optional
from typing import List
class PushRequest(BaseModel):
    do_reset: Optional[bool] = False
    
class SearchRequest(BaseModel):
    text: str
    limit: Optional[int] = Field(default=5, gt=0)
