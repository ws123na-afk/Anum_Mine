import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_matching_data.dart';
import 'finance_matching_strings.dart';
import 'finance_models.dart' show formatAmount;

/// Automatic matching on the phone (docs/finance-matching.md): the matches
/// ANUM made or holds for review (link, dismiss, undo), the matching policy,
/// the history backfill with its progress and results, Tamara installments
/// with their match status, and SMS and Apple Pay capture health. Every
/// action goes through the same API as the web; nothing moves money.

String _percent(String value) {
  final n = double.tryParse(value);
  return n == null ? '-' : '${(n * 100).round()}%';
}

String _day(String? value) =>
    value == null || value.length < 10 ? '-' : value.substring(0, 10);

String _time(String? value) => value == null
    ? '-'
    : value.replaceFirst('T', ' ').split('.').first.replaceAll('Z', '');

/// Loads [load] once and on [reload], with the loading, denied and error states.
class _Loaded<T> extends StatefulWidget {
  const _Loaded({required this.load, required this.builder, super.key});
  final Future<T> Function() load;
  final Widget Function(BuildContext context, T value, VoidCallback reload)
      builder;

  @override
  State<_Loaded<T>> createState() => _LoadedState<T>();
}

class _LoadedState<T> extends State<_Loaded<T>> {
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  T? value;

  @override
  void initState() {
    super.initState();
    reload();
  }

  Future<void> reload() async {
    setState(() => phase = value == null ? RecordsPhase.loading : phase);
    try {
      final got = await widget.load();
      if (!mounted) return;
      setState(() {
        value = got;
        phase = RecordsPhase.ready;
      });
    } catch (error) {
      if (!mounted) return;
      final f = RecordsFailure.from(error);
      setState(() {
        failure = f;
        phase = f.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (phase != RecordsPhase.ready || value == null) {
      return ListView(padding: const EdgeInsets.all(AnumSpacing.md), children: [
        RecordsStateView(
            phase: phase,
            failure: failure,
            deniedHint: FinanceMatchingStrings.of(context).t('denied'),
            onRetry: reload),
      ]);
    }
    return RefreshIndicator(
        onRefresh: reload, child: widget.builder(context, value as T, reload));
  }
}

// -- the hub ------------------------------------------------------------------------

class MatchingHubScreen extends StatelessWidget {
  const MatchingHubScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    Widget tile(String key, IconData icon, Widget Function() page) => Card(
          child: ListTile(
            key: Key('matching-$key'),
            leading: Icon(icon),
            title: Text(s.t('hub.$key')),
            subtitle: Text(s.t('hub.${key}Text')),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context)
                .push(MaterialPageRoute<void>(builder: (_) => page())),
          ),
        );
    return Scaffold(
      appBar: AppBar(title: Text(s.t('hub.title'))),
      body: ListView(padding: const EdgeInsets.all(AnumSpacing.md), children: [
        Text(s.t('hub.intro'), style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.sm),
        tile('review', Icons.rule,
            () => MatchReviewScreen(repository: repository)),
        tile('policy', Icons.tune,
            () => MatchingPolicyScreen(repository: repository)),
        tile('history', Icons.history,
            () => BackfillScreen(repository: repository)),
        tile('installments', Icons.event_repeat,
            () => InstallmentMatchScreen(repository: repository)),
        tile('capture', Icons.phone_iphone,
            () => CaptureHealthScreen(repository: repository)),
      ]),
    );
  }
}

// -- the matches ---------------------------------------------------------------------

class MatchReviewScreen extends StatefulWidget {
  const MatchReviewScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  State<MatchReviewScreen> createState() => _MatchReviewScreenState();
}

class _MatchReviewScreenState extends State<MatchReviewScreen> {
  String tab = 'review';
  int generation = 0, limit = 25;
  bool busy = false;

