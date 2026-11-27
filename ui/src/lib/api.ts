/**
 * Thin API boundary: auth headers + typed JSON fetch.
 *
 * Views keep their own state machines; this module only removes copy-pasted
 * localStorage/token and JSON-plumbing code. No retries, no caches, no magic.
 */

export const API_KEY_STORAGE_KEY = 'openeval_api_key';
export const API_KEY_ENV_STORAGE_KEY = 'OPENEVAL_API_KEY';

/** Read a bearer token from localStorage, accepting a raw key or "Bearer <key>". */
export function getStoredApiKey(): string | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw =
      localStorage.getItem(API_KEY_STORAGE_KEY) ??
      localStorage.getItem(API_KEY_ENV_STORAGE_KEY) ??
      '';
    const key = raw.trim().replace(/^Bearer\s+/i, '');
    return key || null;
  } catch {
    return null;
  }
}

export function setStoredApiKey(key: string | null): void {
  if (typeof localStorage === 'undefined') return;
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE_KEY, key.trim());
    else localStorage.removeItem(API_KEY_STORAGE_KEY);
  } catch {
    // Storage can be unavailable (private mode / SSR); callers stay anonymous.
  }
}

export function buildAuthHeaders(
  extra: Record<string, string> = {}
): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  const key = getStoredApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  return headers;
}

export interface ApiFetchOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit | null;
  /** Serialized as JSON; also sets Content-Type. */
  json?: unknown;
  /** Attach `X-OpenEval-Surface: demo` for demo-bound reads. */
  demo?: boolean;
  signal?: AbortSignal;
  credentials?: RequestCredentials;
}

/** Fetch + parse JSON, throwing on non-2xx so callers can fail loudly. */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {}
): Promise<T> {
  const { json, demo, headers, ...init } = options;
  const merged = buildAuthHeaders({
    ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...(demo ? { 'X-OpenEval-Surface': 'demo' } : {}),
    ...headers,
  });
  const res = await fetch(path, {
    ...init,
    headers: merged,
    body: json !== undefined ? JSON.stringify(json) : init.body,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status} ${res.statusText || ''} for ${path}`.trim());
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
