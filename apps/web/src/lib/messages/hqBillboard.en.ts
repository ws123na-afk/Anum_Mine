// HQ building billboards (docs/ANUM_MINE_HQ_BILLBOARDS.md). hqBillboard.ar.ts must have exactly these keys and placeholders (test/i18n.test.ts).
// Every sentence here is fixed text: the billboard never prints anything that is not one of these or a real record field.
import type { Message } from './types.ts';

export const hqBillboardEn = {
  'hqBillboard.group': 'Billboard for {name}',
  'hqBillboard.headAria': '{name}. {summary}. Billboard.',
  'hqBillboard.purpose.executive': 'An executive building: the leadership centre of the campus.',
  'hqBillboard.purpose.business': 'A business building: where one business of the campus is run.',
  'hqBillboard.purpose.department': 'A department building: the home of one department.',
  'hqBillboard.purpose.personal': 'A personal building: the owner\'s own office.',
  'hqBillboard.purpose.unset': 'Its building type is not set yet. Its real rooms are listed below.',
  'hqBillboard.label.about': 'What it does',
  'hqBillboard.label.size': 'Size',
  'hqBillboard.label.departments': 'Departments',
  'hqBillboard.label.rooms': 'Rooms',
  'hqBillboard.label.presence': 'Agents now',
  'hqBillboard.label.work': 'Open work',
  'hqBillboard.notConnected': 'Not connected',
  'hqBillboard.noAccess': 'No access',
  'hqBillboard.more': '+{count} more',
  'hqBillboard.noRooms': 'No rooms in the registry yet',
  'hqBillboard.work.open': 'Open tasks',
  'hqBillboard.work.waiting': 'Waiting for approval',
  'hqBillboard.work.blocked': 'Blocked',
  'hqBillboard.verified': 'Verified {time}',
  'hqBillboard.stale': 'Stale: last verified {time}',
  'hqBillboard.noTime': 'Verification time not reported',
  'hqBillboard.open': 'Open {name}',
  'hqBillboard.unavailable': 'This building is not available to you right now.',
} satisfies Record<string, Message>;
