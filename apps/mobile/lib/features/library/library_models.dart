/// Favorites, knowledge base and reports (docs/knowledge-base.md,
/// docs/reports.md): the API's shapes and the pure rules the screens use,
/// mirroring the web client's `apps/web/src/lib/library.ts`.
library;

import '../../data/json_read.dart';
import '../reports/report_sections.dart';

const favoriteTypes = [
  'conversation',
  'task',
  'report',
  'collection',
  'file',
  'memory',
  'automation',
  'agent',
];

class Favorite {
  const Favorite({
    required this.id,
    required this.itemType,
    required this.itemId,
    required this.label,
    required this.createdAt,
  });

  factory Favorite.fromReader(JsonReader r) => Favorite(
        id: r.string('id'),
        itemType: r.string('item_type'),
        itemId: r.string('item_id'),
        label: r.string('label'),
        createdAt: r.date('created_at'),
      );

  final String id, itemType, itemId, label;
  final DateTime createdAt;
}

class LibraryPage<T> {
  const LibraryPage(this.items, this.total);
  final List<T> items;
  final int total;
}

class ReportMetric {
  const ReportMetric({
    required this.label,
    required this.value,
    this.unit,
    this.changePercent,
  });

  factory ReportMetric.fromReader(JsonReader r) => ReportMetric(
        label: r.string('label'),
        value: r.string('value'),
        unit: r.optString('unit'),
        changePercent: r.optNumber('change_percent'),
      );

  final String label, value;
  final String? unit;
  final double? changePercent;

  String get display => unit == null || unit!.isEmpty ? value : '$value $unit';
}

/// "+8.5%" / "-3%"; null when there is no earlier period to compare with.
String? changeLabel(double? change) {
  if (change == null || !change.isFinite) return null;
  final rounded = (change * 10).round() / 10;
  final text = rounded == rounded.roundToDouble()
      ? rounded.toStringAsFixed(0)
      : rounded.toString();
  return '${rounded > 0 ? '+' : ''}$text%';
}

/// The card under a structured Assistant answer ("View Full Report").
class ReportCard {
  const ReportCard({
    required this.reportId,
    required this.title,
    required this.type,
    required this.metrics,
    required this.highlights,
    this.turnId,
    this.periodStart,
    this.periodEnd,
  });

  factory ReportCard.fromReader(JsonReader r) => ReportCard(
        reportId: r.string('report_id'),
        turnId: r.optString('turn_id'),
        title: r.string('title'),
        type: r.string('type'),
        periodStart: r.optString('period_start'),
        periodEnd: r.optString('period_end'),
        metrics: r.optObjects('metrics', ReportMetric.fromReader),
        highlights: r.optStrings('highlights') ?? const [],
      );

  /// A card from a stream event's `report`, or null when it is not one.
  static ReportCard? tryParse(Object? value) {
    if (value is! Map<String, Object?>) return null;
    try {
      return ReportCard.fromReader(JsonReader(value));
    } on FormatException {
      return null;
    }
  }

  final String reportId, title, type;
  final String? turnId, periodStart, periodEnd;
  final List<ReportMetric> metrics;
  final List<String> highlights;
}

class ReportSummary {
  const ReportSummary({
    required this.id,
    required this.type,
    required this.title,
    required this.summary,
    required this.metrics,
    required this.visibility,
    required this.createdByKind,
    required this.createdAt,
    this.periodStart,
    this.periodEnd,
  });

  factory ReportSummary.fromReader(JsonReader r) => ReportSummary(
        id: r.string('id'),
        type: r.string('type'),
        title: r.string('title'),
        summary: r.optString('summary') ?? '',
        metrics: r.optObjects('metrics', ReportMetric.fromReader),
        visibility: r.string('visibility'),
        createdByKind: r.string('created_by_kind'),
        createdAt: r.date('created_at'),
        periodStart: r.optString('period_start'),
        periodEnd: r.optString('period_end'),
      );

  final String id, type, title, summary, visibility, createdByKind;
  final String? periodStart, periodEnd;
  final List<ReportMetric> metrics;
  final DateTime createdAt;
}

class ReportPage extends LibraryPage<ReportSummary> {
  const ReportPage(super.items, super.total, this.types);
  final List<String> types;
}

