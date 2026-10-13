// The one API client (ADR-019): plain fetch, the backend's error envelope unwrapped once.
// A 2xx whose body is not JSON is an error, never an empty success (miStudio's client once read a
// misrouted ingress's HTML page as {}).

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    // A multipart body sets its own Content-Type (with the boundary); forcing JSON would break it.
    const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData;
    response = await fetch(path, {
      ...init,
      headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, 'NETWORK', 'The miDataworks API is unreachable. Check that the backend is running.');
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let body: unknown;
  try {
    body = text ? JSON.parse(text) : undefined;
  } catch {
    throw new ApiError(response.status, 'NOT_JSON', `The API answered ${response.status} with something that is not JSON.`);
  }
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    throw new ApiError(
      response.status,
      error?.code ?? 'HTTP_ERROR',
      error?.message ?? `The API answered ${response.status}.`,
      error?.details ?? {},
    );
  }
  return body as T;
}

export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});
