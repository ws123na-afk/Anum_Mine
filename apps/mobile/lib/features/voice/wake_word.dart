/// Wake-by-name matching. Pure functions so they are easy to test.
library;

const defaultAssistantName = 'Anum';

/// Recognizers rarely spell an invented name the same way twice, so the
/// default name also answers to common transcriptions, in Latin and Arabic.
const _defaultNameAliases = [
  'anum',
  'a num',
  'anam',
  'anoom',
  'anoum',
  'annum',
  'أنوم',
  'انوم',
  'آنوم',
  'أنم',
  'انم',
];

List<String> wakeAliases(String name) {
  final clean = name.trim();
  if (clean.isEmpty) return const [];
  if (clean.toLowerCase() == defaultAssistantName.toLowerCase()) {
    return _defaultNameAliases;
  }
  return [clean];
}

const _separators = r'[\s,.!?،:;-]';

/// If [heard] calls the assistant by [name] (optionally after hey, hi, ok,
/// okay, hello or يا), returns what was said after the name, which may be
/// empty. Returns null when the name was not said.
///
/// "Anum, what's running?" -> "what's running?"; "hey anum" -> "".
String? matchWakeWord(String heard, String name) {
  final aliases = wakeAliases(name);
  if (aliases.isEmpty) return null;
  final names = aliases.map(RegExp.escape).join('|');
  final pattern = RegExp(
    '(^|\\s)((hey|hi|ok|okay|hello|يا)\\s*)?($names)(?=$_separators|\$)',
    caseSensitive: false,
    unicode: true,
  );
  final match = pattern.firstMatch(heard);
  if (match == null) return null;
  return heard
      .substring(match.end)
      .replaceFirst(RegExp('^$_separators+', unicode: true), '')
      .trim();
}

/// Keeps only characters the API accepts for an assistant name.
String sanitizeAssistantName(String value) {
  final clean = value
      .replaceAll(RegExp(r"[^\p{L}\p{N} .'_-]", unicode: true), '')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();
  return clean.length <= 40 ? clean : clean.substring(0, 40).trim();
}
