// System health over the live API (docs/system-health.md). Platform operators only; read-only.
import { apiRequest } from './api';
import type { SystemHealth } from './systemHealth';

export const getSystemHealth = (): Promise<SystemHealth> => apiRequest('/api/v1/platform/health', { method: 'GET' });
