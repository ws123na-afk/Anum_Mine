import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_export.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'books_controller.dart';
import 'books_extra_data.dart';
import 'books_extra_strings.dart';
import 'books_more_data.dart';

/// The books pages the phone gained for parity with the web
/// (docs/business-books.md): the fixed asset register (roll-forward, CSV,
/// an asset's schedule, its depreciation settings, disposal and approved
/// impairments), the business's loans (new loan, schedule against actual,
/// repayments, voids) and a new manual journal entry. Owners and
/// accountants change; members read and are told who acts.

String _money(BuildContext context, String amount, String currency) =>
    currency.isEmpty
        ? amount
        : formatMoneyText(amount, currency,
            locale: Localizations.localeOf(context));

Future<String?> _askReason(BuildContext context,
    {required String title,
    required String label,
    required String action,
    required String problem}) async {
  String? reason;
  final done = await openRecordsForm(
    context,
    RecordsForm(
      title: title,
      submitLabel: action,
      fields: [
        RecordsInput.text('reason', label, required: true, maxLength: 300)
      ],
      onSubmit: (v) async {
        final text = ((v['reason'] as String?) ?? '').trim();
        if (text.length < 5) throw FormatException(problem);
        reason = text;
      },
    ),
  );
  return done ? reason : null;
}

int? _wholeNumber(String? typed) =>
    typed == null ? null : int.tryParse(decimalText(typed) ?? '');

// -- the fixed asset register -------------------------------------------------------

class FixedAssetsScreen extends StatefulWidget {
  const FixedAssetsScreen(
      {required this.controller,
      required this.more,
      this.saver = const SystemExportSaver(),
      super.key});
  final BooksController controller;
  final BooksMoreRepository more;
  final ExportSaver saver;

  @override
  State<FixedAssetsScreen> createState() => _FixedAssetsScreenState();
}

class _FixedAssetsScreenState extends State<FixedAssetsScreen> {
  String _status = 'all';
  AssetRegister? _register;
  late final RecordsList<BookAsset> _list =
      RecordsList<BookAsset>((query, _, token) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page =
        await widget.more.assets(status: _status, query: query, offset: offset);
    if (offset == 0 && mounted) setState(() => _register = page);
    final end = offset + page.items.length;
    return RecordsChunk(page.items,
        next: page.items.isNotEmpty && end < page.total ? '$end' : null,
        total: page.total);
  });

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

  Future<void> _open(BookAsset asset) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => AssetScreen(
            more: widget.more,
            id: asset.id,
            userId: widget.controller.setup?.userId)));
    if (mounted) await _list.load();
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final k = KitStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(x.t('asset.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) {
          final register = _register;
          return AnumPage(onRefresh: _list.load, children: [
            Text(x.t('asset.intro'), style: muted),
            if (register != null && !register.canEdit)
              Text(x.t('accountantsOnly'), style: muted),
            if (register != null && register.pending > 0)
              Text(x.t('asset.pending', {'n': register.pending}),
                  style: TextStyle(color: context.palette.warn)),
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.sm,
                children: [
                  ExportCsvButton(
                      key: const Key('assets-export'),
                      load: widget.more.assetsCsv,
                      fileName: x.t('asset.export'),
                      saver: widget.saver),
                ]),
            RecordsSearchField(
                initial: _list.query,
                hint: x.t('asset.search'),
                onSearch: (q) => _list.load(query: q)),
            RecordsFilterChips(
              options: {
                for (final s in const ['all', 'active', 'disposed'])
                  s: x.t('asset.filter.$s')
              },
              selected: _status,
              onSelected: (s) {
                setState(() => _status = s);
                _list.load(filter: s);
              },
            ),
            ...recordsPagedBody<BookAsset>(
              _list,
              deniedHint: x.t('denied'),
              empty: _list.narrowed || _status != 'all'
                  ? AnumEmpty(
                      icon: Icons.search_off,
                      title: k.t('noMatch'),
                      message: k.t('noMatchText'))
                  : AnumEmpty(
                      icon: Icons.inventory_2_outlined,
                      title: x.t('asset.empty'),
                      message: x.t('asset.emptyText')),
              row: (BookAsset a) => AnumRow(
                key: Key('asset-${a.id}'),
                icon: Icons.inventory_2_outlined,
                tone: !a.sourceLive || a.disposed
                    ? AnumTone.neutral
                    : a.pendingImpairments > 0
                        ? AnumTone.warn
                        : AnumTone.ok,
                title: a.name,
                subtitle: [
                  x.t('asset.acquired',
                      {'date': recordsYearDate(context, a.acquiredOn)}),
                  x.t('asset.method.${a.method}'),
                  if (a.disposed) x.t('asset.disposed'),
                  if (!a.sourceLive) x.t('asset.sourceVoided'),
                  if (a.pendingImpairments > 0) x.t('asset.pendingBadge'),
                ].join(' · '),
                detail: [
                  x.t('asset.cost',
                      {'amount': _money(context, a.cost, a.currency)}),
                  x.t('asset.nbv',
                      {'amount': _money(context, a.netBookValue, a.currency)}),
                ].join(' · '),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _open(a),
              ),
            ),
            if (register != null)
              for (final roll in register.rollForward)
                RecordsSection(
                  key: Key('assets-roll-${roll['currency']}'),
                  title: '${x.t('asset.roll', {
                        'from': ltrValue(context, register.from),
                        'to': ltrValue(context, register.to)
                      })} · ${roll['currency']}',
                  children: [
                    for (final key in const [
                      'opening_cost',
                      'additions',
                      'disposals_cost',
                      'closing_cost',
                      'depreciation',
                      'impairment',
                      'closing_depreciation',
                      'opening_nbv',
                      'closing_nbv'
                    ])
                      if (roll[key] != null)
                        RecordsField(
                            x.t('asset.roll.$key'),
                            _money(
                                context, roll[key]!, roll['currency'] ?? '')),
                  ],
                ),
          ]);
        },
      ),
    );
  }
}

