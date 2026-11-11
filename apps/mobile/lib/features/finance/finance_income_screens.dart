import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_money_models.dart';
import 'finance_money_strings.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';
import 'finance_personal_models.dart';
import 'finance_tags_screens.dart' show pickPayment;

/// Finance, Payslips and Send home on the phone (docs/finance.md#payslips,
/// #sending-money-home): add, edit and delete each record, link it to the
/// real transaction (the salary credit, the payment that paid a transfer)
/// and remove a link. Nothing here moves money.

String? _currency(RecordsValues v, FinanceMoneyStrings m) {
  final c = (v['currency'] as String?)?.toUpperCase();
  if (c != null && !RegExp(r'^[A-Z]{3}$').hasMatch(c)) {
    throw FormatException(m.t('plan.badCurrency'));
  }
  return c;
}

// -- payslips ----------------------------------------------------------------------

class PayslipsScreen extends StatefulWidget {
  const PayslipsScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<PayslipsScreen> createState() => _PayslipsScreenState();
}

class _PayslipsScreenState extends State<PayslipsScreen> {
  late final RecordsList<Payslip> _list = RecordsList((query, _, token) =>
      widget.repository.payslips(query: query, token: token));

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
    final saved = await openPayslipForm(context, widget.repository);
    if (saved != null && mounted) await _open(saved);
  }

  Future<void> _open(Payslip slip) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) =>
            PayslipScreen(repository: widget.repository, id: slip.id)));
    if (mounted) await _list.load();
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final k = KitStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('slip.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('slip.intro'),
              style: TextStyle(color: context.palette.muted)),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('slip-new'),
              onPressed: _new,
              icon: const Icon(Icons.add),
              label: Text(m.t('slip.new')),
            ),
          ),
          RecordsSearchField(
              initial: _list.query,
              hint: m.t('slip.search'),
              onSearch: (q) => _list.load(query: q)),
          ...recordsPagedBody<Payslip>(
            _list,
            deniedHint: FinanceMoreStrings.of(context).t('denied'),
            empty: _list.narrowed
                ? AnumEmpty(
                    icon: Icons.search_off,
                    title: k.t('noMatch'),
                    message: k.t('noMatchText'))
                : AnumEmpty(
                    icon: Icons.receipt_outlined,
                    title: m.t('slip.empty'),
                    message: m.t('slip.emptyText'),
                    action: m.t('slip.new'),
                    onAction: _new),
            row: (Payslip s) => AnumRow(
              key: Key('slip-${s.id}'),
              icon: Icons.receipt_outlined,
              tone: _slipTone(s.status),
              title: '${s.month} · ${s.employer}',
              subtitle:
                  m.t('slip.net', {'amount': formatAmount(s.net, s.currency)}),
              detail: _slipStatus(m, s),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(s),
            ),
          ),
        ]),
      ),
    );
  }
}