  Future<void> _act(Future<void> Function() action, String done) async {
    final s = FinanceMatchingStrings.of(context);
    setState(() => busy = true);
    try {
      await action();
      if (mounted) recordsToast(context, s.t(done));
    } catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
          generation += 1;
        });
      }
    }
  }

  Future<void> _runNow() async {
    final s = FinanceMatchingStrings.of(context);
    setState(() => busy = true);
    try {
      final counts = await widget.repository.runNow();
      if (mounted) {
        recordsToast(
            context,
            s.t('ran', {
              'linked': counts['auto_linked'] ?? 0,
              'review': counts['review'] ?? 0
            }));
      }
    } catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
          generation += 1;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('hub.review')), actions: [
        IconButton(
            key: const Key('matching-run'),
            tooltip: s.t('run'),
            onPressed: busy ? null : _runNow,
            icon: const Icon(Icons.sync)),
      ]),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, 0),
          child: RecordsFilterChips(
            options: {
              for (final k in const ['review', 'auto_linked', 'stale'])
                k: s.t('tab.$k')
            },
            selected: tab,
            onSelected: (k) => setState(() {
              tab = k;
              limit = 25;
            }),
          ),
        ),
        Expanded(
          child: _Loaded<MatchRecordPage>(
            key: ValueKey('$tab-$generation-$limit'),
            load: () => widget.repository.records(tab, limit: limit),
            builder: (context, page, _) => ListView(
              padding: const EdgeInsets.all(AnumSpacing.md),
              children: [
                if (page.items.isEmpty)
                  AnumEmpty(
                      icon: Icons.rule,
                      title: s.t('empty.$tab'),
                      message: s.t('emptyText.$tab')),
                for (final item in page.items)
                  Padding(
                    padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                    child: _RecordCard(
                      item: item,
                      busy: busy,
                      onAccept: () => _act(
                          () => widget.repository
                              .decide(item.id, 'accept', item.version),
                          'accepted'),
                      onReject: () => _act(
                          () => widget.repository
                              .decide(item.id, 'reject', item.version),
                          'rejected'),
                      onUndo: () => _act(
                          () => widget.repository
                              .decide(item.id, 'undo', item.version),
                          'undone'),
                    ),
                  ),
                if (page.total > page.items.length && limit < 100)
                  Center(
                    child: OutlinedButton(
                        onPressed: () =>
                            setState(() => limit = (limit + 25).clamp(25, 100)),
                        child: Text(KitStrings.of(context).t('loadMore'))),
                  ),
              ],
            ),
          ),
        ),
      ]),
    );
  }
}

class _RecordCard extends StatelessWidget {
  const _RecordCard(
      {required this.item,
      required this.busy,
      required this.onAccept,
      required this.onReject,
      required this.onUndo});
  final MatchRecordItem item;
  final bool busy;
  final VoidCallback onAccept, onReject, onUndo;

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    final p = context.palette;
    final reason = item.reasonCode.startsWith('link_refused')
        ? 'link_refused'
        : item.reasonCode;
    final label = item.label.isEmpty ? s.t('kind.${item.kind}') : item.label;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
          Text(s.t('kind.${item.kind}'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          Text(s.t('state.${item.evidenceState}'),
              style: TextStyle(
                  color: item.evidenceState == 'verified' ? p.ok : p.warn,
                  fontSize: AnumType.sm)),
          Text(s.t('confidence', {'percent': _percent(item.confidence)}),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(label, style: Theme.of(context).textTheme.titleMedium),
        if (item.dueOn != null) Text(s.t('due', {'date': _day(item.dueOn)})),
        Text(s.t('paid', {
          'date': _day(item.postedOn),
          'amount': formatAmount(item.amount, item.currency)
        })),
        Text(s.label('reason', reason), style: TextStyle(color: p.muted)),
        if (item.competitors > 0)
          Text(s.t('others', {'count': item.competitors}),
              style: TextStyle(color: p.muted)),
        for (final flag in item.flags)
          Text(s.label('flag', flag), style: TextStyle(color: p.warn)),
        if (item.evidence.isNotEmpty)
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: Text(s.t('evidence')),
            children: [
              for (final e in item.evidence)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: Text(
                      '${s.label('evidence', e.code)}${e.value == null ? '' : ' (${e.value})'}'),
                ),
            ],
          ),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          if (item.waiting)
            FilledButton(
                key: Key('match-accept-${item.id}'),
                onPressed: busy ? null : onAccept,
                child: Text(s.t('accept'))),
          if (item.waiting)
            OutlinedButton(
                key: Key('match-reject-${item.id}'),
                onPressed: busy ? null : onReject,
                child: Text(s.t('reject'))),
          if (item.undoable)
            OutlinedButton(
                key: Key('match-undo-${item.id}'),
                onPressed: busy ? null : onUndo,
                child: Text(s.t('undo'))),
        ]),
      ]),
    );
  }
}

