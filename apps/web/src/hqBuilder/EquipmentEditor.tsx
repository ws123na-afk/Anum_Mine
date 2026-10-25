import { useId } from 'react';
import type { OfficeEquipmentItem, OfficeEquipmentKind } from '@anum/contracts';
import { t, type MessageKey } from '../lib/i18n.ts';
import { EQUIPMENT_KINDS, MAX_EQUIPMENT_KINDS } from './labels.ts';

interface Props {
  items: OfficeEquipmentItem[];
  /** Equipment kinds the server offers (GET /templates); the fixed list when that call failed. */
  kinds: readonly OfficeEquipmentKind[];
  disabled: boolean;
  onChange: (items: OfficeEquipmentItem[]) => void;
}

export const equipmentLabel = (kind: string): string => t(`hqb.equipment.${kind}` as MessageKey);

/** Equipment from the fixed catalogue: each kind once, with a quantity and an optional note. */
export function EquipmentEditor({ items, kinds, disabled, onChange }: Props) {
  const addId = useId();
  const catalogue = kinds.length ? kinds : EQUIPMENT_KINDS;
  const free = catalogue.filter((kind) => !items.some((item) => item.kind === kind));
  const update = (kind: OfficeEquipmentKind, patch: Partial<OfficeEquipmentItem>) =>
    onChange(items.map((item) => (item.kind === kind ? { ...item, ...patch } : item)));
  return (
    <fieldset className="hqbFieldset" disabled={disabled}>
      <legend>{t('hqb.room.equipment')}</legend>
      {items.length === 0 && <p className="hqbHint">{t('hqb.room.equipmentEmpty')}</p>}
      <ul className="hqbEquipment">
        {items.map((item) => (
          <li key={item.kind}>
            <span className="hqbEquipmentName">{equipmentLabel(item.kind)}</span>
            <label>
              <span className="hqbVisuallyHidden">{t('hqb.room.equipmentQty', { name: equipmentLabel(item.kind) })}</span>
              <input type="number" min={1} max={999} value={item.quantity} onChange={(e) => update(item.kind, { quantity: Math.max(1, Math.min(999, Math.round(Number(e.target.value)) || 1)) })} />
            </label>
            <label className="hqbEquipmentNote">
              <span className="hqbVisuallyHidden">{t('hqb.room.equipmentNote', { name: equipmentLabel(item.kind) })}</span>
              <input type="text" maxLength={120} dir="auto" placeholder={t('hqb.room.equipmentNotePlaceholder')} value={item.note ?? ''} onChange={(e) => update(item.kind, { note: e.target.value || undefined })} />
            </label>
            <button type="button" className="hqbIconButton" aria-label={t('hqb.room.equipmentRemove', { name: equipmentLabel(item.kind) })} onClick={() => onChange(items.filter((x) => x.kind !== item.kind))}>×</button>
          </li>
        ))}
      </ul>
      {items.length < MAX_EQUIPMENT_KINDS && free.length > 0 && (
        <div className="hqbRow">
          <label htmlFor={addId} className="hqbVisuallyHidden">{t('hqb.room.equipmentAdd')}</label>
          <select id={addId} value="" onChange={(e) => { if (e.target.value) onChange([...items, { kind: e.target.value as OfficeEquipmentKind, quantity: 1 }]); }}>
            <option value="">{t('hqb.room.equipmentAdd')}</option>
            {free.map((kind) => <option key={kind} value={kind}>{equipmentLabel(kind)}</option>)}
          </select>
        </div>
      )}
    </fieldset>
  );
}
