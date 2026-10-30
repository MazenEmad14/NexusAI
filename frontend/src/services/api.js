/**
 * NexusAI — Production API Service Layer
 *
 * Handles all communication with the FastAPI backend.
 * Features: retry logic, abort controllers, error normalization.
 */

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000';

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 800;

/* ──────────────────── helpers ──────────────────── */

class ApiError extends Error {
  constructor(message, status, data = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Core request function with retry + abort support.
 * @param {string} endpoint
 * @param {RequestInit & { retries?: number, signal?: AbortSignal }} options
 * @returns {Promise<any>}
 */
async function request(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const retries = options.retries ?? MAX_RETRIES;

  const headers = { ...options.headers };
  const token = localStorage.getItem("jwt_token");
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // Don't set Content-Type for FormData (browser sets boundary automatically)
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }

  const config = {
    ...options,
    headers,
  };

  let lastError = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, config);

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ error: res.statusText }));
        // FastAPI validation errors put a list of {msg} objects in `detail`.
        const detail = Array.isArray(errBody.detail)
          ? errBody.detail.map(d => d.msg).filter(Boolean).join('; ')
          : errBody.detail;
        const msg = errBody.error || errBody.message || detail || `HTTP ${res.status}`;

        // Don't retry 4xx client errors (except 429)
        if (res.status >= 400 && res.status < 500 && res.status !== 429) {
          // A 401 from the sign-in / sign-up call means "wrong credentials", not "session expired":
          // redirecting would reload the page and wipe the error message the user needs to see.
          if (res.status === 401 && !endpoint.startsWith('/api/v1/auth/local/')) {
            localStorage.removeItem("jwt_token");
            window.location.href = '/auth';
          }
          throw new ApiError(msg, res.status, errBody);
        }

        // For 5xx: set lastError and continue retry loop
        lastError = new ApiError(msg, res.status, errBody);
        if (attempt < retries) {
          await sleep(RETRY_DELAY_MS * Math.pow(2, attempt));
        }
        continue;
      }

      const contentType = res.headers.get('content-type') || '';
      if (options.responseType === 'blob') {
        return await res.blob();
      }
      if (contentType.includes('application/json')) {
        return await res.json();
      }
      return { data: await res.text() };
    } catch (err) {
      // Don't retry aborted requests
      if (err.name === 'AbortError') {
        throw err;
      }

      lastError = err;

      // Don't retry client errors (they won't succeed on retry)
      if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 429) {
        throw err;
      }

      // Retry with exponential backoff
      if (attempt < retries) {
        await sleep(RETRY_DELAY_MS * Math.pow(2, attempt));
      }
    }
  }

  throw lastError || new ApiError('Request failed after retries', 0);
}

/* ──────────────────── Agent Chat ──────────────────── */

/**
 * Send a chat message to the Secretary AI agent.
 * @param {string} query — the user's message
 * @param {string} projectId — project context
 * @param {string} threadId — conversation thread id
 * @param {AbortSignal} [signal] — optional abort signal
 * @returns {Promise<{response, type, metadata, status, thread_id, action_details}>}
 */
export async function sendChatMessage(query, projectId = 'default', threadId = 'default_thread', signal, filePath = null) {
  return request('/api/v1/agent/chat', {
    method: 'POST',
    body: JSON.stringify({
      query,
      project_id: projectId,
      thread_id: threadId,
      file_path: filePath,
    }),
    signal,
  });
}

/**
 * Stream a chat message response from the Secretary AI agent.
 * @param {string} query 
 * @param {string} projectId 
 * @param {string} threadId 
 * @param {Function} onChunk 
 * @param {Function} onAction 
 * @param {Function} onMetadata 
 * @param {AbortSignal} [signal] 
 * @param {string|null} filePath
 */
