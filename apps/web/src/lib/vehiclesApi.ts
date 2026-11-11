// Vehicles over the live API (docs/vehicles.md#api). The server checks vehicle:read / vehicle:write on every route,
// and finance:manage for linking a payment; a personal vehicle of someone else answers 404.
import type {
  FinanceAccount, Vehicle, VehicleAllocationCandidate, VehicleCashInput, VehicleCostKind, VehicleFuelEntry, VehicleFuelInput, VehicleInput, VehicleLinkInput,
  VehicleOdometerReading, VehicleOverview, VehiclePage, VehiclePatch, VehiclePaymentCandidate, VehiclePaymentResult, VehiclePreferences,
  VehicleRecordKind, VehicleReport, VehicleServiceInput, VehicleServiceRecord, VehicleTrip, VehicleTripInput,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/vehicles';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown, key?: string) => apiRequest<T>(`${BASE}${path}`, {
  method, body: body === undefined ? undefined : JSON.stringify(body), headers: key ? { 'Idempotency-Key': key } : undefined,
});
const id = encodeURIComponent;

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const listVehicles = (q: string, archived: boolean, offset: number, limit = 20) =>
  get<VehiclePage<Vehicle>>(`${query({ q, archived: archived || null, offset, limit })}`);
export const getVehicle = (vehicleId: string) => get<Vehicle>(`/${id(vehicleId)}`);
export const createVehicle = (body: VehicleInput) => send<Vehicle>('POST', '', body);
export const updateVehicle = (vehicleId: string, body: VehiclePatch) => send<Vehicle>('PATCH', `/${id(vehicleId)}`, body);
export const archiveVehicle = (vehicleId: string, version: number, archived: boolean) => send<Vehicle>('POST', `/${id(vehicleId)}/archive`, { version, archived });
export const deleteVehicle = (vehicleId: string) => apiRequestVoid(`${BASE}/${id(vehicleId)}`, { method: 'DELETE' });

export const overview = (start?: string, end?: string) => get<VehicleOverview>(`/overview${query({ start, end })}`);
export const report = (vehicleId: string, start?: string, end?: string) => get<VehicleReport>(`/${id(vehicleId)}/report${query({ start, end })}`);
export const getPreferences = () => get<VehiclePreferences>('/preferences');
export const savePreferences = (body: Omit<VehiclePreferences, 'owner_user_id' | 'updated_at'>) => send<VehiclePreferences>('PUT', '/preferences', body);

export const listReadings = (vehicleId: string, offset: number, limit = 20) => get<VehiclePage<VehicleOdometerReading>>(`/${id(vehicleId)}/odometer${query({ offset, limit })}`);
export const addReading = (vehicleId: string, body: { reading_km: string; read_at?: string | null; correction?: boolean; reason?: string | null }) =>
  send<VehicleOdometerReading>('POST', `/${id(vehicleId)}/odometer`, body);

type RecordOf<K extends VehicleRecordKind> = K extends 'fuel' ? VehicleFuelEntry : K extends 'trips' ? VehicleTrip : VehicleServiceRecord;
type InputOf<K extends VehicleRecordKind> = K extends 'fuel' ? VehicleFuelInput : K extends 'trips' ? VehicleTripInput : VehicleServiceInput;

export const listRecords = <K extends VehicleRecordKind>(kind: K, vehicleId: string, q: string, offset: number, limit = 20) =>
  get<VehiclePage<RecordOf<K>>>(`/${id(vehicleId)}/${kind}${query({ q, offset, limit })}`);
export const createRecord = <K extends VehicleRecordKind>(kind: K, vehicleId: string, body: InputOf<K>, key: string) =>
  send<RecordOf<K> & { replayed: boolean }>('POST', `/${id(vehicleId)}/${kind}`, body, key);
export const updateRecord = <K extends VehicleRecordKind>(kind: K, recordId: string, body: Partial<InputOf<K>> & { version: number }) =>
  send<RecordOf<K>>('PATCH', `/${kind}/${id(recordId)}`, body);
export const deleteRecord = (kind: VehicleRecordKind, recordId: string) => apiRequestVoid(`${BASE}/${kind}/${id(recordId)}`, { method: 'DELETE' });

export const paymentCandidates = (q: string, around?: string | null, amount?: string | null) =>
  get<{ items: VehiclePaymentCandidate[] }>(`/payments${query({ q, around, amount })}`);
export const linkPayment = (kind: VehicleCostKind, recordId: string, body: VehicleLinkInput, key: string) =>
  send<VehiclePaymentResult>('POST', `/costs/${kind}/${id(recordId)}/payment`, body, key);
export const recordCash = (kind: VehicleCostKind, recordId: string, body: VehicleCashInput, key: string) =>
  send<VehiclePaymentResult>('POST', `/costs/${kind}/${id(recordId)}/cash`, body, key);
export const unlinkPayment = (kind: VehicleCostKind, recordId: string) => apiRequestVoid(`${BASE}/costs/${kind}/${id(recordId)}/payment`, { method: 'DELETE' });
/** A business vehicle's cost from a business allocation (docs/vehicles.md#business-allocations). */
export const allocationCandidates = (around?: string | null, amount?: string | null) =>
  get<{ items: VehicleAllocationCandidate[] }>(`/allocations${query({ around, amount })}`);
export const applyAllocation = (kind: VehicleCostKind, recordId: string, allocationId: string) =>
  send<VehiclePaymentResult>('POST', `/costs/${kind}/${id(recordId)}/allocation`, { allocation_id: allocationId });
export const releaseAllocation = (kind: VehicleCostKind, recordId: string) => apiRequestVoid(`${BASE}/costs/${kind}/${id(recordId)}/allocation`, { method: 'DELETE' });

export const financeAccounts = () => apiRequest<FinanceAccount[]>('/api/v1/finance/accounts', { method: 'GET' });

/** Download one vehicle's log as CSV. */
export async function exportCsv(vehicleId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/${id(vehicleId)}/export.csv`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  saveBlob(await response.blob(), fileNameFromDisposition(response.headers.get('content-disposition'), 'anum-vehicle.csv'));
}
