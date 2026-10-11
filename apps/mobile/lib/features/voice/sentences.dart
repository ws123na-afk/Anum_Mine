/// Sentence splitting for speaking a reply while it streams, as the web
/// Voice view does (`takeSentences` in apps/web/src/lib/chat.ts).
library;

final _sentenceEnd = RegExp('([.!?。؟…]+["\'”’)\\]]*)(\\s+)|(\\n+)');

/// Splits off the complete sentences of a growing reply so speech can start
/// before the reply ends. The unfinished tail comes back as `rest`; pass
/// [isFinal] at the end to flush it. Very short fragments ("e.g.", "1.") are
/// merged into the next sentence.
({List<String> sentences, String rest}) takeSentences(String text,
    {bool isFinal = false, int minLength = 12}) {
  final sentences = <String>[];
  var start = 0;
  var pending = '';
  for (final match in _sentenceEnd.allMatches(text)) {
    final end = match.start + (match.group(1)?.length ?? 0);
    final piece = (pending + text.substring(start, end)).trim();
    start = match.end;
    if (piece.length < minLength) {
      pending = piece.isEmpty ? '' : '$piece ';
      continue;
    }
    sentences.add(piece);
    pending = '';
  }
  var rest = pending + text.substring(start);
  if (isFinal) {
    if (rest.trim().isNotEmpty) sentences.add(rest.trim());
    rest = '';
  }
  return (sentences: sentences, rest: rest);
}
