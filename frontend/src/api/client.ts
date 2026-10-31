// The one API client (ADR-019): plain fetch, the backend's error envelope unwrapped once.
// A 2xx whose body is not JSON is an error, never an empty success (miStudio's client once read a
// misrouted ingress's HTML page as {}).
//
// Feature 014: every request carries the Settings PIN session when there is one. A Settings write
// answered 403 PIN_REQUIRED asks the operator for the PIN (the dialog App mounts) and is sent ONCE
// more with the new session; cancelled, the refusal reaches the caller as it came.
import { PIN_SESSION_HEADER, currentPinPrompter, pinSessionToken, setPinSessionToken } from './pinSession';

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
  try {
    return await send<T>(path, init);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 403 || error.code !== 'PIN_REQUIRED') throw error;
    setPinSessionToken(null); // whatever was held is not valid now
    const prompt = currentPinPrompter();
    if (!prompt || !(await prompt(error.message))) throw error;
    return send<T>(path, init);
  }
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    // A multipart body sets its own Content-Type (with the boundary); forcing JSON would break it.
    const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData;
    const pin = pinSessionToken();
    response = await fetch(path, {
      ...init,
      headers: {
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        ...(pin ? { [PIN_SESSION_HEADER]: pin } : {}),
        ...(init.headers ?? {}),
      },
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