// -- the policy -----------------------------------------------------------------------

class MatchingPolicyScreen extends StatelessWidget {
  const MatchingPolicyScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
            title: Text(FinanceMatchingStrings.of(context).t('hub.policy'))),
        body: _Loaded<(MatchPolicyView, List<(String, String)>)>(
          load: () async =>
              (await repository.policy(), await repository.accounts()),
          builder: (context, value, reload) => _PolicyForm(
              view: value.$1,
              accounts: value.$2,
              repository: repository,
              onSaved: reload),
        ),
      );
}

class _PolicyForm extends StatefulWidget {
  const _PolicyForm(
      {required this.view,
      required this.accounts,
      required this.repository,
      required this.onSaved});
  final MatchPolicyView view;
  final List<(String, String)> accounts;
  final FinanceMatchingRepository repository;
  final VoidCallback onSaved;

  @override
  State<_PolicyForm> createState() => _PolicyFormState();
}

class _PolicyFormState extends State<_PolicyForm> {
  late MatchPolicy form = widget.view.policy.copy();
  bool busy = false;

  @override
  void didUpdateWidget(covariant _PolicyForm old) {
    super.didUpdateWidget(old);
    if (old.view != widget.view) form = widget.view.policy.copy();
  }

  Future<void> _save(bool approve) async {
    final s = FinanceMatchingStrings.of(context);
    setState(() => busy = true);
    try {
      final saved = await widget.repository.savePolicy(form, approve: approve);
      if (mounted) {
        recordsToast(context, s.t(saved.approved ? 'savedApproved' : 'saved'));
      }
      widget.onSaved();
    } catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _toggle(List<String> list, String value, bool on) => setState(() {
        list.remove(value);
        if (on) list.add(value);
      });

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    final p = context.palette;
    final view = widget.view;
    final dirty = !form.sameAs(view.policy);
    final master = form.switches['match_transactions'] ?? false;
    return ListView(padding: const EdgeInsets.all(AnumSpacing.md), children: [
      AnumSurface(
        child: Text(
          view.approved && !dirty
              ? s.t('policy.approved', {'version': view.version})
              : s.t('policy.notApproved'),
          style: TextStyle(
              color: view.approved && !dirty ? p.ok : p.warn,
              fontWeight: FontWeight.w600),
        ),
      ),
      for (final key in matchSwitches)
        SwitchListTile(
          key: Key('match-switch-$key'),
          contentPadding: EdgeInsets.zero,
          title: Text(s.t('switch.$key')),
          value: form.switches[key] ?? false,
          onChanged: key == 'match_transactions' ||
                  key == 'prevent_duplicates' ||
                  key == 'notify_exceptions' ||
                  master
              ? (on) => setState(() => form.switches[key] = on)
              : null,
        ),
      Text(s.t('fixed'), style: TextStyle(color: p.muted)),
      const SizedBox(height: AnumSpacing.sm),
      DropdownButtonFormField<String>(
        initialValue: const ['0.80', '0.85', '0.90', '0.95', '1.00'].firstWhere(
            (v) => double.parse(v) == double.tryParse(form.minConfidence),
            orElse: () => '0.90'),
        decoration: InputDecoration(labelText: s.t('minConfidence')),
        items: [
          for (final v in const ['0.80', '0.85', '0.90', '0.95', '1.00'])
            DropdownMenuItem(value: v, child: Text(_percent(v))),
        ],
        onChanged: (v) =>
            setState(() => form.minConfidence = v ?? form.minConfidence),
      ),
      RecordsSection(title: s.t('settled.title'), children: [
        Text(s.t('settled.sub'), style: TextStyle(color: p.muted)),
        for (final source in matchSources)
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(s.t('source.$source')),
            value: form.settledSources.contains(source),
            onChanged: (on) =>
                _toggle(form.settledSources, source, on ?? false),
          ),
      ]),
      const SizedBox(height: AnumSpacing.sm),
      RecordsSection(title: s.t('accounts.title'), children: [
        Text(s.t('accounts.sub'), style: TextStyle(color: p.muted)),
        if (widget.accounts.isEmpty) Text(s.t('accounts.none')),
        for (final (id, name) in widget.accounts)
          CheckboxListTile(
            key: Key('match-account-$id'),
            contentPadding: EdgeInsets.zero,
            title: Text(name),
            value: form.sourceAccountIds.contains(id),
            onChanged: (on) => _toggle(form.sourceAccountIds, id, on ?? false),
          ),
      ]),
      const SizedBox(height: AnumSpacing.sm),
      Text(s.t('spending'), style: TextStyle(color: p.muted)),
      Text(
          s.t('providers', {
            'connected': view.connectedProviders.isEmpty
                ? '-'
                : view.connectedProviders.join(', '),
            'others': view.otherProviders.isEmpty
                ? '-'
                : view.otherProviders.join(', '),
          }),
          style: TextStyle(color: p.muted)),
      ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: Text(s.t('never')),
        children: [
          for (final k in view.neverAutomated)
            Align(
                alignment: AlignmentDirectional.centerStart,
                child: Text(k.replaceAll('_', ' '))),
        ],
      ),
      const SizedBox(height: AnumSpacing.sm),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        FilledButton(
            key: const Key('match-approve'),
            onPressed: busy ? null : () => _save(true),
            child: Text(s.t('saveApprove'))),
        OutlinedButton(
            onPressed: busy || !dirty ? null : () => _save(false),
            child: Text(s.t('save'))),
      ]),
    ]);
  }
}

