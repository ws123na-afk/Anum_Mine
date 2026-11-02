// The company details and legal facts behind the public legal pages (docs/selling-readiness.md#company-and-legal):
// GET /api/v1/public/legal needs no sign-in and gets no auth headers. Kept apart from the billing client so
// the public pages load only this.
import { apiBaseUrl } from './api';
import type { PublicLegal } from './selling';

export async function getPublicLegal(fetcher: typeof fetch = fetch): Promise<PublicLegal> {
  const response = await fetcher(`${apiBaseUrl}/api/v1/public/legal`, { headers: { accept: 'application/json' }, cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<PublicLegal>;
}