class AssetScreen extends StatefulWidget {
  const AssetScreen(
      {required this.more, required this.id, this.userId, super.key});
  final BooksMoreRepository more;
  final String id;

  /// The signed-in person, so their own impairment request shows who
  /// decides instead of Approve.
  final String? userId;

  @override
  State<AssetScreen> createState() => _AssetScreenState();
}

class _AssetScreenState extends State<AssetScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  BookAsset? _asset;
  bool _all = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final asset = await widget.more.asset(widget.id);
      if (!mounted) return;
      setState(() {
        _asset = asset;
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

  Future<void> _done(bool saved, String message) async {
    if (!saved || !mounted) return;
    recordsToast(context, message);
    await _load();
  }

  Future<void> _edit(BookAsset a) async {
    final x = BooksExtraStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: x.t('asset.edit'),
        intro: x.t('asset.editIntro'),
        submitLabel: x.t('asset.save'),
        fields: [
          RecordsInput.text('name', x.t('asset.name'),
              required: true, initial: a.name, maxLength: 120),
          RecordsInput.text('category', x.t('asset.category'),
              initial: a.category, maxLength: 40),
          RecordsInput.choice('method', x.t('asset.f.method'),
              options: {
                'straight_line': x.t('asset.method.straight_line'),
                'reducing_balance': x.t('asset.method.reducing_balance'),
              },
              initial: a.method),
          RecordsInput.number('rate_pct', x.t('asset.f.rate'),
              initial: a.ratePct),
          RecordsInput.text('life', x.t('asset.life'),
              initial: a.usefulLifeMonths?.toString()),
          RecordsInput.number(
              'residual', '${x.t('asset.f.residual')} (${a.currency})',
              initial: a.residualValue),
          RecordsInput.date('start', x.t('asset.f.start'),
              required: true, initial: a.depreciationStart),
        ],
        onSubmit: (v) async {
          final name = ((v['name'] as String?) ?? '').trim();
          if (name.length < 2) throw FormatException(x.t('asset.badName'));
          final life = _wholeNumber(v['life'] as String?);
          if (v['life'] != null && (life == null || life < 1 || life > 600)) {
            throw FormatException(x.t('asset.badLife'));
          }
          final method = (v['method'] as String?) ?? 'straight_line';
          final rate = double.tryParse((v['rate_pct'] as String?) ?? '');
          if (method == 'reducing_balance' &&
              (rate == null || rate <= 0 || rate > 100)) {
            throw FormatException(x.t('asset.badRate'));
          }
          await widget.more.updateAsset(a.id, {
            'name': name,
            'category': (v['category'] as String?) ?? 'equipment',
            'useful_life_months': life,
            'residual_value': (v['residual'] as String?) ?? '0',
            'method': method,
            'rate_pct': method == 'reducing_balance' ? v['rate_pct'] : null,
            'depreciation_start': v['start'],
            'version': a.version,
          });
        },
      ),
    );
    await _done(saved, x.t('asset.saved'));
  }

  Future<void> _dispose(BookAsset a) async {
    final x = BooksExtraStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: x.t('asset.dispose'),
        intro: x.t('asset.disposeIntro'),
        submitLabel: x.t('asset.disposeSave'),
        fields: [
          RecordsInput.choice('kind', x.t('asset.disposeKind'),
              options: {
                'sale': x.t('asset.kind.sale'),
                'write_off': x.t('asset.kind.write_off'),
              },
              initial: 'sale'),
          RecordsInput.date('on', x.t('asset.disposeOn'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.number(
              'proceeds', '${x.t('asset.f.proceeds')} (${a.currency})'),
        ],
        onSubmit: (v) async {
          final kind = (v['kind'] as String?) ?? 'sale';
          if (kind == 'sale' && v['proceeds'] == null) {
            throw FormatException(x.t('asset.needProceeds'));
          }
          await widget.more.disposeAsset(a.id, {
            'disposed_on': v['on'],
            'kind': kind,
            'proceeds': kind == 'sale' ? v['proceeds'] : '0',
            'version': a.version,
          });
        },
      ),
    );
    await _done(saved, x.t('asset.disposedToast'));
  }

  Future<void> _askImpairment(BookAsset a) async {
    final x = BooksExtraStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: x.t('asset.askImpairment'),
        intro: x.t('asset.impairmentsText'),
        submitLabel: x.t('asset.askImpairment'),
        fields: [
          RecordsInput.date('day', x.t('asset.impDay'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.number(
              'amount', '${x.t('asset.impAmount')} (${a.currency})',
              required: true),
          RecordsInput.text('reason', x.t('asset.impReason'),
              required: true, maxLength: 300),
        ],
        onSubmit: (v) async {
          if (((v['reason'] as String?) ?? '').trim().length < 5) {
            throw FormatException(x.t('asset.badReason'));
          }
          await widget.more.askImpairment(a.id, {
            'entry_date': v['day'],
            'amount': v['amount'],
            'reason': (v['reason'] as String).trim(),
          });
        },
      ),
    );
    await _done(saved, x.t('asset.impAsked'));
  }

  Future<void> _decide(BookAsset a, AssetImpairment imp, bool approve) async {
    final x = BooksExtraStrings.of(context);
    try {
      await widget.more.decideImpairment(a.id, imp.id, approve, imp.version);
      await _done(true, x.t(approve ? 'asset.approved' : 'asset.rejected'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final a = _asset;
    return Scaffold(
      appBar: AppBar(title: Text(a?.name ?? x.t('asset.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || a == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: x.t('denied'),
              onRetry: _load)
        else
          ..._body(context, a),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, BookAsset a) {
    final x = BooksExtraStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    String m(String v) => _money(context, v, a.currency);
    final months = _all ? a.schedule : a.schedule.take(12).toList();
    final editable = a.canEdit && !a.disposed && a.sourceLive;
    return [
      if (!a.sourceLive)
        Text(x.t('asset.sourceVoidedText'), style: TextStyle(color: p.warn)),
      if (!a.canEdit) Text(x.t('accountantsOnly'), style: muted),
      if (editable)
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
          FilledButton.icon(
            key: const Key('asset-edit'),
            onPressed: () => _edit(a),
            icon: const Icon(Icons.tune),
            label: Text(x.t('asset.edit')),
          ),
          OutlinedButton.icon(
            key: const Key('asset-impair'),
            onPressed: () => _askImpairment(a),
            icon: const Icon(Icons.trending_down),
            label: Text(x.t('asset.askImpairment')),
          ),
          OutlinedButton.icon(
            key: const Key('asset-dispose'),
            onPressed: () => _dispose(a),
            icon: const Icon(Icons.sell_outlined),
            label: Text(x.t('asset.dispose')),
          ),
        ]),
      RecordsSection(title: a.name, children: [
        RecordsField(x.t('asset.f.cost'), m(a.cost)),
        RecordsField(
            x.t('asset.f.acquired'), recordsYearDate(context, a.acquiredOn)),
        RecordsField(x.t('asset.f.method'), x.t('asset.method.${a.method}')),
        if (a.ratePct != null) RecordsField(x.t('asset.f.rate'), a.ratePct),
        RecordsField(x.t('asset.life'),
            a.usefulLifeMonths?.toString() ?? x.t('asset.noLife')),
        RecordsField(x.t('asset.f.residual'), m(a.residualValue)),
        RecordsField(x.t('asset.f.start'),
            recordsYearDate(context, a.depreciationStart)),
        RecordsField(x.t('asset.f.accumulated'), m(a.accumulated)),
        RecordsField(x.t('asset.f.impairment'), m(a.impairment)),
        RecordsField(x.t('asset.f.nbv'), m(a.netBookValue)),
        Text(x.t('asset.f.source.${a.sourceKind}'), style: muted),
        if (a.disposed) ...[
          RecordsField(x.t('asset.f.disposedOn'),
              '${recordsYearDate(context, a.disposedOn)} · ${x.t('asset.kind.${a.disposalKind ?? 'sale'}')}'),
          if (a.proceeds != null)
            RecordsField(x.t('asset.f.proceeds'), m(a.proceeds!)),
          if (a.gainOrLoss != null)
            RecordsField(x.t('asset.f.gain'), m(a.gainOrLoss!)),
        ],
      ]),
      AnumHeader(
          title: x.t('asset.impairments'),
          subtitle: x.t('asset.impairmentsText')),
      if (a.impairments.isEmpty)
        Text(x.t('asset.impairmentsNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final imp in a.impairments)
              RecordsRowAction(
                row: AnumRow(
                  key: Key('impairment-${imp.id}'),
                  icon: Icons.trending_down,
                  tone: imp.status == 'pending'
                      ? AnumTone.warn
                      : imp.status == 'approved'
                          ? AnumTone.ok
                          : AnumTone.neutral,
                  title:
                      '${recordsYearDate(context, imp.entryDate)} · ${m(imp.amount)}',
                  subtitle: x.t('asset.impStatus.${imp.status}'),
                  detail: [
                    imp.reason,
                    if (imp.status == 'pending' &&
                        (imp.requestedBy == (a.me ?? widget.userId) ||
                            !a.canEdit))
                      x.t('asset.waitingOther'),
                  ].join(' · '),
                ),
                action: imp.status == 'pending' &&
                        a.canEdit &&
                        imp.requestedBy != (a.me ?? widget.userId)
                    ? Wrap(spacing: AnumSpacing.xs, children: [
                        TextButton(
                            key: Key('impairment-reject-${imp.id}'),
                            onPressed: () => _decide(a, imp, false),
                            child: Text(x.t('asset.reject'))),
                        FilledButton(
                            key: Key('impairment-approve-${imp.id}'),
                            onPressed: () => _decide(a, imp, true),
                            child: Text(x.t('asset.approve'))),
                      ])
                    : null,
              ),
          ]),
        ),
      AnumHeader(title: x.t('asset.schedule')),
      if (a.schedule.isEmpty)
        Text(x.t('asset.scheduleNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final row in months)
              AnumRow(
                icon: Icons.event_outlined,
                tone: row.state == 'scheduled' ? null : AnumTone.ok,
                title: x.t('asset.month', {
                  'month': ltrValue(context, row.month),
                  'amount': m(row.amount)
                }),
                subtitle: x.t('asset.state.${row.state}'),
                detail:
                    x.t('asset.accumulated', {'amount': m(row.accumulated)}),
              ),
          ]),
        ),
      if (!_all && a.schedule.length > 12)
        Align(
          alignment: AlignmentDirectional.center,
          child: OutlinedButton(
            key: const Key('asset-all'),
            onPressed: () => setState(() => _all = true),
            child: Text(x.t('asset.showAll', {'n': a.schedule.length})),
          ),
        ),
    ];
  }
}

// -- loans ---------------------------------------------------------------------

class BookLoansScreen extends StatefulWidget {
  const BookLoansScreen(
      {required this.controller, required this.more, super.key});
  final BooksController controller;
  final BooksMoreRepository more;

  @override
  State<BookLoansScreen> createState() => _BookLoansScreenState();
}

class _BookLoansScreenState extends State<BookLoansScreen> {
  String _status = 'active';
  late final RecordsList<BookLoan> _list = RecordsList<BookLoan>(
      (_, __, token) => widget.more.loans(status: _status, token: token));

  bool get _account => widget.controller.setup?.permissions.account ?? false;

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

  Future<void> _new() async {
    final x = BooksExtraStrings.of(context);
    final key = newIdempotencyKey('bloan');
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: x.t('loan.new'),
        intro: x.t('loan.intro'),
        submitLabel: x.t('loan.create'),
        fields: [
          RecordsInput.text('lender', x.t('loan.lender'),
              required: true, maxLength: 120),
          RecordsInput.number('principal', x.t('loan.principal'),
              required: true),
          RecordsInput.text('currency', x.t('loan.currency'),
              required: true,
              initial: widget.controller.setup?.baseCurrency ?? ''),
          RecordsInput.number('rate', x.t('loan.rate'), required: true),
          RecordsInput.date('start', x.t('loan.start'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.text('term', x.t('loan.term'), required: true),
          RecordsInput.choice('frequency', x.t('loan.frequency'),
              options: {
                for (final f in const ['monthly', 'quarterly', 'yearly'])
                  f: x.t('loan.frequency.$f')
              },
              initial: 'monthly'),
          RecordsInput.choice('method', x.t('loan.method'),
              options: {
                for (final f in const ['annuity', 'equal_principal'])
                  f: x.t('loan.method.$f')
              },
              initial: 'annuity'),
          RecordsInput.choice('early', x.t('loan.early'),
              options: {
                for (final f in const ['reduce_term', 'reduce_payment'])
                  f: x.t('loan.early.$f')
              },
              initial: 'reduce_term'),
        ],
        onSubmit: (v) async {
          if (((v['lender'] as String?) ?? '').trim().length < 2) {
            throw FormatException(x.t('loan.badLender'));
          }
          final currency = ((v['currency'] as String?) ?? '').toUpperCase();
          if (!RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
            throw FormatException(x.t('loan.badCurrency'));
          }
          final rate = double.tryParse((v['rate'] as String?) ?? '');
          if (rate == null || rate < 0 || rate > 100) {
            throw FormatException(x.t('loan.badRate'));
          }
          final term = _wholeNumber(v['term'] as String?);
          if (term == null || term < 1 || term > 600) {
            throw FormatException(x.t('loan.badTerm'));
          }
          await widget.more.createLoan({
            'lender': (v['lender'] as String).trim(),
            'principal': v['principal'],
            'currency': currency,
            'annual_rate': v['rate'],
            'start_date': v['start'],
            'term_months': term,
            'frequency': v['frequency'] ?? 'monthly',
            'method': v['method'] ?? 'annuity',
            'early_repayment': v['early'] ?? 'reduce_term',
          }, idempotencyKey: key);
        },
      ),
    );
    if (saved && mounted) {
      recordsToast(context, x.t('loan.created'));
      await _list.load();
    }
  }

  Future<void> _open(BookLoan loan) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => BookLoanScreen(more: widget.more, id: loan.id)));
    if (mounted) await _list.load();
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(x.t('loan.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(x.t('loan.intro'), style: muted),
          if (_account)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                key: const Key('bloans-new'),
                onPressed: _new,
                icon: const Icon(Icons.add),
                label: Text(x.t('loan.new')),
              ),
            )
          else
            Text(x.t('accountantsOnly'), style: muted),
          RecordsFilterChips(
            options: {
              for (final s in const ['active', 'void', 'all'])
                s: x.t('loan.filter.$s')
            },
            selected: _status,
            onSelected: (s) {
              setState(() => _status = s);
              _list.load(filter: s);
            },
          ),
          ...recordsPagedBody<BookLoan>(
            _list,
            deniedHint: x.t('denied'),
            empty: AnumEmpty(
                icon: Icons.account_balance_outlined,
                title: x.t('loan.empty'),
                message: x.t('loan.emptyText'),
                action: _account ? x.t('loan.new') : null,
                onAction: _account ? _new : null),
            row: (BookLoan l) => AnumRow(
              key: Key('bloan-${l.id}'),
              icon: Icons.account_balance_outlined,
              tone: l.voided ? AnumTone.neutral : AnumTone.ok,
              title: l.lender,
              subtitle: [
                _money(context, l.principal, l.currency),
                '${l.annualRate}%',
                x.t('loan.frequency.${l.frequency}'),
                if (l.voided) x.t('loan.void'),
              ].join(' · '),
              detail: l.voided
                  ? null
                  : [
                      x.t('loan.left', {
                        'amount': _money(context, l.outstanding, l.currency)
                      }),
                      if (l.nextDue != null && l.nextPayment != null)
                        x.t('loan.next', {
                          'amount': _money(context, l.nextPayment!, l.currency),
                          'date': recordsYearDate(context, l.nextDue),
                        }),
                    ].join(' · '),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(l),
            ),
          ),
        ]),
      ),
    );
  }
}

