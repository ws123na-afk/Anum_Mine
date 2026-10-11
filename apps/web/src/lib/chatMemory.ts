// "Remember things from my chats": the caller's own switch for conversation memory
// (GET/PUT /api/v1/me/preferences, docs/memory.md#from-conversations). Pure, so the unit tests
// run without a browser.

/** Whether the Assistant remembers from and recalls into the caller's chats, and if not, why. */
export type ConversationMemoryState = 'on' | 'off_by_user' | 'off_on_server' | 'off_for_organization' | 'not_for_role';

export interface UserPreferences {
  remember_conversations: boolean;
  updated_at: string | null;
  conversation_memory: ConversationMemoryState;
}

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

const KEPT = 'Memories already saved from your chats stay until you delete them.';

export function chatMemoryStatus(preferences: Pick<UserPreferences, 'conversation_memory'>): ChatMemoryStatus {
  switch (preferences.conversation_memory) {
    case 'on':
      return { badge: 'On', tone: 'success', text: 'The Assistant remembers lasting facts you state in your chats (your name, role or how you like answers) and uses them in later answers. Only you can see them.' };
    case 'off_by_user':
      return { badge: 'Off', tone: 'neutral', text: `Nothing new is remembered from your chats and no memories are used in your answers. ${KEPT}` };
    case 'off_on_server':
      return { badge: 'Off on this server', tone: 'warning', text: `The server administrator has turned chat memory off for everyone (ANUM_CONVERSATION_MEMORY_ENABLED). Your choice is saved and applies if it is turned back on. ${KEPT}` };
    case 'off_for_organization':
      return { badge: 'Off for your organization', tone: 'warning', text: `Your organization's memory policy does not allow memories from chats. An organization administrator can allow the "conversation" source under Governance. Your choice is saved and applies if they do. ${KEPT}` };
    case 'not_for_role':
      return { badge: 'Not remembering', tone: 'warning', text: 'Your role in this workspace cannot save memories, so nothing new is remembered from your chats; memories you can read are still used in your answers. A workspace owner can change your role.' };
    default:
      return { badge: 'Unknown', tone: 'neutral', text: 'The server reported a state this version does not know.' };
  }
}

/** The sentence after a successful save. */
export function savedMessage(preferences: Pick<UserPreferences, 'remember_conversations'>): string {
  return preferences.remember_conversations
    ? 'Saved. The Assistant will remember things from your chats.'
    : 'Saved. The Assistant will no longer remember or use things from your chats.';
}

/** The sentence after "Delete all from my chats". */
export function deletedMessage(deleted: number): string {
  if (deleted === 0) return 'There were no memories from your chats to delete.';
  return `Deleted ${deleted} ${deleted === 1 ? 'memory' : 'memories'} from your chats.`;
}
