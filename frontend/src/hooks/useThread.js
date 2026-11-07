import { useState, useCallback, useMemo } from 'react';

// The conversation list is kept per person. It used to live under one key for the whole browser, so whoever
// signed in next saw the previous person's conversation titles.
const LEGACY_KEYS = ['nexusai_sessions', 'nexusai_active_session'];

export function storageKeys(owner) {
  const who = encodeURIComponent((owner || 'anonymous').toLowerCase());
  return { sessions: `nexusai_sessions:${who}`, active: `nexusai_active_session:${who}` };
}

/** Remove the shared lists an older version left behind: there is no telling whose they were. */
function dropLegacyKeys() {
  try {
    LEGACY_KEYS.forEach((key) => localStorage.removeItem(key));
  } catch {
    /* storage unavailable */
  }
}

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
function loadSessions(keys) {
  try {
    const raw = localStorage.getItem(keys.sessions);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore parse errors */
  }
  return null;
}

/**
 * Save sessions to localStorage.
 */
function saveSessions(keys, sessions) {
  try {
    localStorage.setItem(keys.sessions, JSON.stringify(sessions));
  } catch {
    /* storage full or unavailable: the list just is not remembered */
  }
}

/**
 * Load active session ID from localStorage.
 */
function loadActiveSessionId(keys) {
  try {
    return localStorage.getItem(keys.active) || null;
  } catch {
    return null;
  }
}

/**
 * Save active session ID to localStorage.
 */
function saveActiveSessionId(keys, id) {
  try {
    localStorage.setItem(keys.active, id);
  } catch {
    /* ignore */
  }
}

/**
 * useThread — the signed-in person's conversation list, kept in localStorage under their own key.
 *
 * Each "session" maps to a { id, title, threadId }. The messages themselves live on the server under the
 * thread id (the agent's checkpoint), and are loaded when a conversation is opened.
 *
 * @param {string} owner  who is signed in (their email); the list is theirs alone
 */
export function useThread(owner) {
  const keys = useMemo(() => storageKeys(owner), [owner]);

  // Initialize from localStorage or create a fresh first session
  const [sessions, setSessionsState] = useState(() => {
    dropLegacyKeys();
    const saved = loadSessions(keys);
    if (saved && saved.length > 0) return saved;

    const firstSession = {
      id: generateSessionId(),
      title: 'New Conversation',
      threadId: generateThreadId(),
    };
    saveSessions(keys, [firstSession]);
    saveActiveSessionId(keys, firstSession.id);
    return [firstSession];
  });

  const [activeSessionId, setActiveSessionIdState] = useState(() => {
    const saved = loadActiveSessionId(keys);
    if (saved && sessions.some((s) => s.id === saved)) return saved;
    return sessions[0]?.id || '';
  });

  // Derive current thread ID
  const activeSession = sessions.find(s => s.id === activeSessionId) || sessions[0];
  const threadId = activeSession?.threadId || generateThreadId();

  const setSessions = useCallback((updater) => {
    setSessionsState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      saveSessions(keys, next);
      return next;
    });
  }, [keys]);

  const setActiveSessionId = useCallback((id) => {
    setActiveSessionIdState(id);
    saveActiveSessionId(keys, id);
  }, [keys]);

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
