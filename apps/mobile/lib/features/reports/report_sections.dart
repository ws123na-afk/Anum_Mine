import '../../data/json_read.dart';

/// How a value is shown: unit before or after it and the decimals
/// (docs/reports.md, `Number`).
class NumberStyle {
  const NumberStyle({this.prefix, this.suffix, this.decimals = 0});
  final String? prefix, suffix;
  final int decimals;

  factory NumberStyle.fromJson(JsonReader j) => NumberStyle(
        prefix: _clean(j.optString('prefix')),
        suffix: _clean(j.optString('suffix')),
        decimals: (j.optInt('decimals') ?? 0).clamp(0, 4),
      );

  /// `SAR 319,742`, `42,000 km`, `5.10 SAR`.
  String format(double value) {
    final number = formatNumber(value, decimals: decimals);
    final before = prefix == null ? '' : '$prefix ';
    final after = suffix == null
        ? ''
        : suffix == '%'
            ? '%'
            : ' $suffix';
    return '$before$number$after';
  }

  /// Short form for chart axes: `120k`, `1.2M`.
  String compact(double value) {
    final abs = value.abs();
    if (abs >= 1e6) return '${_trim(value / 1e6)}M';
    if (abs >= 1e3) return '${_trim(value / 1e3)}k';
    return formatNumber(value, decimals: abs < 10 ? decimals : 0);
  }
}

String? _clean(String? value) =>
    value == null || value.trim().isEmpty ? null : value.trim();

String _trim(double value) {
  final text = value.toStringAsFixed(1);
  return text.endsWith('.0') ? text.substring(0, text.length - 2) : text;
}

/// Thousands separators without the intl package: `319742` -> `319,742`.
String formatNumber(double value, {int decimals = 0}) {
  final negative = value < 0;
  final fixed = value.abs().toStringAsFixed(decimals);
  final parts = fixed.split('.');
  final whole = parts.first;
  final buffer = StringBuffer();
  for (var i = 0; i < whole.length; i++) {
    if (i > 0 && (whole.length - i) % 3 == 0) buffer.write(',');
    buffer.write(whole[i]);
  }
  final fraction = parts.length > 1 ? '.${parts[1]}' : '';
  return '${negative ? '-' : ''}$buffer$fraction';
}

/// `+66.6%` / `-4.9%`; whole numbers lose the `.0`.
String formatChange(double value) {
  final rounded = (value * 10).round() / 10;
  final text = rounded == rounded.roundToDouble()
      ? rounded.toStringAsFixed(0)
      : rounded.toStringAsFixed(1);
  return '${rounded > 0 ? '+' : ''}$text%';
}

class SectionMetric {
  const SectionMetric({
    required this.label,
    required this.value,
    this.style = const NumberStyle(),
    this.changePct,
    this.higherIsBetter = true,
    this.caption,
  });

  final String label;
  final double value;
  final NumberStyle style;
  final double? changePct;
  final bool higherIsBetter;
  final String? caption;

  /// Whether the change is good news (a cost going down, sales going up).
  bool? get changeIsGood {
    final change = changePct;
    if (change == null || change == 0) return null;
    return (change > 0) == higherIsBetter;
  }

  factory SectionMetric.fromJson(JsonReader j) => SectionMetric(
        label: j.string('label'),
        value: j.number('value'),
        style: NumberStyle.fromJson(j),
        changePct: j.optNumber('change_pct'),
        higherIsBetter: j.optBool('higher_is_better') ?? true,
        caption: j.optString('caption'),
      );
}

class SectionPoint {
  const SectionPoint(this.label, this.value, {this.changePct, this.detail});
  final String label;
  final double value;
  final double? changePct;
  final String? detail;

  factory SectionPoint.fromJson(JsonReader j) => SectionPoint(
        j.string('label'),
        j.number('value'),
        changePct: j.optNumber('change_pct'),
        detail: j.optString('detail'),
      );
}

/// One typed section of a report. Unknown types (a newer API) are kept as
/// [UnsupportedSection] and shown as such, never dropped silently.
sealed class ReportSection {
  const ReportSection({required this.id, this.title, this.tab});
  final String id;
  final String? title;
  final String? tab;

  static ReportSection fromJson(JsonReader j) {
    final id = j.string('id');
    final title = j.optString('title');
    final tab = j.optString('tab');
    return switch (j.optString('type')) {
      'metrics' => MetricsSection(
          id: id,
          title: title,
          tab: tab,
          items: j.objects('items', SectionMetric.fromJson)),
      'series' => SeriesSection(
          id: id,
          title: title,
          tab: tab,
          style: NumberStyle.fromJson(j),
          points: j.objects('points', SectionPoint.fromJson)),
      'breakdown' => BreakdownSection(
          id: id,
          title: title,
          tab: tab,
          style: NumberStyle.fromJson(j),
          items: j.objects('items', SectionPoint.fromJson)),
      'ranked' => RankedSection(
          id: id,
          title: title,
          tab: tab,
          style: NumberStyle.fromJson(j),
          higherIsBetter: j.optBool('higher_is_better') ?? true,
          items: j.objects('items', SectionPoint.fromJson)),
      'text' =>
        TextSection(id: id, title: title, tab: tab, body: j.string('body')),
      final other =>
        UnsupportedSection(id: id, title: title, tab: tab, type: other ?? ''),
    };
  }
}

