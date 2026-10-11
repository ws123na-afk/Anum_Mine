// "Remember things from my chats": the caller's own switch for conversation memory
// (GET/PUT /api/v1/me/preferences, docs/memory.md#from-conversations). Pure, so the unit tests
// run without a browser.
import { t } from './i18n.ts';

import type { ConversationMemoryState, UserPreferencesView } from '@anum/contracts';

/** Whether the Assistant remembers from and recalls into the caller's chats, and if not, why. */
export type { ConversationMemoryState };
/** `GET`/`PUT /api/v1/me/preferences` (packages/contracts/src/account.ts). */
export type UserPreferences = UserPreferencesView;

export const PREFERENCES_PATH = '/api/v1/me/preferences';
/** DELETE: every memory remembered from the caller's own chats in this workspace; answers `{deleted}`. */
export const CHAT_MEMORIES_PATH = '/api/v1/memories?source_type=conversation';

export interface ChatMemoryStatus {
  /** Short badge text. */
  badge: string;
  tone: 'success' | 'neutral' | 'warning';
  /** What happens now, and who can change it when it is not the caller. */
  text: string;
}


export function chatMemoryStatus(preferences: Pick<UserPreferences, 'conversation_memory'>): ChatMemoryStatus {
  switch (preferences.conversation_memory) {
    case 'on':
      return { badge: t('chatMemory.badge.on'), tone: 'success', text: t('chatMemory.text.on') };
    case 'off_by_user':
      return { badge: t('chatMemory.badge.off'), tone: 'neutral', text: t('chatMemory.text.offByUser', { kept: t('chatMemory.kept') }) };
    case 'off_on_server':
      return { badge: t('chatMemory.badge.offServer'), tone: 'warning', text: t('chatMemory.text.offServer', { kept: t('chatMemory.kept') }) };
    case 'off_for_organization':
      return { badge: t('chatMemory.badge.offOrg'), tone: 'warning', text: t('chatMemory.text.offOrg', { kept: t('chatMemory.kept') }) };
    case 'not_for_role':
      return { badge: t('chatMemory.badge.notForRole'), tone: 'warning', text: t('chatMemory.text.notForRole') };
    default:
      return { badge: t('chatMemory.badge.unknown'), tone: 'neutral', text: t('chatMemory.text.unknown') };
  }
}

/** The sentence after a successful save. */
export function savedMessage(preferences: Pick<UserPreferences, 'remember_conversations'>): string {
  return preferences.remember_conversations ? t('chatMemory.savedOn') : t('chatMemory.savedOff');
}

/** The sentence after "Delete all from my chats". */
export function deletedMessage(deleted: number): string {
  return t('chatMemory.deleted', { count: deleted });
}
