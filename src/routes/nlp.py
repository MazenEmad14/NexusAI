import asyncio
from fastapi import APIRouter, Depends, status, Request
from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.services import services_of
from helpers.project_scope import scoped_project_dependency
from fastapi.responses import JSONResponse
from .schemes.nlp import PushRequest, SearchRequest
from controllers import NLPController
import logging
from models import ResponseSignal
from models.ProjectModel import ProjectModel
from services.documents import index_project_chunks
from services.jobs import INDEX_DOCUMENTS, submit_job

logger = logging.getLogger('uvicorn.error')

nlp_router = APIRouter(
    prefix="/api/v1/nlp",
    tags=["api_v1"],
    dependencies=[Depends(get_current_user)],
)


# -----------------------------
# INDEX DATA INTO VECTOR DB
# -----------------------------
@nlp_router.post("/index/push/{project_id}")
async def index_project(request: Request, push_request: PushRequest,
                        project_id: str = Depends(scoped_project_dependency),
                        current_user: dict = Depends(get_current_user)):
    services = services_of(request)

    if push_request.background:
        job, created = await submit_job(
            services.mongo_db, get_settings(), INDEX_DOCUMENTS, str(current_user["_id"]), project_id,
            {"do_reset": push_request.do_reset})
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"job": job, "already_queued": not created})

    outcome = await index_project_chunks(services, project_id, push_request.do_reset)
    return JSONResponse(status_code=outcome.status, content=outcome.body)


# -----------------------------
# GET COLLECTION INFO
# -----------------------------
@nlp_router.get("/index/info/{project_id}")
async def get_project_index_info(request: Request, project_id: str = Depends(scoped_project_dependency)):

    project_model = await ProjectModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=services_of(request).vectordb_client,
        generation_client=services_of(request).generation_client,
        embedding_client=services_of(request).embedding_client,
        template_parser=services_of(request).template_parser,
    )

    collection_info = await asyncio.to_thread(nlp_controller.get_vector_db_collection_info, project=project)

    # ✅ تحويل الـ object إلى JSON
    return {
        "signal": ResponseSignal.VECTORDB_COLLECTION_RETRIEVED.value,
        "collection_info": collection_info.model_dump()
    }

@nlp_router.post("/index/search/{project_id}")
async def search_index(request: Request, search_request: SearchRequest, project_id: str = Depends(scoped_project_dependency)):
    
    project_model = await ProjectModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=services_of(request).vectordb_client,
        generation_client=services_of(request).generation_client,
        embedding_client=services_of(request).embedding_client,
        template_parser=services_of(request).template_parser,
    )

    results = await asyncio.to_thread(
        nlp_controller.search_vector_db_collection,
        project=project, text=search_request.text, limit=search_request.limit
    )

    if not results:
        return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": ResponseSignal.VECTORDB_SEARCH_ERROR.value
                }
            )
    
    formatted_results = [
        result.model_dump() if hasattr(result, "model_dump") else result.dict()
        for result in results
    ]

    return JSONResponse(
        content={
            "signal": ResponseSignal.VECTORDB_SEARCH_SUCCESS.value,
            "results": formatted_results
        }
    )

@nlp_router.post("/index/answer/{project_id}")
async def answer_rag(request: Request, search_request: SearchRequest, project_id: str = Depends(scoped_project_dependency)):
    
    project_model = await ProjectModel.create_instance(
        db_client=services_of(request).mongo_db
    )

    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )

    nlp_controller = NLPController(
        vectordb_client=services_of(request).vectordb_client,
        generation_client=services_of(request).generation_client,
        embedding_client=services_of(request).embedding_client,
        template_parser=services_of(request).template_parser,
    )

    answer, full_prompt, chat_history = await asyncio.to_thread(
        nlp_controller.answer_rag_question,
        project=project,
        query=search_request.text,
        limit=search_request.limit,
    )

    if not answer:
        return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": ResponseSignal.RAG_ANSWER_ERROR.value
                }
        )
    
    return JSONResponse(
        content={
            "signal": ResponseSignal.RAG_ANSWER_SUCCESS.value,
            "answer": answer,
            "full_prompt": full_prompt,
            "chat_history": chat_history
        }
    )
