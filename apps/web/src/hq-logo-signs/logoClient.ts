/**
 * Calls of the building sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): upload and remove the logo, save the colour and style, and read the
 * stored logo back. The logo has no public URL: it is fetched with the person's own credentials from the API and kept in memory as a
 * decoded bitmap, never written to browser storage.
 */
import type { OfficeBuildingUpdate } from '@anum/contracts';
import * as api from '../lib/api.ts';
import type { OfficeBuilding } from '../lib/officeSpaceClient.ts';

const ROOT = '/api/v1/office-spaces/buildings';
const enc = encodeURIComponent;
const query = (revision: number) => new URLSearchParams({ expected_revision: String(revision), input_mode: 'click' }).toString();

/** Upload a logo file (raw bytes as the body). The server sniffs, scans, decodes and re-encodes it; the answer is the building. */
export const uploadLogo = (buildingId: string, revision: number, file: Blob): Promise<OfficeBuilding> =>
  api.apiRequest<OfficeBuilding>(`${ROOT}/${enc(buildingId)}/logo?${query(revision)}`, {
    method: 'PUT', body: file, headers: { 'content-type': file.type || 'application/octet-stream' },
  });

export const removeLogo = (buildingId: string, revision: number): Promise<OfficeBuilding> =>
  api.apiRequest<OfficeBuilding>(`${ROOT}/${enc(buildingId)}/logo?${query(revision)}`, { method: 'DELETE' });

export const saveLook = (buildingId: string, body: Pick<OfficeBuildingUpdate, 'expected_revision' | 'sign_style' | 'brand_color'>): Promise<OfficeBuilding> =>
  api.apiRequest<OfficeBuilding>(`${ROOT}/${enc(buildingId)}`, { method: 'PATCH', body: JSON.stringify(body) });

/** The stored PNG, or null when the building has none (404). Revalidated with its ETag, so an unchanged logo costs a 304. */
export async function fetchLogo(buildingId: string, signal?: AbortSignal): Promise<Blob | null> {
  const response = await fetch(`${api.apiBaseUrl}${ROOT}/${enc(buildingId)}/logo`, { headers: await api.authHeaders(), cache: 'no-cache', signal });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`logo ${response.status}`);
  const blob = await response.blob();
  return blob.type === 'image/png' || blob.type === '' ? blob : null;
}
