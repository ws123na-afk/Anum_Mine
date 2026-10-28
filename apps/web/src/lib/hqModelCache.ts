/**
 * Loading and fallback rules for the loadable models, with the loader injected so the unit runner can
 * cover them (no three.js, no DOM). `ModelCache` loads each model once, never rejects, and times out;
 * `resolvePlacements` decides, from what actually loaded, which placements to draw and which groups
 * keep their procedural stand-in.
 */
import { PERSON_KEYS, isFigureKey, specOf, type ModelGroup, type ModelKey, type ModelPlacement } from './hqModels.ts';

export type LoadOutcome<T> = Readonly<{ model: T; bytes: number }>;

export class ModelCache<T> {
  private readonly pending = new Map<ModelKey, Promise<LoadOutcome<T> | null>>();
  private readonly done = new Map<ModelKey, LoadOutcome<T>>();
  private readonly failed = new Set<ModelKey>();
  private readonly load: (key: ModelKey) => Promise<LoadOutcome<T>>;
  private readonly timeoutMs: number;

  constructor(load: (key: ModelKey) => Promise<LoadOutcome<T>>, timeoutMs = 20_000) {
    this.load = load;
    this.timeoutMs = timeoutMs;
  }

  /** The loaded model, or null (still loading, or failed). Synchronous, so a rebuilt scene does not flicker. */
  peek(key: ModelKey): LoadOutcome<T> | null {
    return this.done.get(key) ?? null;
  }

  hasFailed(key: ModelKey): boolean {
    return this.failed.has(key);
  }

  /** Loads once per key. Resolves to null on any error or timeout; never rejects. */
  get(key: ModelKey): Promise<LoadOutcome<T> | null> {
    const cached = this.pending.get(key);
    if (cached) return cached;
    const run = new Promise<LoadOutcome<T> | null>((resolve) => {
      const timer = setTimeout(() => { this.failed.add(key); resolve(null); }, this.timeoutMs);
      this.load(key).then(
        (value) => { clearTimeout(timer); if (this.failed.has(key)) { resolve(null); return; } this.done.set(key, value); resolve(value); },
        () => { clearTimeout(timer); this.failed.add(key); resolve(null); },
      );
    });
    this.pending.set(key, run);
    return run;
  }

  /** Total bytes of the models loaded so far. */
  get bytes(): number {
    let n = 0;
    for (const v of this.done.values()) n += v.bytes;
    return n;
  }
}

export type Resolved = Readonly<{
  /** The placements to draw, with a person remapped to a variant that loaded. */
  placements: readonly ModelPlacement[];
  /** Groups that were asked for and are not ready: their procedural pieces stay on screen. */
  fallbackGroups: readonly ModelGroup[];
  /** Groups that are ready: their procedural pieces are hidden. */
  readyGroups: ReadonlySet<ModelGroup>;
}>;

/**
 * Which placements can be drawn given the keys that loaded. A person group is ready when any person
 * model loaded (a placement whose own variant failed uses another one); a furniture group is ready only
 * when every model it needs loaded, so a half-dressed room never mixes styles.
 */
export function resolvePlacements(asked: readonly ModelPlacement[], loaded: ReadonlySet<ModelKey>): Resolved {
  const groups = new Set(asked.map((p) => p.group));
  const ready = new Set<ModelGroup>();
  const fallbackGroups: ModelGroup[] = [];
  for (const group of groups) {
    const needed = new Set(asked.filter((p) => p.group === group).map((p) => p.key));
    // Kit figures are all or nothing (a room never mixes styles); the legacy people need any one model.
    const kit = [...needed].some(isFigureKey);
    const ok = group === 'figure' ? (kit ? [...needed].every((k) => loaded.has(k)) : PERSON_KEYS.some((k) => loaded.has(k))) : [...needed].every((k) => loaded.has(k));
    if (ok) ready.add(group); else fallbackGroups.push(group);
  }
  const people = PERSON_KEYS.filter((k) => loaded.has(k));
  const placements = asked.filter((p) => ready.has(p.group)).map((p) => {
    if (p.group !== 'figure' || loaded.has(p.key)) return p;
    return { ...p, key: people[PERSON_KEYS.indexOf(p.key) % people.length] };
  });
  return { placements, fallbackGroups, readyGroups: ready };
}

/** The model keys a set of placements needs (what to fetch). */
export function keysNeeded(placements: readonly ModelPlacement[]): ModelKey[] {
  return [...new Set(placements.map((p) => p.key))];
}

/** Bytes the catalogue says are needed for these placements (shipped sizes). */
export function catalogBytes(keys: readonly ModelKey[]): number {
  return keys.reduce((n, k) => n + specOf(k).bytes, 0);
}
