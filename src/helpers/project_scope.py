"""Per-user project namespacing.

A project id coming from a client is only a *name*; the id used for storage
(Mongo project record, upload folder, vector collection) is that name scoped to
the logged-in user, so users can never read or overwrite each other's projects.
"""
import re

from fastapi import Depends, HTTPException

from helpers.auth_utils import get_current_user

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


def scoped_project_id(user_id: str, project_name: str) -> str:
    """Return the storage id for a user's project (alphanumeric, as Project requires)."""
    if not user_id:
        raise ValueError("user_id is required")
    if not _PROJECT_NAME_RE.fullmatch(project_name or ""):
        raise ValueError("project_id must be 1-64 alphanumeric characters")
    return f"u{user_id}p{project_name}"


async def scoped_project_dependency(project_id: str, current_user: dict = Depends(get_current_user)) -> str:
    """FastAPI dependency: turn the `{project_id}` path parameter into the caller's scoped id."""
    try:
        return scoped_project_id(str(current_user["_id"]), project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