class Report extends ReportSummary {
  const Report({
    required super.id,
    required super.type,
    required super.title,
    required super.summary,
    required super.metrics,
    required super.visibility,
    required super.createdByKind,
    required super.createdAt,
    super.periodStart,
    super.periodEnd,
    required this.highlights,
    required this.columns,
    required this.rows,
    required this.bodyMarkdown,
    required this.canManage,
    required this.createdBy,
    this.conversationId,
    this.layout = const ReportLayout(),
  });

  factory Report.fromReader(JsonReader r) {
    final summary = ReportSummary.fromReader(r);
    final data = r.optObject('data');
    return Report(
      id: summary.id,
      type: summary.type,
      title: summary.title,
      summary: summary.summary,
      metrics: summary.metrics,
      visibility: summary.visibility,
      createdByKind: summary.createdByKind,
      createdAt: summary.createdAt,
      periodStart: summary.periodStart,
      periodEnd: summary.periodEnd,
      highlights: r.optStrings('highlights') ?? const [],
      columns: data?.optStrings('columns') ?? const [],
      rows: [
        for (final row in data?.optList('rows') ?? const <Object?>[])
          if (row is List) [for (final cell in row) cell],
      ],
      bodyMarkdown: r.optString('body_markdown') ?? '',
      canManage: r.optBool('can_manage') ?? false,
      createdBy: r.optString('created_by') ?? '',
      conversationId: r.optObject('source')?.optString('conversation_id'),
      layout: ReportLayout.fromReader(r),
    );
  }

  final List<String> highlights, columns;
  final List<List<Object?>> rows;
  final String bodyMarkdown, createdBy;
  final bool canManage;
  final String? conversationId;

  /// Typed sections and tabs, when the producer sent them (docs/reports.md).
  final ReportLayout layout;
}

/// The data snapshot as CSV (the same table the API's CSV export holds);
/// cells that start like a formula are quoted so spreadsheets show text.
String reportCsv(Report report) {
  String cell(Object? value) {
    var text = value == null ? '' : '$value';
    if (text.isNotEmpty && '=+-@\t\r'.contains(text[0])) text = "'$text";
    final quote = text.contains(RegExp('[",\n\r]'));
    return quote ? '"${text.replaceAll('"', '""')}"' : text;
  }

  final lines = <String>[];
  if (report.columns.isNotEmpty) {
    lines.add(report.columns.map(cell).join(','));
    for (final row in report.rows) {
      lines.add(row.map(cell).join(','));
    }
  } else if (!report.layout.isEmpty) {
    lines.add(sectionColumns.map(cell).join(','));
    for (final row in sectionRows(report.layout)) {
      lines.add(row.map(cell).join(','));
    }
  } else {
    lines.add('metric,value,unit,change_percent');
    for (final m in report.metrics) {
      lines.add(
        [
          m.label,
          m.value,
          m.unit ?? '',
          m.changePercent ?? '',
        ].map(cell).join(','),
      );
    }
  }
  return '${lines.join('\r\n')}\r\n';
}

/// A file name for a report download: the title as a slug and the date.
String reportFileName(Report report, String extension) {
  final slug = report.title
      .toLowerCase()
      .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
      .replaceAll(RegExp(r'^-+|-+$'), '');
  final day = report.createdAt.toUtc().toIso8601String().substring(0, 10);
  return '${slug.isEmpty ? report.type : slug}-$day.$extension';
}

/// A reply without the `anum-report` block the server turns into a stored
/// report and a card; while streaming, hidden from its first line on.
String visibleReply(String text) {
  final match = RegExp(r'\n?```anum-report[^\n]*(\n|$)').firstMatch(text);
  if (match == null) {
    final partial = text.lastIndexOf('```');
    if (partial >= 0) {
      final tail = text.substring(partial).trimRight();
      if (tail.length > 3 && '```anum-report'.startsWith(tail)) {
        return text.substring(0, partial).trimRight();
      }
    }
    return text;
  }
  final after = text.substring(match.end);
  final close = after.indexOf('\n```');
  final rest = close >= 0 ? after.substring(close + 4) : '';
  return (text.substring(0, match.start) + rest).trimRight();
}

class KnowledgeCollection {
  const KnowledgeCollection({
    required this.id,
    required this.name,
    required this.description,
    required this.visibility,
    required this.itemCount,
    required this.canManage,
  });

