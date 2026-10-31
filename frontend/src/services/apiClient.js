/**
 * The one place that talks HTTP to the backend and knows about authentication.
 *
 * The session lives in httpOnly cookies that this code cannot read (so a script injected into the page cannot
 * steal it). Every request is sent with `credentials: 'include'`. What this code does handle:
 *
 *  - CSRF: requests that change something echo a csrf token (kept in memory, received at sign-in) in a header;
 *  - expiry: when a call answers 401 it renews the session once, silently (concurrent calls share that one
 *    renewal) and repeats the call; if renewal fails it reports the session as expired;
 *  - errors: one way of turning a failed response into a readable ApiError.
 *
 * It takes its dependencies (fetch, base URL, callbacks) as arguments so it can be tested without a browser.
 */

export class ApiError extends Error {
  constructor(message, status, data = null, requestId = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
    this.requestId = requestId; // quote this to support: it finds the log lines of the failed request
  }
}

/** VITE_API_BASE wins; in development the local backend; in a production build the same origin as the page. */
export function resolveApiBase(env = {}) {
  const base = env.VITE_API_BASE ?? (env.DEV ? 'http://localhost:5000' : '');
  return base.replace(/\/+$/, '');
}

/** The readable text of an error response body (FastAPI puts validation errors in a `detail` list). */
export function errorMessageFrom(body, status) {
  const detail = Array.isArray(body?.detail)
    ? body.detail.map(d => d?.msg).filter(Boolean).join('; ')
    : body?.detail;
  return body?.error || body?.message || detail || `HTTP ${status}`;
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const AUTH_PREFIX = '/api/v1/auth/local/';
const ME_PATH = `${AUTH_PREFIX}me`;
const REFRESH_PATH = `${AUTH_PREFIX}refresh`;

const defaultSleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

export function createApiClient({
  baseUrl = '',
  fetchImpl = (...args) => fetch(...args),
  onSessionExpired = () => {},
  sleep = defaultSleep,
  maxRetries = 3,
  retryDelayMs = 800,
} = {}) {
  let csrfToken = null;
  let refreshInFlight = null;
  let csrfInFlight = null;

  const methodOf = (init) => (init?.method || 'GET').toUpperCase();

  function rawFetch(path, init = {}) {
    const headers = { ...init.headers };
    if (UNSAFE_METHODS.has(methodOf(init)) && csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
    return fetchImpl(`${baseUrl}${path}`, { ...init, headers, credentials: 'include' });
  }

  function rememberCsrf(body) {
    if (body?.csrf_token) csrfToken = body.csrf_token;
  }

  /** Renew the session with the refresh cookie. Several callers at once share one request. */
  function refreshSession() {
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        try {
          const res = await rawFetch(REFRESH_PATH, { method: 'POST' });
          if (!res.ok) return false;
          rememberCsrf(await res.json().catch(() => null));
          return true;
        } catch {
          return false;
        } finally {
          refreshInFlight = null;
        }
      })();
    }
    return refreshInFlight;
  }

  /** After a page reload the csrf token is gone from memory; the server hands it back with the session. */
  function loadCsrfToken() {
    if (!csrfInFlight) {
      csrfInFlight = (async () => {
        try {
          let res = await rawFetch(ME_PATH);
          if (res.status === 401 && await refreshSession()) return; // the refresh response carries the token
          if (res.ok) rememberCsrf(await res.json().catch(() => null));
        } catch {
          /* the request itself will report the problem */
        } finally {
          csrfInFlight = null;
        }
      })();
    }
    return csrfInFlight;
  }

  async function isCsrfRejection(res) {
    if (res.status !== 403) return false;
    const body = await res.clone().json().catch(() => null);
    return typeof body?.detail === 'string' && body.detail.toLowerCase().includes('csrf');
  }

  /** fetch with the session handled: csrf header, one silent renewal on 401, one csrf reload on 403. */
  async function fetchWithAuth(path, init = {}, attempt = {}) {
    const unsafe = UNSAFE_METHODS.has(methodOf(init));
    const isAuthRoute = path.startsWith(AUTH_PREFIX);
    if (unsafe && !csrfToken && !isAuthRoute) await loadCsrfToken();

    const res = await rawFetch(path, init);

    // A 401 from sign-in / sign-up means "wrong credentials", not "the session expired": no renewal, no redirect.
    if (res.status === 401 && !isAuthRoute) {
      if (!attempt.renewed && await refreshSession()) {
        return fetchWithAuth(path, init, { ...attempt, renewed: true });
      }
      onSessionExpired();
      return res;
    }

    // Another tab renewed the session, which rotated the csrf cookie: fetch the new token and try again once.
    if (unsafe && !isAuthRoute && !attempt.csrfReloaded && await isCsrfRejection(res)) {
      csrfToken = null;
      await loadCsrfToken();
      if (csrfToken) return fetchWithAuth(path, init, { ...attempt, csrfReloaded: true });
    }
    return res;
  }

  async function errorFrom(res) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    return new ApiError(errorMessageFrom(body, res.status), res.status, body, body?.request_id || res.headers?.get?.('x-request-id') || null);
  }

  const isClientError = (status) => status >= 400 && status < 500 && status !== 429;

  /**
   * JSON request with retries (5xx, 429 and network errors; never other 4xx) and abort support.
   * @param {string} endpoint
   * @param {RequestInit & { retries?: number, signal?: AbortSignal, responseType?: 'blob' }} options
   */
  async function request(endpoint, options = {}) {
    const retries = options.retries ?? maxRetries;
    const headers = { ...options.headers };
    // Don't set Content-Type for FormData (the browser adds the boundary)
    if (!(typeof FormData !== 'undefined' && options.body instanceof FormData)) {
      headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    }
    const { retries: _retries, responseType, ...init } = options;

    let lastError = null;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await fetchWithAuth(endpoint, { ...init, headers });
        if (!res.ok) {
          const error = await errorFrom(res);
          if (isClientError(res.status)) throw error;
          lastError = error;
        } else {
          if (responseType === 'blob') return await res.blob();
          const contentType = res.headers.get('content-type') || '';
          return contentType.includes('application/json') ? await res.json() : { data: await res.text() };
        }
      } catch (err) {
        if (err.name === 'AbortError') throw err; // never retry an abort
        lastError = err;
        if (err instanceof ApiError && isClientError(err.status)) throw err;
      }
      if (attempt < retries) await sleep(retryDelayMs * Math.pow(2, attempt));
    }
    throw lastError || new ApiError('Request failed after retries', 0);
  }

  /** For streaming responses: returns the Response once it is known to be OK (errors are thrown as ApiError). */
  async function stream(endpoint, init = {}) {
    const res = await fetchWithAuth(endpoint, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init.headers },
    });
    if (!res.ok) throw await errorFrom(res);
    return res;
  }

  /** Who is signed in, if anyone. Renews an expired session first; null means "not signed in". */
  async function loadSession() {
    try {
      let res = await rawFetch(ME_PATH);
      if (res.status === 401 && await refreshSession()) res = await rawFetch(ME_PATH);
      if (!res.ok) return null;
      const body = await res.json();
      rememberCsrf(body);
      return body.user || null;
    } catch {
      return null;
    }
  }

  return {
    request,
    stream,
    loadSession,
    setCsrfToken: (token) => { csrfToken = token || null; },
    clearSession: () => { csrfToken = null; },
    // exposed for tests
    _csrfToken: () => csrfToken,
  };
}