// -- history ------------------------------------------------------------------------

class BackfillScreen extends StatefulWidget {
  const BackfillScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  State<BackfillScreen> createState() => _BackfillScreenState();
}

class _BackfillScreenState extends State<BackfillScreen> {
  int generation = 0;
  bool busy = false;

  Future<void> _step(Future<BackfillRunItem> Function() action) async {
    setState(() => busy = true);
    try {
      var run = await action();
      for (var i = 0; run.running && i < 40; i++) {
        run = await widget.repository.continueRun(run.id);
      }
    } catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
          generation += 1;
        });
      }
    }
  }

  Future<void> _undo(BackfillRunItem run) async {
    final s = FinanceMatchingStrings.of(context);
    setState(() => busy = true);
    try {
      final removed = await widget.repository.undoRun(run.id);
      if (mounted) {
        recordsToast(context, s.t('history.undone', {'count': removed}));
      }
    } catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
          generation += 1;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('hub.history'))),
      body: _Loaded<(List<BackfillRunItem>, MatchPolicyView)>(
        key: ValueKey(generation),
        load: () async =>
            (await widget.repository.runs(), await widget.repository.policy()),
        builder: (context, value, _) {
          final (runs, policy) = value;
          final canApply = policy.approved &&
              (policy.policy.switches['match_transactions'] ?? false) &&
              (policy.policy.switches['reprocess_history'] ?? false);
          final run = runs.isEmpty ? null : runs.first;
          return ListView(
              padding: const EdgeInsets.all(AnumSpacing.md),
              children: [
                Text(s.t('history.intro'), style: TextStyle(color: p.muted)),
                const SizedBox(height: AnumSpacing.sm),
                Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      OutlinedButton(
                          key: const Key('history-preview'),
                          onPressed: busy
                              ? null
                              : () => _step(
                                  () => widget.repository.startRun('preview')),
                          child: Text(s.t('history.preview'))),
                      FilledButton(
                          key: const Key('history-apply'),
                          onPressed: busy || !canApply
                              ? null
                              : () => _step(
                                  () => widget.repository.startRun('apply')),
                          child: Text(s.t('history.apply'))),
                    ]),
                if (!canApply)
                  Text(s.t('history.applyOff'),
                      style: TextStyle(color: p.muted)),
                if (busy)
                  const Padding(
                      padding: EdgeInsets.all(AnumSpacing.sm),
                      child: LinearProgressIndicator()),
                const SizedBox(height: AnumSpacing.sm),
                if (run == null)
                  AnumEmpty(
                      icon: Icons.history,
                      title: s.t('history.empty'),
                      message: s.t('history.emptyText'))
                else
                  AnumSurface(
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              '${s.t('history.mode.${run.mode}')} · ${s.t('history.status.${run.status}')}',
                              style: Theme.of(context).textTheme.titleMedium),
                          if (run.startOn != null)
                            Text(s.t('history.progress', {
                              'from': _day(run.startOn),
                              'to': _day(run.endOn),
                              'cursor': _day(run.cursorOn)
                            })),
                          const SizedBox(height: AnumSpacing.xs),
                          LinearProgressIndicator(
                              value: run.progress,
                              semanticsLabel: s.t('hub.history')),
                          if (run.status == 'failed')
                            Text(
                                s.t('history.error',
                                    {'code': run.errorCode ?? '-'}),
                                style: TextStyle(color: p.stop)),
                          const SizedBox(height: AnumSpacing.xs),
                          for (final key in matchCountKeys)
                            if ((run.counts[key] ?? 0) > 0)
                              RecordsField(
                                  s.t('count.$key'), '${run.counts[key]}'),
                          Wrap(spacing: AnumSpacing.xs, children: [
                            if (run.running)
                              FilledButton(
                                  onPressed: busy
                                      ? null
                                      : () => _step(() => widget.repository
                                          .continueRun(run.id)),
                                  child: Text(s.t('history.continue'))),
                            if (run.mode == 'apply' &&
                                (run.status == 'done' ||
                                    run.status == 'failed') &&
                                (run.counts['auto_linked'] ?? 0) > 0)
                              OutlinedButton(
                                  key: const Key('history-undo'),
                                  onPressed: busy ? null : () => _undo(run),
                                  child: Text(s.t('history.undo'))),
                          ]),
                        ]),
                  ),
              ]);
        },
      ),
    );
  }
}

