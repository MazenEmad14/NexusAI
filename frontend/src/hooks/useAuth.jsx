import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { loadSession, onSessionExpired, signIn as apiSignIn, signOut as apiSignOut, signUp as apiSignUp } from '../services/api';

/**
 * Who is signed in.
 *
 * The session itself is in httpOnly cookies this code cannot read, so the page learns whether it has one by
 * asking the server (`/auth/local/me`) when it starts. status is "loading" until that answer arrives, then
 * "authenticated" or "anonymous".
 */
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState({ status: 'loading', user: null });

  useEffect(() => {
    let cancelled = false;
    loadSession().then((user) => {
      if (!cancelled) setAuth(user ? { status: 'authenticated', user } : { status: 'anonymous', user: null });
    });
    return () => { cancelled = true; };
  }, []);

  // The session could not be renewed while the app was in use: send the user back to sign in.
  useEffect(() => onSessionExpired(() => setAuth({ status: 'anonymous', user: null })), []);

  const signIn = useCallback(async (email, password) => {
    const result = await apiSignIn(email, password);
    setAuth({ status: 'authenticated', user: result.user });
    return result;
  }, []);

  const signUp = useCallback(async (name, email, password) => {
    const result = await apiSignUp(name, email, password);
    setAuth({ status: 'authenticated', user: result.user });
    return result;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiSignOut();
    } finally {
      setAuth({ status: 'anonymous', user: null });
    }
  }, []);

  const value = useMemo(() => ({ ...auth, signIn, signUp, signOut }), [auth, signIn, signUp, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>');
  return context;
}

/** Shows its children only to a signed-in user; everyone else goes to the sign-in page. */
export function RequireAuth({ children }) {
  const { status } = useAuth();
  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-[#0a0a0a] flex items-center justify-center text-white/40 text-sm">
        Loading…
      </div>
    );
  }
  if (status === 'anonymous') return <Navigate to="/auth" replace />;
  return children;
}
