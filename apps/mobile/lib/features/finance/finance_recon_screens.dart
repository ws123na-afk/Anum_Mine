import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_export.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';
import 'finance_review_strings.dart';

/// Finance, Reconciliation on the phone (docs/finance.md#reconciliation-sessions):
/// every open exception across accounts (sorted by the server, paged, CSV),
/// the sessions with their state, and one session's equation, card check
/// and exceptions with Edit figures and Delete while it is open. Matching
/// rows, correcting entries, completing and reopening stay on the web.

AnumTone reconTone(ReconRow s) => switch (s.label) {
      'changed' => AnumTone.stop,
      'completed' => AnumTone.ok,
      'ready' => AnumTone.ok,
      _ => AnumTone.warn,
    };

AnumTone _severityTone(String severity) => switch (severity) {
      'blocking' => AnumTone.stop,
      'warning' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String _issueLine(BuildContext context, ReconIssue issue, String currency) {
  final m = FinanceMoreStrings.of(context);
  return [
    if (issue.postedOn != null) ltrValue(context, issue.postedOn!),
    if (issue.postedOn == null && issue.month != null)
      m.t('recon.missingMonth', {'month': ltrValue(context, issue.month!)}),
    if (issue.amount != null && currency.isNotEmpty)
      formatAmount(issue.amount!, currency),
    if (issue.description != null && issue.description!.isNotEmpty)
      issue.description!,
    if (issue.reason != null && m.maybe('recon.reason.${issue.reason}') != null)
      m.t('recon.reason.${issue.reason}'),
  ].join(' · ');
}

String _kindLabel(BuildContext context, String kind) =>
    FinanceReviewStrings.of(context).maybe('reconKind.$kind') ??
    kind.replaceAll('_', ' ');

class ReconciliationScreen extends StatefulWidget {
  const ReconciliationScreen(
      {required this.repository,
      this.saver = const SystemExportSaver(),
      super.key});
  final FinanceMoreRepository repository;
  final ExportSaver saver;

  @override
  State<ReconciliationScreen> createState() => _ReconciliationScreenState();
}

class _ReconciliationScreenState extends State<ReconciliationScreen> {
  String _sort = '', _state = '';
  bool _desc = false;
  late final RecordsList<ReconQueueItem> _queue = RecordsList((_, __, token) =>
      widget.repository
          .reconExceptions(sort: _sort, desc: _desc, token: token));
  late final RecordsList<ReconRow> _sessions = RecordsList((_, __, token) =>
      widget.repository.reconSessions(state: _state, token: token));

  @override
  void initState() {
    super.initState();
    _queue.load();
    _sessions.load();
  }

  @override
  void dispose() {
    _queue.dispose();
    _sessions.dispose();
    super.dispose();
  }

  Future<void> _reload() => Future.wait([_queue.load(), _sessions.load()]);

  void _order({String? sort, bool? desc}) {
    setState(() {
      _sort = sort ?? _sort;
      _desc = desc ?? _desc;
    });
    _queue.load(filter: '$_sort|$_desc');
  }

  Future<void> _open(String id) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) =>
            ReconSessionScreen(repository: widget.repository, id: id)));
    if (mounted) await _reload();
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('recon.title'))),
      body: ListenableBuilder(
        listenable: Listenable.merge([_queue, _sessions]),
        builder: (context, _) => AnumPage(onRefresh: _reload, children: [
          Text(m.t('recon.intro'), style: muted),
          AnumHeader(
              title: m.t('recon.queue.title'),
              subtitle: m.t('recon.queue.sub')),
          Wrap(
            spacing: AnumSpacing.sm,
            runSpacing: AnumSpacing.sm,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              ConstrainedBox(
                constraints: const BoxConstraints(minWidth: 180, maxWidth: 260),
                child: DropdownButtonFormField<String>(
                  key: const Key('recon-sort'),
                  initialValue: _sort,
                  isExpanded: true,
                  decoration: InputDecoration(labelText: m.t('recon.sortBy')),
                  items: [
                    for (final by in ['', ...reconSorts])
                      DropdownMenuItem(
                          value: by,
                          child: Text(m.t('recon.sort.$by'),
                              overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (by) {
                    if (by != null && by != _sort) _order(sort: by);
                  },
                ),
              ),
              if (_sort.isNotEmpty)
                IconButton(
                  key: const Key('recon-sort-direction'),
                  tooltip: m.t(_desc ? 'recon.descending' : 'recon.ascending'),
                  onPressed: () => _order(desc: !_desc),
                  icon: Icon(_desc ? Icons.arrow_downward : Icons.arrow_upward),
                ),
              ExportCsvButton(
                key: const Key('recon-export'),
                load: () => widget.repository
                    .reconExceptionsCsv(sort: _sort, desc: _desc),
                fileName: m.t('recon.export'),
                saver: widget.saver,
              ),
            ],
          ),
          ...recordsPagedBody<ReconQueueItem>(
            _queue,
            deniedHint: m.t('denied'),
            empty: AnumEmpty(
                icon: Icons.task_alt,
                title: m.t('recon.queue.empty'),
                message: m.t('recon.queue.emptyText')),
            row: (item) => AnumRow(
              key: Key('recon-issue-${item.issue.id}'),
              icon: Icons.rule_outlined,
              tone: _severityTone(item.issue.severity),
              title: _kindLabel(context, item.issue.kind),
              subtitle: [
                item.accountName,
                if (item.periodStart != null && item.periodEnd != null)
                  m.t('recon.period', {
                    'start': ltrValue(context, item.periodStart!),
                    'end': ltrValue(context, item.periodEnd!)
                  }),
              ].join(' · '),
              detail: [
                m.t('recon.severity.${item.issue.severity}'),
                _issueLine(context, item.issue, item.currency),
              ].where((t) => t.isNotEmpty).join(' · '),
              trailing: item.reconciliationId == null
                  ? null
                  : const Icon(Icons.chevron_right),
              onTap: item.reconciliationId == null
                  ? null
                  : () => _open(item.reconciliationId!),
            ),
          ),
          AnumHeader(
              title: m.t('recon.sessions.title'),
              subtitle: m.t('recon.sessions.sub')),
          RecordsFilterChips(
            options: {
              for (final s in const [
                '',
                'open',
                'completed',
                'changed',
                'unresolved'
              ])
                s: m.t('recon.filter.$s')
            },
            selected: _state,
            onSelected: (s) {
              setState(() => _state = s);
              _sessions.load(filter: s);
            },
          ),
          ...recordsPagedBody<ReconRow>(
            _sessions,
            deniedHint: m.t('denied'),
            empty: AnumEmpty(
                icon: Icons.fact_check_outlined,
                title: m.t('recon.sessions.empty'),
                message: m.t('recon.sessions.emptyText')),
            row: (s) => AnumRow(
              key: Key('recon-session-${s.id}'),
              icon: s.accountKind == 'card'
                  ? Icons.credit_card_outlined
                  : Icons.account_balance_outlined,
              tone: reconTone(s),
              title: s.accountName,
              subtitle: m.t('recon.period', {
                'start': ltrValue(context, s.periodStart),
                'end': ltrValue(context, s.periodEnd)
              }),
              detail: [
                m.t('recon.status.${s.label}'),
                if (!s.canComplete && s.open)
                  m.t('recon.unexplained',
                      {'amount': formatAmount(s.unexplained, s.currency)}),
                if (s.exceptions > 0) m.t('recon.items', {'n': s.exceptions}),
              ].join(' · '),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(s.id),
            ),
          ),
        ]),
      ),
    );
  }
}

