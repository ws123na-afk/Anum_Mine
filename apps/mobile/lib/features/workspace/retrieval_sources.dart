// "Sources used" on a run, read from the run's `retrieval` step, which the API
// records with chunk ids, source ids and scores only, never the retrieved text
// (docs/memory.md, At run time). Mirrors apps/web/src/lib/retrieval.ts. Nothing
// here invents data: a run without a retrieval step has no sources record.
import 'workspace_models.dart';

/// The retrieval step's `status`; an unknown value reads as [unavailable].
enum RetrievalState { ok, noResults, skipped, unavailable }

/// One retrieved passage a run's prompt used (contracts' `RetrievedSourceUsed`).
class RetrievedSourceUsed {
  const RetrievedSourceUsed({
    required this.chunkId,
    required this.sourceType,
    required this.sourceId,
    required this.chunkIndex,
    required this.score,
    required this.truncated,
  });

  final String chunkId;

  /// `memory` or `file`.
  final String sourceType;
  final String sourceId;

  /// Zero-based position of the passage in its source.
  final int chunkIndex;

  /// Cosine similarity, 0 to 1.
  final double score;

  /// Whether the passage was shortened to fit the prompt limit.
  final bool truncated;

  bool get isMemory => sourceType == 'memory';
}

/// The run's retrieval record.
class RunSources {
  const RunSources({
    required this.state,
    required this.sources,
    this.reason,
    this.truncated = false,
    this.embeddingModel,
  });

  final RetrievalState state;
  final List<RetrievedSourceUsed> sources;
  final String? reason;
  final bool truncated;
  final String? embeddingModel;
}

/// Passages of one memory or file, in first-used order.
class SourceGroup {
  SourceGroup(this.sourceType, this.sourceId);
  final String sourceType, sourceId;
  final List<RetrievedSourceUsed> passages = [];
  bool get isMemory => sourceType == 'memory';
}

RetrievalState _state(Object? value) => switch (value) {
      'ok' => RetrievalState.ok,
      'no_results' => RetrievalState.noResults,
      'skipped' => RetrievalState.skipped,
      _ => RetrievalState.unavailable,
    };

RetrievedSourceUsed? _source(Object? value) {
  if (value is! Map) return null;
  final type = value['source_type'];
  final chunkId = value['chunk_id'];
  final sourceId = value['source_id'];
  if (type is! String || (type != 'memory' && type != 'file')) return null;
  if (chunkId is! String || sourceId is! String) return null;
  final index = value['chunk_index'];
  final score = value['score'];
  return RetrievedSourceUsed(
    chunkId: chunkId,
    sourceType: type,
    sourceId: sourceId,
    chunkIndex: index is num ? index.toInt() : 0,
    score: score is num && score.isFinite ? score.toDouble() : 0,
    truncated: value['truncated'] == true,
  );
}

/// The run's retrieval record, or null when the run has no retrieval step.
/// Malformed source entries are dropped rather than shown with made-up values.
RunSources? runSources(WorkspaceRun? run) {
  if (run == null) return null;
  RunStep? step;
  for (final candidate in run.steps) {
    if (candidate.type == 'retrieval') {
      step = candidate;
      break;
    }
  }
  if (step == null) return null;
  final metadata = step.metadata;
  final raw = metadata['sources'];
  final reason = metadata['reason'];
  final model = metadata['embedding_model'];
  return RunSources(
    state: _state(metadata['status']),
    sources: raw is List
        ? raw.map(_source).whereType<RetrievedSourceUsed>().toList()
        : const [],
    reason: reason is String && reason.isNotEmpty ? reason : null,
    truncated: metadata['truncated'] == true,
    embeddingModel: model is String ? model : null,
  );
}

List<SourceGroup> groupSources(List<RetrievedSourceUsed> sources) {
  final groups = <String, SourceGroup>{};
  for (final source in sources) {
    groups
        .putIfAbsent('${source.sourceType}:${source.sourceId}',
            () => SourceGroup(source.sourceType, source.sourceId))
        .passages
        .add(source);
  }
  return groups.values.toList();
}

String sourceTypeLabel(String sourceType) =>
    sourceType == 'memory' ? 'Memory' : 'File';

/// One line explaining an empty or degraded retrieval, or null.
String? sourcesNote(RunSources record) => switch (record.state) {
      RetrievalState.ok => record.truncated
          ? 'Some retrieved text was shortened to fit the prompt limit.'
          : null,
      RetrievalState.noResults =>
        'No indexed memory or file matched this task.',
      RetrievalState.skipped =>
        'Retrieval skipped${record.reason == null ? '' : ': ${record.reason}'}.',
      RetrievalState.unavailable =>
        'Retrieval was unavailable; the task ran without workspace context.',
    };

String scoreLabel(double score) =>
    '${(score.clamp(0, 1) * 100).round()}% match';

/// "passage 2, 87% match, shortened" (chunk indexes are zero-based).
String passageLabel(RetrievedSourceUsed passage) =>
    'passage ${passage.chunkIndex + 1}, ${scoreLabel(passage.score)}'
    '${passage.truncated ? ', shortened' : ''}';
