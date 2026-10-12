// Display labels for values the API sends as codes (task, run and approval statuses, run step
// types, risk levels). A code without a catalog entry shows as itself with underscores as spaces,
// so a status added on the server still reads sensibly before the catalogs learn it.
import { CATALOGS, t, type MessageKey } from './i18n.ts';

function known(key: string): key is MessageKey {
  return key in CATALOGS.en;
}

function label(prefix: string, value: string): string {
  const key = `${prefix}.${value}`;
  return known(key) ? t(key) : value.replaceAll('_', ' ');
}

export const statusLabel = (value: string) => label('status', value);
export const stepLabel = (value: string) => label('step', value);
export const riskLabel = (value: string) => label('approvals.risk', value.toLowerCase());
export const indexStateLabel = (value: string) => label('memory.index.state', value);
