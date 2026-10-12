import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'library_models.dart';
import 'library_repository.dart';
import '../reports/report_sections_view.dart';

/// Favorites, knowledge base and reports on the phone (docs/knowledge-base.md,
/// docs/reports.md): the same API and rules as the web report centre.

String _failure(BuildContext context, Object error, String deniedKey) {
  final l = context.anum;
  if (error is ApiException && error.isPermissionDenied) return l.t(deniedKey);
  if (error is ApiException) {
    return l.f('libFailed', {'message': error.message});
  }
  return l.t('libOffline');
}

String reportTypeLabel(AnumLocalizations l, String type) {
  const known = {
    'tyre',
    'commerce',
    'task_summary',
    'automation_summary',
    'commerce_summary',
    'tyre_summary',
    'marketing',
    'custom',
  };
  if (known.contains(type)) return l.t('libType_$type');
  final words = type.replaceAll('_', ' ');
  return words.isEmpty ? type : words[0].toUpperCase() + words.substring(1);
}

String? _period(AnumLocalizations l, String? start, String? end) {
  if (start != null && end != null) {
    return l.f('libPeriod', {'start': start, 'end': end});
  }
  return start ?? end;
}

/// A centred, width-limited scrolling page under an app bar.
class _Page extends StatelessWidget {
  const _Page({
    required this.title,
    required this.children,
    this.onRefresh,
    this.fab,
    this.actions,
  });
  final String title;
  final List<Widget> children;
  final Future<void> Function()? onRefresh;
  final Widget? fab;
  final List<Widget>? actions;

