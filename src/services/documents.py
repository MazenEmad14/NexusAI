"""Turning a project's uploaded files into searchable chunks, and chunks into vectors.

These are the two slow jobs of the knowledge base. They are plain functions of the database and the model
clients, so the same code runs inside a request (the default) or in a background worker (`background: true`).
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from controllers import NLPController, ProcessController
from helpers.file_types import is_indexable
from models import ResponseSignal
from models.AssetModel import AssetModel
from models.ChunkModel import ChunkModel
from models.ProjectModel import ProjectModel
from models.db_schemes import DataChunk
from models.enums.AssetTypeEnum import AssetTypeEnum

logger = logging.getLogger("uvicorn.error")

# async def progress(done, total): reports how far a long job is
Progress = Optional[Callable[[int, int], Awaitable[None]]]


@dataclass
class Outcome:
    """What a job produced: the HTTP status it would have as a response, and the JSON body."""
    status: int
    body: dict

    @property
    def ok(self) -> bool:
        return self.status < 400


async def resolve_files(db, project_id: str, file_id: Optional[str]):
    """The project and the {asset id: file name} to process, or an `Outcome` describing why there is nothing to do."""
    project = await (await ProjectModel.create_instance(db_client=db)).get_project_or_create_one(project_id=project_id)
    asset_model = await AssetModel.create_instance(db_client=db)

    if file_id:
        record = await asset_model.get_asset_record_by_id(asset_project_id=project.id, asset_id=file_id)
        if record is None:
            record = await asset_model.get_asset_record(asset_project_id=project.id, asset_name=file_id)
        if record is None:
            return Outcome(400, {"signal": ResponseSignal.FILE_ID_ERROR.value})
        files = {record.id: record.asset_name}
    else:
        records = await asset_model.get_all_project_assets(
            asset_project_id=project.id, asset_type=AssetTypeEnum.FILE.value)
        files = {record.id: record.asset_name for record in records}

    if not files:
        return Outcome(400, {"signal": ResponseSignal.NO_FILES_ERROR.value})
    return project, files


async def process_project_files(db, project_id: str, file_id: Optional[str], chunk_size: int, overlap_size: int,
                                do_reset: bool, progress: Progress = None) -> Outcome:
    """Read, split and store the chunks of one file or of every file of the project."""
    resolved = await resolve_files(db, project_id, file_id)
    if isinstance(resolved, Outcome):
        return resolved
    project, files = resolved

    process_controller = ProcessController(project_id=project_id)
    chunk_model = await ChunkModel.create_instance(db_client=db)

    if do_reset:
        await chunk_model.delete_chunks_by_project_id(project_id=project.id)

    results, no_records, no_files = [], 0, 0

    for position, (asset_id, name) in enumerate(files.items()):
        if progress:
            await progress(position, len(files))
        result = {"file": name}
        results.append(result)

        if not is_indexable(name):
            # Spreadsheets are for data analysis, not for the knowledge base: not an error.
            result.update(status="skipped", reason="not_indexable")
            continue

        try:
            # Parsing and splitting are CPU/disk bound: keep them off the event loop.
            file_content = await asyncio.to_thread(process_controller.get_file_content, name)
            if file_content is None:
                result.update(status="failed", reason="unreadable")
                continue
            file_chunks = await asyncio.to_thread(
                process_controller.process_file_content,
                file_content=file_content, file_id=name, chunk_size=chunk_size, overlap_size=overlap_size,
            )
        except Exception:
            logger.error(f"Error while processing file: {name}", exc_info=True)
            result.update(status="failed", reason="unreadable")
            continue

        if not file_chunks:
            # For example a scanned PDF with no text layer.
            result.update(status="failed", reason="no_text")
            continue

        records = [
            DataChunk(
                chunk_text=chunk.page_content, chunk_metadata=chunk.metadata, chunk_order=i + 1,
                chunk_project_id=project.id, chunk_asset_id=asset_id,
            )
            for i, chunk in enumerate(file_chunks)
        ]

        # Processing a file again replaces its chunks instead of duplicating them.
        await chunk_model.delete_chunks_by_asset_id(project_id=project.id, asset_id=asset_id)
        inserted = await chunk_model.insert_many_chunks(chunks=records)

        result.update(status="processed", chunks=inserted)
        no_records += inserted
        no_files += 1

    if progress:
        await progress(len(files), len(files))

    # One bad file no longer rolls back or hides the others; only "nothing worked" is an error.
    if no_files == 0 and any(r["status"] == "failed" for r in results):
        return Outcome(400, {"signal": ResponseSignal.PROCESSING_FAILED.value, "results": results})

    return Outcome(200, {
        "signal": ResponseSignal.PROCESSING_SUCCESS.value,
        "inserted_chunks": no_records,
        "processed_files": no_files,
        "results": results,
    })


async def index_project_chunks(services: Any, project_id: str, do_reset: bool, progress: Progress = None) -> Outcome:
    """Embed the project's chunks and push them into the vector database.

    `services` is anything with `mongo_db`, `vectordb_client`, `generation_client`, `embedding_client` and
    `template_parser` (the API's `Services`, or a worker's `JobContext`).
    """
    project = await (await ProjectModel.create_instance(db_client=services.mongo_db)).get_project_or_create_one(
        project_id=project_id)
    if not project:
        return Outcome(400, {"signal": ResponseSignal.PROJECT_NOT_FOUND_ERROR.value})

    chunk_model = await ChunkModel.create_instance(db_client=services.mongo_db)
    nlp_controller = NLPController(
        vectordb_client=services.vectordb_client,
        generation_client=services.generation_client,
        embedding_client=services.embedding_client,
        template_parser=services.template_parser,
    )

    page_no, inserted_items_count = 1, 0
    while True:
        page_chunks = await chunk_model.get_project_chunks(project_id=project.id, page_no=page_no)
        if not page_chunks:
            break

        is_inserted = await asyncio.to_thread(
            nlp_controller.index_into_vector_db,
            project=project, chunks=page_chunks, do_reset=do_reset if page_no == 1 else False,
        )
        page_no += 1

        if not is_inserted:
            return Outcome(400, {"signal": ResponseSignal.INSERT_INTO_VECTORDB_ERROR.value})

        inserted_items_count += len(page_chunks)
        if progress:
            await progress(inserted_items_count, inserted_items_count)  # the total is not known up front

    return Outcome(200, {
        "signal": ResponseSignal.INSERT_INTO_VECTORDB_SUCCESS.value,
        "inserted_items_count": inserted_items_count,
    })