class MetricsSection extends ReportSection {
  const MetricsSection(
      {required super.id, super.title, super.tab, required this.items});
  final List<SectionMetric> items;
}

class SeriesSection extends ReportSection {
  const SeriesSection({
    required super.id,
    super.title,
    super.tab,
    required this.points,
    this.style = const NumberStyle(),
  });
  final List<SectionPoint> points;
  final NumberStyle style;
}

class BreakdownSection extends ReportSection {
  const BreakdownSection({
    required super.id,
    super.title,
    super.tab,
    required this.items,
    this.style = const NumberStyle(),
  });
  final List<SectionPoint> items;
  final NumberStyle style;

  double get total => items.fold(0, (sum, item) => sum + item.value);
}

class RankedSection extends ReportSection {
  const RankedSection({
    required super.id,
    super.title,
    super.tab,
    required this.items,
    this.style = const NumberStyle(),
    this.higherIsBetter = true,
  });
  final List<SectionPoint> items;
  final NumberStyle style;
  final bool higherIsBetter;
}

class TextSection extends ReportSection {
  const TextSection(
      {required super.id, super.title, super.tab, required this.body});
  final String body;
}

class UnsupportedSection extends ReportSection {
  const UnsupportedSection(
      {required super.id, super.title, super.tab, required this.type});
  final String type;
}

/// A report's typed sections and tabs (docs/reports.md), read from the
/// library's report (`sections`, `tabs`).
class ReportLayout {
  const ReportLayout({this.tabs = const [], this.sections = const []});
  final List<String> tabs;
  final List<ReportSection> sections;

  bool get isEmpty => sections.isEmpty;

  factory ReportLayout.fromReader(JsonReader j) => ReportLayout(
        tabs: j.optStrings('tabs') ?? const [],
        sections: j.optObjects('sections', ReportSection.fromJson),
      );

  /// Sections of [tab]; with no tabs every section, and a section without a
  /// tab belongs to the first one.
  List<ReportSection> sectionsFor(String? tab) {
    if (tabs.isEmpty || tab == null) return sections;
    return [
      for (final section in sections)
        if ((section.tab ?? tabs.first) == tab) section
    ];
  }
}

/// A report saved to Files as CSV, with a short-lived link to share.
class ReportExport {
  const ReportExport(
      {required this.fileId, required this.fileName, required this.link});
  final String fileId, fileName;
  final Uri link;
}

/// The columns of the sections CSV, the same as the API's export.
const sectionColumns = [
  'tab',
  'section',
  'type',
  'label',
  'value',
  'unit',
  'change_pct',
  'detail',
];

num _plain(double value) =>
    value == value.roundToDouble() ? value.toInt() : value;

/// Every section flattened to rows of [sectionColumns].
List<List<Object?>> sectionRows(ReportLayout layout) {
  final first = layout.tabs.isEmpty ? '' : layout.tabs.first;
  final rows = <List<Object?>>[];
  for (final section in layout.sections) {
    final base = <Object?>[
      section.tab ?? first,
      section.title ?? section.id,
      switch (section) {
        MetricsSection() => 'metrics',
        SeriesSection() => 'series',
        BreakdownSection() => 'breakdown',
        RankedSection() => 'ranked',
        TextSection() => 'text',
        UnsupportedSection(:final type) => type,
      },
    ];
    String unit(NumberStyle style) => style.prefix ?? style.suffix ?? '';
    switch (section) {
      case MetricsSection(:final items):
        for (final m in items) {
          rows.add([
            ...base,
            m.label,
            _plain(m.value),
            unit(m.style),
            m.changePct == null ? '' : _plain(m.changePct!),
            m.caption ?? '',
          ]);
        }
      case SeriesSection(:final points, :final style):
        for (final p in points) {
          rows.add([...base, p.label, _plain(p.value), unit(style), '', '']);
        }
      case BreakdownSection(:final items, :final style):
        for (final p in items) {
          rows.add([...base, p.label, _plain(p.value), unit(style), '', '']);
        }
      case RankedSection(:final items, :final style):
        for (final p in items) {
          rows.add([
            ...base,
            p.label,
            _plain(p.value),
            unit(style),
            p.changePct == null ? '' : _plain(p.changePct!),
            p.detail ?? '',
          ]);
        }
      case TextSection(:final body):
        rows.add([...base, '', '', '', '', body]);
      case UnsupportedSection():
        break;
    }
  }
  return rows;
}