  @override
  Widget build(BuildContext context) {
    final list = ListView(
      padding: const EdgeInsetsDirectional.fromSTEB(
        AnumSpacing.md,
        AnumSpacing.sm,
        AnumSpacing.md,
        AnumSpacing.xxl,
      ),
      children: children,
    );
    return Scaffold(
      appBar: AppBar(title: Text(title), actions: actions),
      floatingActionButton: fab,
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: onRefresh == null
              ? list
              : RefreshIndicator(onRefresh: onRefresh!, child: list),
        ),
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading(this.label);
  final String label;
  @override
  Widget build(BuildContext context) => Semantics(
        label: label,
        liveRegion: true,
        child: const Padding(
          padding: EdgeInsets.all(AnumSpacing.xl),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
}

class _Problem extends StatelessWidget {
  const _Problem({required this.message, this.onRetry});
  final String message;
  final VoidCallback? onRetry;
  @override
  Widget build(BuildContext context) => AnumEmpty(
        icon: Icons.error_outline,
        title: context.anum.t('libProblemTitle'),
        message: message,
        action: onRetry == null ? null : context.anum.t('retry'),
        onAction: onRetry,
      );
}

class MetricChips extends StatelessWidget {
  const MetricChips(this.metrics, {super.key});
  final List<ReportMetric> metrics;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Wrap(
      spacing: AnumSpacing.xs,
      runSpacing: AnumSpacing.xs,
      children: [
        for (final m in metrics)
          Container(
            constraints: const BoxConstraints(minWidth: 120),
            padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.sm,
              vertical: AnumSpacing.xs,
            ),
            decoration: BoxDecoration(
              color: p.text.withValues(alpha: 0.05),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: p.text.withValues(alpha: 0.09)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(m.label, style: TextStyle(color: p.muted, fontSize: 12)),
                Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: AnumSpacing.xs,
                  children: [
                    Text(
                      m.display,
                      style: TextStyle(
                        color: p.text,
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    if (changeLabel(m.changePercent) case final change?)
                      Text(
                        change,
                        textDirection: TextDirection.ltr,
                        style: TextStyle(
                          color: (m.changePercent ?? 0) > 0 ? p.warn : p.ok,
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// The card under a structured Assistant answer, with "View Full Report".
class ReportAnswerCard extends StatelessWidget {
  const ReportAnswerCard({required this.card, this.onOpen, super.key});
  final ReportCard card;
  final ValueChanged<String>? onOpen;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final period = _period(l, card.periodStart, card.periodEnd);
    return Semantics(
      container: true,
      label: l.f('libCardLabel', {'title': card.title}),
      child: Container(
        margin: const EdgeInsets.only(top: AnumSpacing.xs),
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: p.violet.withValues(alpha: 0.4)),
          color: p.text.withValues(alpha: 0.04),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.insert_chart_outlined, color: p.violet, size: 20),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                  child: Text(
                    card.title,
                    style: TextStyle(
                      color: p.text,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            Text(
              [
                reportTypeLabel(l, card.type),
                if (period != null) period,
              ].join(' · '),
              style: TextStyle(color: p.muted, fontSize: 12),
            ),
            const SizedBox(height: AnumSpacing.xs),
            MetricChips(card.metrics),
            for (final h in card.highlights.take(3))
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xxs),
                child: Text('• $h', style: TextStyle(color: p.text)),
              ),
            if (onOpen != null)
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: TextButton.icon(
                  onPressed: () => onOpen!(card.reportId),
                  icon: const Icon(Icons.open_in_new, size: 18),
                  label: Text(l.t('libViewFullReport')),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// --------------------------------------------------------------- favorites

class FavoritesScreen extends StatefulWidget {
  const FavoritesScreen({
    required this.repository,
    required this.onOpenOther,
    super.key,
  });
  final LibraryRepository repository;

  /// A starred item the phone opens in another destination (tasks, files...).
  final ValueChanged<Favorite> onOpenOther;

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  String? _type;
  List<Favorite> _items = [];
  int _total = 0;
  Object? _error;
  bool _loading = true, _more = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await widget.repository.favorites(type: _type);
      if (!mounted) return;
      setState(() {
        _items = page.items;
        _total = page.total;
        _loading = false;
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _error = e;
          _loading = false;
        });
      }
    }
  }

  Future<void> _loadMore() async {
    setState(() => _more = true);
    try {
      final page = await widget.repository.favorites(
        type: _type,
        offset: _items.length,
      );
      if (mounted) setState(() => _items = [..._items, ...page.items]);
    } on Object catch (e) {
      if (mounted) _snack(_failure(context, e, 'libDenied'));
    } finally {
      if (mounted) setState(() => _more = false);
    }
  }

  void _snack(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  Future<void> _remove(Favorite f) async {
    final l = context.anum;
    try {
      await widget.repository.unstar(f.itemType, f.itemId);
      setState(() {
        _items = _items.where((x) => x.id != f.id).toList();
        _total = _total > 0 ? _total - 1 : 0;
      });
      _snack(l.f('libFavRemoved', {'name': f.label}));
    } on Object catch (e) {
      if (mounted) _snack(_failure(context, e, 'libDenied'));
    }
  }

  void _open(Favorite f) {
    final repo = widget.repository;
    if (f.itemType == 'report') {
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ReportDetailScreen(repository: repo, id: f.itemId),
        ),
      );
    } else if (f.itemType == 'collection') {
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) =>
              KnowledgeScreen(repository: repo, initialCollectionId: f.itemId),
        ),
      );
    } else {
      widget.onOpenOther(f);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return _Page(
      title: l.t('libFavorites'),
      onRefresh: _load,
      children: [
        Text(
          l.t('libFavIntro'),
          style: TextStyle(color: context.palette.muted),
        ),
        const SizedBox(height: AnumSpacing.sm),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              for (final type in [null, ...favoriteTypes])
                Padding(
                  padding: const EdgeInsetsDirectional.only(
                    end: AnumSpacing.xs,
                  ),
                  child: ChoiceChip(
                    label: Text(
                      type == null ? l.t('libAll') : l.t('libItem_$type'),
                    ),
                    selected: _type == type,
                    onSelected: (_) {
                      setState(() => _type = type);
                      _load();
                    },
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: AnumSpacing.sm),
        AnumSurface(
          child: _loading
              ? _Loading(l.t('libLoading'))
              : _error != null
                  ? _Problem(
                      message: _failure(context, _error!, 'libDenied'),
                      onRetry: _load,
                    )
                  : _items.isEmpty
                      ? AnumEmpty(
                          icon: Icons.star_border,
                          title: l.t('libFavEmpty'),
                          message: l.t('libFavEmptyText'),
                        )
                      : Column(
                          children: [
                            for (final f in _items)
                              AnumRow(
                                icon: Icons.star,
                                tone: AnumTone.warn,
                                title: f.label,
                                subtitle: l.t('libItem_${f.itemType}'),
                                onTap: () => _open(f),
                                trailing: IconButton(
                                  tooltip: l.f('libUnstar', {'name': f.label}),
                                  icon: const Icon(Icons.star_border),
                                  onPressed: () => _remove(f),
                                ),
                              ),
                            if (_items.length < _total)
                              TextButton(
                                onPressed: _more ? null : _loadMore,
                                child: Text(
                                  l.f('libMore', {
                                    'shown': _items.length,
                                    'total': _total,
                                  }),
                                ),
                              ),
                          ],
                        ),
        ),
      ],
    );
  }
}

// --------------------------------------------------------------- reports

class ReportsScreen extends StatefulWidget {
  const ReportsScreen({required this.repository, super.key});
  final LibraryRepository repository;
  @override
  State<ReportsScreen> createState() => _ReportsScreenState();
}

class _ReportsScreenState extends State<ReportsScreen> {
  final _search = TextEditingController();
  String? _type;
  List<ReportSummary> _items = [];
  List<String> _types = [];
  int _total = 0;
  Object? _error;
  bool _loading = true, _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    if (!more) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final page = await widget.repository.reports(
        query: _search.text.trim(),
        type: _type,
        offset: more ? _items.length : 0,
      );
      if (!mounted) return;
      setState(() {
        _items = more ? [..._items, ...page.items] : page.items;
        _total = page.total;
        if (page.types.isNotEmpty) _types = page.types;
        _loading = false;
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _error = e;
          _loading = false;
        });
      }
    }
  }

  Future<void> _generate(String type) async {
    final l = context.anum;
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      final report = await widget.repository.generate(type, 7);
      messenger.showSnackBar(
        SnackBar(content: Text(l.f('libGenerated', {'title': report.title}))),
      );
      await _load();
      if (mounted) _openReport(report.id);
    } on Object catch (e) {
      if (mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(_failure(context, e, 'libGenerateDenied'))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openReport(String id) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            ReportDetailScreen(repository: widget.repository, id: id),
      ),
    );
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return _Page(
      title: l.t('libReports'),
      onRefresh: _load,
      children: [
        Text(l.t('libReportsIntro'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.sm),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                l.t('libGenerateTitle'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AnumSpacing.xs),
              Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton.tonal(
                    onPressed: _busy ? null : () => _generate('task_summary'),
                    child: Text(l.t('libGenerateTasks')),
                  ),
                  FilledButton.tonal(
                    onPressed:
                        _busy ? null : () => _generate('automation_summary'),
                    child: Text(l.t('libGenerateAutomations')),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          controller: _search,
          textInputAction: TextInputAction.search,
          onSubmitted: (_) => _load(),
          decoration: InputDecoration(
            labelText: l.t('libSearchReports'),
            prefixIcon: const Icon(Icons.search),
          ),
        ),
        if (_types.length > 1) ...[
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              for (final type in [null, ..._types])
                ChoiceChip(
                  label: Text(
                    type == null ? l.t('libAll') : reportTypeLabel(l, type),
                  ),
                  selected: _type == type,
                  onSelected: (_) {
                    setState(() => _type = type);
                    _load();
                  },
                ),
            ],
          ),
        ],
        const SizedBox(height: AnumSpacing.sm),
        AnumSurface(
          child: _loading
              ? _Loading(l.t('libLoading'))
              : _error != null
                  ? _Problem(
                      message: _failure(context, _error!, 'libReportsDenied'),
                      onRetry: _load,
                    )
                  : _items.isEmpty
                      ? AnumEmpty(
                          icon: Icons.insert_chart_outlined,
                          title: l.t('libReportsEmpty'),
                          message: l.t('libReportsEmptyText'),
                        )
                      : Column(
                          children: [
                            for (final r in _items)
                              AnumRow(
                                icon: Icons.insert_chart_outlined,
                                tone: AnumTone.accent,
                                title: r.title,
                                subtitle: [
                                  reportTypeLabel(l, r.type),
                                  if (_period(l, r.periodStart, r.periodEnd)
                                      case final period?)
                                    period,
                                ].join(' · '),
                                detail: r.metrics
                                    .take(2)
                                    .map((m) => '${m.label}: ${m.display}')
                                    .join('  ·  '),
                                trailing: r.visibility == 'private'
                                    ? AnumPill(
                                        label: l.t('libPrivate'),
                                        icon: Icons.lock_outline,
                                      )
                                    : null,
                                onTap: () => _openReport(r.id),
                              ),
                            if (_items.length < _total)
                              TextButton(
                                onPressed: () => _load(more: true),
                                child: Text(
                                  l.f('libMore', {
                                    'shown': _items.length,
                                    'total': _total,
                                  }),
                                ),
                              ),
                          ],
                        ),
        ),
      ],
    );
  }
}

class ReportDetailScreen extends StatefulWidget {
  const ReportDetailScreen({
    required this.repository,
    required this.id,
    super.key,
  });
  final LibraryRepository repository;
  final String id;
  @override
  State<ReportDetailScreen> createState() => _ReportDetailScreenState();
}

class _ReportDetailScreenState extends State<ReportDetailScreen> {
  Report? _report;
  Object? _error;
  bool _busy = false, _starred = false, _asking = false;
  final _question = TextEditingController();

  @override
  void dispose() {
    _question.dispose();
    super.dispose();
  }

  /// Saves the CSV in the workspace's files and copies a share link.
  Future<void> _saveToFiles(Report report) async {
    final l = context.anum;
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    setState(() => _busy = true);
    try {
      final saved = await widget.repository.saveReportToFiles(report.id);
      await Clipboard.setData(ClipboardData(text: saved.link.toString()));
      messenger.showSnackBar(SnackBar(
        duration: const Duration(seconds: 8),
        content: Text(l.f('reportExported', {'name': saved.fileName})),
        action: SnackBarAction(
            label: l.t('reportOpenFiles'),
            onPressed: () => navigator.pushNamed('/files')),
      ));
    } on Object catch (e) {
      if (mounted) {
        messenger.showSnackBar(
            SnackBar(content: Text(_failure(context, e, 'libManageDenied'))));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// "Ask ANUM to analyze further": creates the task and opens it.
  Future<void> _ask(Report report) async {
    final question = _question.text.trim();
    if (question.isEmpty) return;
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    setState(() => _asking = true);
    try {
      final task = await widget.repository.askAboutReport(report.id, question);
      _question.clear();
      await navigator.pushNamed('/tasks/${Uri.encodeComponent(task)}');
    } on Object catch (e) {
      if (mounted) {
        messenger.showSnackBar(
            SnackBar(content: Text(_failure(context, e, 'libManageDenied'))));
      }
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final report = await widget.repository.report(widget.id);
      final stars = await widget.repository
          .favorites(type: 'report')
          .then((p) => p.items.any((f) => f.itemId == widget.id))
          .catchError((Object _) => false);
      if (mounted) {
        setState(() {
          _report = report;
          _starred = stars;
        });
      }
    } on Object catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<bool> _act(Future<void> Function() action, String doneKey) async {
    final l = context.anum;
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await action();
      messenger.showSnackBar(SnackBar(content: Text(l.t(doneKey))));
      return true;
    } on Object catch (e) {
      if (mounted) {
        messenger.showSnackBar(
          SnackBar(content: Text(_failure(context, e, 'libManageDenied'))),
        );
      }
      return false;
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete(Report report) async {
    final l = context.anum;
    final navigator = Navigator.of(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('libDeleteReportTitle')),
        content: Text(l.f('libDeleteReportText', {'title': report.title})),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l.t('libCancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l.t('libDelete')),
          ),
        ],
      ),
    );
    if (yes != true) return;
    final deleted = await _act(
      () => widget.repository.deleteReport(report.id),
      'libDeleted',
    );
    if (mounted && deleted) navigator.pop();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final report = _report;
    if (report == null) {
      return _Page(
        title: l.t('libReport'),
        children: [
          _error == null
              ? _Loading(l.t('libLoading'))
              : _Problem(
                  message: _error is ApiException &&
                          (_error as ApiException).statusCode == 404
                      ? l.t('libReportMissing')
                      : _failure(context, _error!, 'libReportsDenied'),
                  onRetry: _load,
                ),
        ],
      );
    }
    final period = _period(l, report.periodStart, report.periodEnd);
    return _Page(
      title: report.title,
      children: [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            AnumPill(label: reportTypeLabel(l, report.type)),
            if (period != null) AnumPill(label: period),
            AnumPill(label: l.t('libBy_${report.createdByKind}')),
            AnumPill(
              label: report.visibility == 'private'
                  ? l.t('libPrivate')
                  : l.t('libShared'),
              icon: report.visibility == 'private'
                  ? Icons.lock_outline
                  : Icons.group_outlined,
            ),
          ],
        ),
        if (report.summary.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(report.summary, style: TextStyle(color: p.text)),
        ],
        const SizedBox(height: AnumSpacing.sm),
        if (report.metrics.isNotEmpty && report.layout.isEmpty)
          MetricChips(report.metrics),
        if (!report.layout.isEmpty) ...[
          ReportSectionsView(layout: report.layout),
          const SizedBox(height: AnumSpacing.sm),
        ],
        for (final h in report.highlights)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xxs),
            child: Text('• $h', style: TextStyle(color: p.text)),
          ),
        if (report.columns.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              columns: [
                for (final c in report.columns) DataColumn(label: Text(c)),
              ],
              rows: [
                for (final row in report.rows.take(50))
                  DataRow(
                    cells: [
                      for (final cell in row)
                        DataCell(Text(cell?.toString() ?? '')),
                    ],
                  ),
              ],
            ),
          ),
          if (report.rows.length > 50)
            Text(
              l.f('libRowsShown', {'shown': 50, 'total': report.rows.length}),
              style: TextStyle(color: p.muted, fontSize: 12),
            ),
        ],
        const SizedBox(height: AnumSpacing.md),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            OutlinedButton.icon(
              onPressed: _busy
                  ? null
                  : () => _act(() async {
                        await widget.repository.saveReport(report, 'csv');
                      }, 'libSaved'),
              icon: const Icon(Icons.download),
              label: Text(l.t('libSaveCsv')),
            ),
            OutlinedButton.icon(
              onPressed: _busy
                  ? null
                  : () => _act(() async {
                        await widget.repository.saveReport(report, 'md');
                      }, 'libSaved'),
              icon: const Icon(Icons.description_outlined),
              label: Text(l.t('libSaveMd')),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : () => _saveToFiles(report),
              icon: const Icon(Icons.ios_share),
              label: Text(l.t('reportSaveToFiles')),
            ),
            OutlinedButton.icon(
              onPressed: _busy
                  ? null
                  : () => _act(() async {
                        if (_starred) {
                          await widget.repository.unstar('report', report.id);
                        } else {
                          await widget.repository.star('report', report.id);
                        }
                        setState(() => _starred = !_starred);
                      }, _starred ? 'libUnstarred' : 'libStarred'),
              icon: Icon(_starred ? Icons.star : Icons.star_border),
              label: Text(_starred ? l.t('libUnstarShort') : l.t('libStar')),
            ),
          ],
        ),
        const SizedBox(height: AnumSpacing.sm),
        if (report.canManage)
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              OutlinedButton.icon(
                onPressed: _busy
                    ? null
                    : () => _act(() async {
                          final updated = await widget.repository.updateReport(
                            report.id,
                            visibility: report.visibility == 'private'
                                ? 'workspace'
                                : 'private',
                          );
                          setState(() => _report = updated);
                        }, 'libVisibilityChanged'),
                icon: Icon(
                  report.visibility == 'private'
                      ? Icons.group_outlined
                      : Icons.lock_outline,
                ),
                label: Text(
                  report.visibility == 'private'
                      ? l.t('libShareWorkspace')
                      : l.t('libMakePrivate'),
                ),
              ),
              OutlinedButton.icon(
                onPressed: _busy ? null : () => _delete(report),
                icon: Icon(Icons.delete_outline, color: p.stop),
                label: Text(l.t('libDelete')),
              ),
            ],
          )
        else
          Text(l.t('libReadOnly'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.md),
        ReportAskBar(
          controller: _question,
          busy: _asking,
          onSend: () => _ask(report),
        ),
      ],
    );
  }
}

