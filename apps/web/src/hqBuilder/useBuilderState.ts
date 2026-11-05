// State and actions of the office builder: the server records, the staged DRAFT of the open floor
// (with undo and redo), and every call to the builder API. Layout moves are staged and saved as
// one atomic PUT; creating, copying, deleting and the details of a room are immediate server
// actions, merged into the draft without losing it. No browser storage is used for any of it.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { OfficeDeskUpdate, OfficeRoomUpdate, OfficeTemplateCatalogue, OfficeRoomTemplate } from '@anum/contracts';
import type { OfficeBuilding, OfficeDesk, OfficeFloor, OfficeRegistryData, OfficeRoom } from '../lib/officeSpaceClient.ts';
import { t } from '../lib/i18n.ts';
import { makeOfficeId, type BuilderApi } from './builderApi.ts';
import {
  canRedo, canUndo, commit, deskFromRecord, isDirty, mapHistory, rebase, redo, rectOf, roomFromRecord, rotateRoom,
  setDeskPosition, setRect, snapshotFromRecords, startHistory, toLayoutRequest, undo, withEntries, withoutEntries, withRoom,
  type DraftRoom, type DroppedChange, type History, type Revisions, type Snapshot,
} from './draftModel.ts';
import { explainError, type Explained } from './explain.ts';
import { DEFAULT_NEW_ROOM, clampDesk, findFreeSpot, freeDeskSpot, type Bounds, type Rect } from './geometry.ts';
import { hasBlocking, validateDraft, type BuilderIssue } from './validate.ts';

const EMPTY: Snapshot = { rooms: {}, desks: {} };
const blankData: OfficeRegistryData = { buildings: [], floors: [], rooms: [], desks: [] };

export interface UseBuilderOptions {
  api: BuilderApi;
  load: () => Promise<OfficeRegistryData>;
  /** Changes when the workspace changes: the builder reloads and drops the draft. */
  scopeKey: string;
}

export type Phase = 'loading' | 'ready' | 'error';

/** Bounds of a floor, or null for a floor made before bounds existed. */
export function floorBounds(floor: OfficeFloor | undefined): Bounds | null {
  if (!floor) return null;
  const width = Number(floor.width_m);
  const depth = Number(floor.depth_m);
  return Number.isFinite(width) && Number.isFinite(depth) && width > 0 && depth > 0 ? { width, depth } : null;
}

