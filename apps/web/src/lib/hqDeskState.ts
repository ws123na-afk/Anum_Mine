/** What a desk looks like, from the registry and the presence record behind it (docs/ANUM_MINE_GREEN_CONCRETE.md). Pure. */
import { visualOfStatus, type VisualState } from '../hqAssets/interior/stateVisual.ts';
import type { SceneDesk } from './headquartersProjection.ts';
import type { HqAgentPresence } from './hqPresence.ts';

/**
 * A desk with nobody is vacant. A person whose shift is not now is `offshift`: a dark screen whatever work is waiting (the
 * label says "Off shift" or "Off shift, work waiting", never "absent"). Anyone else shows what their presence record says, and
 * a stale record is drawn as unknown. A screen is lit only for a fresh working, queued, waiting or meeting record of an on shift person.
 */
export function deskVisual(desk: Pick<SceneDesk, 'agentId' | 'shiftInfo'>, presence: HqAgentPresence | undefined): VisualState {
  if (!desk.agentId && !presence) return 'vacant';
  if (desk.shiftInfo && !desk.shiftInfo.onShift) return 'offshift';
  return presence ? visualOfStatus(presence.status, presence.stale) : 'assigned';
}