// --------------------------------------------------------------- knowledge

class KnowledgeScreen extends StatefulWidget {
  const KnowledgeScreen({
    required this.repository,
    this.initialCollectionId,
    this.onOpenMemories,
    super.key,
  });
  final LibraryRepository repository;
  final String? initialCollectionId;

  /// Opens the memories and files ANUM keeps (Resources); the menu's
  /// Knowledge Base entry lands here, so both stay one tap apart.
  final VoidCallback? onOpenMemories;
  @override
  State<KnowledgeScreen> createState() => _KnowledgeScreenState();
}

class _KnowledgeScreenState extends State<KnowledgeScreen> {
  List<KnowledgeCollection>? _collections;
  Object? _error;
  final _question = TextEditingController();
  KnowledgeAnswer? _answer;
  bool _asking = false;

  @override
  void initState() {
    super.initState();
    _load(openInitial: true);
  }

  @override
  void dispose() {
    _question.dispose();
    super.dispose();
  }

  Future<void> _load({bool openInitial = false}) async {
    setState(() => _error = null);
    try {
      final rows = await widget.repository.collections();
      if (!mounted) return;
      setState(() => _collections = rows);
      final target = widget.initialCollectionId;
      if (openInitial && target != null) {
        final found = rows.where((c) => c.id == target);
        if (found.isNotEmpty) {
          _open(found.first);
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(context.anum.t('libCollectionMissing'))),
          );
        }
      }
    } on Object catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _open(KnowledgeCollection c) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            CollectionScreen(repository: widget.repository, collection: c),
      ),
    );
    if (mounted) _load();
  }

  Future<void> _ask() async {
    final question = _question.text.trim();
    if (question.isEmpty) return;
    setState(() => _asking = true);
    try {
      final answer = await widget.repository.ask(question);
      if (mounted) setState(() => _answer = answer);
    } on Object catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_failure(context, e, 'libAskDenied'))),
        );
      }
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  Future<void> _create() async {
    final created = await showDialog<(String, bool)>(
      context: context,
      builder: (_) => const _CollectionDialog(),
    );
    if (created == null || !mounted) return;
    try {
      final c = await widget.repository.createCollection(
        created.$1,
        created.$2 ? 'private' : 'workspace',
      );
      await _load();
      if (mounted) _open(c);
    } on Object catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_failure(context, e, 'libCreateDenied'))),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final rows = _collections;
    return _Page(
      title: l.t('libKnowledge'),
      onRefresh: _load,
      actions: [
        if (widget.onOpenMemories case final open?)
          TextButton.icon(
            key: const Key('kb-memories'),
            onPressed: open,
            icon: const Icon(Icons.psychology_outlined),
            label: Text(l.t('libMemoriesFiles')),
          ),
      ],
      fab: FloatingActionButton.extended(
        onPressed: _create,
        icon: const Icon(Icons.create_new_folder_outlined),
        label: Text(l.t('libNewCollection')),
      ),
      children: [
        Text(l.t('libKnowledgeIntro'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.sm),
        if (rows != null && rows.isNotEmpty)
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TextField(
                  controller: _question,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _ask(),
                  decoration: InputDecoration(
                    labelText: l.t('libAskLabel'),
                    suffixIcon: IconButton(
                      tooltip: l.t('libAsk'),
                      onPressed: _asking ? null : _ask,
                      icon: const Icon(Icons.auto_awesome),
                    ),
                  ),
                ),
                if (_asking) const LinearProgressIndicator(),
                if (_answer case final answer?) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Text(answer.answer, style: TextStyle(color: p.text)),
                  for (final c in answer.citations)
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Text(
                        '[${c.n}] ${c.title} · ${c.provenance == 'file' ? l.t('libDocument') : l.t('libNote')}\n${c.snippet}',
                        style: TextStyle(color: p.muted, fontSize: 13),
                      ),
                    ),
                  if (answer.citations.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Text(
                        l.t('libUntrusted'),
                        style: TextStyle(color: p.faint, fontSize: 12),
                      ),
                    ),
                ],
              ],
            ),
          ),
        const SizedBox(height: AnumSpacing.sm),
        AnumSurface(
          child: rows == null
              ? (_error == null
                  ? _Loading(l.t('libLoading'))
                  : _Problem(
                      message: _failure(
                        context,
                        _error!,
                        'libKnowledgeDenied',
                      ),
                      onRetry: _load,
                    ))
              : rows.isEmpty
                  ? AnumEmpty(
                      icon: Icons.local_library_outlined,
                      title: l.t('libKnowledgeEmpty'),
                      message: l.t('libKnowledgeEmptyText'),
                      action: l.t('libNewCollection'),
                      onAction: _create,
                    )
                  : Column(
                      children: [
                        for (final c in rows)
                          AnumRow(
                            icon: c.isPrivate
                                ? Icons.lock_outline
                                : Icons.folder_shared_outlined,
                            tone: AnumTone.accent,
                            title: c.name,
                            subtitle:
                                c.description.isEmpty ? null : c.description,
                            detail: l.f('libItemCount', {'n': c.itemCount}),
                            onTap: () => _open(c),
                          ),
                      ],
                    ),
        ),
        const SizedBox(height: AnumSpacing.huge),
      ],
    );
  }
}