export async function streamChatMessage(query, projectId = 'default', threadId = 'default_thread', onChunk, onAction, onMetadata, signal, filePath = null) {
  const url = `${API_BASE}/api/v1/agent/chat/stream`;
  
  const headers = {
    'Content-Type': 'application/json',
  };
  const token = localStorage.getItem("jwt_token");
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      query,
      project_id: projectId,
      thread_id: threadId,
      file_path: filePath,
    }),
    signal,
  });

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem("jwt_token");
      window.location.href = '/auth';
    }
    const errBody = await response.json().catch(() => ({ error: response.statusText }));
    throw new ApiError(errBody.error || `HTTP ${response.status}`, response.status, errBody);
  }

  if (!response.body) {
    throw new Error('Response body is null — streaming not supported in this environment');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    
    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      if (!chunk) continue;
      
      if (chunk.startsWith('data: ')) {
        const dataStr = chunk.slice(6);
        if (dataStr === '[DONE]') {
          return;
        }
        
        try {
          const parsed = JSON.parse(dataStr);
          if (parsed.type === 'token') {
            if (onChunk) onChunk(parsed.content);
          } else if (parsed.type === 'pending_action') {
            if (onAction) onAction(parsed.action_details, parsed.action_type, parsed.metadata);
          } else if (parsed.type === 'metadata') {
            if (onMetadata) onMetadata(parsed.metadata, parsed.chart_data, parsed.status, parsed.action_type, parsed.request_type);
          } else if (parsed.type === 'error') {
            throw new Error(parsed.content);
          }
        } catch (e) {
          // Re-throw intentional backend errors; swallow JSON parse noise
          if (e.name !== 'SyntaxError') {
            throw e;
          }
          if (e.message !== 'Unexpected end of JSON input') {
            console.error('Failed to parse SSE data:', e, dataStr);
          }
        }
      }
    }
  }
}

/**
 * Resume or reject a pending human-in-the-loop action.
 * @param {string} threadId
 * @param {boolean} approve
 * @param {AbortSignal} [signal]
 * @returns {Promise<{response, type, status, thread_id}>}
 */
export async function resumeAction(threadId, approve, signal) {
  return request('/api/v1/agent/resume', {
    method: 'POST',
    body: JSON.stringify({
      thread_id: threadId,
      approve,
    }),
    signal,
  });
}

/**
 * Convert text to speech.
 * @param {string} text 
 * @param {AbortSignal} [signal]
 * @returns {Promise<string>} An object URL pointing to the audio blob
 */
export async function generateSpeech(text, signal) {
  const blob = await request('/api/v1/agent/tts', {
    method: 'POST',
    body: JSON.stringify({ text }),
    signal,
    responseType: 'blob',
    retries: 1,
  });
  return URL.createObjectURL(blob);
}

/**
 * Convert speech to text.
 * @param {Blob} audioBlob 
 * @param {AbortSignal} [signal]
 * @returns {Promise<string>} The transcribed text
 */
export async function transcribeAudio(audioBlob, signal) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'recording.webm');
  
  const response = await request('/api/v1/agent/stt', {
    method: 'POST',
    body: formData,
    signal,
  });
  return response.text;
}

/* ──────────────────── File Upload & Processing ──────────────────── */

/**
 * Upload a file to the knowledge base.
 * @param {File} file
 * @param {string} projectId
 * @returns {Promise<{signal, file_id}>}
 */
export async function uploadFile(file, projectId = 'default') {
  const formData = new FormData();
  formData.append('file', file);

  return request(`/api/v1/data/upload/${projectId}`, {
    method: 'POST',
    body: formData,
    retries: 1, // Don't retry large uploads multiple times
  });
}

/**
 * Process uploaded files (chunking).
 * @param {string} projectId
 * @param {object} options
 * @returns {Promise<{signal, inserted_chunks, processed_files}>}
 */
export async function processFiles(projectId = 'default', options = {}) {
  return request(`/api/v1/data/process/${projectId}`, {
    method: 'POST',
    body: JSON.stringify({
      file_id: options.fileId || null,
      chunk_size: options.chunkSize || 512,
      overlap_size: options.overlapSize || 128,
      do_reset: options.doReset || false,
    }),
  });
}

/* ──────────────────── NLP / RAG ──────────────────── */

/**
 * Push/index chunks into the vector DB.
 */
export async function pushIndex(projectId, doReset = false) {
  return request(`/api/v1/nlp/index/push/${projectId}`, {
    method: 'POST',
    body: JSON.stringify({ do_reset: doReset }),
  });
}

/**
 * Search the vector DB index.
 */
export async function searchIndex(projectId, text, limit = 5) {
  return request(`/api/v1/nlp/index/search/${projectId}`, {
    method: 'POST',
    body: JSON.stringify({ text, limit }),
  });
}

