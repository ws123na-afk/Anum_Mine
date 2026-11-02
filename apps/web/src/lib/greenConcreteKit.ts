/** Green Concrete agents kit: client and pure helpers (contract packages/contracts/src/greenConcreteKit.ts, spec
 * docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md). Reading changes nothing; setup and the routine switch always send
 * `input_mode: "click"`, setup with the hash of each item the owner reviewed. */
import type { GcKit, GcKitSkill, GcRoutine, GcRoutineStateResult, GcSetupItem, GcSetupResult } from '@anum/contracts';
import type { MessageKey } from './i18n.ts';

export type KitRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class KitApiError extends Error { constructor(message: string) { super(message); this.name = 'KitApiError'; } }

export const KIT_BASE = '/api/v1/office/green-concrete/kit';
export const GC_BUILDING = 'gc-green-concrete';
const HASH = /^[0-9a-f]{64}$/;
const ROUTINE = /^[a-z_]{3,40}$/;

export function createKitClient(request: KitRequester) {
  return {
    async get(lang: 'en' | 'ar'): Promise<GcKit> {
      const body = await request<GcKit>(`${KIT_BASE}?lang=${lang}`, { method: 'GET' });
      if (!body || body.version !== 1 || !Array.isArray(body.skills) || !Array.isArray(body.routines)) throw new KitApiError('The kit response is not version 1');
      return body;
    },
    async setup(items: Pick<GcSetupItem, 'id' | 'hash'>[]): Promise<GcSetupResult> {
      if (items.length < 1 || items.length > 10) throw new KitApiError('Choose between 1 and 10 steps');
      if (items.some((i) => !HASH.test(i.hash))) throw new KitApiError('A step has no valid hash');
      return request<GcSetupResult>(`${KIT_BASE}/setup`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ input_mode: 'click', items: items.map((i) => ({ id: i.id, hash: i.hash })) }),
      });
    },
    async routine(key: string, enabled: boolean): Promise<GcRoutineStateResult> {
      if (!ROUTINE.test(key)) throw new KitApiError('Unknown routine');
      return request<GcRoutineStateResult>(`${KIT_BASE}/routines/${key}/state`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ input_mode: 'click', enabled }),
      });
    },
  };
}
export type KitClient = ReturnType<typeof createKitClient>;

export const kitText = (text: { en: string; ar: string }, locale: string): string => (locale === 'ar' && text.ar) || text.en;

/** Skills by state: ready first, then the ones waiting for data, then the rest. */
const SKILL_ORDER: Record<GcKitSkill['state'], number> = { ready: 0, not_connected: 1, no_access: 2, unavailable: 3 };
export function sortSkills(skills: readonly GcKitSkill[]): GcKitSkill[] {
  return [...skills].sort((a, b) => SKILL_ORDER[a.state] - SKILL_ORDER[b.state]);
}

export function skillCounts(skills: readonly GcKitSkill[]): { ready: number; waiting: number } {
  const ready = skills.filter((s) => s.state === 'ready').length;
  return { ready, waiting: skills.length - ready };
}

/** "Every day at 06:00", "Every Monday at 08:00": the routines' schedules in words (a fixed time, every day or one weekday). */
export function scheduleWords(cron: string): { key: MessageKey; time: string; day?: MessageKey } | null {
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\*|[0-6])$/.exec(cron.trim());
  if (!m) return null;
  const time = `${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}`;
  if (m[3] === '*') return { key: 'gck.schedule.daily', time };
  return { key: 'gck.schedule.weekly', time, day: `gck.day.${m[3]}` as MessageKey };
}

/** What the owner can do with a routine now. */
export function routineAction(routine: GcRoutine, canManage: boolean): 'add' | 'turn_on' | 'turn_off' | 'needs_agent' | 'none' {
  if (!canManage) return 'none';
  if (routine.state === 'missing') return 'add';
  if (routine.state === 'off') return 'turn_on';
  if (routine.state === 'on') return 'turn_off';
  if (routine.state === 'needs_agent') return 'needs_agent';
  return 'none';
}

export function setupOutcomeKey(outcome: GcSetupResult['results'][number]['outcome']): MessageKey {
  return `gck.outcome.${outcome}` as MessageKey;
}
