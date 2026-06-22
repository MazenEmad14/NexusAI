import sys
from pathlib import Path
from fastapi import FastAPI
sys.path.insert(0, str(Path(__file__).resolve().parent))
from routes import base, data , nlp, agent
from motor.motor_asyncio import AsyncIOMotorClient
from helpers.config import get_settings
from stores.llm.LLMProviderFactory import LLMProviderFactory
from stores.vectordb.VectorDBProviderFactory import VectorDBProviderFactory
from stores.llm.templates.template_parser import TemplateParser
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Import new Secretary AI services
from stores.gmail.GmailService import GmailService
from stores.calendar.CalendarService import CalendarService
from controllers.agent_graph import build_agent_graph

app = FastAPI()

@app.on_event("startup")
async def startup_db_client():
    settings = get_settings()

    # 1. MongoDB Connection (for Secretary Collections)
    # Using default local connection for now if URL not in settings
    mongodb_url = getattr(settings, "MONGODB_URL", "mongodb://localhost:27017/")
    app.mongo_conn = AsyncIOMotorClient(mongodb_url)
    app.mongo_db = app.mongo_conn["depi"]

    # 2. PostgreSQL Connection (for existing RAG data if needed)
    postgres_conn = f"postgresql+asyncpg://{settings.POSTGRES_USERNAME}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_MAIN_DATABASE}"
    app.db_engine = create_async_engine(postgres_conn)
    app.db_client = sessionmaker(
        app.db_engine, class_=AsyncSession, expire_on_commit=False
    )
    
    llm_provider_factory = LLMProviderFactory(settings)
    vectordb_provider_factory = VectorDBProviderFactory(settings)

    # 3. Generation client
    app.generation_client = llm_provider_factory.create(provider=settings.GENERATION_BACKEND)
    app.generation_client.set_generation_model(model_id=settings.GENERATION_MODEL_ID)

    # 4. Embedding client
    app.embedding_client = llm_provider_factory.create(provider=settings.EMBEDDING_BACKEND)
    app.embedding_client.set_embedding_model(model_id=settings.EMBEDDING_MODEL_ID,
                                             embedding_size=settings.EMBEDDING_MODEL_SIZE)
    # 5. Vector DB client
    app.vectordb_client = vectordb_provider_factory.create(provider=settings.VECTOR_DB_BACKEND)
    app.vectordb_client.connect()
    
    # 6. Template Parser
    app.template_parser = TemplateParser(
        language=settings.PRIMARY_LANG,
        default_language=settings.DEFAULT_LANG)

    # 7. Google Services Authentication (Optional on startup, but recommended to load tokens)
    app.gmail_service = GmailService()
    # Try to load existing token silently
    if Path(app.gmail_service.token_path).exists():
        try:
            app.gmail_service.authenticate()
        except Exception:
            pass

    app.calendar_service = CalendarService()
    if Path(app.calendar_service.token_path).exists():
        try:
            app.calendar_service.authenticate()
        except Exception:
            pass

    # 8. Compile LangGraph Agent
    app.agent_graph = build_agent_graph(
        db_client=app.mongo_db,
        generation_client=app.generation_client,
        embedding_client=app.embedding_client,
        vectordb_client=app.vectordb_client,
        template_parser=app.template_parser,
        gmail_service=app.gmail_service,
        calendar_service=app.calendar_service
    )


@app.on_event("shutdown")
async def shutdown_db_client():
    app.mongo_conn.close()
    await app.db_engine.dispose()
    app.vectordb_client.disconnect()


app.include_router(base.base_router)
app.include_router(data.data_router)
app.include_router(nlp.nlp_router)
app.include_router(agent.agent_router)

