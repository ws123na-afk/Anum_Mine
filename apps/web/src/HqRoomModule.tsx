/** The module link of a room: in the room inspector and in the room list. Pure view of `resolveRoomModule`. */
import { ArrowRight } from 'lucide-react';
import { resolveRoomModule, moduleHash, type RoomModuleInput } from './lib/hqRoomModule.ts';
import { t, type MessageKey } from './lib/i18n.ts';

export type RoomModuleActionProps = Readonly<{
  room: RoomModuleInput;
  /** Owner of the workspace (workspace:manage): sees the "set a department" hint. */
  canManage?: boolean;
  /** Toggles the live flow dock. */
  onOpenFlow: () => void;
  /** Opens the Organisation gaps view. */
  onOpenGaps: () => void;
  variant?: 'panel' | 'list';
}>;

export function RoomModuleAction({ room, canManage = false, onOpenFlow, onOpenGaps, variant = 'panel' }: RoomModuleActionProps) {
  const m = resolveRoomModule(room);
  const cls = variant === 'panel' ? 'hqPrimary' : 'hqRoomModuleLink';
  const moduleName = t(`hqRoomModule.name.${m.id}` as MessageKey);
  const label = t('hqRoomModule.open', { module: moduleName });
  const hash = moduleHash(m.target);
  const showHint = variant === 'panel' && m.needsDepartment && !(m.target.type === 'none' && m.target.why === 'no_visitor_register');
  return (
    <div className="hqRoomModule" data-variant={variant} data-source={m.source} data-module={m.id}>
      {hash !== null && <a className={cls} href={hash} data-testid="room-module">{label}<ArrowRight aria-hidden="true" className="flip" /></a>}
      {m.target.type === 'flow' && <button type="button" className={cls} data-testid="room-module" onClick={onOpenFlow}>{label}<ArrowRight aria-hidden="true" className="flip" /></button>}
      {m.target.type === 'none' && (
        <>
          <p className="hqNote" data-testid="room-module-none">{t(m.target.why === 'no_visitor_register' ? 'hqRoomModule.noVisitor' : 'hqRoomModule.noModule')}</p>
          {variant === 'panel' && <button type="button" className="hqGhost" onClick={onOpenGaps}>{t('hqRoomModule.gaps')}</button>}
        </>
      )}
      {variant === 'panel' && m.source === 'name' && <p className="hqNote">{t('hqRoomModule.from.name')}</p>}
      {showHint && <p className="hqNote" data-testid="room-module-hint">{t(canManage ? 'hqRoomModule.setDepartment' : 'hqRoomModule.setDepartmentOwner')}</p>}
    </div>
  );
}
