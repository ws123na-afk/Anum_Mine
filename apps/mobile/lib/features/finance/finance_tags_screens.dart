import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';
import 'finance_personal_models.dart';

/// Finance, Foreign-currency balances and Trips and projects on the phone
/// (docs/finance.md#foreign-currency-balances, #trips-and-projects). Both
/// are views: nothing is posted, moved or recategorised.

String _totals(Map<String, String> totals) => totals.isEmpty
    ? ''
    : totals.entries.map((e) => formatAmount(e.value, e.key)).join(' · ');

// -- foreign-currency balances ----------------------------------------------------

class FxBalancesScreen extends StatefulWidget {
  const FxBalancesScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<FxBalancesScreen> createState() => _FxBalancesScreenState();
}

class _FxBalancesScreenState extends State<FxBalancesScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  FxBalances? _page;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final page = await widget.repository.fxBalances();
      if (!mounted) return;
      setState(() {
        _page = page;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final page = _page;
    return Scaffold(
      appBar: AppBar(title: Text(m.t('fx.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || page == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else
          ..._body(context, page),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, FxBalances page) {
    final m = FinanceMoreStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final base = page.baseCurrency;
    return [
      Text(m.t('fx.intro', {'base': base}), style: muted),
      if (page.items.isEmpty)
        AnumEmpty(
            icon: Icons.currency_exchange,
            title: m.t('fx.empty'),
            message: m.t('fx.emptyText', {'base': base}))
      else ...[
        if (page.unrealisedTotal != null)
          AnumMetricGrid(children: [
            AnumMetric(
                label: m.t('fx.unrealised'),
                value: formatAmount(page.unrealisedTotal!, base),
                icon: Icons.trending_up),
          ]),
        if (page.incomplete > 0)
          Text(m.t('fx.incomplete', {'n': page.incomplete}), style: muted),
        for (final row in page.items)
          AnumSurface(
            key: Key('fx-${row.accountId}'),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AnumRow(
                  icon: Icons.currency_exchange,
                  tone: row.status == 'ok' ? AnumTone.ok : AnumTone.warn,
                  title: row.name,
                  subtitle: m.t('fx.balance',
                      {'amount': formatAmount(row.balance, row.currency)}),
                ),
                RecordsField(m.t('fx.worthNow'),
                    row.value == null ? null : formatAmount(row.value!, base)),
                RecordsField(m.t('fx.worthThen'),
                    row.cost == null ? null : formatAmount(row.cost!, base)),
                RecordsField(
                    m.t('fx.unrealised'),
                    row.unrealised == null
                        ? null
                        : formatAmount(row.unrealised!, base)),
                if (row.rate != null)
                  Text(
                      m.t('fx.rate', {
                        'rate': row.rate!,
                        'date': row.rateAsOf ?? '-',
                        'source': row.rateSource ?? '-',
                      }),
                      style: muted),
                if (row.status != 'ok')
                  Text(
                      m.t('fx.st.${row.status}', {
                        'currency': row.currency,
                        'base': base,
                        'dates': row.missingRateDates.take(5).join(', '),
                      }),
                      style: TextStyle(color: p.warn)),
              ],
            ),
          ),
      ],
      Text(m.t('fx.asOf', {'date': page.asOf, 'days': page.rateMaxAgeDays}),
          style: muted),
    ];
  }
}

// -- trips and projects -----------------------------------------------------------

class TagsScreen extends StatefulWidget {
  const TagsScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<TagsScreen> createState() => _TagsScreenState();
}

class _TagsScreenState extends State<TagsScreen> {
  String _kind = '';
  bool _archived = false;
  late final RecordsList<FinanceTag> _list = RecordsList((query, _, token) =>
      widget.repository
          .tags(kind: _kind, query: query, archived: _archived, token: token));

  @override
  void initState() {
    super.initState();
    _list.load();
  }

  @override
  void dispose() {
    _list.dispose();
    super.dispose();
  }

  void _reload() => _list.load(filter: '$_kind|$_archived');

  Future<void> _new() async {
    final saved = await openTagForm(context, widget.repository);
    if (saved != null && mounted) {
      _list.upsert(saved, (t) => t.id == saved.id);
      await _open(saved);
    }
  }

  Future<void> _open(FinanceTag tag) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => TagScreen(repository: widget.repository, id: tag.id)));
    if (mounted) await _list.load();
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final k = KitStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('tag.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('tag.intro'),
              style: TextStyle(color: context.palette.muted)),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('tag-new'),
              onPressed: _new,
              icon: const Icon(Icons.add),
              label: Text(m.t('tag.new')),
            ),
          ),
          RecordsSearchField(
              initial: _list.query,
              hint: m.t('tag.search'),
              onSearch: (q) => _list.load(query: q)),
          RecordsFilterChips(
            options: {
              for (final kind in const ['', 'trip', 'project'])
                kind: m.t('tag.kind.$kind')
            },
            selected: _kind,
            onSelected: (kind) {
              setState(() => _kind = kind);
              _reload();
            },
          ),
          Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('tag-archived'),
              contentPadding: EdgeInsets.zero,
              title: Text(m.t('tag.archivedToggle')),
              value: _archived,
              onChanged: (on) {
                setState(() => _archived = on);
                _reload();
              },
            ),
          ),
          ...recordsPagedBody<FinanceTag>(
            _list,
            deniedHint: m.t('denied'),
            empty: _list.narrowed || _kind.isNotEmpty
                ? AnumEmpty(
                    icon: Icons.search_off,
                    title: k.t('noMatch'),
                    message: k.t('noMatchText'))
                : AnumEmpty(
                    icon: Icons.luggage_outlined,
                    title: m.t('tag.empty'),
                    message: m.t('tag.emptyText'),
                    action: m.t('tag.new'),
                    onAction: _new),
            row: (t) => AnumRow(
              key: Key('tag-${t.id}'),
              icon: t.kind == 'trip'
                  ? Icons.luggage_outlined
                  : Icons.work_outline,
              tone: t.archived ? AnumTone.neutral : null,
              title: t.name,
              subtitle: [
                m.t('tag.kind1.${t.kind}'),
                if (t.startsOn != null || t.endsOn != null)
                  m.t('tag.dates',
                      {'start': t.startsOn ?? '-', 'end': t.endsOn ?? '-'}),
                if (t.archived) m.t('tag.f.archived'),
              ].join(' · '),
              detail:
                  t.totals.isEmpty ? m.t('tag.noTotals') : _totals(t.totals),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(t),
            ),
          ),
        ]),
      ),
    );
  }
}