class CollectionScreen extends StatefulWidget {
  const CollectionScreen({
    required this.repository,
    required this.collection,
    super.key,
  });
  final LibraryRepository repository;
  final KnowledgeCollection collection;
  @override
  State<CollectionScreen> createState() => _CollectionScreenState();
}

class _CollectionScreenState extends State<CollectionScreen> {
  List<KnowledgeItem> _items = [];
  int _total = 0;
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool more = false}) async {
    if (!more) setState(() => _loading = true);
    try {
      final page = await widget.repository.items(
        widget.collection.id,
        offset: more ? _items.length : 0,
      );
      if (!mounted) return;
      setState(() {
        _items = more ? [..._items, ...page.items] : page.items;
        _total = page.total;
        _loading = false;
        _error = null;
      });
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _error = e;
          _loading = false;
        });
      }
    }
  }

  void _snack(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  Future<void> _addNote() async {
    final l = context.anum;
    final values = await showDialog<(String, String, String)>(
      context: context,
      builder: (_) => const _NoteDialog(),
    );
    if (values == null || !mounted) return;
    if (values.$1.isEmpty || values.$2.isEmpty) {
      _snack(l.t('libNoteRequired'));
      return;
    }
    try {
      await widget.repository.addNote(
        widget.collection.id,
        values.$1,
        values.$2,
        parseTags(values.$3),
      );
      _snack(l.f('libNoteAdded', {'title': values.$1}));
      await _load();
    } on Object catch (e) {
      if (mounted) _snack(_failure(context, e, 'libAddDenied'));
    }
  }

  Future<void> _remove(KnowledgeItem item) async {
    try {
      await widget.repository.removeItem(item.id);
      if (!mounted) return;
      _snack(context.anum.f('libRemoved', {'title': item.title}));
      await _load();
    } on Object catch (e) {
      if (mounted) _snack(_failure(context, e, 'libItemDenied'));
    }
  }

  Future<void> _deleteCollection() async {
    final l = context.anum;
    final navigator = Navigator.of(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('libDeleteCollectionTitle')),
        content: Text(
          l.f('libDeleteCollectionText', {'name': widget.collection.name}),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l.t('libCancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l.t('libDelete')),
          ),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await widget.repository.deleteCollection(widget.collection.id);
      navigator.pop();
    } on Object catch (e) {
      if (mounted) _snack(_failure(context, e, 'libManageDenied'));
    }
  }

  String _state(AnumLocalizations l, KnowledgeItem item) => item.available
      ? l.t('libIndex_${item.indexState}')
      : l.t('libIndex_gone');

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final c = widget.collection;
    return _Page(
      title: c.name,
      onRefresh: _load,
      fab: FloatingActionButton.extended(
        onPressed: _addNote,
        icon: const Icon(Icons.note_add_outlined),
        label: Text(l.t('libAddNote')),
      ),
      children: [
        Text(
          c.isPrivate
              ? l.t('libPrivateCollection')
              : l.t('libSharedCollection'),
          style: TextStyle(color: p.muted),
        ),
        if (c.description.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(c.description, style: TextStyle(color: p.text)),
        ],
        if (c.canManage)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
              onPressed: _deleteCollection,
              icon: Icon(Icons.delete_outline, color: p.stop),
              label: Text(l.t('libDeleteCollection')),
            ),
          ),
        const SizedBox(height: AnumSpacing.xs),
        AnumSurface(
          child: _loading
              ? _Loading(l.t('libLoading'))
              : _error != null
                  ? _Problem(
                      message: _failure(context, _error!, 'libKnowledgeDenied'),
                      onRetry: _load,
                    )
                  : _items.isEmpty
                      ? AnumEmpty(
                          icon: Icons.menu_book_outlined,
                          title: l.t('libCollectionEmpty'),
                          message: l.t('libCollectionEmptyText'),
                          action: l.t('libAddNote'),
                          onAction: _addNote,
                        )
                      : Column(
                          children: [
                            for (final item in _items)
                              AnumRow(
                                icon: item.isNote
                                    ? Icons.sticky_note_2_outlined
                                    : Icons.description_outlined,
                                tone: item.indexState == 'failed' ||
                                        !item.available
                                    ? AnumTone.stop
                                    : item.indexState == 'indexed'
                                        ? AnumTone.ok
                                        : AnumTone.neutral,
                                title: item.title,
                                subtitle: [
                                  item.isNote
                                      ? l.t('libNote')
                                      : l.t('libDocument'),
                                  _state(l, item),
                                  ...item.tags,
                                ].join(' · '),
                                trailing: IconButton(
                                  tooltip: l.f(
                                      'libRemoveItem', {'title': item.title}),
                                  onPressed: () => _remove(item),
                                  icon: const Icon(Icons.remove_circle_outline),
                                ),
                              ),
                            if (_items.length < _total)
                              TextButton(
                                onPressed: () => _load(more: true),
                                child: Text(
                                  l.f('libMore', {
                                    'shown': _items.length,
                                    'total': _total,
                                  }),
                                ),
                              ),
                          ],
                        ),
        ),
        const SizedBox(height: AnumSpacing.huge),
      ],
    );
  }
}

