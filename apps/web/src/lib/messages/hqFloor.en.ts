// Floor overview (docs/ANUM_MINE_FLOOR_OVERVIEW.md). hqFloor.ar.ts must have exactly these keys and placeholders (test/i18n.test.ts).
import type { Message } from './types.ts';

export const hqFloorEn = {
  'hqFloor.short': 'Floor overview',
  'hqFloor.enter': 'Show the whole floor from above, every room labelled',
  'hqFloor.leave': 'Leave the floor overview',
  'hqFloor.stats': 'Desks {desks}, agents {agents}',
  'hqFloor.listTitle': 'Rooms on {floor}',
  'hqFloor.summary': 'Rooms {rooms}, desks {desks}, agents seated {agents}.',
  'hqFloor.back': 'Back to the building view',
  'hqFloor.rail': 'Whole floor',
  'hqFloor.railHint': 'Show the whole open floor from above with every room labelled',
} satisfies Record<string, Message>;