AnumTone _slipTone(String status) => switch (status) {
      'matched' => AnumTone.ok,
      'differs' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String _slipStatus(FinanceMoneyStrings m, Payslip s) => m.t(
    'slip.status.${s.status}',
    {'amount': formatAmount(s.difference, s.currency)});

/// The payslip form: new, or [slip] edited (the full body is sent).
Future<Payslip?> openPayslipForm(
    BuildContext context, FinanceMoreRepository repository,
    {Payslip? slip}) async {
  final m = FinanceMoneyStrings.of(context);
  Payslip? saved;
  String? existing(String kind) =>
      slip?.deductions.where((d) => d.kind == kind).firstOrNull?.amount;
  await openRecordsForm(
    context,
    RecordsForm(
      title: slip == null ? m.t('slip.new') : m.t('slip.edit'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.text('employer', m.t('slip.employer'),
            required: true, initial: slip?.employer, maxLength: 120),
        RecordsInput.text('month', m.t('slip.month'),
            required: true, initial: slip?.month),
        RecordsInput.date('paid_on', m.t('slip.paidOn'), initial: slip?.paidOn),
        RecordsInput.text('currency', m.t('slip.currency'),
            initial: slip?.currency),
        RecordsInput.number('gross', m.t('slip.gross'),
            required: true, initial: slip?.gross),
        for (final kind in deductionKinds)
          RecordsInput.number(
              'd_$kind', m.t('slip.ded', {'kind': m.t('slip.kind.$kind')}),
              initial: existing(kind), help: m.t('slip.dedHelp')),
        RecordsInput.multiline('notes', m.t('slip.notes'),
            initial: slip?.notes, maxLength: 500),
      ],
      onSubmit: (v) async {
        final month = v['month'] as String;
        if (!RegExp(r'^\d{4}-(0[1-9]|1[0-2])$').hasMatch(month)) {
          throw FormatException(m.t('slip.badMonth'));
        }
        saved = await repository.savePayslip(slip?.id, {
          'employer': v['employer'],
          'month': month,
          'paid_on': v['paid_on'],
          'currency': _currency(v, m),
          'gross': v['gross'],
          'deductions': [
            for (final kind in deductionKinds)
              if (v['d_$kind'] != null &&
                  double.tryParse(v['d_$kind'] as String) != 0)
                Deduction(kind, v['d_$kind'] as String).toJson(),
          ],
          'notes': v['notes'],
        });
      },
    ),
  );
  return saved;
}

/// A linked record's page with its links: payslips and transfers home share
/// the shape (`movements`, `POST|DELETE .../links`).
abstract class _LinkedRecordState<T extends StatefulWidget, R>
    extends State<T> {
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  R? record;

  Future<R> fetch();

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() => phase = RecordsPhase.loading);
    try {
      final found = await fetch();
      if (!mounted) return;
      setState(() {
        record = found;
        phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final f = RecordsFailure.from(error);
      setState(() {
        failure = f;
        phase = f.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> act(Future<void> Function() work, String done) async {
    try {
      await work();
      if (!mounted) return;
      recordsToast(context, done);
      await load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> remove(
      {required String title,
      required String text,
      required String action,
      required String done,
      required Future<void> Function() work}) async {
    if (!await confirmRecords(context,
        title: title, message: text, action: action)) {
      return;
    }
    try {
      await work();
      if (!mounted) return;
      recordsToast(context, done);
      Navigator.of(context).pop();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Widget links(BuildContext context, String title, List<LoanMovement> all,
      {required String unlinkLabel,
      required void Function(LoanMovement link) onUnlink}) {
    final active = all.where((l) => l.active).toList();
    if (active.isEmpty) return const SizedBox.shrink();
    return RecordsSection(title: title, children: [
      for (final link in active)
        AnumRow(
          key: Key('record-link-${link.linkId}'),
          icon: Icons.link,
          title: link.description ?? link.postedOn ?? '-',
          subtitle: [
            if (link.postedOn != null) link.postedOn!,
            if (link.amount != null && link.currency != null)
              formatAmount(link.amount!, link.currency!),
          ].join(' · '),
          trailing: IconButton(
            key: Key('record-unlink-${link.linkId}'),
            tooltip: unlinkLabel,
            icon: const Icon(Icons.link_off),
            onPressed: () => onUnlink(link),
          ),
        ),
    ]);
  }
}

class PayslipScreen extends StatefulWidget {
  const PayslipScreen({required this.repository, required this.id, super.key});
  final FinanceMoreRepository repository;
  final String id;

  @override
  State<PayslipScreen> createState() => _PayslipScreenState();
}

class _PayslipScreenState extends _LinkedRecordState<PayslipScreen, Payslip> {
  @override
  Future<Payslip> fetch() => widget.repository.payslip(widget.id);

  Future<void> _link(Payslip s) async {
    final m = FinanceMoneyStrings.of(context);
    final picked = await pickPayment(context,
        title: m.t('slip.link.title'),
        intro: m.t('slip.link.text'),
        load: (q) => widget.repository.recordCandidates('payslip',
            around: s.paidOn, currency: s.currency, query: q));
    if (picked == null || !mounted) return;
    await act(
        () => widget.repository.linkRecord('payslips', s.id, picked.canonicalId,
            idempotencyKey: newIdempotencyKey('slip')),
        m.t('slip.linked'));
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final s = record;
    return Scaffold(
      appBar: AppBar(title: Text(s == null ? m.t('slip.title') : s.month)),
      body: AnumPage(onRefresh: load, children: [
        if (phase != RecordsPhase.ready || s == null)
          RecordsStateView(
              phase: phase,
              failure: failure,
              deniedHint: FinanceMoreStrings.of(context).t('denied'),
              onRetry: load)
        else ...[
          AnumHeader(title: s.employer, subtitle: s.month),
          Wrap(spacing: AnumSpacing.xs, children: [
            AnumPill(label: _slipStatus(m, s), tone: _slipTone(s.status)),
          ]),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            FilledButton.icon(
              key: const Key('slip-link'),
              onPressed: () => _link(s),
              icon: const Icon(Icons.add_link),
              label: Text(m.t('slip.link')),
            ),
            OutlinedButton.icon(
              key: const Key('slip-edit'),
              onPressed: () async {
                final saved =
                    await openPayslipForm(context, widget.repository, slip: s);
                if (saved != null && context.mounted) {
                  recordsToast(context, m.t('slip.saved'));
                  await load();
                }
              },
              icon: const Icon(Icons.edit_outlined),
              label: Text(m.t('slip.edit')),
            ),
            OutlinedButton.icon(
              key: const Key('slip-delete'),
              onPressed: () => remove(
                  title: m.t('slip.delete.title', {'month': s.month}),
                  text: m.t('slip.delete.text'),
                  action: m.t('slip.delete'),
                  done: m.t('slip.deleted'),
                  work: () => widget.repository.deletePayslip(s.id)),
              icon: const Icon(Icons.delete_outline),
              label: Text(m.t('slip.delete')),
            ),
          ]),
          RecordsSection(title: m.t('slip.title'), children: [
            RecordsField(m.t('slip.gross'), formatAmount(s.gross, s.currency)),
            for (final d in s.deductions)
              RecordsField(m.t('slip.kind.${d.kind}'),
                  formatAmount(d.amount, s.currency)),
            RecordsField(m.t('slip.deductions', {'amount': ''}).trim(),
                formatAmount(s.totalDeductions, s.currency)),
            RecordsField(m.t('slip.net', {'amount': ''}).trim(),
                formatAmount(s.net, s.currency)),
            RecordsField(m.t('slip.received', {'amount': ''}).trim(),
                formatAmount(s.received, s.currency)),
            RecordsField(m.t('slip.paidOn'), s.paidOn),
            RecordsField(m.t('slip.notes'), s.notes),
          ]),
          links(context, m.t('slip.links'), s.movements,
              unlinkLabel: m.t('slip.unlink'),
              onUnlink: (link) => _unlink(s, link)),
        ],
      ]),
    );
  }

  Future<void> _unlink(Payslip s, LoanMovement link) async {
    final m = FinanceMoneyStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('slip.unlink.title'),
        message: m.t('slip.unlink.text'),
        action: m.t('slip.unlink'))) {
      return;
    }
    await act(
        () => widget.repository.unlinkRecord('payslips', s.id, link.linkId),
        m.t('slip.unlinked'));
  }
}

// -- send home ---------------------------------------------------------------------

class SendHomeScreen extends StatefulWidget {
  const SendHomeScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<SendHomeScreen> createState() => _SendHomeScreenState();
}

class _SendHomeScreenState extends State<SendHomeScreen> {
  List<RemitProvider> _providers = const [];
  late final RecordsList<Remittance> _list =
      RecordsList((query, _, token) async {
    final page =
        await widget.repository.remittances(query: query, token: token);
    if (token == null && mounted) setState(() => _providers = page.providers);
    return page.chunk;
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

  Future<void> _new() async {
    final saved = await openRemittanceForm(context, widget.repository);
    if (saved != null && mounted) await _open(saved);
  }

  Future<void> _open(Remittance r) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) =>
            RemittanceScreen(repository: widget.repository, id: r.id)));
    if (mounted) await _list.load();
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final k = KitStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('home.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('home.intro'),
              style: TextStyle(color: context.palette.muted)),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('home-new'),
              onPressed: _new,
              icon: const Icon(Icons.add),
              label: Text(m.t('home.new')),
            ),
          ),
          RecordsSearchField(
              initial: _list.query,
              hint: m.t('home.search'),
              onSearch: (q) => _list.load(query: q)),
          ...recordsPagedBody<Remittance>(
            _list,
            deniedHint: FinanceMoreStrings.of(context).t('denied'),
            empty: _list.narrowed
                ? AnumEmpty(
                    icon: Icons.search_off,
                    title: k.t('noMatch'),
                    message: k.t('noMatchText'))
                : AnumEmpty(
                    icon: Icons.send_outlined,
                    title: m.t('home.empty'),
                    message: m.t('home.emptyText'),
                    action: m.t('home.new'),
                    onAction: _new),
            row: (Remittance r) => AnumRow(
              key: Key('home-${r.id}'),
              icon: Icons.send_outlined,
              title: m.t('home.row', {
                'amount': formatAmount(r.amount, r.currency),
                'recipient': r.recipient,
              }),
              subtitle: '${r.sentOn} · ${r.provider}',
              detail: [
                m.t('home.detail', {
                  'fee': formatAmount(r.fee, r.currency),
                  'total': formatAmount(r.totalPaid, r.currency),
                }),
                if (r.receivedAmount != null && r.receivedCurrency != null)
                  m.t('home.arrived', {
                    'amount':
                        formatAmount(r.receivedAmount!, r.receivedCurrency!)
                  }),
                if (r.regular) m.t('home.regularPill'),
              ].join(' · '),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(r),
            ),
          ),
          if (_providers.isNotEmpty) ...[
            AnumHeader(
                title: m.t('home.providers'),
                subtitle: m.t('home.providers.sub')),
            AnumSurface(
              key: const Key('home-providers'),
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
              child: Column(children: [
                for (final p in _providers)
                  AnumRow(
                    icon: Icons.compare_arrows,
                    title: m.t('home.provider.row',
                        {'provider': p.provider, 'corridor': p.corridor}),
                    subtitle: m.t('home.provider.detail', {
                      'n': p.transfers,
                      'fee': p.averageFeePercent ?? '-',
                      'rate': p.bestRate ?? '-',
                    }),
                  ),
              ]),
            ),
          ],
        ]),
      ),
    );
  }
}

