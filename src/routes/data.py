from fastapi import APIRouter, Depends, UploadFile, status, Request
from fastapi.responses import JSONResponse
import asyncio
import os
from helpers.config import get_settings, Settings
from helpers.services import services_of
from controllers import DataController, ProjectController, ProcessController
import aiofiles
from models import ResponseSignal
import logging
from helpers.auth_utils import get_current_user
from helpers.file_types import SNIFF_BYTES, is_indexable
from helpers.project_scope import scoped_project_dependency
from .schemes.data import ProcessRequest
from models.AssetModel import AssetModel
from models.db_schemes import Asset
from models.ProjectModel import ProjectModel
from services.documents import Outcome, process_project_files, resolve_files
from services.jobs import PROCESS_DOCUMENTS, submit_job
from models.enums.AssetTypeEnum import AssetTypeEnum

logger = logging.getLogger('uvicorn.error')

class UploadTooLarge(Exception):
    pass


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


data_router = APIRouter(
    prefix="/api/v1/data",
    tags=["api_v1", "data"],
    dependencies=[Depends(get_current_user)],
)

@data_router.post("/upload/{project_id}")
async def upload_data(request: Request, file: UploadFile,
                      project_id: str = Depends(scoped_project_dependency),
                      app_settings: Settings = Depends(get_settings)):
        
    
    project_model = await ProjectModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    # validate the file properties
    data_controller = DataController()

    is_valid, result_signal = data_controller.validate_uploaded_file(file=file)

    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": result_signal
            }
        )

    file_path, file_id = data_controller.generate_unique_filepath(
        orig_file_name=file.filename,
        project_id=project_id
    )

    # The leading bytes must match the extension; the declared Content-Type is client-controlled.
    head = await file.read(SNIFF_BYTES)
    is_valid, result_signal = data_controller.validate_content(file_id, head)
    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": result_signal}
        )

    # Stream to disk with a running byte count: file.size is optional and can be absent.
    max_bytes = data_controller.max_upload_bytes
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as f:
            chunk = head
            while chunk:
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge()
                await f.write(chunk)
                chunk = await file.read(app_settings.FILE_DEFAULT_CHUNK_SIZE)
    except UploadTooLarge:
        _remove_quietly(file_path)
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"signal": ResponseSignal.FILE_SIZE_EXCEEDED.value}
        )
    except Exception as e:
        logger.error(f"Error while uploading file: {e}")
        _remove_quietly(file_path)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": ResponseSignal.FILE_UPLOAD_FAILED.value}
        )

    # store the assets into the database
    asset_model = await AssetModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    asset_resource = Asset(
        asset_project_id=project.id,
        asset_type=AssetTypeEnum.FILE.value,
        asset_name=file_id,
        asset_size=os.path.getsize(file_path)
    )

    try:
        asset_record = await asset_model.create_asset(asset=asset_resource)
    except Exception:
        # Without its record the file would be orphaned on disk.
        logger.error("Could not save the asset record", exc_info=True)
        _remove_quietly(file_path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"signal": ResponseSignal.FILE_UPLOAD_FAILED.value}
        )

    return JSONResponse(
            content={
                "signal": ResponseSignal.FILE_UPLOAD_SUCCESS.value,
                "file_id": str(asset_record.id),
                "file_path": file_id,
                # Only text and PDF are chunked into the knowledge base; spreadsheets feed data analysis.
                "indexable": is_indexable(file_id),
            }
        )

@data_router.post("/process/{project_id}")
async def process_endpoint(request: Request, process_request: ProcessRequest,
                           project_id: str = Depends(scoped_project_dependency),
                           current_user: dict = Depends(get_current_user)):
    services = services_of(request)
    settings = get_settings()

    if process_request.background:
        # Check the request now, so a wrong file name is an immediate 400 and not a job that fails later.
        resolved = await resolve_files(services.mongo_db, project_id, process_request.file_id)
        if isinstance(resolved, Outcome):
            return JSONResponse(status_code=resolved.status, content=resolved.body)
        job, created = await submit_job(
            services.mongo_db, settings, PROCESS_DOCUMENTS, str(current_user["_id"]), project_id,
            {"file_id": process_request.file_id, "chunk_size": process_request.chunk_size,
             "overlap_size": process_request.overlap_size, "do_reset": process_request.do_reset})
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"job": job, "already_queued": not created})

    outcome = await process_project_files(
        services.mongo_db, project_id, process_request.file_id, process_request.chunk_size,
        process_request.overlap_size, process_request.do_reset)
    return JSONResponse(status_code=outcome.status, content=outcome.body)
