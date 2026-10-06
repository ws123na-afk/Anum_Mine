// The API's error envelope as a typed error (docs/api-contracts.md, Error Model).
// Pure, so `pnpm --filter @anum/web test:unit` can exercise it without a browser.
import type { ApiErrorCode } from '@anum/contracts';

/** A non-2xx API answer: HTTP status, the envelope's stable code and its message. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | null;
  /** The API's own message, without the status prefix. */
  readonly detail: string | null;
  readonly correlationId: string | null;

  constructor(status: number, code: ApiErrorCode | null, detail: string | null, correlationId: string | null = null, prefix = 'ANUM API request failed') {
    super(detail ? `${prefix}: ${status}: ${detail}` : `${prefix}: ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.correlationId = correlationId;
  }
}

/** Build an ApiError from a status and a parsed JSON body (`{ error: {...} }` or FastAPI's `{ detail }`). */
export function apiErrorFrom(status: number, body: unknown, prefix?: string): ApiError {
  const envelope = isRecord(body) && isRecord(body.error) ? body.error : null;
  const message = envelope && typeof envelope.message === 'string' && envelope.message
    ? envelope.message
    : isRecord(body) && typeof body.detail === 'string' && body.detail ? body.detail : null;
  const code = envelope && typeof envelope.code === 'string' ? envelope.code as ApiErrorCode : null;
  const correlation = envelope && typeof envelope.correlation_id === 'string' ? envelope.correlation_id : null;
  return new ApiError(status, code, message, correlation, prefix);
}

/** Read a failed fetch Response into an ApiError; a body that is not JSON keeps only the status. */
export async function apiErrorFromResponse(response: Response, prefix?: string): Promise<ApiError> {
  const body = await response.json().catch(() => undefined);
  return apiErrorFrom(response.status, body, prefix);
}

/** HTTP 402 `model_budget_exceeded`: the tenant's or workspace's monthly model budget is used up. */
export function isBudgetExceeded(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 402 || error.code === 'model_budget_exceeded');
}

/** HTTP 403 from the API: the caller's role does not grant the permission. */
export function isPermissionDenied(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403;
}

export const BUDGET_FALLBACK_MESSAGE = 'This workspace has used its monthly model budget. An owner can raise it under Model budgets.';

/** The budget message to show: the API's own sentence (it names the reset date) or a fallback. */
export function budgetMessage(error: ApiError): string {
  return error.detail ?? BUDGET_FALLBACK_MESSAGE;
}

/** A sentence for a person: the API's message for known failures, never a stack trace. */
export function describeError(error: unknown, fallback = 'Operation failed.'): string {
  if (error instanceof ApiError) {
    if (error.status === 402 || error.code === 'model_budget_exceeded') return `Monthly model budget reached. ${budgetMessage(error)}`;
    if (error.status === 401) return 'Your sign-in expired. Sign in again.';
    if (error.detail) return error.detail;
    return error.message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
