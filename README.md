# Mini RAG App

Mini RAG App is a FastAPI backend for experimenting with Retrieval-Augmented Generation (RAG).
It lets you upload text or PDF files, split them into chunks, store metadata in MongoDB, index embeddings in Qdrant, then search or generate answers from the indexed content.

## Features

- Upload files per project.
- Supports `text/plain` and `application/pdf`.
- Split uploaded files into chunks with configurable chunk size and overlap.
- Store projects, assets, and chunks in MongoDB.
- Store embeddings in a local Qdrant vector database.
- Supports multiple LLM providers:
  - OpenAI
  - Cohere
  - Groq
- Semantic search and RAG answer endpoints.

## Project Structure

```text
mini-RAG-APP/
|-- src/                    # FastAPI backend
|   |-- controllers/        # File processing and NLP logic
|   |-- helpers/            # Application settings
|   |-- models/             # Data models and database schemas
|   |-- routes/             # FastAPI routes
|   |-- stores/
|   |   |-- llm/            # LLM providers
|   |   `-- vectordb/       # Vector database provider
|   |-- .env.example        # Environment variables example
|   |-- main.py             # FastAPI entry point
|   `-- requirements.txt
|-- frontend/               # React (Vite) frontend application
|-- docker/                 # Docker Compose configurations (e.g., MongoDB)
|-- tests/                  # Pytest unit and integration tests
|-- docs/                   # Additional documentation
|-- backup_script.py        # Utility script for backups
|-- check_mongo.py          # Utility script to verify MongoDB connection
|-- pytest.ini              # Pytest configuration
`-- README.md
```

## Requirements

- Python 3.10 or newer.
- Node.js (for running the React frontend).
- MongoDB running locally or remotely (can be run via Docker in `docker/`).
- API key for the LLM provider you want to use.
- Git and a terminal.

## Installation

Clone the repository and enter the project directory:

```bash
git clone https://github.com/ahmedokasha74/mini-RAG-APP.git
cd mini-RAG-APP
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r src/requirements.txt
```

## Environment Setup

Copy the example environment file:

```bash
copy src\.env.example src\.env
```

On Linux/macOS:

```bash
cp src/.env.example src/.env
```

Then update `src/.env` with your own values.

Example:

```env
APP_NAME="mini-RAG"
APP_VERSION="0.1"

FILE_ALLOWED_TYPES="text/plain,application/pdf"
FILE_MAX_SIZE=10
FILE_DEFAULT_CHUNK_SIZE=512000

MONGODB_URL="mongodb://admin:admin@localhost:27007/?authSource=admin"
MONGODB_DATABASE="mini-rag"

GENERATION_BACKEND="OPENAI"
EMBEDDING_BACKEND="OPENAI"

OPENAI_API_KEY="your-openai-api-key"
OPENAI_API_URL=""
COHERE_API_KEY=""
GROQ_API_KEY=""

GENERATION_MODEL_ID="gpt-4o-mini"
EMBEDDING_MODEL_ID="text-embedding-3-small"
EMBEDDING_MODEL_SIZE=1536

INPUT_DAFAULT_MAX_CHARACTERS=1000
GENERATION_DAFAULT_MAX_TOKENS=1000
GENERATION_DAFAULT_TEMPERATURE=0.1

VECTOR_DB_BACKEND="QDRANT"
VECTOR_DB_PATH="src/assets/database/qdrant_db"
VECTOR_DB_DISTANCE_METHOD="cosine"
```

Do not commit `src/.env` to GitHub because it may contain API keys and connection credentials.

## Running the App

### Backend
```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 5000
```

After startup:

- API root: `http://localhost:5000/api/v1/`
- Swagger docs: `http://localhost:5000/docs`
- ReDoc: `http://localhost:5000/redoc`

### Frontend
In a new terminal, run the following to start the React UI:

```bash
cd frontend
npm install
npm run dev
```

## Usage Flow

Use the same `project_id` across all steps.

### 1. Check the API

```bash
curl http://localhost:5000/api/v1/
```

### 2. Upload a File

Windows:

```bash
curl -X POST "http://localhost:5000/api/v1/data/upload/demo-project" ^
  -F "file=@sample.pdf"
```

Linux/macOS:

```bash
curl -X POST "http://localhost:5000/api/v1/data/upload/demo-project" \
  -F "file=@sample.pdf"
```

The response returns a `file_id` that can be used in the processing step.

### 3. Process the File

Windows:

```bash
curl -X POST "http://localhost:5000/api/v1/data/process/demo-project" ^
  -H "Content-Type: application/json" ^
  -d "{\"file_id\":\"YOUR_FILE_ID\",\"chunk_size\":100,\"overlap_size\":20,\"do_reset\":true}"
```

Linux/macOS:

```bash
curl -X POST "http://localhost:5000/api/v1/data/process/demo-project" \
  -H "Content-Type: application/json" \
  -d '{"file_id":"YOUR_FILE_ID","chunk_size":100,"overlap_size":20,"do_reset":true}'
```

You can omit `file_id` to process all files in the project.

### 4. Index Data in Qdrant

Windows:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/push/demo-project" ^
  -H "Content-Type: application/json" ^
  -d "{\"do_reset\":true}"
```

Linux/macOS:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/push/demo-project" \
  -H "Content-Type: application/json" \
  -d '{"do_reset":true}'
```

### 5. Get Collection Info

```bash
curl http://localhost:5000/api/v1/nlp/index/info/demo-project
```

### 6. Search the Index

Windows:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/search/demo-project" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"Write your question here\",\"limit\":5}"
```

Linux/macOS:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/search/demo-project" \
  -H "Content-Type: application/json" \
  -d '{"text":"Write your question here","limit":5}'
```

### 7. Generate a RAG Answer

Windows:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/answer/demo-project" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"Write your question here\",\"limit\":5}"
```

Linux/macOS:

```bash
curl -X POST "http://localhost:5000/api/v1/nlp/index/answer/demo-project" \
  -H "Content-Type: application/json" \
  -d '{"text":"Write your question here","limit":5}'
```

## API Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/api/v1/` | Return application name and version |
| POST | `/api/v1/data/upload/{project_id}` | Upload a file |
| POST | `/api/v1/data/process/{project_id}` | Process files and create chunks |
| POST | `/api/v1/nlp/index/push/{project_id}` | Index chunks in Qdrant |
| GET | `/api/v1/nlp/index/info/{project_id}` | Get project collection info |
| POST | `/api/v1/nlp/index/search/{project_id}` | Run semantic search |
| POST | `/api/v1/nlp/index/answer/{project_id}` | Generate a RAG answer |

## Notes

- MongoDB must be running before starting the app.
- Qdrant is used as a local file-based vector database through `qdrant-client`.
- Runtime files inside `src/assets/database/` should not be committed.
- `src/.env` should not be committed.
- If you use Groq for generation, use another provider for embeddings because the current Groq provider does not implement embeddings.

## License

This project is intended for learning and experimentation.
