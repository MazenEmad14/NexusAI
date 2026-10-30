# Backend Code Review: NexusAI Performance & Hangs

This report outlines the potential causes for the application "hanging" (freezing/stopping responding) when using the AI agent chat, Gmail, Calendar, or RAG features. These issues typically stem from blocking the FastAPI event loop, missing timeouts, or synchronous CPU-heavy work in async endpoints.

## 🔴 Critical Priority

### 1. Synchronous CPU/IO Bound Work in Async Endpoints (STT/TTS)
- **File**: `src/routes/agent.py`
- **Lines**: 552, 565
- **Description**: The endpoints `/stt` and `/tts` are declared as `async def`, but they perform purely synchronous, heavy operations. `whisper_model.transcribe(tmp_path)` and `tts.write_to_fp(fp)` block the main event loop completely, causing the entire FastAPI application to hang for all users until the transcription/audio generation is done.
- **Why it causes a hang**: FastAPI uses a single event loop. Synchronous execution in an async endpoint blocks the loop, preventing any other request from being served.
- **Suggested Fix**: Offload these tasks to a separate thread using `await asyncio.to_thread()`. 
  ```python
  result = await asyncio.to_thread(whisper_model.transcribe, tmp_path)
  ```

### 2. Synchronous Embedding/Indexing inside `while True` Loop
- **File**: `src/routes/nlp.py`
- **Lines**: 54-67
- **Description**: Inside the `async def index_project` endpoint, there is a `while True:` loop that fetches chunks and then calls `nlp_controller.index_into_vector_db(...)`. The `index_into_vector_db` method performs synchronous LLM API calls and VectorDB insertions.
- **Why it causes a hang**: This blocks the async event loop for the entirety of the project indexing (which could be minutes depending on file size).
- **Suggested Fix**: Execute the indexing call in a thread pool:
  ```python
  is_inserted = await asyncio.to_thread(nlp_controller.index_into_vector_db, project, page_chunks, ...)
  ```

### 3. Synchronous LLM Provider SDK usage in LangGraph
- **File**: `src/stores/llm/providers/OpenAIProvider.py`
- **Description**: The application wraps LLM calls (OpenAI, Groq) using standard synchronous methods (`generate_text`, `embed_text`). Since LangGraph is invoked via `await agent_graph.ainvoke()`, the underlying tools and node actions will block the event loop if they make synchronous HTTP requests without using `asyncio.to_thread`.
- **Why it causes a hang**: During LLM streaming or generation, the entire FastAPI event loop is held hostage by the synchronous HTTP request waiting for the LLM response.
- **Suggested Fix**: Refactor LLM providers to use the official Async clients (e.g., `AsyncOpenAI`), or ensure that LangGraph nodes wrapping these providers dispatch them via `await asyncio.to_thread()`.

## 🟠 High Priority

### 4. Hardcoded `time.sleep()` in Gmail Integration
- **File**: `src/stores/gmail/GmailService.py`
- **Lines**: 217
- **Description**: In the `send_email` method, there is a retry mechanism that catches an exception and explicitly calls `time.sleep(1)`.
- **Why it causes a hang**: If `send_email` is triggered by the AI Agent running inside the main asyncio loop, `time.sleep(1)` will block the whole application for 1 second.
- **Suggested Fix**: Use `asyncio.sleep(1)` if the method can be made async, or ensure `send_email` is always executed in a separate thread.

### 5. Notification Engine Blocks on Daily Summary Generation
- **File**: `src/services/notification_engine.py`
- **Lines**: 49
- **Description**: The background polling task `check_once()` linearly `await`s the `DailySummaryService.generate()` function. Generating a summary involves multiple LLM and database queries.
- **Why it causes a hang**: While the daily summary is being generated, the proactive scheduler stops checking for urgent emails, causing a pile-up or delay in notifications.
- **Suggested Fix**: Fire and forget the daily summary task using `asyncio.create_task()`.
  ```python
  asyncio.create_task(summary_service.generate(project_id="default"))
  ```

## 🟡 Medium Priority

### 6. Missing Timeouts on External Requests
- **File**: `src/tests/run_advanced_tests.py` and various integration files.
- **Description**: Using HTTP requests or client SDK calls without explicit `timeout=` parameters.
- **Why it causes a hang**: If an external service (like Groq, OpenAI, or Google APIs) stops responding but doesn't drop the connection, the system will wait indefinitely, leading to zombie threads and memory exhaustion.
- **Suggested Fix**: Enforce strict timeouts (e.g., `timeout=30.0` seconds) on all outgoing network calls, including those via the `google-api-python-client`.

### 7. Swallowed Exceptions
- **File**: `src/services/retrieval.py` (Line 27), `src/routes/dashboard.py` (Line 42)
- **Description**: Empty `except Exception:` blocks exist that suppress errors completely. 
- **Why it causes a hang**: If an operation fails silently in the middle of a LangGraph node or a critical flow, the flow may not return a proper response to the frontend, leaving the client waiting forever for an HTTP response or WebSocket message.
- **Suggested Fix**: Always explicitly log the exception `logger.error("...", exc_info=True)` and either re-raise it or return a safe error state that the router handles.

## 🟢 Low Priority

### 8. `while True` loop in Event Generators
- **File**: `src/routes/agent.py` (Line 583)
- **Description**: The SSE endpoint `notifications_endpoint` runs an infinite loop `while True:`. While it correctly uses `await asyncio.wait_for`, a client disconnecting ungracefully might not break the loop immediately.
- **Suggested Fix**: Monitor `await request.is_disconnected()` robustly, but it's generally well-handled here by the `is_disconnected()` check at the start of the loop.

---
### Summary
- **Critical**: 3
- **High**: 2
- **Medium**: 2
- **Low**: 1

Total Issues Found: 8
