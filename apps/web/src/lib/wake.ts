// Wake-by-name matching and greetings. Pure functions, kept in step with the Flutter app
// (apps/mobile/lib/features/voice/wake_word.dart) so both answer to the same phrases.

export const defaultAssistantName = 'Anum';

/** Recognisers rarely spell an invented name the same way twice, so the default name also answers to common transcriptions. */
const defaultNameAliases = ['anum', 'a num', 'anam', 'anoom', 'anoum', 'annum', 'أنوم', 'انوم', 'آنوم', 'أنم', 'انم'];

export function wakeAliases(name: string): string[] {
  const clean = name.trim();
  if (!clean) return [];
  if (clean.toLowerCase() === defaultAssistantName.toLowerCase()) return defaultNameAliases;
  return [clean];
}

const separators = "[\\s,.!?،:;-]";
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * If `heard` calls the assistant by `name` (optionally after hey, hi, ok, okay, hello or يا),
 * returns what was said after the name, which may be empty. Returns null when the name was not said.
 * "Anum, what's running?" -> "what's running?"; "hey anum" -> "".
 */
export function matchWakeWord(heard: string, name: string): string | null {
  const aliases = wakeAliases(name);
  if (!aliases.length) return null;
  const names = aliases.map(escape).join('|');
  const pattern = new RegExp(`(^|\\s)((hey|hi|ok|okay|hello|يا)\\s*)?(${names})(?=${separators}|$)`, 'iu');
  const match = pattern.exec(heard);
  if (!match) return null;
  return heard.slice(match.index + match[0].length).replace(new RegExp(`^${separators}+`, 'u'), '').trim();
}

/** Keeps only characters the API accepts for an assistant name. */
export function sanitizeAssistantName(value: string): string {
  return value.replace(/[^\p{L}\p{N} .'_-]/gu, '').replace(/\s+/g, ' ').trim().slice(0, 40).trim();
}

/** A short, time-aware greeting in the assistant's own voice. */
export function greeting(name: string, locale: string, hour: number): string {
  if (locale.toLowerCase().startsWith('ar')) {
    return `${hour < 12 ? 'صباح الخير' : 'مساء الخير'}. أنا ${name}.`;
  }
  const part = hour < 5 ? 'Good evening' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  return `${part}. I'm ${name}.`;
}