/**
 * Get a RAG answer from the knowledge base.
 */
export async function ragAnswer(projectId, text, limit = 5) {
  return request(`/api/v1/nlp/index/answer/${projectId}`, {
    method: 'POST',
    body: JSON.stringify({ text, limit }),
  });
}

/**
 * Get vector DB collection info.
 */
export async function getIndexInfo(projectId) {
  return request(`/api/v1/nlp/index/info/${projectId}`, {
    method: 'GET',
  });
}

/* ──────────────────── Dashboard Endpoints ──────────────────── */

/**
 * Fetch pending tasks from MongoDB.
 * @param {string} projectId
 * @param {AbortSignal} [signal]
 */
export async function fetchTasks(projectId = 'default', signal) {
  return request(`/api/v1/tasks?project_id=${encodeURIComponent(projectId)}`, {
    method: 'GET',
    signal,
  });
}

/**
 * Fetch reminders from MongoDB.
 * @param {string} projectId
 * @param {AbortSignal} [signal]
 */
export async function fetchReminders(projectId = 'default', signal) {
  return request(`/api/v1/reminders?project_id=${encodeURIComponent(projectId)}`, {
    method: 'GET',
    signal,
  });
}

/**
 * Fetch upcoming calendar events from Google Calendar.
 * @param {AbortSignal} [signal]
 */
export async function fetchCalendarEvents(signal) {
  return request('/api/v1/calendar/events', {
    method: 'GET',
    signal,
  });
}

/**
 * Generate and fetch a daily summary briefing.
 * @param {string} projectId
 * @param {AbortSignal} [signal]
 */
export async function fetchDailySummary(projectId = 'default', signal) {
  return request(`/api/v1/summary/daily?project_id=${encodeURIComponent(projectId)}`, {
    method: 'GET',
    signal,
    retries: 1, // Summary generation can be slow, don't overretry
  });
}

export async function getGoogleAuthUrl() {
  const currentOrigin = encodeURIComponent(window.location.origin);
  return request(`/api/v1/auth/google/login?origin=${currentOrigin}`, { method: 'GET' });
}

export async function checkGoogleAuthStatus() {
  return request('/api/v1/auth/google/status');
}

/**
 * Open the per-user notifications stream.
 * EventSource cannot send an Authorization header, so a one-time ticket is
 * requested first and passed as a query parameter.
 * @returns {Promise<EventSource>}
 */
export async function openNotificationStream() {
  const { ticket } = await request('/api/v1/agent/notifications/ticket', { method: 'POST', retries: 0 });
  return new EventSource(`${API_BASE}/api/v1/agent/notifications?ticket=${encodeURIComponent(ticket)}`);
}

/**
 * Tell the server which time zone the user is in, so "today", "tomorrow" and the 8am briefing
 * are theirs. Sent only when it changed since the last time on this browser.
 */
export async function syncTimezone() {
  let zone;
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return;
  }
  if (!zone) return;
  try {
    if (localStorage.getItem('tz_synced') === zone) return;
  } catch { /* storage unavailable: just send it */ }
  try {
    await request('/api/v1/auth/local/timezone', { method: 'PUT', body: JSON.stringify({ timezone: zone }), retries: 0 });
    try { localStorage.setItem('tz_synced', zone); } catch { /* ignore */ }
  } catch {
    // Not critical: the server falls back to its default zone.
  }
}

export async function signIn(email, password) {
  return request('/api/v1/auth/local/signin', {
    method: 'POST',
    body: JSON.stringify({ email, password })
  });
}

export async function signUp(name, email, password) {
  return request('/api/v1/auth/local/signup', {
    method: 'POST',
    body: JSON.stringify({ name, email, password })
  });
}

/* ──────────────────── Health Check ──────────────────── */

export async function healthCheck() {
  try {
    await request('/api/v1', { method: 'GET', retries: 0 });
    return true;
  } catch {
    return false;
  }
}

export async function servicesHealthCheck() {
  try {
    const res = await request('/api/v1/health/services', { method: 'GET', retries: 0 });
    return res?.data || { database: 'unknown', gmail: 'unknown', calendar: 'unknown' };
  } catch {
    return { database: 'error', gmail: 'error', calendar: 'error' };
  }
}
