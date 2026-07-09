import { useState, useCallback, useRef } from 'react';

const STORAGE_KEY_SESSIONS = 'nexusai_sessions';
const STORAGE_KEY_ACTIVE = 'nexusai_active_session';

/**
 * Generate a UUID-v4-style thread ID.
 */
function generateThreadId() {
  return 'thread_' + crypto.randomUUID();
}

/**
 * Generate a session ID.
 */
function generateSessionId() {
  return 'session_' + crypto.randomUUID();
}

/**
 * Load sessions from localStorage.
 */
function loadSessions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_SESSIONS);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore parse errors */
  }
  return null;
}

/**
 * Save sessions to localStorage.
 */
function saveSessions(sessions) {
  localStorage.setItem(STORAGE_KEY_SESSIONS, JSON.stringify(sessions));
}

/**
 * Load active session ID from localStorage.
 */
function loadActiveSessionId() {
  return localStorage.getItem(STORAGE_KEY_ACTIVE) || null;
}

/**
 * Save active session ID to localStorage.
 */
function saveActiveSessionId(id) {
  localStorage.setItem(STORAGE_KEY_ACTIVE, id);
}

/**
 * useThread — manages thread persistence via localStorage.
 *
 * Each "session" maps to a { id, title, threadId }.
 * Thread IDs are persisted so the LangGraph MemorySaver can resume context.
 */
export function useThread() {
  // Initialize from localStorage or create a fresh first session
  const [sessions, setSessionsState] = useState(() => {
    const saved = loadSessions();
    if (saved && saved.length > 0) return saved;

    const firstSession = {
      id: generateSessionId(),
      title: 'New Conversation',
      threadId: generateThreadId(),
    };
    saveSessions([firstSession]);
    saveActiveSessionId(firstSession.id);
    return [firstSession];
  });

  const [activeSessionId, setActiveSessionIdState] = useState(() => {
    const saved = loadActiveSessionId();
    if (saved) return saved;
    return sessions[0]?.id || '';
  });

  // Derive current thread ID
  const activeSession = sessions.find(s => s.id === activeSessionId) || sessions[0];
  const threadId = activeSession?.threadId || generateThreadId();

  const setSessions = useCallback((updater) => {
    setSessionsState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      saveSessions(next);
      return next;
    });
  }, []);

  const setActiveSessionId = useCallback((id) => {
    setActiveSessionIdState(id);
    saveActiveSessionId(id);
  }, []);

  /**
   * Create a new chat session with a fresh thread ID.
   * Returns the new session object.
   */
  const createNewSession = useCallback(() => {
    const newSession = {
      id: generateSessionId(),
      title: 'New Conversation',
      threadId: generateThreadId(),
    };

    setSessions(prev => [newSession, ...prev]);
    setActiveSessionId(newSession.id);

    return newSession;
  }, [setSessions, setActiveSessionId]);

  /**
   * Update the title of a session (e.g., from the first user message).
   */
  const updateSessionTitle = useCallback((sessionId, title) => {
    setSessions(prev =>
      prev.map(s => (s.id === sessionId ? { ...s, title } : s))
    );
  }, [setSessions]);

  /**
   * Switch to an existing session.
   */
  const switchSession = useCallback((sessionId) => {
    setActiveSessionId(sessionId);
  }, [setActiveSessionId]);

  return {
    sessions,
    activeSessionId,
    activeSession,
    threadId,
    createNewSession,
    updateSessionTitle,
    switchSession,
  };
}