  factory KnowledgeCollection.fromReader(JsonReader r) => KnowledgeCollection(
        id: r.string('id'),
        name: r.string('name'),
        description: r.optString('description') ?? '',
        visibility: r.string('visibility'),
        itemCount: r.optInt('item_count') ?? 0,
        canManage: r.optBool('can_manage') ?? false,
      );

  final String id, name, description, visibility;
  final int itemCount;
  final bool canManage;
  bool get isPrivate => visibility == 'private';
}

class KnowledgeItem {
  const KnowledgeItem({
    required this.id,
    required this.collectionId,
    required this.kind,
    required this.title,
    required this.tags,
    required this.indexState,
    required this.available,
    required this.createdAt,
    this.content,
    this.indexError,
  });

  factory KnowledgeItem.fromReader(JsonReader r) => KnowledgeItem(
        id: r.string('id'),
        collectionId: r.string('collection_id'),
        kind: r.string('kind'),
        title: r.string('title'),
        tags: r.optStrings('tags') ?? const [],
        indexState: r.optObject('index')?.optString('state') ?? 'pending',
        indexError: r.optObject('index')?.optString('error'),
        available: r.optBool('available') ?? true,
        createdAt: r.date('created_at'),
        content: r.optString('content'),
      );

  final String id, collectionId, kind, title, indexState;
  final List<String> tags;
  final bool available;
  final DateTime createdAt;
  final String? content;

  /// Why the item is not indexed, e.g. `no_text_layer` for a scanned PDF.
  final String? indexError;
  bool get isNote => kind == 'note';

  /// The index state key for the label: a scanned PDF (no text layer) gets
  /// its own, so it is never shown as a plain "Not searchable".
  String get indexLabelKey => !available
      ? 'libIndex_gone'
      : indexState == 'skipped' && indexError == 'no_text_layer'
          ? 'libIndex_noTextLayer'
          : 'libIndex_$indexState';
}

class Citation {
  const Citation({
    required this.n,
    required this.title,
    required this.provenance,
    required this.snippet,
    required this.score,
    this.page,
    this.pageUnit,
    this.pageLabel,
  });

  factory Citation.fromReader(JsonReader r) => Citation(
        n: r.integer('n'),
        title: r.string('title'),
        provenance: r.string('provenance'),
        snippet: r.optString('snippet') ?? '',
        score: r.optNumber('score') ?? 0,
        page: r.optInt('page'),
        pageUnit: r.optString('page_unit'),
        pageLabel: r.optString('page_label'),
      );

  final int n;
  final String title, provenance, snippet;
  final double score;

  /// The PDF page, slide or sheet of a document passage (null for notes and text).
  final int? page;
  final String? pageUnit, pageLabel;
}

/// Where in a document a cited passage is: "page 3", "slide 2",
/// "sheet 1 (Budget)"; empty for notes and text files.
String citationPlace(Citation c, String Function(String key) t) {
  final unit = c.pageUnit;
  if (c.page == null ||
      unit == null ||
      !{'page', 'slide', 'sheet'}.contains(unit)) {
    return '';
  }
  final place = t('libPlace_$unit').replaceAll('{n}', '${c.page}');
  final label = c.pageLabel;
  return label == null || label.isEmpty ? place : '$place ($label)';
}

class KnowledgeAnswer {
  const KnowledgeAnswer({
    required this.status,
    required this.answer,
    required this.citations,
  });

  factory KnowledgeAnswer.fromReader(JsonReader r) => KnowledgeAnswer(
        status: r.string('status'),
        answer: r.optString('answer') ?? '',
        citations: r.optObjects('citations', Citation.fromReader),
      );

  final String status, answer;
  final List<Citation> citations;
  bool get answered => status == 'answered';
}

/// Tags typed as "a, b , c": trimmed, lowercased, unique, at most 12.
List<String> parseTags(String text) {
  final tags = <String>[];
  for (final raw in text.split(',')) {
    var tag = raw.trim().toLowerCase();
    if (tag.length > 40) tag = tag.substring(0, 40);
    if (tag.isNotEmpty && !tags.contains(tag)) tags.add(tag);
  }
  return tags.take(12).toList();
}
