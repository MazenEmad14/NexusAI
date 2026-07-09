import { useState, useCallback, useRef } from 'react';

const MAX_TOASTS = 3;
let toastCounter = 0;

/**
 * useToast — lightweight toast notification manager.
 *
 * Usage:
 *   const { toasts, showSuccess, showError, showInfo, dismissToast } = useToast();
 */
export function useToast() {
  const [toasts, setToasts] = useState([]);
  const timersRef = useRef({});

  const dismiss = useCallback((id) => {
    clearTimeout(timersRef.current[id]);
    delete timersRef.current[id];
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((message, variant = 'info', duration = 4000) => {
    const id = ++toastCounter;

    setToasts(prev => {
      const next = [...prev, { id, message, variant }];
      // Keep max visible
      return next.length > MAX_TOASTS ? next.slice(-MAX_TOASTS) : next;
    });

    // Auto-dismiss
    timersRef.current[id] = setTimeout(() => dismiss(id), duration);

    return id;
  }, [dismiss]);

  const showSuccess = useCallback((msg) => addToast(msg, 'success'), [addToast]);
  const showError = useCallback((msg) => addToast(msg, 'error', 6000), [addToast]);
  const showInfo = useCallback((msg) => addToast(msg, 'info'), [addToast]);

  return {
    toasts,
    showSuccess,
    showError,
    showInfo,
    dismissToast: dismiss,
  };
}
