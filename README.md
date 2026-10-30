# NexusAI — Autonomous AI Secretary

NexusAI is a powerful, multi-agent AI secretary powered by LangGraph. It is designed to act as an autonomous assistant capable of managing your inbox, scheduling meetings, querying knowledge bases, and interacting via voice—all orchestrated through an intelligent, proactive backend.

## Features

NexusAI integrates multiple sophisticated capabilities into a single cohesive platform:

- **Multi-Agent Intelligence**: Built on LangGraph, the core agent can route tasks, make decisions, and use tools dynamically.
- **Proactive Inbox Management**: Automatically monitors your Gmail inbox, evaluates incoming emails, and triggers notifications.
- **Gmail & Calendar Automation**: Connects with your Google account to read/send emails and manage calendar events.
- **RAG Knowledge Base**: Upload documents (Text, PDF), index them in Qdrant, and perform semantic search and grounded generation.
- **Voice Interaction**: Built-in Support for Speech-to-Text (STT) and Text-to-Speech (TTS) for conversational interfaces.
- **Web Search**: Dynamic web searching capabilities to answer questions using real-time information.
- **Notification Engine**: A robust notification system that alerts the frontend via WebSockets/polling.
- **CSV Data Agent**: Specialized agent to query and extract insights from CSV data.

## Project Structure

```text
mini-RAG-APP/ (NexusAI Workspace)
|-- src/                    # FastAPI backend
|   |-- controllers/        # Agent logic (LangGraph, Proactive Agent)
|   |-- services/           # Core services (Gmail, Calendar, Notifications, RAG)
|   |-- helpers/            # Application settings
|   |-- models/             # Data models and database schemas
|   |-- routes/             # FastAPI endpoints (Agent, Auth, RAG, etc.)
|   |-- stores/             # Data access layers (LLMs, Vector DBs, Gmail, Calendar)
|   |-- .env.example        # Environment variables example
|   |-- main.py             # FastAPI entry point
|   |-- requirements.txt        (runtime dependencies)
|   |-- requirements.lock       (every package pinned)
|   |-- requirements-dev.txt    (adds the test tools)
|   `-- requirements-voice.txt  (optional speech-to-text)
|-- frontend/               # React (Vite) frontend application
|-- docker/                 # Docker Compose configurations (e.g., MongoDB)
|-- tests/                  # Pytest unit and E2E integration tests
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
- API key for the LLM provider you want to use (OpenAI, Cohere, Groq).
- Google Cloud Project credentials (`credentials.json`) for Gmail and Calendar integration.
- Git and a terminal.

## Installation & Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/ahmedokasha74/mini-RAG-APP.git
   cd mini-RAG-APP
   ```

2. **Set up the Python backend:**

   Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/macOS
   source .venv/bin/activate
   ```

   Install dependencies (Python 3.10):

   ```bash
   pip install -r src/requirements.txt -c src/requirements.lock
   ```

   `src/requirements.txt` lists what the backend uses; `src/requirements.lock` pins every package
   those pull in, so everyone gets the same versions. Optional extras:

   ```bash
   pip install -r src/requirements-voice.txt -c src/requirements.lock   # speech-to-text (installs PyTorch, large)
   pip install -r src/requirements-dev.txt -c src/requirements.lock     # tests
   ```

   Without the voice extra, `POST /api/v1/agent/stt` answers 503 and everything else works.

   **Updating dependencies.** Edit the version in `src/requirements.txt`, then rebuild the lock in a
   throw-away environment so it reflects a clean install:

   ```bash
   python -m venv /tmp/lockenv
   /tmp/lockenv/bin/pip install -r src/requirements-dev.txt        # Windows: Scripts\pip
   /tmp/lockenv/bin/pip check                                      # must report no conflicts
   /tmp/lockenv/bin/python -m pytest src/tests                     # must pass
   /tmp/lockenv/bin/pip freeze | sort -f > /tmp/freeze.txt         # then replace the lines under the header of src/requirements.lock
   ```

   Do not install into your global Python: it accumulates unrelated packages whose requirements
   conflict with this project's.

3. **Environment Setup:**

   Copy the example environment file:

   ```bash
   # Windows
   copy src\.env.example src\.env
   # Linux/macOS
   cp src/.env.example src/.env
   ```

   Update `src/.env` with your API keys, MongoDB URI, and preferred models.

4. **Google Credentials (Optional but required for Gmail/Calendar):**
   Place your Google `credentials.json` file inside the `src/` directory. On startup, the app will request authorization to generate `token.json` and `calendar_token.json`.

## Running the App

### Backend (FastAPI & Agent Engine)

Start the backend server (which also boots up the background notification engine and proactive inbox scheduler):

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 5000
```
*(Or simply run `./src/start.sh` on Linux/macOS)*

After startup:
- API root: `http://localhost:5000/api/v1/`
- Swagger docs: `http://localhost:5000/docs`

### Frontend (React UI)

In a new terminal, run the following to start the NexusAI UI:

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`.

## Core Workflows

### AI Agent Chat
Interact with NexusAI via the `/api/v1/agent/` endpoints or directly from the frontend dashboard. The agent dynamically decides whether to search the web, read your emails, check your calendar, or query your uploaded knowledge base.

### RAG (Knowledge Base)
You can upload files (`text/plain` or `application/pdf`) to a specific `project_id`. The system chunks the text, computes embeddings using the configured LLM, and stores them in Qdrant for the agent to query during conversations.

### Proactive Secretary
Once authenticated with Google, NexusAI runs a background scheduler (every 1 minute) to evaluate your latest unread emails. It categorizes them and sends proactive notifications to the frontend if immediate action or awareness is required.

## Testing

Run the end-to-end integration test suite using pytest:

```bash
pytest tests/
```

## License

This project is intended for learning and experimentation.