// --------------------------------------------------------------- home

/// Quick access on the home screen: favorites, the knowledge base and reports.
class LibrarySection extends StatefulWidget {
  const LibrarySection({
    required this.repository,
    required this.onOpenOther,
    super.key,
  });
  final LibraryRepository repository;
  final ValueChanged<Favorite> onOpenOther;
  @override
  State<LibrarySection> createState() => _LibrarySectionState();
}

class _LibrarySectionState extends State<LibrarySection> {
  List<Favorite> _favorites = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final page = await widget.repository.favorites();
      if (mounted) setState(() => _favorites = page.items.take(4).toList());
    } on Object {
      // The tiles still work; the starred list just stays empty.
    }
  }

  void _push(Widget screen) => Navigator.of(context)
      .push(MaterialPageRoute<void>(builder: (_) => screen))
      .then((_) => _load());

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final repo = widget.repository;
    final favorites = FavoritesScreen(
      repository: repo,
      onOpenOther: widget.onOpenOther,
    );
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnumHeader(eyebrow: l.t('libEyebrow'), title: l.t('libQuickAccess')),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              ActionChip(
                avatar: const Icon(Icons.star_outline, size: 18),
                label: Text(l.t('libFavorites')),
                onPressed: () => _push(favorites),
              ),
              ActionChip(
                avatar: const Icon(Icons.local_library_outlined, size: 18),
                label: Text(l.t('libKnowledge')),
                onPressed: () => _push(KnowledgeScreen(repository: repo)),
              ),
              ActionChip(
                avatar: const Icon(Icons.insert_chart_outlined, size: 18),
                label: Text(l.t('libReports')),
                onPressed: () => _push(ReportsScreen(repository: repo)),
              ),
            ],
          ),
          for (final f in _favorites)
            AnumRow(
              icon: Icons.star,
              tone: AnumTone.warn,
              title: f.label,
              subtitle: l.t('libItem_${f.itemType}'),
              onTap: () {
                if (f.itemType == 'report') {
                  _push(ReportDetailScreen(repository: repo, id: f.itemId));
                } else if (f.itemType == 'collection') {
                  _push(
                    KnowledgeScreen(
                      repository: repo,
                      initialCollectionId: f.itemId,
                    ),
                  );
                } else {
                  widget.onOpenOther(f);
                }
              },
            ),
        ],
      ),
    );
  }
}

