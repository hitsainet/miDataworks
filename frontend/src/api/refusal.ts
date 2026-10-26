// A form's view of a backend refusal: the envelope's message verbatim, plus each field problem a
// 422 carries in details.errors (the message alone, "The request is not valid.", names no field).
import { ApiError } from '@/api/client';

export interface FormRefusal {
  code: string;
  message: string;
  /** "field: what is wrong", one per problem the backend listed. Empty when it listed none. */
  problems: string[];
}

export function formRefusal(e: unknown): FormRefusal {
  if (!(e instanceof ApiError)) return { code: 'ERROR', message: e instanceof Error ? e.message : String(e), problems: [] };
  const errors = Array.isArray(e.details.errors) ? (e.details.errors as Array<{ loc?: unknown; msg?: unknown }>) : [];
  const problems = errors.map((err) => {
    // FastAPI roots every location at "body" (the request body); a field may also be named body.
    const raw = Array.isArray(err.loc) ? err.loc.map(String) : [];
    const loc = raw[0] === 'body' ? raw.slice(1) : raw;
    const msg = String(err.msg ?? '');
    return loc.length ? `${loc.join('.')}: ${msg}` : msg;
  });
  return { code: e.code, message: e.message, problems };
}

/** A client-side refusal, so a form shows its own checks the same way as the backend's. */
export const localRefusal = (message: string): FormRefusal => ({ code: 'LOCAL', message, problems: [] });
