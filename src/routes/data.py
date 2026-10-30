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
from models.ProjectModel import ProjectModel
from models.ChunkModel import ChunkModel
from models.AssetModel import AssetModel
from models.db_schemes import DataChunk, Asset
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
async def process_endpoint(request: Request, process_request: ProcessRequest, project_id: str = Depends(scoped_project_dependency)):

    chunk_size = process_request.chunk_size
    overlap_size = process_request.overlap_size
    do_reset = process_request.do_reset

    project_model = await ProjectModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    asset_model = await AssetModel.create_instance(
            db_client=services_of(request).mongo_db
        )

    project_files_ids = {}
    if process_request.file_id:
        asset_record = await asset_model.get_asset_record_by_id(
            asset_project_id=project.id,
            asset_id=process_request.file_id
        )

        if asset_record is None:
            asset_record = await asset_model.get_asset_record(
                asset_project_id=project.id,
                asset_name=process_request.file_id
            )

        if asset_record is None:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": ResponseSignal.FILE_ID_ERROR.value,
                }
            )

        project_files_ids = {
            asset_record.id: asset_record.asset_name
        }
    
    else:
        

        project_files = await asset_model.get_all_project_assets(
            asset_project_id=project.id,
            asset_type=AssetTypeEnum.FILE.value,
        )

        project_files_ids = {
            record.id: record.asset_name
            for record in project_files
        }

    if len(project_files_ids) == 0:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.NO_FILES_ERROR.value,
            }
        )
    
    process_controller = ProcessController(project_id=project_id)

    chunk_model = await ChunkModel.create_instance(
                        db_client=services_of(request).mongo_db
                    )

    if do_reset:
        _ = await chunk_model.delete_chunks_by_project_id(
            project_id=project.id
        )

    results = []
    no_records = 0
    no_files = 0

    for asset_id, file_id in project_files_ids.items():
        result = {"file": file_id}
        results.append(result)

        if not is_indexable(file_id):
            # Spreadsheets are for data analysis, not for the knowledge base: not an error.
            result.update(status="skipped", reason="not_indexable")
            continue

        try:
            # Parsing and splitting are CPU/disk bound: keep them off the event loop.
            file_content = await asyncio.to_thread(process_controller.get_file_content, file_id)
            if file_content is None:
                result.update(status="failed", reason="unreadable")
                continue
            file_chunks = await asyncio.to_thread(
                process_controller.process_file_content,
                file_content=file_content,
                file_id=file_id,
                chunk_size=chunk_size,
                overlap_size=overlap_size,
            )
        except Exception:
            logger.error(f"Error while processing file: {file_id}", exc_info=True)
            result.update(status="failed", reason="unreadable")
            continue

        if not file_chunks:
            # For example a scanned PDF with no text layer.
            result.update(status="failed", reason="no_text")
            continue

        file_chunks_records = [
            DataChunk(
                chunk_text=chunk.page_content,
                chunk_metadata=chunk.metadata,
                chunk_order=i+1,
                chunk_project_id=project.id,
                chunk_asset_id=asset_id
            )
            for i, chunk in enumerate(file_chunks)
        ]

        # Processing a file again replaces its chunks instead of duplicating them.
        await chunk_model.delete_chunks_by_asset_id(project_id=project.id, asset_id=asset_id)
        inserted = await chunk_model.insert_many_chunks(chunks=file_chunks_records)

        result.update(status="processed", chunks=inserted)
        no_records += inserted
        no_files += 1

    # One bad file no longer rolls back or hides the others; only "nothing worked" is an error.
    if no_files == 0 and any(r["status"] == "failed" for r in results):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.PROCESSING_FAILED.value,
                "results": results,
            }
        )

    return JSONResponse(
        content={
            "signal": ResponseSignal.PROCESSING_SUCCESS.value,
            "inserted_chunks": no_records,
            "processed_files": no_files,
            "results": results,
        }
    )
