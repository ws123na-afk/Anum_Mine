// Retrying a read that failed for a passing reason (docs/web-design-system.md#page-errors).

/** A failure worth one more try: rate limited, a gateway or service hiccup, or no response at all. */
export function isTransient(error: unknown): boolean {
  if (error instanceof TypeError) return true; // fetch: network error, nothing came back
  const status = (error as { status?: unknown } | null)?.status;
  return status === 429 || status === 502 || status === 503 || status === 504;
}

const sleep = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms); });

/**
 * `read()`, tried again after each delay while it fails for a passing reason. A permanent answer
 * (403, 404, 422) is returned at once: retrying cannot change it.
 */
export async function retryTransient<T>(read: () => Promise<T>, delaysMs: readonly number[] = [1000, 3000], wait: (ms: number) => Promise<void> = sleep): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await read();
    } catch (error) {
      if (attempt >= delaysMs.length || !isTransient(error)) throw error;
      await wait(delaysMs[attempt]);
    }
  }
}