/// New collection: a name and whether it is private. The dialog owns its
/// controller, so it is disposed only after the route is gone.
class _CollectionDialog extends StatefulWidget {
  const _CollectionDialog();
  @override
  State<_CollectionDialog> createState() => _CollectionDialogState();
}

class _CollectionDialogState extends State<_CollectionDialog> {
  final _name = TextEditingController();
  bool _private = false;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('libNewCollection')),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(
          controller: _name,
          autofocus: true,
          maxLength: 120,
          decoration: InputDecoration(labelText: l.t('libName')),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: _private,
          onChanged: (v) => setState(() => _private = v),
          title: Text(l.t('libPrivateToMe')),
        ),
      ]),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('libCancel'))),
        FilledButton(
          onPressed: () {
            final name = _name.text.trim();
            if (name.isNotEmpty) Navigator.pop(context, (name, _private));
          },
          child: Text(l.t('libCreate')),
        ),
      ],
    );
  }
}

/// A note: title, text and tags.
class _NoteDialog extends StatefulWidget {
  const _NoteDialog();
  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _title = TextEditingController();
  final _content = TextEditingController();
  final _tags = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    _content.dispose();
    _tags.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('libAddNote')),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
              controller: _title,
              maxLength: 200,
              decoration: InputDecoration(labelText: l.t('libTitle'))),
          TextField(
              controller: _content,
              maxLines: 6,
              minLines: 3,
              maxLength: 16000,
              decoration: InputDecoration(labelText: l.t('libText'))),
          TextField(
              controller: _tags,
              decoration: InputDecoration(labelText: l.t('libTags'))),
        ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('libCancel'))),
        FilledButton(
          onPressed: () => Navigator.pop(
              context, (_title.text.trim(), _content.text.trim(), _tags.text)),
          child: Text(l.t('libSaveNote')),
        ),
      ],
    );
  }
}