// -- installments -----------------------------------------------------------------------

class InstallmentMatchScreen extends StatelessWidget {
  const InstallmentMatchScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    final p = context.palette;
    Color tone(String status) => switch (status) {
          'verified_paid' || 'refunded' => p.ok,
          'overdue' => p.stop,
          'upcoming' || 'due_soon' => p.muted,
          _ => p.warn,
        };
    return Scaffold(
      appBar: AppBar(title: Text(s.t('hub.installments'))),
      body: _Loaded<List<InstallmentOrderItem>>(
        load: repository.installments,
        builder: (context, orders, _) =>
            ListView(padding: const EdgeInsets.all(AnumSpacing.md), children: [
          if (orders.isEmpty)
            AnumEmpty(
                icon: Icons.event_repeat,
                title: s.t('inst.empty'),
                message: s.t('inst.emptyText')),
          for (final o in orders)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: RecordsSection(
                title: s.t(
                    'inst.order', {'merchant': o.merchant, 'order': o.orderId}),
                children: [
                  Text(s.t('inst.left',
                      {'amount': formatAmount(o.stillDue, o.currency)})),
                  if (o.nextDueOn != null)
                    Text(s.t('inst.next', {'date': _day(o.nextDueOn)})),
                  for (final r in o.installments)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('${s.t('inst.number', {
                            'number': r.number,
                            'total': r.total
                          })} · ${formatAmount(r.amount, r.currency)}'),
                      subtitle: Text(_day(r.dueOn)),
                      trailing: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 160),
                        child: Text(s.t('inst.status.${r.status}'),
                            textAlign: TextAlign.end,
                            style: TextStyle(color: tone(r.status))),
                      ),
                    ),
                ],
              ),
            ),
        ]),
      ),
    );
  }
}