/// The trip or project form: new, or [tag] edited (the full body is sent).
Future<FinanceTag?> openTagForm(
    BuildContext context, FinanceMoreRepository repository,
    {FinanceTag? tag}) async {
  final m = FinanceMoreStrings.of(context);
  FinanceTag? saved;
  await openRecordsForm(
    context,
    RecordsForm(
      title: tag == null ? m.t('tag.new') : m.t('tag.edit'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.choice('kind', m.t('tag.f.kind'),
            options: {
              'trip': m.t('tag.kind1.trip'),
              'project': m.t('tag.kind1.project'),
            },
            initial: tag?.kind ?? 'trip'),
        RecordsInput.text('name', m.t('tag.f.name'),
            required: true, initial: tag?.name, maxLength: 120),
        RecordsInput.date('starts_on', m.t('tag.f.startsOn'),
            initial: tag?.startsOn),
        RecordsInput.date('ends_on', m.t('tag.f.endsOn'), initial: tag?.endsOn),
        RecordsInput.text('keywords', m.t('tag.f.keywords'),
            initial: tag?.keywords,
            help: m.t('tag.f.keywordsHelp'),
            maxLength: 200),
        RecordsInput.multiline('notes', m.t('tag.f.notes'),
            initial: tag?.notes, maxLength: 1000),
        if (tag != null)
          RecordsInput.toggle('archived', m.t('tag.f.archived'),
              initial: tag.archived),
      ],
      onSubmit: (v) async {
        final start = v['starts_on'] as String?, end = v['ends_on'] as String?;
        if (start != null && end != null && end.compareTo(start) < 0) {
          throw FormatException(m.t('tag.badDates'));
        }
        saved = await repository.saveTag(tag?.id, {
          'kind': v['kind'] ?? 'trip',
          'name': v['name'],
          'starts_on': start,
          'ends_on': end,
          'keywords': v['keywords'],
          'notes': v['notes'],
          'archived': v['archived'] ?? false,
        });
      },
    ),
  );
  return saved;
}

/// A payment from the person's own records, picked from a sheet with a
/// search. Null when the person closes it.
Future<LoanCandidate?> pickPayment(
  BuildContext context, {
  required String title,
  required String intro,
  required Future<List<LoanCandidate>> Function(String query) load,
}) =>
    showRecordsSheet<LoanCandidate>(context,
        (sheet) => _PaymentPicker(title: title, intro: intro, load: load));

class _PaymentPicker extends StatefulWidget {
  const _PaymentPicker(
      {required this.title, required this.intro, required this.load});
  final String title, intro;
  final Future<List<LoanCandidate>> Function(String query) load;

  @override
  State<_PaymentPicker> createState() => _PaymentPickerState();
}

class _PaymentPickerState extends State<_PaymentPicker> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<LoanCandidate> _items = const [];
  String _query = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final items = await widget.load(_query);
      if (!mounted) return;
      setState(() {
        _items = items;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(widget.title, style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: AnumSpacing.xs),
        Text(widget.intro, style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.sm),
        RecordsSearchField(
            initial: _query,
            hint: m.t('split.search'),
            onSearch: (q) {
              _query = q;
              _load();
            }),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else if (_items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.md),
            child: Text(m.t('split.noPayments'),
                style: TextStyle(color: context.palette.muted)),
          )
        else
          for (final c in _items)
            ListTile(
              key: Key('pay-${c.canonicalId}'),
              contentPadding: EdgeInsets.zero,
              title: Text(c.merchant.isEmpty ? c.postedOn : c.merchant),
              subtitle: Text([
                c.postedOn,
                formatAmount(c.amount, c.currency),
                m.t('split.left',
                    {'amount': formatAmount(c.remaining, c.currency)}),
              ].join(' · ')),
              onTap: () => Navigator.pop(context, c),
            ),
      ],
    );
  }
}