/// One session: its equation, the card check, what blocks completing it and
/// its exceptions; Edit figures and Delete while it is open.
class ReconSessionScreen extends StatefulWidget {
  const ReconSessionScreen(
      {required this.repository, required this.id, super.key});
  final FinanceMoreRepository repository;
  final String id;

  @override
  State<ReconSessionScreen> createState() => _ReconSessionScreenState();
}

class _ReconSessionScreenState extends State<ReconSessionScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  ReconDetail? _detail;
  List<ReconIssue> _issues = const [];
  bool _loadingMore = false;
  String? _moreError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      _show(await widget.repository.recon(widget.id));
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  void _show(ReconDetail detail) {
    if (!mounted) return;
    setState(() {
      _detail = detail;
      _issues = detail.issues;
      _moreError = null;
      _phase = RecordsPhase.ready;
    });
  }

  Future<void> _more() async {
    setState(() {
      _loadingMore = true;
      _moreError = null;
    });
    try {
      final next =
          await widget.repository.recon(widget.id, offset: _issues.length);
      if (mounted) setState(() => _issues = [..._issues, ...next.issues]);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _moreError = RecordsFailure.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _edit(ReconDetail d) async {
    final m = FinanceMoreStrings.of(context);
    final kind = d.row.accountKind;
    final card = kind == 'card';
    String? amount(RecordsValues v, String key, String field,
        {bool owed = false}) {
      final typed = v[key] as String?;
      if (typed == null) return null;
      final parsed =
          parseAmount(owed ? ledgerFigure(kind, typed) : typed, signed: owed);
      if (parsed == null) {
        throw FormatException(m.t('recon.edit.badAmount', {'field': field}));
      }
      return parsed;
    }

    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: m.t('recon.edit'),
        intro: [
          m.t('recon.edit.intro'),
          if (d.owedKind) m.t('recon.edit.owedHelp'),
        ].join('\n\n'),
        submitLabel: m.t('recon.edit.save'),
        fields: [
          RecordsInput.text('opening', m.t('recon.edit.opening'),
              initial: typedFigure(kind, d.figures['statement_opening'])),
          RecordsInput.text('closing', m.t('recon.edit.closing'),
              required: true,
              initial: typedFigure(kind, d.figures['statement_closing'])),
          if (card) ...[
            RecordsInput.text('due', m.t('recon.edit.due'),
                initial: d.card?.amountDue),
            RecordsInput.text('minimum', m.t('recon.edit.minimum'),
                initial: d.card?.minimumDue),
            RecordsInput.date('due_on', m.t('recon.edit.dueOn'),
                initial: d.card?.dueOn),
          ],
        ],
        onSubmit: (v) async {
          if (v['closing'] == null) {
            throw FormatException(m.t('recon.edit.closingNeeded'));
          }
          final body = <String, Object?>{
            'version': d.row.version,
            'statement_closing':
                amount(v, 'closing', m.t('recon.edit.closing'), owed: true),
            'statement_opening':
                amount(v, 'opening', m.t('recon.edit.opening'), owed: true),
            if (card) ...{
              'amount_due': amount(v, 'due', m.t('recon.edit.due')),
              'minimum_due': amount(v, 'minimum', m.t('recon.edit.minimum')),
              'due_on': v['due_on'],
            },
          };
          _show(await widget.repository.updateRecon(d.row.id, body));
        },
      ),
    );
    if (saved && mounted) recordsToast(context, m.t('recon.edit.saved'));
  }

  Future<void> _delete(ReconDetail d) async {
    final m = FinanceMoreStrings.of(context);
    final ok = await confirmRecords(context,
        title: m.t('recon.delete.title'),
        message: m.t('recon.delete.text', {
          'account': d.row.accountName,
          'start': ltrValue(context, d.row.periodStart),
          'end': ltrValue(context, d.row.periodEnd),
        }),
        action: m.t('recon.delete'));
    if (!ok || !mounted) return;
    try {
      await widget.repository.deleteRecon(d.row.id);
      if (!mounted) return;
      recordsToast(context, m.t('recon.deleted'));
      Navigator.of(context).pop();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final d = _detail;
    return Scaffold(
      appBar: AppBar(title: Text(d?.row.accountName ?? m.t('recon.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || d == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else
          ..._body(context, d),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, ReconDetail d) {
    final m = FinanceMoreStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final c = d.row.currency;
    String money(String? v) => v == null ? '-' : formatAmount(v, c);
    return [
      AnumHeader(
        title: m.t('recon.period', {
          'start': ltrValue(context, d.row.periodStart),
          'end': ltrValue(context, d.row.periodEnd),
        }),
        subtitle: d.row.accountName,
      ),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        AnumPill(
            label: m.t('recon.status.${d.row.label}'), tone: reconTone(d.row)),
      ]),
      if (d.row.changed)
        Text(m.t('recon.changedNote'), style: TextStyle(color: p.warn))
      else if (!d.row.open)
        Text(m.t('recon.completedNote'), style: muted),
      if (d.row.open)
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
          FilledButton.icon(
            key: const Key('recon-edit'),
            onPressed: () => _edit(d),
            icon: const Icon(Icons.edit_outlined),
            label: Text(m.t('recon.edit')),
          ),
          OutlinedButton.icon(
            key: const Key('recon-delete'),
            onPressed: () => _delete(d),
            icon: const Icon(Icons.delete_outline),
            label: Text(m.t('recon.delete')),
          ),
        ]),
      RecordsSection(title: m.t('recon.eq.title'), children: [
        for (final key in ReconFigures.keys)
          if (d.figures[key] != null || key == 'statement_closing')
            RecordsField(m.t('recon.eq.$key'), money(d.figures[key])),
      ]),
      if (d.card != null)
        RecordsSection(title: m.t('recon.card.title'), children: [
          RecordsField(m.t('recon.card.due'), money(d.card!.amountDue)),
          RecordsField(m.t('recon.card.owed'), money(d.card!.owedInLedger)),
          RecordsField(
              m.t('recon.card.difference'), money(d.card!.dueDifference)),
          RecordsField(m.t('recon.card.minimum'), money(d.card!.minimumDue)),
          RecordsField(m.t('recon.card.paid'), money(d.card!.paidSince)),
          RecordsField(m.t('recon.card.dueOn'), d.card!.dueOn ?? '-'),
          if (d.card!.minimumCovered != null)
            Text(
                m.t(d.card!.minimumCovered!
                    ? 'recon.card.covered'
                    : 'recon.card.notCovered'),
                style: muted),
        ]),
      for (final blocker in d.blockers)
        Text(
            m.t('recon.blocker.$blocker', {
              'amount': formatAmount(d.row.unexplained, c),
            }),
            style: TextStyle(color: p.warn)),
      AnumHeader(title: m.t('recon.issues.title')),
      if (_issues.isEmpty)
        Text(m.t('recon.issues.none'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final issue in _issues)
              AnumRow(
                key: Key('recon-detail-issue-${issue.id}'),
                icon: Icons.rule_outlined,
                tone: _severityTone(issue.severity),
                title: _kindLabel(context, issue.kind),
                subtitle: m.t('recon.severity.${issue.severity}'),
                detail: _issueLine(context, issue, c),
              ),
          ]),
        ),
      if (_moreError != null)
        Text(KitStrings.of(context).t('moreFailed', {'message': _moreError!}),
            style: TextStyle(color: p.stop)),
      if (_issues.length < d.total)
        Align(
          alignment: AlignmentDirectional.center,
          child: OutlinedButton.icon(
            key: const Key('recon-issues-more'),
            onPressed: _loadingMore ? null : _more,
            icon: const Icon(Icons.expand_more),
            label: Text(m.t('recon.moreIssues',
                {'shown': _issues.length, 'total': d.total})),
          ),
        ),
      Text(m.t('recon.resolveOnWeb'), style: muted),
    ];
  }
}