/// The transfer form: new, or [r] edited (the full body is sent).
Future<Remittance?> openRemittanceForm(
    BuildContext context, FinanceMoreRepository repository,
    {Remittance? r}) async {
  final m = FinanceMoneyStrings.of(context);
  Remittance? saved;
  await openRecordsForm(
    context,
    RecordsForm(
      title: r == null ? m.t('home.new') : m.t('home.edit'),
      intro: m.t('home.rateOrReceived'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.date('sent_on', m.t('home.sentOn'),
            required: true, initial: r?.sentOn),
        RecordsInput.number('amount', m.t('home.amount'),
            required: true, initial: r?.amount),
        RecordsInput.text('currency', m.t('home.currency'),
            initial: r?.currency),
        RecordsInput.number('fee', m.t('home.fee'), initial: r?.fee ?? '0'),
        RecordsInput.number('fx_rate', m.t('home.rate'), initial: r?.fxRate),
        RecordsInput.number('received_amount', m.t('home.received'),
            initial: r?.receivedAmount),
        RecordsInput.text('received_currency', m.t('home.receivedCurrency'),
            initial: r?.receivedCurrency),
        RecordsInput.text('recipient', m.t('home.recipient'),
            required: true, initial: r?.recipient, maxLength: 120),
        RecordsInput.text('provider', m.t('home.provider'),
            required: true, initial: r?.provider, maxLength: 120),
        RecordsInput.toggle('regular', m.t('home.regular'),
            initial: r?.regular ?? false),
        RecordsInput.multiline('notes', m.t('home.notes'),
            initial: r?.notes, maxLength: 500),
      ],
      onSubmit: (v) async {
        final received = (v['received_currency'] as String?)?.toUpperCase();
        if (received != null && !RegExp(r'^[A-Z]{3}$').hasMatch(received)) {
          throw FormatException(m.t('plan.badCurrency'));
        }
        saved = await repository.saveRemittance(r?.id, {
          'sent_on': v['sent_on'],
          'amount': v['amount'],
          'currency': _currency(v, m),
          'fee': v['fee'] ?? '0',
          'fx_rate': v['fx_rate'],
          'received_amount': v['received_amount'],
          'received_currency': received,
          'recipient': v['recipient'],
          'provider': v['provider'],
          'regular': v['regular'] ?? false,
          'notes': v['notes'],
        });
      },
    ),
  );
  return saved;
}