class TagScreen extends StatefulWidget {
  const TagScreen({required this.repository, required this.id, super.key});
  final FinanceMoreRepository repository;
  final String id;

  @override
  State<TagScreen> createState() => _TagScreenState();
}

class _TagScreenState extends State<TagScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  FinanceTag? _tag;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final tag = await widget.repository.tag(widget.id);
      if (!mounted) return;
      setState(() {
        _tag = tag;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _act(Future<void> Function() work, String done) async {
    try {
      await work();
      if (!mounted) return;
      recordsToast(context, done);
      await _load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _edit(FinanceTag tag) async {
    final m = FinanceMoreStrings.of(context);
    final saved = await openTagForm(context, widget.repository, tag: tag);
    if (saved != null && mounted) {
      recordsToast(context, m.t('tag.saved'));
      await _load();
    }
  }

  Future<void> _delete(FinanceTag tag) async {
    final m = FinanceMoreStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('tag.delete.title', {'name': tag.name}),
        message: m.t('tag.delete.text'),
        action: m.t('tag.delete'))) {
      return;
    }
    try {
      await widget.repository.deleteTag(tag.id);
      if (!mounted) return;
      recordsToast(context, m.t('tag.deleted'));
      Navigator.of(context).pop();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _add(FinanceTag tag) async {
    final m = FinanceMoreStrings.of(context);
    final picked = await pickPayment(context,
        title: m.t('tag.pick.title', {'name': tag.name}),
        intro: m.t('tag.pick.text'),
        load: (q) => widget.repository.tagCandidates(kind: tag.kind, query: q));
    if (picked == null || !mounted) return;
    await _act(
        () => widget.repository.tagPayment(tag.id, picked.canonicalId,
            idempotencyKey: newIdempotencyKey('tag')),
        m.t('tag.tagged'));
  }

  Future<void> _untag(FinanceTag tag, LoanMovement link) async {
    final m = FinanceMoreStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('tag.untag.title'),
        message: m.t('tag.untag.text'),
        action: m.t('tag.untag'))) {
      return;
    }
    await _act(() => widget.repository.untagPayment(tag.id, link.linkId),
        m.t('tag.untagged'));
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final tag = _tag;
    return Scaffold(
      appBar: AppBar(title: Text(tag?.name ?? m.t('tag.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || tag == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else
          ..._body(context, tag),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, FinanceTag tag) {
    final m = FinanceMoreStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final active = tag.movements.where((l) => l.active).toList();
    return [
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        AnumPill(label: m.t('tag.kind1.${tag.kind}')),
        if (tag.archived) AnumPill(label: m.t('tag.f.archived')),
      ]),
      Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
        FilledButton.icon(
          key: const Key('tag-add'),
          onPressed: () => _add(tag),
          icon: const Icon(Icons.add_link),
          label: Text(m.t('tag.add')),
        ),
        OutlinedButton.icon(
          key: const Key('tag-edit'),
          onPressed: () => _edit(tag),
          icon: const Icon(Icons.edit_outlined),
          label: Text(m.t('tag.edit')),
        ),
        OutlinedButton.icon(
          key: const Key('tag-delete'),
          onPressed: () => _delete(tag),
          icon: const Icon(Icons.delete_outline),
          label: Text(m.t('tag.delete')),
        ),
      ]),
      RecordsSection(title: tag.name, children: [
        RecordsField(m.t('tag.f.startsOn'), tag.startsOn),
        RecordsField(m.t('tag.f.endsOn'), tag.endsOn),
        RecordsField(m.t('tag.f.keywords'), tag.keywords),
        RecordsField(m.t('tag.f.notes'), tag.notes),
        RecordsField(m.t('tag.count', {'n': tag.links}),
            tag.totals.isEmpty ? m.t('tag.noTotals') : _totals(tag.totals)),
      ]),
      AnumHeader(title: m.t('tag.payments')),
      if (active.isEmpty)
        Text(m.t('tag.paymentsNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final link in active)
              AnumRow(
                key: Key('tag-link-${link.linkId}'),
                icon: link.role == 'refund'
                    ? Icons.call_received
                    : Icons.call_made,
                title: link.description ?? link.postedOn ?? '-',
                subtitle: [
                  m.t(link.role == 'refund' ? 'tag.refund' : 'tag.payment'),
                  if (link.postedOn != null) link.postedOn!,
                  if (link.amount != null && link.currency != null)
                    formatAmount(link.amount!, link.currency!),
                ].join(' · '),
                trailing: IconButton(
                  key: Key('tag-untag-${link.linkId}'),
                  tooltip: m.t('tag.untag'),
                  icon: const Icon(Icons.link_off),
                  onPressed: () => _untag(tag, link),
                ),
              ),
          ]),
        ),
    ];
  }
}
