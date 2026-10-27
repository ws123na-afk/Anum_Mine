/** Role titles the Green Concrete action sets are shown in the page language; any other role stays as stored. */
import { t, type MessageKey } from './i18n.ts';

export function roleText(role: string): string {
  const key = `hqGc.role.${role}` as MessageKey;
  const text = t(key);
  return text === key ? role : text;
}
