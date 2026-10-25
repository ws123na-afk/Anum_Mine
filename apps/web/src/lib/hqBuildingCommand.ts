/** "Add a building" from the assistant (spec: docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building). The sentence parser only
 * PROPOSES: it asks the read-only `propose_add_building` tool. Creation is `confirmBuilding`, called from a click on the
 * proposal card; no spoken or typed word ever reaches it. */
import type { BuildingConfirm, BuildingConfirmResult, BuildingProposal } from '@anum/contracts';

export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;
export interface BuildingAsk { name: string | null; floors: number | null }

const WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
  واحد: 1, واحدة: 1, اثنين: 2, اثنان: 2, ثلاثة: 3, ثلاث: 3, أربعة: 4, اربعة: 4, خمسة: 5, ستة: 6, سبعة: 7, ثمانية: 8, تسعة: 9, عشرة: 10,
};
const AR_DIGITS = '٠١٢٣٤٥٦٧٨٩';
const digits = (v: string) => v.replace(/[٠-٩]/g, (d) => String(AR_DIGITS.indexOf(d)));

/** Is this sentence a request to add a building? Returns what it said (fields may be missing), or null for any other sentence. */
export function parseBuildingRequest(text: string): BuildingAsk | null {
  const s = digits(text).replace(/\s+/g, ' ').trim();
  const lead = /^(?:please\s+)?(?:add|create|set up|setup|make|build|new)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?building(?![\p{L}\p{N}])(.*)$/iu.exec(s)
    ?? /^(?:أضف|اضف|أنشئ|انشئ|ابن|أنشيء)\s+(?:لي\s+)?مبنى(?![\p{L}\p{N}])(.*)$/u.exec(s);
  if (!lead) return null;
  const rest = lead[1];
  const floorsMatch = /(?:with|has|of|and)?\s*(\d{1,3}|[A-Za-z؀-ۿ]+)\s+(?:floors?|levels?|stor(?:e)?ys?|طوابق|طابق|أدوار|دور)(?![\p{L}\p{N}])/iu.exec(rest)
    ?? /(?:طوابق|طابق|floors?)\s*[:=]?\s*(\d{1,3})(?![\p{L}\p{N}])/iu.exec(rest);
  let floors: number | null = null;
  if (floorsMatch) {
    const raw = floorsMatch[1].toLowerCase();
    floors = /^\d+$/.test(raw) ? Number(raw) : WORDS[raw] ?? WORDS[raw.replace(/^ب/, '')] ?? null;
  }
  const before = floorsMatch ? rest.slice(0, floorsMatch.index) : rest;
  const named = /(?:called|named|name(?:d)?\s*[:=]?|اسمه|اسمها|باسم|بإسم)\s*["'“”]?(.+?)["'“”]?\s*(?:,|\bwith\b|\bhaving\b|\band\b\s*$|$)/i.exec(before);
  const bare = !named ? /^\s*(?:for|لـ|ل)?\s*["'“”]?([^,"'“”]+?)["'“”]?\s*(?:,|\bwith\b|$)/i.exec(before) : null;
  const rawName = (named?.[1] ?? bare?.[1] ?? '').trim().replace(/\s+/g, ' ');
  const name = rawName && !/^(?:with|and|has)$/i.test(rawName) ? rawName.slice(0, 100) : null;
  return { name, floors };
}

/** The one short question to ask when the sentence is missing something. */
export function missingQuestion(ask: BuildingAsk): 'name' | 'floors' | null {
  if (!ask.name) return 'name';
  if (ask.floors === null) return 'floors';
  return null;
}

export const proposalArgs = (ask: BuildingAsk, lang: 'en' | 'ar') => ({ ...(ask.name ? { name: ask.name } : {}), ...(ask.floors !== null ? { floors: ask.floors } : {}), lang });

export function createBuildingClient(request: Requester) {
  return {
    proposal: (name: string, floors: number, lang: 'en' | 'ar') =>
      request<BuildingProposal>(`/api/v1/office/building-proposals?${new URLSearchParams({ name, floors: String(floors), lang })}`, { method: 'GET' }),
    /** Only call this from a click handler. It always sends `input_mode: "click"` and the hash that was shown. */
    confirm: (proposal: BuildingProposal, key: string): Promise<BuildingConfirmResult> => {
      const body: BuildingConfirm = {
        name: proposal.content.building.name, floors: proposal.content.floors.length, lang: proposal.content.lang,
        payload_hash: proposal.payload_hash, input_mode: 'click',
      };
      return request<BuildingConfirmResult>('/api/v1/office/building-proposals/confirm', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body) });
    },
  };
}

/** A floor count in a short answer: "3", "three", "٣", "3 floors". */
export function parseFloorCount(text: string): number | null {
  const s = digits(text).trim().toLowerCase();
  const n = /\d{1,3}/.exec(s);
  if (n) return Number(n[0]);
  const word = s.split(/\s+/).map((w) => w.replace(/[^\p{L}]/gu, '')).find((w) => w in WORDS);
  return word ? WORDS[word] : null;
}

/** The reply to the assistant's one question: it fills exactly the field that was asked for. */
export function answerQuestion(ask: BuildingAsk, reply: string): BuildingAsk {
  const text = reply.replace(/\s+/g, ' ').trim();
  if (ask.name === null) return { ...ask, name: text.replace(/^(?:it(?:'s| is)?\s+)?(?:called|named)\s+/i, '').replace(/^["'“]|["'”]$/g, '').slice(0, 100) || null };
  return { ...ask, floors: parseFloorCount(text) };
}