// -- capture health ---------------------------------------------------------------------

class CaptureHealthScreen extends StatelessWidget {
  const CaptureHealthScreen({required this.repository, super.key});
  final FinanceMatchingRepository repository;

  @override
  Widget build(BuildContext context) {
    final s = FinanceMatchingStrings.of(context);
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('hub.capture'))),
      body: _Loaded<CaptureHealthView>(
        load: repository.captureHealth,
        builder: (context, h, _) =>
            ListView(padding: const EdgeInsets.all(AnumSpacing.md), children: [
          AnumSurface(
            child: Text(s.t('cap.state.${h.state}'),
                style: TextStyle(
                    color: h.state == 'ok' ? p.ok : p.warn,
                    fontWeight: FontWeight.w600)),
          ),
          for (final problem in h.problems)
            Text(s.label('cap.problem', problem),
                style: TextStyle(color: p.warn)),
          const SizedBox(height: AnumSpacing.sm),
          for (final c in h.channels)
            RecordsSection(title: s.t('cap.channel.${c.channel}'), children: [
              Text(c.lastCapturedAt == null
                  ? s.t('cap.never')
                  : s.t('cap.lastCaptured', {'time': _time(c.lastCapturedAt)})),
              if (c.lastFailedAt != null)
                Text(
                    s.t('cap.lastFailed', {
                      'time': _time(c.lastFailedAt),
                      'code': c.lastFailedCode ?? '-'
                    }),
                    style: TextStyle(color: p.stop)),
              Text(
                  s.t('cap.counts',
                      {'refused': c.refused, 'waiting': c.waiting}),
                  style: TextStyle(color: p.muted)),
            ]),
          const SizedBox(height: AnumSpacing.sm),
          RecordsSection(title: s.t('cap.devices'), children: [
            if (h.devices.isEmpty) Text(s.t('cap.noDevices')),
            for (final (name, status, lastOk, error) in h.devices) ...[
              Text(lastOk == null
                  ? s.t('cap.deviceNever', {'name': name, 'status': status})
                  : s.t('cap.device',
                      {'name': name, 'status': status, 'time': _time(lastOk)})),
              if (error != null)
                Text(s.t('cap.deviceError', {'code': error}),
                    style: TextStyle(color: p.stop)),
            ],
          ]),
          if (h.senders.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            RecordsSection(title: s.t('cap.senders'), children: [
              for (final (sender, count) in h.senders)
                Text(s.t('cap.sender', {'sender': sender, 'count': count})),
            ]),
          ],
          const SizedBox(height: AnumSpacing.sm),
          RecordsSection(title: s.t('cap.mappings'), children: [
            if (h.mappings.isEmpty) Text(s.t('cap.noMappings')),
            for (final (kind, label, account) in h.mappings)
              Text(s.t('cap.mapping.$kind',
                  {'label': label, 'account': account ?? '-'})),
            if (h.unmappedAccounts.isNotEmpty)
              Text(
                  s.t('cap.unmappedAccounts',
                      {'names': h.unmappedAccounts.join(', ')}),
                  style: TextStyle(color: p.muted)),
            if (h.unmappedWallet > 0)
              Text(s.t('cap.unmappedWallet', {'count': h.unmappedWallet}),
                  style: TextStyle(color: p.warn)),
          ]),
          if (h.gaps.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            RecordsSection(title: s.t('cap.gaps'), children: [
              for (final (start, hours) in h.gaps)
                Text(s.t('cap.gap', {'hours': hours, 'start': _time(start)})),
            ]),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Text(s.t('cap.limits'), style: TextStyle(color: p.muted)),
        ]),
      ),
    );
  }
}
