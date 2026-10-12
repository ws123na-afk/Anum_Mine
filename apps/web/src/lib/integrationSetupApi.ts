// Connection setup calls (docs/tools-and-integrations.md#connection-setup). Every call carries the
// current tenant and workspace through the shared request's headers.
import { apiRequest } from './api';
import type { IntegrationSetup, SetupCheck } from './integrationSetup';

export const getIntegrationSetup = () => apiRequest<IntegrationSetup>('/api/v1/integrations/setup', { method: 'GET' });
export const checkIntegrationSetup = (itemId: string) =>
  apiRequest<SetupCheck>(`/api/v1/integrations/setup/${encodeURIComponent(itemId)}/check`, { method: 'POST', body: '{}' });
