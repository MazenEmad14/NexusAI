from .BaseController import BaseController
from .ProjectController import ProjectController
from fastapi import UploadFile
from helpers.file_types import allowed_extensions, content_matches, extension_of
from models import ResponseSignal
import os
import re

class DataController(BaseController):

    def __init__(self):
        super().__init__()
        self.size_scale = 1048576 # convert MB to bytes

    @property
    def max_upload_bytes(self) -> int:
        return self.app_settings.FILE_MAX_SIZE * self.size_scale

    def validate_uploaded_file(self, file: UploadFile):
        """Cheap checks before any bytes are read: accepted extension and declared size."""
        extension = extension_of(self.get_clean_file_name(file.filename or ""))
        if extension not in allowed_extensions(self.app_settings.FILE_ALLOWED_TYPES):
            return False, ResponseSignal.FILE_TYPE_NOT_SUPPORTED.value

        # The declared size is a convenience; the streaming byte count in the route is what enforces the limit.
        if file.size is not None and file.size > self.max_upload_bytes:
            return False, ResponseSignal.FILE_SIZE_EXCEEDED.value

        return True, ResponseSignal.FILE_VALIDATED_SUCCESS.value

    def validate_content(self, file_name: str, head: bytes):
        """The file's leading bytes must match its extension (not just a client-supplied header)."""
        if not head:
            return False, ResponseSignal.FILE_EMPTY.value
        if not content_matches(extension_of(file_name), head):
            return False, ResponseSignal.FILE_TYPE_NOT_SUPPORTED.value
        return True, ResponseSignal.FILE_VALIDATED_SUCCESS.value

    def generate_unique_filepath(self, orig_file_name: str, project_id: str):

        random_key = self.generate_random_string()
        project_path = ProjectController().get_project_path(project_id=project_id)

        cleaned_file_name = self.get_clean_file_name(
            orig_file_name=orig_file_name
        )

        new_file_path = os.path.join(
            project_path,
            random_key + "_" + cleaned_file_name
        )

        while os.path.exists(new_file_path):
            random_key = self.generate_random_string()
            new_file_path = os.path.join(
                project_path,
                random_key + "_" + cleaned_file_name
            )

        return new_file_path, random_key + "_" + cleaned_file_name

    def get_clean_file_name(self, orig_file_name: str):

        # spaces become underscores first; then everything except word characters and dots is dropped
        name = re.sub(r'[^\w.]', '', orig_file_name.strip().replace(" ", "_"))

        # a name made only of dots would be hidden or meaningless; keep the extension lower-case so
        # that ".PDF" is recognised everywhere the extension is checked
        stem, extension = os.path.splitext(name)
        return (stem.strip(".") or "file") + extension.lower()
