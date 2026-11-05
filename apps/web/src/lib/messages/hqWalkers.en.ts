// Agent walkers on the campus (docs/ANUM_MINE_HQ_AGENT_WALKERS.md). hqWalkers.ar.ts must have exactly these keys and placeholders (test/i18n.test.ts).
export const hqWalkersEn = {
  'hqWalkers.because.meeting_started': 'Because: meeting started {time} in {place}',
  'hqWalkers.because.meeting_ended': 'Because: meeting in {place} ended {time}',
  'hqWalkers.because.desk_moved': 'Because: moved to {place}, verified {time}',
  'hqWalkers.place.room': 'a room',
  'hqWalkers.place.building': 'another building',
  'hqWalkers.who.unnamed': 'An agent',
  'hqWalkers.walking': '{who} is on the way to {building}. {because}.',
  'hqWalkers.arrived': '{who} arrived at {building}. {because}.',
  'hqWalkers.figure': '{who}: {because}',
  'hqWalkers.status.label': 'Agents on the move',
  'hqWalkers.status.count': '{count} on the move',
  'hqWalkers.status.more': '{count} more agents on the move',
  'hqWalkers.status.off': 'Agents on the move: Not connected',
  'hqWalkers.status.offHint': 'Meetings could not be read, so no agent is drawn walking. Nothing is shown in place of a record.',
  'hqWalkers.legend': 'Figures are drawn larger than life. Each one walks for one stored record.',
} as const;
