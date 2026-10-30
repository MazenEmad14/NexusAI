from .BaseController import BaseController
from fastapi import UploadFile
from models import ResponseSignal
import os

class ProjectController(BaseController):
    
    def __init__(self):
        super().__init__()

    def get_project_path(self, project_id: str):
        # Defense in depth: the id must be a plain name and stay inside files_dir.
        if not project_id.isalnum():
            raise ValueError("Invalid project id")
        project_dir = os.path.join(
            self.files_dir,
            project_id
        )
        if os.path.commonpath([os.path.abspath(project_dir), os.path.abspath(self.files_dir)]) != os.path.abspath(self.files_dir):
            raise ValueError("Invalid project path")

        if not os.path.exists(project_dir):
            os.makedirs(project_dir)

        return project_dir

    
