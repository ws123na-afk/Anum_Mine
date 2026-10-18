// Owner agent controls over the live API (docs/agent-controls.md). Every permission is checked by the
// server: any member reads the switch, owners turn it on, only the organization owner turns it off,
// owners read and change agent caps.
import { apiRequest } from './api';
import { capsPath, heldPath, type AgentCap, type AgentCapsPage, type AgentControls, type HeldPage } from './agentControls';

const BASE = '/api/v1/agent-controls';

export const getAgentControls = (): Promise<AgentControls> => apiRequest(BASE, { method: 'GET' });

export const setEmergencyStop = (engaged: boolean, reason: string): Promise<AgentControls> =>
  apiRequest(`${BASE}/emergency-stop`, { method: 'PUT', body: JSON.stringify({ engaged, reason }) });

export const listAgentCaps = (query = '', offset = 0): Promise<AgentCapsPage> => apiRequest(capsPath(query, offset), { method: 'GET' });

export const setAgentCap = (agentId: string, cost: number | null, tokens: number | null = null): Promise<AgentCap> =>
  apiRequest(`${BASE}/agent-caps/${encodeURIComponent(agentId)}`, { method: 'PUT', body: JSON.stringify({ monthly_cost_limit_usd: cost, monthly_token_limit: tokens }) });

export const listHeld = (offset = 0): Promise<HeldPage> => apiRequest(heldPath(offset), { method: 'GET' });
