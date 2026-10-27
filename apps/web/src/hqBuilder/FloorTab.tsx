import { lazy, Suspense, useEffect, useState } from 'react';
import { t } from '../lib/i18n.ts';
import { pickName } from '../lib/registryNames.ts';
import type { BuilderController } from './useBuilderState.ts';
import { DEFAULT_DAY_START, DEFAULT_NIGHT_START, HOUR_PATTERN } from '../lib/hqShifts.ts';

// The building sign (logo, colour, style) is a chunk of its own, fetched when the Floor tab opens (docs/ANUM_MINE_HQ_LOGO_SIGNS.md).
const BuildingSignSection = lazy(() => import('../hq-logo-signs/BuildingSignSection.tsx'));

interface Props { b: BuilderController; editable: boolean }

/** Floor outline and names, and deleting an empty floor or building (the server lists what blocks it). */
export function FloorTab({ b, editable }: Props) {
  const floor = b.floor;
  const building = b.data.buildings.find((x) => x.id === b.buildingId);
  const [buildingName, setBuildingName] = useState(building?.name ?? '');
  const [buildingNameAr, setBuildingNameAr] = useState(building?.name_ar ?? '');
  const [name, setName] = useState(floor?.name ?? '');
  const [nameAr, setNameAr] = useState(floor?.name_ar ?? '');
  const [level, setLevel] = useState(floor?.level ?? 0);
  const [width, setWidth] = useState(floor?.width_m ?? 60);
  const [depth, setDepth] = useState(floor?.depth_m ?? 40);
  const [dayStart, setDayStart] = useState(building?.shift_day_start ?? '');
  const [nightStart, setNightStart] = useState(building?.shift_night_start ?? '');
  const [confirm, setConfirm] = useState<'' | 'floor' | 'building'>('');
  useEffect(() => { setBuildingName(building?.name ?? ''); setBuildingNameAr(building?.name_ar ?? ''); setDayStart(building?.shift_day_start ?? ''); setNightStart(building?.shift_night_start ?? ''); }, [building?.id, building?.revision]);
  useEffect(() => {
    setName(floor?.name ?? ''); setNameAr(floor?.name_ar ?? ''); setLevel(floor?.level ?? 0); setWidth(floor?.width_m ?? 60); setDepth(floor?.depth_m ?? 40); setConfirm('');
  }, [floor?.id, floor?.revision]);
  if (!floor || !building) return <p className="hqbHint">{t('hqb.floor.none')}</p>;
  const patch: { name?: string; name_ar?: string | null; level?: number; width_m?: number; depth_m?: number } = {};
  if (name.trim() && name.trim() !== floor.name) patch.name = name.trim();
  if ((nameAr.trim() || null) !== (floor.name_ar ?? null)) patch.name_ar = nameAr.trim() || null;
  if (level !== floor.level) patch.level = level;
  if (width !== floor.width_m || depth !== floor.depth_m) { patch.width_m = width; patch.depth_m = depth; }
  const changed = Object.keys(patch).length > 0;
  const buildingPatch: { name?: string; name_ar?: string | null; shift_day_start?: string | null; shift_night_start?: string | null } = {};
  if (buildingName.trim() && buildingName.trim() !== building.name) buildingPatch.name = buildingName.trim();
  if ((buildingNameAr.trim() || null) !== (building.name_ar ?? null)) buildingPatch.name_ar = buildingNameAr.trim() || null;
  if ((dayStart || null) !== (building.shift_day_start ?? null)) buildingPatch.shift_day_start = dayStart || null;
  if ((nightStart || null) !== (building.shift_night_start ?? null)) buildingPatch.shift_night_start = nightStart || null;
  const buildingChanged = Object.keys(buildingPatch).length > 0;
  const hoursValid = (!dayStart || HOUR_PATTERN.test(dayStart)) && (!nightStart || HOUR_PATTERN.test(nightStart)) && !(dayStart && dayStart === nightStart);
  const sizeValid = width >= 1 && width <= 1000 && depth >= 1 && depth <= 1000;
  const off = !editable || b.busy;
  return (
    <div className="hqbFloorTab">
      <form className="hqbSection" onSubmit={(e) => { e.preventDefault(); if (changed && sizeValid && name.trim()) void b.saveFloor(patch); }}>
        <h3>{t('hqb.floor.title')}</h3>
        {!b.bounds && <p className="hqbHint" role="status">{t('hqb.floor.unbounded')}</p>}
        <label className="hqbField"><span>{t('hqb.floor.name')}</span><input value={name} maxLength={100} dir="auto" disabled={off} onChange={(e) => setName(e.target.value)} /></label>
        <label className="hqbField"><span>{t('hqb.floor.nameAr')}</span><input value={nameAr} maxLength={100} dir="rtl" lang="ar" disabled={off} data-testid="hqb-floor-name-ar" onChange={(e) => setNameAr(e.target.value)} /></label>
        {!nameAr.trim() && <p className="hqbHint" role="note">{t('hqb.nameAr.hint')}</p>}
        <div className="hqbRow">
          <label className="hqbGrow"><span>{t('hqb.floor.level')}</span><input type="number" min={-20} max={120} value={level} disabled={off} onChange={(e) => setLevel(Math.round(Number(e.target.value)))} /></label>
          <label className="hqbGrow"><span>{t('hqb.floor.width')}</span><input type="number" min={1} max={1000} step="0.5" value={width} disabled={off} onChange={(e) => setWidth(Number(e.target.value))} /></label>
          <label className="hqbGrow"><span>{t('hqb.floor.depth')}</span><input type="number" min={1} max={1000} step="0.5" value={depth} disabled={off} onChange={(e) => setDepth(Number(e.target.value))} /></label>
        </div>
        {editable && <button type="submit" className="hqbPrimary" disabled={b.busy || !changed || !sizeValid || !name.trim()}>{t('hqb.floor.save')}</button>}
        {b.dirty && editable && <p className="hqbHint">{t('hqb.floor.draftNote')}</p>}
      </form>

      <form className="hqbSection" onSubmit={(e) => { e.preventDefault(); if (buildingChanged && hoursValid) void b.saveBuilding(buildingPatch); }}>
        <h3>{t('hqb.building.title')}</h3>
        <label className="hqbField"><span>{t('hqb.building.name')}</span><input value={buildingName} maxLength={100} dir="auto" disabled={off} onChange={(e) => setBuildingName(e.target.value)} /></label>
        <label className="hqbField"><span>{t('hqb.building.nameAr')}</span><input value={buildingNameAr} maxLength={100} dir="rtl" lang="ar" disabled={off} data-testid="hqb-building-name-ar" onChange={(e) => setBuildingNameAr(e.target.value)} /></label>
        {!buildingNameAr.trim() && <p className="hqbHint" role="note">{t('hqb.nameAr.hint')}</p>}
        <fieldset className="hqbShiftHours" data-testid="hqb-shift-hours">
          <legend>{t('hqGc.builder.shiftHours')}</legend>
          <div className="hqbRow">
            <label className="hqbGrow"><span>{t('hqGc.builder.dayStart')}</span><input type="text" inputMode="numeric" placeholder={DEFAULT_DAY_START} maxLength={5} dir="ltr" value={dayStart} disabled={off} aria-invalid={!hoursValid} data-testid="hqb-day-start" onChange={(e) => setDayStart(e.target.value.trim())} /></label>
            <label className="hqbGrow"><span>{t('hqGc.builder.nightStart')}</span><input type="text" inputMode="numeric" placeholder={DEFAULT_NIGHT_START} maxLength={5} dir="ltr" value={nightStart} disabled={off} aria-invalid={!hoursValid} data-testid="hqb-night-start" onChange={(e) => setNightStart(e.target.value.trim())} /></label>
          </div>
          <p className="hqbHint" role="note">{t('hqGc.builder.hoursHelp')}</p>
          {!hoursValid && <p className="hqbHint" role="alert">{t('hqGc.builder.hoursInvalid')}</p>}
        </fieldset>
        {editable && <button type="submit" disabled={b.busy || !buildingName.trim() || !buildingChanged || !hoursValid}>{t('hqb.building.save')}</button>}
      </form>

      <Suspense fallback={<p className="hqbHint" role="status">{t('hqLogo.loading')}</p>}>
        <BuildingSignSection building={building} editable={editable} busy={b.busy} onChanged={b.applyBuilding} onReload={() => { void b.reload(); }} />
      </Suspense>

      {editable && (
        <section className="hqbSection">
          <h3>{t('hqb.floor.dangerTitle')}</h3>
          <p className="hqbHint">{t('hqb.floor.dangerHint')}</p>
          {confirm === '' && (
            <div className="hqbRow">
              <button type="button" className="hqbQuiet" onClick={() => setConfirm('floor')}>{t('hqb.floor.delete')}</button>
              <button type="button" className="hqbQuiet" onClick={() => setConfirm('building')}>{t('hqb.building.delete')}</button>
            </div>
          )}
          {confirm !== '' && (
            <div className="hqbConfirm" role="alertdialog" aria-label={t('hqb.floor.dangerTitle')}>
              <p>{confirm === 'floor' ? t('hqb.floor.deleteQuestion', { name: pickName(floor.name, floor.name_ar) }) : t('hqb.building.deleteQuestion', { name: pickName(building.name, building.name_ar) })}</p>
              <div className="hqbRow">
                <button type="button" className="hqbDanger" disabled={b.busy} onClick={() => { void (confirm === 'floor' ? b.deleteFloor() : b.deleteBuilding()).then(() => setConfirm('')); }}>
                  {confirm === 'floor' ? t('hqb.floor.delete') : t('hqb.building.delete')}
                </button>
                <button type="button" onClick={() => setConfirm('')}>{t('hqb.common.cancel')}</button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