function uniqueName(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base.toLowerCase())) return base;
  for (let n = 2; n < 1000; n += 1) {
    const candidate = `${base} ${n}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
  return `${base} ${Date.now()}`;
}

export function useBuilder({ api, load, scopeKey }: UseBuilderOptions) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [loadError, setLoadError] = useState<Explained | null>(null);
  const [data, setData] = useState<OfficeRegistryData>(blankData);
  const [buildingId, setBuildingIdState] = useState('');
  const [floorId, setFloorIdState] = useState('');
  const [base, setBase] = useState<Snapshot>(EMPTY);
  const [history, setHistory] = useState<History>(() => startHistory(EMPTY));
  const [revisions, setRevisions] = useState<Revisions>({});
  const [catalogue, setCatalogue] = useState<OfficeTemplateCatalogue | null>(null);
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Explained | null>(null);
  const [notice, setNotice] = useState('');
  const [stale, setStale] = useState(false);
  const [reapplied, setReapplied] = useState<{ kept: number; dropped: DroppedChange[] } | null>(null);
  const [selectedRoomId, setSelectedRoomId] = useState('');
  const [selectedDeskId, setSelectedDeskId] = useState('');

  // The latest values for callbacks that run after an await.
  const live = useRef({ data, base, history, revisions, floorId, scopeKey });
  live.current = { data, base, history, revisions, floorId, scopeKey };
  const loadedScope = useRef('');

  const present = history.present;
  const floor = data.floors.find((f) => f.id === floorId);
  const bounds = useMemo(() => floorBounds(floor), [floor]);
  const dirty = useMemo(() => isDirty(base, present), [base, present]);
  const issues = useMemo<BuilderIssue[]>(() => validateDraft(base, present, bounds), [base, present, bounds]);
  const blocked = hasBlocking(issues);

  const nameOf = useCallback((id: string): string => {
    const d = live.current.data;
    return d.rooms.find((r) => r.id === id)?.name ?? d.desks.find((x) => x.id === id)?.label ?? d.floors.find((f) => f.id === id)?.name ?? id;
  }, []);

  const fail = useCallback((error: unknown): Explained => {
    const explained = explainError(error, nameOf);
    setProblem(explained);
    if (explained.stale) setStale(true);
    return explained;
  }, [nameOf]);

  const floorSnapshot = useCallback((records: OfficeRegistryData, id: string) => {
    const rooms = records.rooms.filter((r) => r.floor_id === id);
    const roomIds = new Set(rooms.map((r) => r.id));
    return snapshotFromRecords(rooms, records.desks.filter((d) => roomIds.has(d.room_id)));
  }, []);

  const resetFloor = useCallback((records: OfficeRegistryData, id: string) => {
    const { snapshot, revisions: revs } = floorSnapshot(records, id);
    setBase(snapshot);
    setHistory(startHistory(snapshot));
    setRevisions(revs);
    setSelectedRoomId('');
    setSelectedDeskId('');
    setStale(false);
    setReapplied(null);
  }, [floorSnapshot]);

  const pickInitial = useCallback((records: OfficeRegistryData, wantedBuilding: string, wantedFloor: string) => {
    const building = records.buildings.find((b) => b.id === wantedBuilding) ?? records.buildings[0];
    const floors = records.floors.filter((f) => f.building_id === building?.id).sort((a, b) => a.level - b.level);
    const chosen = floors.find((f) => f.id === wantedFloor) ?? floors.find((f) => f.level === 0) ?? floors[0];
    return { building: building?.id ?? '', floor: chosen?.id ?? '' };
  }, []);

  const reload = useCallback(async (want?: { building?: string; floor?: string }) => {
    setPhase((p) => (p === 'ready' ? p : 'loading'));
    setBusy(true);
    try {
      const records = await load();
      const key = live.current.scopeKey;
      loadedScope.current = key;
      const pick = pickInitial(records, want?.building ?? buildingIdRef.current, want?.floor ?? floorIdRef.current);
      setData(records);
      setBuildingIdState(pick.building);
      setFloorIdState(pick.floor);
      resetFloor(records, pick.floor);
      setProblem(null);
      setLoadError(null);
      setPhase('ready');
    } catch (error) {
      setLoadError(explainError(error, nameOf));
      setPhase((p) => (p === 'ready' ? p : 'error'));
      setProblem(explainError(error, nameOf));
    } finally {
      setBusy(false);
    }
  }, [load, nameOf, pickInitial, resetFloor]);
  const buildingIdRef = useRef('');
  const floorIdRef = useRef('');
  buildingIdRef.current = buildingId;
  floorIdRef.current = floorId;

  // First load, and a fresh start whenever the workspace changes.
  useEffect(() => {
    setData(blankData);
    setBuildingIdState('');
    setFloorIdState('');
    buildingIdRef.current = '';
    floorIdRef.current = '';
    setPhase('loading');
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  useEffect(() => {
    let active = true;
    api.templates().then((value) => { if (active) { setCatalogue(value); setCatalogueFailed(false); } })
      .catch(() => { if (active) setCatalogueFailed(true); });
    return () => { active = false; };
  }, [api, scopeKey]);

  const scopeOk = useCallback((): boolean => {
    if (loadedScope.current !== live.current.scopeKey) {
      setProblem({ title: t('hqb.err.scope'), lines: [], stale: false, forbidden: false, unavailable: false });
      return false;
    }
    return true;
  }, []);

  // ---- Selection and navigation ----

  const setBuildingId = useCallback((id: string) => {
    const pick = pickInitial(live.current.data, id, '');
    setBuildingIdState(pick.building);
    setFloorIdState(pick.floor);
    resetFloor(live.current.data, pick.floor);
  }, [pickInitial, resetFloor]);

  const setFloorId = useCallback((id: string) => {
    setFloorIdState(id);
    resetFloor(live.current.data, id);
  }, [resetFloor]);

  // ---- Staged edits ----

  const stage = useCallback((change: (s: Snapshot) => Snapshot, key: string | null = null) => {
    setHistory((h) => commit(h, change(h.present), key));
  }, []);

  const moveRoom = useCallback((id: string, rect: Pick<Rect, 'x' | 'z' | 'width' | 'depth'>, key: string | null = null) => {
    stage((s) => setRect(s, id, rect), key);
  }, [stage]);
  const rotate = useCallback((id: string) => {
    stage((s) => rotateRoom(s, id, floorBounds(live.current.data.floors.find((f) => f.id === live.current.floorId))));
  }, [stage]);
  const setDoor = useCallback((id: string, doorSide: DraftRoom['doorSide']) => {
    stage((s) => withRoom(s, id, { doorSide }));
  }, [stage]);
  const moveDesk = useCallback((id: string, x: number, z: number, key: string | null = null) => {
    stage((s) => setDeskPosition(s, id, x, z), key);
  }, [stage]);
  const placeDesk = useCallback((deskId: string) => {
    stage((s) => {
      const desk = s.desks[deskId];
      const room = desk && s.rooms[desk.roomId];
      if (!desk || !room?.placed) return s;
      const taken = Object.values(s.desks).filter((d) => d.roomId === room.id && d.posX != null && d.posZ != null).map((d) => ({ x: d.posX as number, z: d.posZ as number }));
      const spot = freeDeskSpot(room, taken) ?? clampDesk(room, room.width / 2, room.depth / 2);
      return setDeskPosition(s, deskId, spot.x, spot.z);
    });
  }, [stage]);
  const placeRoom = useCallback((id: string) => {
    const { base: b, history: h } = live.current;
    const room = h.present.rooms[id];
    if (!room) return;
    const bnds = floorBounds(live.current.data.floors.find((f) => f.id === live.current.floorId));
    const occupied = [...Object.values(b.rooms), ...Object.values(h.present.rooms)].filter((r) => r.placed && r.id !== id).map(rectOf);
    const spot = findFreeSpot(occupied, room.width, room.depth, bnds);
    if (!spot) { setProblem(explainCodeOnly('NO_FREE_SPACE')); return; }
    stage((s) => setRect(s, id, { x: spot.x, z: spot.z, width: room.width, depth: room.depth }));
    setSelectedRoomId(id);
  }, [stage]);

  const doUndo = useCallback(() => setHistory((h) => undo(h)), []);
  const doRedo = useCallback(() => setHistory((h) => redo(h)), []);
  const discard = useCallback(() => {
    setHistory(startHistory(live.current.base));
    setStale(false);
    setProblem(null);
    setReapplied(null);
  }, []);

  // ---- Save, with the stale-revision flow ----

  const merge = useCallback((rooms: OfficeRoom[], desks: OfficeDesk[]) => {
    setData((d) => ({
      ...d,
      rooms: [...d.rooms.filter((r) => !rooms.some((n) => n.id === r.id)), ...rooms],
      desks: [...d.desks.filter((x) => !desks.some((n) => n.id === x.id)), ...desks],
    }));
    setRevisions((r) => {
      const next = { ...r };
      for (const room of rooms) next[room.id] = room.revision;
      for (const desk of desks) next[desk.id] = desk.revision;
      return next;
    });
  }, []);

  const save = useCallback(async (): Promise<boolean> => {
    const { base: b, history: h, revisions: revs, floorId: id } = live.current;
    if (!id || !scopeOk()) return false;
    const body = toLayoutRequest(b, h.present, revs);
    if (!body.rooms?.length && !body.desks?.length) return true;
    setBusy(true);
    setProblem(null);
    try {
      const saved = await api.saveLayout(id, body);
      merge(saved.rooms, saved.desks);
      // Take the server's own numbers as the new base (it may round).
      const records: OfficeRegistryData = {
        ...live.current.data,
        rooms: [...live.current.data.rooms.filter((r) => !saved.rooms.some((n) => n.id === r.id)), ...saved.rooms],
        desks: [...live.current.data.desks.filter((x) => !saved.desks.some((n) => n.id === x.id)), ...saved.desks],
      };
      const fresh = floorSnapshot(records, id);
      setBase(fresh.snapshot);
      setHistory(startHistory(fresh.snapshot));
      setRevisions(fresh.revisions);
      setStale(false);
      setReapplied(null);
      setNotice(t('hqb.status.saved', { count: (saved.rooms.length + saved.desks.length) }));
      return true;
    } catch (error) {
      fail(error);
      return false;
    } finally {
      setBusy(false);
    }
  }, [api, fail, floorSnapshot, merge, scopeOk]);

  /** Reload the latest records and put the owner's staged changes back on top where nobody else touched the item. */
  const reloadAndReapply = useCallback(async () => {
    const id = live.current.floorId;
    setBusy(true);
    try {
      const records = await load();
      const result = rebase(live.current.base, live.current.history.present, floorSnapshot(records, id).snapshot);
      const fresh = floorSnapshot(records, id);
      setData(records);
      setBase(fresh.snapshot);
      setHistory(startHistory(result.present));
      setRevisions(fresh.revisions);
      setStale(false);
      setProblem(null);
      setReapplied({ kept: result.kept, dropped: result.dropped });
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }, [fail, floorSnapshot, load]);

  const reloadDiscarding = useCallback(async () => {
    await reload();
  }, [reload]);

  // ---- Immediate server actions ----

  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    if (!scopeOk()) return undefined;
    setBusy(true);
    setProblem(null);
    try {
      return await action();
    } catch (error) {
      fail(error);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [fail, scopeOk]);

  /** A room or desk the server just created or copied joins both the base and the draft. */
  const adopt = useCallback((room: OfficeRoom | null, desks: OfficeDesk[]) => {
    const draftRooms = room ? [roomFromRecord(room)] : [];
    const draftDesks = desks.map(deskFromRecord);
    const change = (s: Snapshot) => withEntries(s, draftRooms, draftDesks);
    setBase(change);
    setHistory((h) => mapHistory(h, change));
    merge(room ? [room] : [], desks);
  }, [merge]);

  const takenNames = useCallback((): Set<string> => new Set(live.current.data.rooms.filter((r) => r.floor_id === live.current.floorId).map((r) => r.name.toLowerCase())), []);

  const spotFor = useCallback((width: number, depth: number, near: Rect | null): { x: number; z: number } | null => {
    const { base: b, history: h } = live.current;
    const bnds = floorBounds(live.current.data.floors.find((f) => f.id === live.current.floorId));
    const occupied = [...Object.values(b.rooms), ...Object.values(h.present.rooms)].filter((r) => r.placed).map(rectOf);
    return findFreeSpot(occupied, width, depth, bnds, near);
  }, []);

  const addFromTemplate = useCallback(async (template: OfficeRoomTemplate, withDesks: boolean): Promise<boolean> => {
    const id = live.current.floorId;
    if (!id) return false;
    const spot = spotFor(template.width, template.depth, null);
    if (!spot) { setProblem(explainCodeOnly('NO_FREE_SPACE')); return false; }
    const taken = takenNames();
    const name = taken.has(template.name.toLowerCase()) ? uniqueName(template.name, taken) : undefined;
    const created = await run(() => api.fromTemplate({ id: makeOfficeId('room'), floor_id: id, template_id: template.id, ...(name ? { name } : {}), x: spot.x, z: spot.z, with_desks: withDesks }));
    if (!created) return false;
    adopt(created.room, created.desks);
    setSelectedRoomId(created.room.id);
    setNotice(t('hqb.status.roomAdded', { name: created.room.name }));
    return true;
  }, [adopt, api, run, spotFor, takenNames]);

  const addBlank = useCallback(async (input: { name: string; kind: OfficeRoom['kind']; capacity: number }): Promise<boolean> => {
    const id = live.current.floorId;
    if (!id) return false;
    const created = await run(() => api.createRoom({ id: makeOfficeId('room'), floor_id: id, name: input.name.trim(), kind: input.kind, capacity: input.capacity }));
    if (!created) return false;
    adopt(created, []);
    // The new room waits in the draft until the owner saves the layout: it needs a place first.
    const spot = spotFor(DEFAULT_NEW_ROOM.width, DEFAULT_NEW_ROOM.depth, null);
    if (spot) stage((s) => setRect(s, created.id, { x: spot.x, z: spot.z, ...DEFAULT_NEW_ROOM }));
    setSelectedRoomId(created.id);
    setNotice(t(spot ? 'hqb.status.blankAdded' : 'hqb.status.blankNoSpace', { name: created.name }));
    return true;
  }, [adopt, api, run, spotFor, stage]);

  const copyRoom = useCallback(async (roomId: string, copyDesks: boolean): Promise<boolean> => {
    const source = live.current.data.rooms.find((r) => r.id === roomId);
    if (!source) return false;
    const draft = live.current.history.present.rooms[roomId];
    const spot = draft?.placed ? spotFor(draft.width, draft.depth, rectOf(draft)) : null;
    if (draft?.placed && !spot) { setProblem(explainCodeOnly('NO_FREE_SPACE')); return false; }
    const name = uniqueName(`${source.name} copy`, takenNames());
    const created = await run(() => api.copyRoom(roomId, { id: makeOfficeId('room'), name, copy_desks: copyDesks, ...(spot ? { x: spot.x, z: spot.z } : {}) }));
    if (!created) return false;
    adopt(created.room, created.desks);
    setSelectedRoomId(created.room.id);
    setNotice(t('hqb.status.roomAdded', { name: created.room.name }));
    return true;
  }, [adopt, api, run, spotFor, takenNames]);

  const saveRoomDetails = useCallback(async (roomId: string, patch: Omit<OfficeRoomUpdate, 'expected_revision'>): Promise<OfficeRoom | undefined> => {
    const revision = live.current.revisions[roomId];
    const updated = await run(() => api.patchRoom(roomId, { ...patch, expected_revision: revision }));
    if (updated) { merge([updated], []); setNotice(t('hqb.status.detailsSaved', { name: updated.name })); }
    return updated;
  }, [api, merge, run]);

  const deleteRoom = useCallback(async (roomId: string): Promise<boolean> => {
    const revision = live.current.revisions[roomId];
    const ok = await run(async () => { await api.deleteRoom(roomId, revision); return true; });
    if (!ok) return false;
    const deskIds = live.current.data.desks.filter((d) => d.room_id === roomId).map((d) => d.id);
    const change = (s: Snapshot) => withoutEntries(s, [roomId], deskIds);
    setBase(change);
    setHistory((h) => mapHistory(h, change));
    setData((d) => ({ ...d, rooms: d.rooms.filter((r) => r.id !== roomId) }));
    setSelectedRoomId('');
    setNotice(t('hqb.status.roomDeleted'));
    return true;
  }, [api, run]);

  const addDesk = useCallback(async (roomId: string, label: string): Promise<boolean> => {
    const created = await run(() => api.createDesk({ id: makeOfficeId('desk'), room_id: roomId, label: label.trim() }));
    if (!created) return false;
    adopt(null, [created]);
    const room = live.current.history.present.rooms[roomId];
    if (room?.placed) {
      setHistory((h) => {
        const r = h.present.rooms[roomId];
        const taken = Object.values(h.present.desks).filter((d) => d.roomId === roomId && d.posX != null && d.posZ != null).map((d) => ({ x: d.posX as number, z: d.posZ as number }));
        const spot = r ? freeDeskSpot(r, taken) : null;
        return spot ? commit(h, setDeskPosition(h.present, created.id, spot.x, spot.z)) : h;
      });
    }
    setSelectedDeskId(created.id);
    setNotice(t('hqb.status.deskAdded', { name: created.label }));
    return true;
  }, [adopt, run]);

  const renameDesk = useCallback(async (deskId: string, label: string): Promise<boolean> => {
    const revision = live.current.revisions[deskId];
    const updated = await run(() => api.patchDesk(deskId, { expected_revision: revision, label: label.trim() }));
    if (!updated) return false;
    merge([], [updated]);
    return true;
  }, [api, merge, run]);

  /** The seat fields of a desk (shift, place at the table, facing, seat type, table group), saved at once with the desk's revision. */
  const saveDeskSeat = useCallback(async (deskId: string, patch: Omit<OfficeDeskUpdate, 'expected_revision'>): Promise<boolean> => {
    const revision = live.current.revisions[deskId];
    const updated = await run(() => api.patchDesk(deskId, { ...patch, expected_revision: revision }));
    if (!updated) return false;
    merge([], [updated]);
    setNotice(t('hqb.status.floorSaved'));
    return true;
  }, [api, merge, run]);

  const deleteDesk = useCallback(async (deskId: string): Promise<boolean> => {
    const revision = live.current.revisions[deskId];
    const ok = await run(async () => { await api.deleteDesk(deskId, revision); return true; });
    if (!ok) return false;
    const change = (s: Snapshot) => withoutEntries(s, [], [deskId]);
    setBase(change);
    setHistory((h) => mapHistory(h, change));
    setData((d) => ({ ...d, desks: d.desks.filter((x) => x.id !== deskId) }));
    setSelectedDeskId('');
    return true;
  }, [api, run]);

  /** Seat an agent: an agent who sits elsewhere is moved (unseated first, then seated, both revision checked). */
  const seatAgent = useCallback(async (deskId: string, agentId: string | null): Promise<boolean> => {
    const records = live.current.data;
    const target = records.desks.find((d) => d.id === deskId);
    if (!target) return false;
    const result = await run(async () => {
      const changed: OfficeDesk[] = [];
      if (agentId) {
        const current = records.desks.find((d) => d.agent_id === agentId && d.id !== deskId);
        if (current) changed.push(await api.assignDesk(current.id, null, live.current.revisions[current.id]));
      }
      changed.push(await api.assignDesk(deskId, agentId, live.current.revisions[deskId]));
      return changed;
    });
    if (!result) return false;
    merge([], result);
    setNotice(t(agentId ? 'hqb.status.seated' : 'hqb.status.unseated'));
    return true;
  }, [api, merge, run]);

  // ---- Floor and building ----

  const saveFloor = useCallback(async (patch: { name?: string; name_ar?: string | null; level?: number; width_m?: number; depth_m?: number }): Promise<boolean> => {
    const current = live.current.data.floors.find((f) => f.id === live.current.floorId);
    if (!current) return false;
    const updated = await run(() => api.patchFloor(current.id, { ...patch, expected_revision: current.revision }));
    if (!updated) return false;
    setData((d) => ({ ...d, floors: d.floors.map((f) => (f.id === updated.id ? updated : f)) }));
    setNotice(t('hqb.status.floorSaved'));
    return true;
  }, [api, run]);

  const saveBuilding = useCallback(async (patch: { name?: string; name_ar?: string | null; shift_day_start?: string | null; shift_night_start?: string | null }): Promise<boolean> => {
    const current = live.current.data.buildings.find((b) => b.id === buildingIdRef.current);
    if (!current) return false;
    const updated = await run(() => api.patchBuilding(current.id, { ...patch, expected_revision: current.revision }));
    if (!updated) return false;
    setData((d) => ({ ...d, buildings: d.buildings.map((b) => (b.id === updated.id ? updated : b)) }));
    setNotice(t('hqb.status.floorSaved'));
    return true;
  }, [api, run]);

  /** A building the sign section just changed (logo, colour or style): the server's answer replaces the record. */
  const applyBuilding = useCallback((updated: OfficeBuilding, message: string) => {
    setData((d) => ({ ...d, buildings: d.buildings.map((b) => (b.id === updated.id ? updated : b)) }));
    setNotice(message);
  }, []);

  const deleteFloor = useCallback(async (): Promise<boolean> => {
    const current = live.current.data.floors.find((f) => f.id === live.current.floorId);
    if (!current) return false;
    const ok = await run(async () => { await api.deleteFloor(current.id, current.revision); return true; });
    if (!ok) return false;
    const next: OfficeRegistryData = { ...live.current.data, floors: live.current.data.floors.filter((f) => f.id !== current.id) };
    const pick = pickInitial(next, current.building_id, '');
    setData(next);
    setBuildingIdState(pick.building);
    setFloorIdState(pick.floor);
    resetFloor(next, pick.floor);
    setNotice(t('hqb.status.floorDeleted'));
    return true;
  }, [api, pickInitial, resetFloor, run]);

  const deleteBuilding = useCallback(async (): Promise<boolean> => {
    const current = live.current.data.buildings.find((b) => b.id === buildingIdRef.current);
    if (!current) return false;
    const ok = await run(async () => { await api.deleteBuilding(current.id, current.revision); return true; });
    if (!ok) return false;
    const next: OfficeRegistryData = { ...live.current.data, buildings: live.current.data.buildings.filter((b) => b.id !== current.id) };
    const pick = pickInitial(next, '', '');
    setData(next);
    setBuildingIdState(pick.building);
    setFloorIdState(pick.floor);
    resetFloor(next, pick.floor);
    setNotice(t('hqb.status.buildingDeleted'));
    return true;
  }, [api, pickInitial, resetFloor, run]);

  const floorRecords = useMemo(() => data.rooms.filter((r) => r.floor_id === floorId), [data.rooms, floorId]);
  const roomsById = useMemo(() => new Map(data.rooms.map((r) => [r.id, r])), [data.rooms]);

  return {
    phase, loadError, data, buildingId, floorId, floor, bounds, base, present, revisions,
    dirty, issues, blocked, busy, problem, notice, stale, reapplied, catalogue, catalogueFailed,
    selectedRoomId, selectedDeskId, floorRecords, roomsById,
    canUndo: canUndo(history), canRedo: canRedo(history),
    setBuildingId, setFloorId, setSelectedRoomId, setSelectedDeskId, setNotice, setProblem, clearReapplied: () => setReapplied(null),
    reload, reloadAndReapply, reloadDiscarding, discard, save,
    moveRoom, rotate, setDoor, moveDesk, placeDesk, placeRoom, undo: doUndo, redo: doRedo,
    addFromTemplate, addBlank, copyRoom, saveRoomDetails, deleteRoom, addDesk, renameDesk, saveDeskSeat, deleteDesk, seatAgent,
    saveFloor, saveBuilding, applyBuilding, deleteFloor, deleteBuilding,
    nameOf,
  };
}
export type BuilderController = ReturnType<typeof useBuilder>;

function explainCodeOnly(code: 'NO_FREE_SPACE'): Explained {
  return { title: t(`hqb.err.${code}`, { name: '', other: '' }), lines: [], stale: false, forbidden: false, unavailable: false };
}
