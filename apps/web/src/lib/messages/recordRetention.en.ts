// How long records are kept (owner decision O5, docs/record-retention.md), English. Spread into en.ts;
// recordRetention.ar.ts has the same keys.
import type { Message } from './types.ts';

export const recordRetentionEn = {
  'recordRetention.title': 'How long records are kept',
  'recordRetention.lead': 'ANUM deletes these records on its own after the periods below. The standard periods apply until an owner changes them.',
  'recordRetention.loading': 'Reading how long records are kept...',
  'recordRetention.error': "Couldn't read how long records are kept: {message} Check your connection, then try again.",
  'recordRetention.denied': 'You cannot see this setting: {message} Ask a workspace owner to add you to this workspace.',
  'recordRetention.retry': 'Try again',
  'recordRetention.days': { one: '{count} day', other: '{count} days' },
  'recordRetention.badge.standard': 'Standard',
  'recordRetention.badge.changed': 'Set by an owner',
  'recordRetention.transcripts.title': 'Meeting transcripts',
  'recordRetention.transcripts.text': 'The messages and event log of a meeting are deleted {period} after it ends. The meeting, its summary, confirmed decisions, their links and tasks stay.',
  'recordRetention.visitors.title': 'Visitor records',
  'recordRetention.visitors.text': "A visit is deleted {period} after its last time: the check-out, the cancellation, or the planned time for a visitor who never came.",
  'recordRetention.snapshots': 'Office history snapshots: none are stored, so there is nothing to delete.',
  'recordRetention.standardIs': 'Standard: {period}.',
  'recordRetention.daysLabel': 'Days to keep, from {min} to {max}',
  'recordRetention.save': 'Save',
  'recordRetention.useStandard': 'Use the standard ({period})',
  'recordRetention.saved': 'Saved. The next daily clean-up uses {period}.',
  'recordRetention.savedStandard': 'Saved. The standard {period} applies again.',
  'recordRetention.invalid.empty': 'Enter a number of days.',
  'recordRetention.invalid.notWhole': 'Enter whole days, for example 90.',
  'recordRetention.invalid.range': 'Choose from {min} to {max} days.',
  'recordRetention.stale': 'Someone changed this setting a moment ago. The current periods are shown: check them and save again.',
  'recordRetention.ownerOnly': 'Only a workspace owner can change how long records are kept.',
  'recordRetention.isDefault': 'Nobody has changed these periods.',
  'recordRetention.lastChanged': 'Last changed by {user}, {when}.',
  'recordRetention.daily': 'The clean-up runs once a day, so a record can stay up to a day longer than its period.',
} satisfies Record<string, Message>;