class BookLoanScreen extends StatefulWidget {
  const BookLoanScreen({required this.more, required this.id, super.key});
  final BooksMoreRepository more;
  final String id;

  @override
  State<BookLoanScreen> createState() => _BookLoanScreenState();
}

class _BookLoanScreenState extends State<BookLoanScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  BookLoan? _loan;
  bool _all = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final loan = await widget.more.loan(widget.id);
      if (!mounted) return;
      setState(() {
        _loan = loan;
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

  Future<void> _record(BookLoan l) async {
    final x = BooksExtraStrings.of(context);
    final key = newIdempotencyKey('brepay');
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: x.t('loan.record'),
        intro: x.t('loan.recordIntro'),
        submitLabel: x.t('loan.recordSave'),
        fields: [
          RecordsInput.date('paid_on', x.t('loan.paidOn'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.number('amount', '${x.t('loan.amount')} (${l.currency})',
              required: true, initial: l.nextPayment),
        ],
        onSubmit: (v) => widget.more.recordRepayment(
            l.id, {'paid_on': v['paid_on'], 'amount': v['amount']},
            idempotencyKey: key),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, x.t('loan.recorded'));
      await _load();
    }
  }

  Future<void> _void(BookLoan l, {LoanRepayment? repayment}) async {
    final x = BooksExtraStrings.of(context);
    final reason = await _askReason(context,
        title: repayment == null
            ? x.t('loan.voidLoan')
            : x.t('loan.voidRepayment'),
        label: x.t('loan.voidReason'),
        action: repayment == null
            ? x.t('loan.voidLoan')
            : x.t('loan.voidRepayment'),
        problem: x.t('loan.badReason'));
    if (reason == null || !mounted) return;
    try {
      if (repayment == null) {
        await widget.more.voidLoan(l.id, reason);
      } else {
        await widget.more.voidRepayment(l.id, repayment.id, reason);
      }
      if (!mounted) return;
      recordsToast(context, x.t('loan.voidDone'));
      await _load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final l = _loan;
    return Scaffold(
      appBar: AppBar(title: Text(l?.lender ?? x.t('loan.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || l == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: x.t('denied'),
              onRetry: _load)
        else
          ..._body(context, l),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, BookLoan l) {
    final x = BooksExtraStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    String m(String v) => _money(context, v, l.currency);
    final rows = _all ? l.actual : l.actual.take(12).toList();
    final active = l.repayments.where((r) => r.active).toList();
    final latest = active.isEmpty ? null : active.first;
    return [
      if (l.voided) Text(x.t('loan.voidText'), style: muted),
      if (!l.canEdit) Text(x.t('accountantsOnly'), style: muted),
      if (l.canEdit && !l.voided)
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
          FilledButton.icon(
            key: const Key('bloan-record'),
            onPressed: () => _record(l),
            icon: const Icon(Icons.add),
            label: Text(x.t('loan.record')),
          ),
          if (active.isEmpty)
            OutlinedButton.icon(
              key: const Key('bloan-void'),
              onPressed: () => _void(l),
              icon: const Icon(Icons.block),
              label: Text(x.t('loan.voidLoan')),
            ),
        ]),
      RecordsSection(title: x.t('loan.terms'), children: [
        RecordsField(x.t('loan.principal'), m(l.principal)),
        RecordsField(x.t('loan.rate'), '${l.annualRate}%'),
        RecordsField(x.t('loan.start'), recordsYearDate(context, l.startDate)),
        RecordsField(x.t('loan.term'), '${l.termMonths}'),
        RecordsField(
            x.t('loan.frequency'), x.t('loan.frequency.${l.frequency}')),
        RecordsField(x.t('loan.method'), x.t('loan.method.${l.method}')),
        RecordsField(x.t('loan.early'), x.t('loan.early.${l.earlyRepayment}')),
        if (l.payment != null) RecordsField(x.t('loan.payment'), m(l.payment!)),
        RecordsField(x.t('loan.periods'), '${l.periods}'),
        RecordsField(
            x.t('loan.maturity'), recordsYearDate(context, l.maturity)),
        if (!l.voided) ...[
          RecordsField(
              x.t('loan.left', {'amount': ''}).trim(), m(l.outstanding)),
          RecordsField(x.t('loan.accrued'), m(l.interestAccrued)),
          if (l.repaid != null) RecordsField(x.t('loan.repaid'), m(l.repaid!)),
        ],
      ]),
      AnumHeader(title: x.t('loan.repayments')),
      if (l.repayments.isEmpty)
        Text(x.t('loan.repaymentsNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final r in l.repayments)
              RecordsRowAction(
                row: AnumRow(
                  key: Key('brepay-${r.id}'),
                  icon: Icons.payments_outlined,
                  tone: r.active ? AnumTone.ok : AnumTone.neutral,
                  title:
                      '${recordsYearDate(context, r.paidOn)} · ${m(r.amount)}',
                  subtitle: r.active
                      ? x.t('loan.repaymentSplit', {
                          'interest': m(r.interest),
                          'principal': m(r.principal),
                        })
                      : x.t('loan.repaymentVoided'),
                ),
                action: l.canEdit && r.id == latest?.id
                    ? TextButton(
                        key: Key('brepay-void-${r.id}'),
                        onPressed: () => _void(l, repayment: r),
                        child: Text(x.t('loan.voidRepayment')))
                    : null,
              ),
          ]),
        ),
      if (l.actual.isNotEmpty) ...[
        AnumHeader(title: x.t('loan.actual')),
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final row in rows)
              AnumRow(
                key: Key('bloan-row-${row.number}'),
                icon: Icons.event_outlined,
                tone: switch (row.state) {
                  'paid' => AnumTone.ok,
                  'overdue' => AnumTone.stop,
                  'part_paid' || 'due' => AnumTone.warn,
                  _ => null,
                },
                title: x.t('loan.row', {
                  'n': row.number,
                  'date': recordsYearDate(context, row.due),
                  'amount': m(row.payment),
                }),
                subtitle: x.t('loan.state.${row.state}'),
                detail: [
                  x.t('loan.rowSplit', {
                    'interest': m(row.interest),
                    'principal': m(row.principal),
                  }),
                  if (double.tryParse(row.paid) != 0)
                    x.t('loan.rowPaid', {'amount': m(row.paid)}),
                ].join(' · '),
              ),
          ]),
        ),
        if (!_all && l.actual.length > 12)
          Align(
            alignment: AlignmentDirectional.center,
            child: OutlinedButton(
              key: const Key('bloan-all'),
              onPressed: () => setState(() => _all = true),
              child: Text(x.t('loan.showAll', {'n': l.actual.length})),
            ),
          ),
      ],
    ];
  }
}