class RemittanceScreen extends StatefulWidget {
  const RemittanceScreen(
      {required this.repository, required this.id, super.key});
  final FinanceMoreRepository repository;
  final String id;

  @override
  State<RemittanceScreen> createState() => _RemittanceScreenState();
}

class _RemittanceScreenState
    extends _LinkedRecordState<RemittanceScreen, Remittance> {
  @override
  Future<Remittance> fetch() => widget.repository.remittance(widget.id);

  Future<void> _link(Remittance r) async {
    final m = FinanceMoneyStrings.of(context);
    final picked = await pickPayment(context,
        title: m.t('home.link.title'),
        intro: m.t('home.link.text'),
        load: (q) => widget.repository.recordCandidates('remittance',
            around: r.sentOn, currency: r.currency, query: q));
    if (picked == null || !mounted) return;
    await act(
        () => widget.repository.linkRecord(
            'remittances', r.id, picked.canonicalId,
            idempotencyKey: newIdempotencyKey('home')),
        m.t('home.linked'));
  }

  Future<void> _unlink(Remittance r, LoanMovement link) async {
    final m = FinanceMoneyStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('slip.unlink.title'),
        message: m.t('slip.unlink.text'),
        action: m.t('slip.unlink'))) {
      return;
    }
    await act(
        () => widget.repository.unlinkRecord('remittances', r.id, link.linkId),
        m.t('slip.unlinked'));
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final r = record;
    return Scaffold(
      appBar: AppBar(title: Text(r == null ? m.t('home.title') : r.recipient)),
      body: AnumPage(onRefresh: load, children: [
        if (phase != RecordsPhase.ready || r == null)
          RecordsStateView(
              phase: phase,
              failure: failure,
              deniedHint: FinanceMoreStrings.of(context).t('denied'),
              onRetry: load)
        else ...[
          AnumHeader(
              title: m.t('home.row', {
                'amount': formatAmount(r.amount, r.currency),
                'recipient': r.recipient,
              }),
              subtitle: '${r.sentOn} · ${r.provider}'),
          if (r.regular)
            Wrap(children: [AnumPill(label: m.t('home.regularPill'))]),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            FilledButton.icon(
              key: const Key('home-link'),
              onPressed: () => _link(r),
              icon: const Icon(Icons.add_link),
              label: Text(m.t('home.link')),
            ),
            OutlinedButton.icon(
              key: const Key('home-edit'),
              onPressed: () async {
                final saved =
                    await openRemittanceForm(context, widget.repository, r: r);
                if (saved != null && context.mounted) {
                  recordsToast(context, m.t('home.saved'));
                  await load();
                }
              },
              icon: const Icon(Icons.edit_outlined),
              label: Text(m.t('home.edit')),
            ),
            OutlinedButton.icon(
              key: const Key('home-delete'),
              onPressed: () => remove(
                  title: m.t('home.delete.title'),
                  text: m.t('home.delete.text'),
                  action: m.t('home.delete'),
                  done: m.t('home.deleted'),
                  work: () => widget.repository.deleteRemittance(r.id)),
              icon: const Icon(Icons.delete_outline),
              label: Text(m.t('home.delete')),
            ),
          ]),
          RecordsSection(title: m.t('home.title'), children: [
            RecordsField(m.t('home.fee'), formatAmount(r.fee, r.currency)),
            RecordsField(m.t('home.rate'), r.fxRate),
            RecordsField(
                m.t('home.received'),
                r.receivedAmount == null || r.receivedCurrency == null
                    ? null
                    : formatAmount(r.receivedAmount!, r.receivedCurrency!)),
            if (r.effectiveRate != null)
              RecordsField(
                  m.t('home.effective', {'rate': ''}).trim(), r.effectiveRate),
            RecordsField(m.t('home.notes'), r.notes),
          ]),
          links(context, m.t('home.links'), r.movements,
              unlinkLabel: m.t('slip.unlink'),
              onUnlink: (link) => _unlink(r, link)),
        ],
      ]),
    );
  }
}
