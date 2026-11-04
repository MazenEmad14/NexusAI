"""Find a file the user uploaded, inside their own project folder only."""
import os
from typing import Optional

from controllers import ProjectController
from helpers.project_scope import scoped_project_id


def resolve_project_file(user_id: str, project_id: str, requested: str, extensions: Optional[set] = None) -> Optional[str]:
    """Map a requested file name to a file inside the caller's own project folder.

    Only the final path component of the requested value is used, so neither the client nor the LLM can
    point the server at any other location. With `extensions` (lower-case, with the dot), other types are refused.
    """
    name = os.path.basename((requested or "").replace("\\", "/"))
    if not name or (extensions is not None and os.path.splitext(name)[1].lower() not in extensions):
        return None
    try:
        project_dir = ProjectController().get_project_path(scoped_project_id(user_id, project_id))
    except ValueError:
        return None
    root = os.path.realpath(project_dir)
    full = os.path.realpath(os.path.join(project_dir, name))
    if os.path.commonpath([full, root]) != root:
        return None
    return full if os.path.isfile(full) else None