// -- a new manual journal entry --------------------------------------------------------

class _Line {
  String? account;
  final debit = TextEditingController();
  final credit = TextEditingController();
  void dispose() {
    debit.dispose();
    credit.dispose();
  }
}

/// A new manual journal entry in the base currency (owners and
/// accountants): the date, what it is for and two or more lines, each a
/// debit or a credit to a ledger account. Debits must equal credits; the
/// page says so before sending. Pops `true` once posted.
class ManualEntryFormScreen extends StatefulWidget {
  const ManualEntryFormScreen(
      {required this.more, required this.currency, super.key});
  final BooksMoreRepository more;
  final String currency;

  @override
  State<ManualEntryFormScreen> createState() => _ManualEntryFormScreenState();
}

class _ManualEntryFormScreenState extends State<ManualEntryFormScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<LedgerAccount> _accounts = const [];
  final _date = TextEditingController(text: isoDay(DateTime.now()));
  final _description = TextEditingController();
  final _lines = [_Line(), _Line()];
  final _key = newIdempotencyKey('manual');
  String? _problem;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _date.dispose();
    _description.dispose();
    for (final line in _lines) {
      line.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final accounts = await widget.more.ledgerAccounts();
      if (!mounted) return;
      setState(() {
        _accounts = accounts;
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

  /// Cents of a typed amount, 0 when empty, null when unreadable.
  int? _cents(String typed) {
    if (typed.trim().isEmpty) return 0;
    final plain = decimalText(typed);
    if (plain == null) return null;
    final parts = plain.split('.');
    if (parts.length > 1 && parts[1].length > 2) return null;
    final fraction = parts.length > 1 ? parts[1].padRight(2, '0') : '00';
    return int.parse(parts[0]) * 100 + int.parse(fraction);
  }

  String _text(int cents) =>
      '${cents ~/ 100}.${(cents % 100).toString().padLeft(2, '0')}';

  ({int debit, int credit}) get _totals {
    var debit = 0, credit = 0;
    for (final line in _lines) {
      debit += _cents(line.debit.text) ?? 0;
      credit += _cents(line.credit.text) ?? 0;
    }
    return (debit: debit, credit: credit);
  }

  Future<void> _save() async {
    final x = BooksExtraStrings.of(context);
    String? problem;
    final lines = <Map<String, Object?>>[];
    if (parseDay(_date.text.trim()) == null) problem = x.t('je.badDate');
    if (problem == null && _description.text.trim().length < 3) {
      problem = x.t('je.badDescription');
    }
    if (problem == null) {
      for (final (i, line) in _lines.indexed) {
        final debit = _cents(line.debit.text),
            credit = _cents(line.credit.text);
        if (line.account == null ||
            debit == null ||
            credit == null ||
            (debit == 0) == (credit == 0)) {
          problem = x.t('je.badLine', {'n': i + 1});
          break;
        }
        lines.add({
          'account_id': line.account,
          'debit': _text(debit),
          'credit': _text(credit),
        });
      }
    }
    final totals = _totals;
    if (problem == null && lines.length < 2) problem = x.t('je.fewLines');
    if (problem == null && totals.debit != totals.credit) {
      problem = x.t('je.unbalanced', {
        'amount': _money(context, _text((totals.debit - totals.credit).abs()),
            widget.currency)
      });
    }
    setState(() => _problem = problem);
    if (problem != null) return;
    setState(() => _saving = true);
    try {
      await widget.more.createManualEntry({
        'entry_date': _date.text.trim(),
        'description': _description.text.trim(),
        'lines': lines,
      }, idempotencyKey: _key);
      if (!mounted) return;
      recordsToast(context, x.t('je.saved'));
      Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _problem = RecordsFailure.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final k = KitStrings.of(context);
    final totals = _totals;
    return Scaffold(
      appBar: AppBar(title: Text(x.t('je.new'))),
      body: AnumPage(children: [
        Text(x.t('je.formIntro', {'currency': widget.currency}),
            style: TextStyle(color: context.palette.muted)),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: x.t('accountantsOnly'),
              onRetry: _load)
        else if (_accounts.isEmpty)
          AnumEmpty(
              icon: Icons.account_tree_outlined,
              title: x.t('je.new'),
              message: x.t('je.noAccounts'))
        else ...[
          RecordsDateField(
              fieldKey: const Key('je-date'),
              controller: _date,
              label: x.t('je.date'),
              required: true),
          TextField(
            key: const Key('je-description'),
            controller: _description,
            maxLength: 300,
            decoration: InputDecoration(labelText: x.t('je.description')),
          ),
          for (final (i, line) in _lines.indexed)
            KeyedSubtree(
              // Each line keeps its own field state when another is removed.
              key: ObjectKey(line),
              child: AnumSurface(
                key: Key('je-line-$i'),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(children: [
                      Expanded(
                        child: Text(x.t('je.line', {'n': i + 1}),
                            style: Theme.of(context).textTheme.titleSmall),
                      ),
                      if (_lines.length > 2)
                        IconButton(
                          key: Key('je-remove-$i'),
                          tooltip: x.t('je.removeLine', {'n': i + 1}),
                          icon: const Icon(Icons.close),
                          onPressed: () => setState(() {
                            _lines.removeAt(i).dispose();
                          }),
                        ),
                    ]),
                    DropdownButtonFormField<String>(
                      key: Key('je-account-$i'),
                      initialValue: line.account,
                      isExpanded: true,
                      decoration: InputDecoration(labelText: x.t('je.account')),
                      items: [
                        for (final a in _accounts)
                          DropdownMenuItem(
                              value: a.id,
                              child: Text(a.label,
                                  overflow: TextOverflow.ellipsis)),
                      ],
                      onChanged: (id) => setState(() => line.account = id),
                    ),
                    Wrap(
                      spacing: AnumSpacing.sm,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        for (final (name, controller) in [
                          ('debit', line.debit),
                          ('credit', line.credit)
                        ])
                          ConstrainedBox(
                            constraints: const BoxConstraints(
                                minWidth: 120, maxWidth: 220),
                            child: TextField(
                              key: Key('je-$name-$i'),
                              controller: controller,
                              keyboardType:
                                  const TextInputType.numberWithOptions(
                                      decimal: true),
                              onChanged: (_) => setState(() {}),
                              decoration: InputDecoration(
                                  labelText:
                                      '${x.t('je.$name')} (${widget.currency})'),
                            ),
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
              key: const Key('je-add-line'),
              onPressed: _lines.length >= 50
                  ? null
                  : () => setState(() => _lines.add(_Line())),
              icon: const Icon(Icons.add),
              label: Text(x.t('je.addLine')),
            ),
          ),
          Text(
              x.t('je.totals', {
                'debit': _money(context, _text(totals.debit), widget.currency),
                'credit':
                    _money(context, _text(totals.credit), widget.currency),
              }),
              key: const Key('je-totals')),
          if (_problem != null)
            Semantics(
              liveRegion: true,
              child: Text(_problem!,
                  key: const Key('je-problem'),
                  style: TextStyle(color: context.palette.stop)),
            ),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: FilledButton(
              key: const Key('je-save'),
              onPressed: _saving ? null : _save,
              child: Text(_saving ? k.t('saving') : x.t('je.save')),
            ),
          ),
        ],
      ]),
    );
  }
}
