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
import 'finance_recon_actions.dart';
import 'finance_review_strings.dart';
import 'finance_tools_data.dart';
import 'finance_tools_strings.dart';

/// Finance, Reconciliation on the phone (docs/finance.md#reconciliation-sessions):
/// every open exception across accounts (sorted by the server, paged, CSV),
/// the sessions with their state, starting one, and one session's equation,
/// card check and exceptions with the owner's decisions: match, confirm on
/// this statement, not on this statement, accept a reversal pair, post a
/// correcting entry approved with its payload hash, undo, complete and
/// reopen with a reason. Edit figures and Delete while it is open.

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

  /// Opens a session; when it asks for Transactions or Import, this page
  /// closes too and hands the request to Finance.
  Future<void> _open(String id) async {
    final next = await Navigator.of(context).push(MaterialPageRoute<String>(
        builder: (_) =>
            ReconSessionScreen(repository: widget.repository, id: id)));
    if (!mounted) return;
    if (next != null) {
      Navigator.of(context).pop(next);
      return;
    }
    await _reload();
  }

  Future<void> _start() async {
    final made = await startReconciliation(context, widget.repository);
    if (made == null || !mounted) return;
    recordsToast(context, FinanceToolsStrings.of(context).t('rc.started'));
    await _open(made.row.id);
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
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('recon-start'),
              onPressed: _start,
              icon: const Icon(Icons.add),
              label: Text(FinanceToolsStrings.of(context).t('rc.start')),
            ),
          ),
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

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) setState(() => _phase = RecordsPhase.loading);
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

  bool _busy = false;

  /// Runs one of the owner's decisions, then reloads the session.
  Future<void> _run(Future<bool> Function() work, String done) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final ok = await work();
      if (!mounted) return;
      if (ok) {
        recordsToast(context, done);
        await _load(quiet: true);
      }
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _act(ReconDetail d, ReconIssue issue, String action) {
    final t = FinanceToolsStrings.of(context);
    final repo = widget.repository;
    switch (action) {
      case 'attach' || 'pending':
        _run(() async {
          await repo.resolveRecon(d.row.id,
              {'kind': action, 'transaction_id': issue.transactionId});
          return true;
        }, t.t('rc.done.$action'));
      case 'accept_reversal':
        _run(() async {
          await repo.resolveRecon(d.row.id, {
            'kind': 'reversal',
            'transaction_id': issue.transactionId,
            'other_transaction_id': issue.otherTransactionId,
          });
          return true;
        }, t.t('rc.done.reversal'));
      case 'match':
        _run(
            () => matchReconRow(context, repo, d, issue), t.t('rc.done.match'));
      case 'correct':
        _run(() => correctReconRow(context, repo, d, issue),
            t.t('rc.done.correct'));
      case 'open':
        Navigator.of(context).pop('transactions');
      case 'import':
        Navigator.of(context).pop('import');
    }
  }

  Future<void> _complete(ReconDetail d) {
    final t = FinanceToolsStrings.of(context);
    return _run(() async {
      _show(await widget.repository.completeRecon(d.row.id, d.row.version));
      return true;
    }, t.t('rc.done.complete'));
  }

  Future<void> _reopen(ReconDetail d) async {
    final t = FinanceToolsStrings.of(context);
    final reason = await askReopenReason(context);
    if (reason == null || !mounted) return;
    await _run(() async {
      _show(
          await widget.repository.reopenRecon(d.row.id, d.row.version, reason));
      return true;
    }, t.t('rc.done.reopen'));
  }

  Future<void> _undo(ReconDetail d, ReconResolution r) {
    final t = FinanceToolsStrings.of(context);
    return _run(() async {
      await widget.repository.undoResolution(d.row.id, r.id);
      return true;
    }, t.t('rc.done.undo'));
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
    final t = FinanceToolsStrings.of(context);
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
      if (d.row.open) ...[
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('recon-complete'),
            onPressed: _busy || !d.row.canComplete ? null : () => _complete(d),
            icon: const Icon(Icons.verified_outlined),
            label: Text(t.t('rc.complete')),
          ),
        ),
        if (!d.row.canComplete) Text(t.t('rc.completeWhy'), style: muted),
      ] else
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            key: const Key('recon-reopen'),
            onPressed: _busy ? null : () => _reopen(d),
            icon: const Icon(Icons.restart_alt),
            label: Text(t.t('rc.reopen')),
          ),
        ),
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
              RecordsRowAction(
                row: AnumRow(
                  key: Key('recon-detail-issue-${issue.id}'),
                  icon: Icons.rule_outlined,
                  tone: _severityTone(issue.severity),
                  title: _kindLabel(context, issue.kind),
                  subtitle: m.t('recon.severity.${issue.severity}'),
                  detail: _issueLine(context, issue, c),
                ),
                action: _actions(context, d, issue),
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
      AnumHeader(title: t.t('rc.decisions')),
      if (d.resolutions.isEmpty)
        Text(t.t('rc.decisionsNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final r in d.resolutions)
              RecordsRowAction(
                row: AnumRow(
                  key: Key('recon-resolution-${r.id}'),
                  icon: Icons.task_alt,
                  title: t.t('rc.resolution.${r.kind}'),
                  subtitle: r.createdAt.isEmpty
                      ? null
                      : recordsDateTime(context, r.createdAt),
                  detail: r.note,
                ),
                action: d.row.open && r.kind != 'correction'
                    ? TextButton.icon(
                        key: Key('recon-undo-${r.id}'),
                        onPressed: _busy ? null : () => _undo(d, r),
                        icon: const Icon(Icons.undo),
                        label: Text(t.t('rc.undo')),
                      )
                    : null,
              ),
          ]),
        ),
      AnumHeader(title: t.t('rc.history')),
      if (d.history.isEmpty)
        Text(t.t('rc.historyNone'), style: muted)
      else
        for (final h in d.history)
          Text(
              [
                t.t('rc.history.${h.action}'),
                if (h.at.isNotEmpty) recordsDateTime(context, h.at),
                if (h.reason != null) h.reason!,
              ].join(' · '),
              key: Key('recon-history-${h.at}')),
    ];
  }

  /// The decisions the owner can make on [issue], as buttons: only while
  /// the session is open, except Open Transactions and Import.
  Widget? _actions(BuildContext context, ReconDetail d, ReconIssue issue) {
    final t = FinanceToolsStrings.of(context);
    final actions = d.row.open
        ? issue.actions
        : issue.actions.where((a) => a == 'open' || a == 'import').toList();
    if (actions.isEmpty) return null;
    return Wrap(
      alignment: WrapAlignment.end,
      spacing: AnumSpacing.xs,
      children: [
        for (final a in actions)
          if (t.maybe('rc.action.$a') != null)
            TextButton(
              key: Key('recon-act-$a-${issue.id}'),
              onPressed: _busy ? null : () => _act(d, issue, a),
              child: Text(t.t('rc.action.$a')),
            ),
      ],
    );
  }
}
