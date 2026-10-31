"""Follow the background jobs a user started."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from helpers.auth_utils import get_current_user
from helpers.services import services_of
from models.JobModel import JobModel

jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@jobs_router.get("")
async def list_jobs(request: Request, limit: int = Query(20, ge=1, le=100), current_user: dict = Depends(get_current_user)):
    """The caller's most recent jobs, newest first."""
    jobs = await JobModel(services_of(request).mongo_db).list_for_user(str(current_user["_id"]), limit)
    return {"jobs": [JobModel.public(job) for job in jobs]}


@jobs_router.get("/{job_id}")
async def get_job(job_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """One job's status, progress and result. Someone else's job is reported as not found."""
    job = await JobModel(services_of(request).mongo_db).get_for_user(job_id, str(current_user["_id"]))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobModel.public(job)
