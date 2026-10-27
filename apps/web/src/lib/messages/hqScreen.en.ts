// HQ live screens (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). hqScreen.ar.ts must have exactly these keys and placeholders (test/i18n.test.ts).
// Every sentence a 3D screen prints is one of these, a label that already exists elsewhere in the catalogue, or a number / time from a real record.
import type { Message } from './types.ts';

export const hqScreenEn = {
  'hqScreen.live': 'Live',
  'hqScreen.stale': 'Stale',
  'hqScreen.notConnected': 'Not connected',
  'hqScreen.updated': 'Updated {time}',
  'hqScreen.verified': 'Verified {time}',
  'hqScreen.offShift': 'Off shift',
  'hqScreen.vacant': 'No one here',
  'hqScreen.record.task': 'Task run',
  'hqScreen.record.queued': 'Task queued',
  'hqScreen.record.meeting': 'Meeting',
  'hqScreen.record.approval': 'Approval',
  'hqScreen.running': 'Running {time}',
  'hqScreen.queuedFor': 'Queued {time}',
  'hqScreen.waitingFor': 'Waiting {time}',
  'hqScreen.flowTitle': 'Latest flow',
  'hqScreen.flowNone': 'Nothing is moving right now.',
  'hqScreen.flowRow': '{kind} · {stage} · {time}',
  'hqScreen.tyreOff': '{name}: Not connected',
  'hqScreen.tyreNoAccess': '{name}: No access',
  'hqScreen.dur.s': '{n}s',
  'hqScreen.dur.m': '{n}m',
  'hqScreen.dur.h': '{h}h {m}m',
  'hqScreen.dur.d': '{d}d {h}h',
  'hqScreen.wallAria': 'Operations wall screen',
} as const satisfies Record<string, Message>;
