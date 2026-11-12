import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_review_models.dart';
import 'finance_sms_setup.dart';
import 'finance_strings.dart';
import 'finance_tools_data.dart';
import 'finance_tools_strings.dart';

/// Finance, iPhone SMS review on the phone (docs/finance-sms.md#review): the
/// messages ANUM read from the person's bank SMS and Apple Pay, filtered by
/// status and paged, each opened to record it as read, correct it, confirm
/// the sender's format, teach a format, ask the AI for the fields (when the
/// person turned AI help on), dismiss it or undo a recorded one; a message
/// pasted by hand; and the formats ANUM learned. **Set up forwarding** opens
/// the safety check, the devices and their tokens, and the Shortcut steps
/// (`finance_sms_setup.dart`).

String _when(BuildContext context, String? value) =>
    value == null || value.isEmpty ? '-' : recordsDateTime(context, value);

/// "Card purchase · AED 25.00 · Example shop" for a message or entry.
String smsLine(BuildContext context, SmsMessage m) {
  final t = FinanceToolsStrings.of(context);
  final p = m.parsed;
  return [
    t.label('sms.kind', p['kind'] ?? 'unknown'),
    if (p['amount'] != null)
      p['currency'] == null
          ? ltrValue(context, p['amount']!)
          : formatAmount(p['amount']!, p['currency']!),
    if (p['merchant'] != null) p['merchant']!,
  ].join(' · ');
}

class SmsReviewScreen extends StatefulWidget {
  const SmsReviewScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<SmsReviewScreen> createState() => _SmsReviewScreenState();
}

class _SmsReviewScreenState extends State<SmsReviewScreen> {
  String _status = 'review';
  late final RecordsList<SmsMessage> _messages = RecordsList((_, __, token) =>
      widget.repository.smsMessages(status: _status, token: token));
  SmsStatus? _state;
  List<SmsRule>? _rules;
  String? _rulesError;
  List<FinanceAccount> _accounts = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _messages.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    await Future.wait([_messages.load(), _loadSide()]);
  }

  Future<void> _loadSide() async {
    final repo = widget.repository;
    try {
      final state = await repo.smsStatus();
      if (mounted) setState(() => _state = state);
    } on Object {
      // The list says what failed; the header stays out.
    }
    try {
      final rules = await repo.smsRules();
      if (mounted) {
        setState(() {
          _rules = rules;
          _rulesError = null;
        });
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() => _rulesError = RecordsFailure.from(error).message);
      }
    }
    try {
      final accounts = await repo.financeAccounts();
      if (mounted) setState(() => _accounts = accounts);
    } on Object {
      // The correction form then offers "as read" only.
    }
  }

  Future<void> _open(SmsMessage m) async {
    final changed = await Navigator.of(context).push(MaterialPageRoute<bool>(
        builder: (_) => SmsMessageScreen(
            repository: widget.repository,
            message: m,
            accounts: _accounts,
            aiOn: _state?.aiOn ?? false)));
    if (changed == true && mounted) await _reload();
  }

  Future<void> _setup() async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => SmsSetupScreen(repository: widget.repository)));
    if (mounted) await _reload();
  }

  Future<void> _paste() async {
    final t = FinanceToolsStrings.of(context);
    String? answer;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: t.t('sms.paste'),
        intro: t.t('sms.pasteHelp'),
        submitLabel: t.t('sms.pasteSend'),
        fields: [
          RecordsInput.text('sender', t.t('sms.pasteSender'),
              required: true, maxLength: 200),
          RecordsInput.multiline('message', t.t('sms.pasteText'),
              required: true, maxLength: 2000),
        ],
        onSubmit: (v) async {
          answer = await widget.repository
              .pasteSms(v['sender']! as String, v['message']! as String);
        },
      ),
    );
    if (!saved || !mounted) return;
    recordsToast(context, answer?.isNotEmpty == true ? answer! : t.t('saved'));
    await _reload();
  }

  Future<void> _rule(SmsRule rule, String change) async {
    final t = FinanceToolsStrings.of(context);
    if (change == 'delete') {
      final ok = await confirmRecords(context,
          title: t.t('sms.ruleDeleteTitle'),
          message: t.t('sms.ruleDeleteText', {'sender': rule.sender}),
          action: t.t('sms.rule.delete'));
      if (!ok || !mounted) return;
    }
    try {
      if (change == 'delete') {
        await widget.repository.deleteSmsRule(rule.id);
      } else {
        await widget.repository.changeSmsRule(rule.id, change);
      }
      if (!mounted) return;
      recordsToast(context, t.t('saved'));
      await _loadSide();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final st = _state;
    return Scaffold(
      appBar: AppBar(title: Text(t.t('sms.title'))),
      body: ListenableBuilder(
        listenable: _messages,
        builder: (context, _) => AnumPage(onRefresh: _reload, children: [
          Text(t.t('sms.intro'), style: muted),
          if (st != null)
            RecordsSection(title: t.t('sms.state'), children: [
              RecordsField(t.t('sms.forwarding'),
                  t.t(st.enabled ? 'sms.on' : 'sms.off')),
              RecordsField(t.t('sms.devices'), '${st.devicesActive}'),
              RecordsField(
                  t.t('sms.lastReceived'), _when(context, st.lastReceivedAt)),
              RecordsField(
                  t.t('sms.lastError'),
                  st.lastError == null
                      ? '-'
                      : t.label('sms.reason', st.lastError!)),
              RecordsField(t.t('sms.waiting'), '${st.reviewCount}'),
              if (!st.ready) Text(t.t('sms.setupOnWeb'), style: muted),
            ]),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
              key: const Key('sms-setup'),
              onPressed: _setup,
              icon: const Icon(Icons.settings_outlined),
              label: Text(SmsSetupStrings.of(context).t('open')),
            ),
            OutlinedButton.icon(
              key: const Key('sms-paste'),
              onPressed: _paste,
              icon: const Icon(Icons.content_paste),
              label: Text(t.t('sms.paste')),
            ),
          ]),
          AnumHeader(title: t.t('sms.review'), subtitle: t.t('sms.reviewSub')),
          RecordsFilterChips(
            options: {
              for (final s in ['', ...smsStatuses])
                s: s.isEmpty ? t.t('sms.status.all') : t.t('sms.status.$s')
            },
            selected: _status,
            onSelected: (s) {
              setState(() => _status = s);
              _messages.load(filter: s);
            },
          ),
          ...recordsPagedBody<SmsMessage>(
            _messages,
            deniedHint: t.t('denied'),
            empty: AnumEmpty(
                icon: Icons.sms_outlined,
                title: t.t('sms.none'),
                message: t.t('sms.noneText')),
            row: (m) => AnumRow(
              key: Key('sms-message-${m.id}'),
              icon: Icons.sms_outlined,
              tone: m.status == 'review' || m.status == 'failed'
                  ? AnumTone.warn
                  : m.posted
                      ? AnumTone.ok
                      : null,
              title: m.sender.isEmpty ? '-' : ltrValue(context, m.sender),
              subtitle: [
                _when(context, m.receivedAt),
                t.t('sms.status.${m.status}'),
                if (m.reason != null) t.label('sms.reason', m.reason!),
              ].join(' · '),
              detail: smsLine(context, m),
              trailing: m.actionable ? const Icon(Icons.chevron_right) : null,
              onTap: m.actionable ? () => _open(m) : null,
            ),
          ),
          AnumHeader(title: t.t('sms.rules'), subtitle: t.t('sms.rulesSub')),
          if (_rulesError != null)
            Text(_rulesError!, style: TextStyle(color: context.palette.stop))
          else if (_rules == null)
            const Center(child: CircularProgressIndicator())
          else if (_rules!.isEmpty)
            Text(t.t('sms.rulesNone'), style: muted)
          else
            AnumSurface(
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
              child: Column(children: [
                for (final r in _rules!)
                  RecordsRowAction(
                    row: AnumRow(
                      key: Key('sms-rule-${r.id}'),
                      icon: Icons.rule,
                      tone: r.status == 'proposed' ? AnumTone.warn : null,
                      title: ltrValue(context, r.sender),
                      subtitle: [
                        t.label('sms.kind', r.kind),
                        t.t('sms.rule.${r.status}'),
                        t.t('sms.rule.hits', {'n': r.hits}),
                      ].join(' · '),
                      detail: r.pattern.isEmpty
                          ? null
                          : ltrValue(context, r.pattern),
                    ),
                    action: Wrap(spacing: AnumSpacing.xs, children: [
                      TextButton(
                        key: Key('sms-rule-toggle-${r.id}'),
                        onPressed: () => _rule(
                            r, r.status == 'active' ? 'disable' : 'activate'),
                        child: Text(t.t(r.status == 'active'
                            ? 'sms.rule.disable'
                            : 'sms.rule.activate')),
                      ),
                      TextButton(
                        key: Key('sms-rule-delete-${r.id}'),
                        onPressed: () => _rule(r, 'delete'),
                        child: Text(t.t('sms.rule.delete')),
                      ),
                    ]),
                  ),
              ]),
            ),
        ]),
      ),
    );
  }
}

/// One message: what ANUM read, the fields to record (prefilled), and the
/// actions the web's correction dialog has.
class SmsMessageScreen extends StatefulWidget {
  const SmsMessageScreen(
      {required this.repository,
      required this.message,
      required this.accounts,
      required this.aiOn,
      super.key});
  final FinanceMoreRepository repository;
  final SmsMessage message;
  final List<FinanceAccount> accounts;
  final bool aiOn;

  @override
  State<SmsMessageScreen> createState() => _SmsMessageScreenState();
}

class _SmsMessageScreenState extends State<SmsMessageScreen> {
  static const _textFields = [
    'amount',
    'currency',
    'converted_amount',
    'merchant',
    'occurred_on',
    'reference',
    'interest',
    'fee',
    'last4',
  ];
  late final Map<String, TextEditingController> _text = {
    for (final key in _textFields)
      key: TextEditingController(text: widget.message.parsed[key] ?? ''),
  };
  String? _kind, _account, _counter, _category;
  bool _confirmFormat = false, _teach = false, _allowDuplicate = false;
  bool _busy = false;
  String? _error, _note;

  @override
  void initState() {
    super.initState();
    final kind = widget.message.parsed['kind'];
    _kind = smsKinds.contains(kind) ? kind : null;
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  /// Only the fields the person filled in, so the server keeps what it read
  /// for the rest (the web's `correctionBody`).
  Map<String, Object?> _body() => {
        if (_kind != null) 'kind': _kind,
        for (final MapEntry(:key, :value) in _text.entries)
          if (value.text.trim().isNotEmpty)
            key: key == 'currency'
                ? value.text.trim().toUpperCase()
                : value.text.trim(),
        if (_account != null) 'account_id': _account,
        if (_counter != null) 'counter_account_id': _counter,
        if (_category != null) 'category': _category,
        if (_confirmFormat) 'confirm_format': true,
        if (_teach) 'teach': true,
        if (_allowDuplicate) 'allow_duplicate': true,
      };

  Future<void> _act(String what) async {
    final t = FinanceToolsStrings.of(context);
    if (what == 'post' && _kind == null) {
      setState(() => _error = t.t('sms.kindNeeded'));
      return;
    }
    if (what == 'reverse') {
      final ok = await confirmRecords(context,
          title: t.t('sms.undoTitle'),
          message: t.t('sms.undoText'),
          action: t.t('sms.undo'));
      if (!ok || !mounted) return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final repo = widget.repository;
    final id = widget.message.id;
    try {
      switch (what) {
        case 'post':
          final result = await repo.postSms(id, _body());
          if (result.ruleError != null) {
            // Recorded; the format could not be taught. Say so and stay.
            if (mounted) {
              setState(() => _note =
                  t.t('sms.recordedRuleError', {'reason': result.ruleError!}));
            }
            return;
          }
          if (mounted) {
            recordsToast(
                context,
                t.t(result.ruleProposed
                    ? 'sms.recordedTaught'
                    : 'sms.recorded'));
          }
        case 'dismiss':
          await repo.dismissSms(id);
          if (mounted) recordsToast(context, t.t('sms.dismissed'));
        case 'reverse':
          await repo.reverseSms(id);
          if (mounted) recordsToast(context, t.t('sms.undone'));
        case 'suggest':
          final suggestion = await repo.suggestSms(id);
          if (!mounted) return;
          setState(() {
            for (final MapEntry(:key, :value) in suggestion.fields.entries) {
              if (_text.containsKey(key)) _text[key]!.text = value;
              if (key == 'kind' && smsKinds.contains(value)) _kind = value;
            }
            _note = suggestion.note;
          });
          return;
      }
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) setState(() => _error = RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _field(String key, String label,
      {TextInputType? keyboard, TextDirection? direction}) {
    return TextField(
      key: Key('sms-field-$key'),
      controller: _text[key],
      keyboardType: keyboard,
      textDirection: direction,
      decoration: InputDecoration(labelText: label),
    );
  }

  Widget _choice(String key, String label, String? value,
      Map<String, String> options, ValueChanged<String?> onChanged) {
    return DropdownButtonFormField<String>(
      key: Key('sms-field-$key'),
      initialValue: value ?? '',
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: [
        for (final MapEntry(:key, :value) in options.entries)
          DropdownMenuItem(
              value: key,
              child: Text(value, overflow: TextOverflow.ellipsis, maxLines: 2)),
      ],
      onChanged: (v) => onChanged(v == null || v.isEmpty ? null : v),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final s = FinanceStrings.of(context);
    final p = context.palette;
    final m = widget.message;
    String accountName(FinanceAccount a) =>
        a.last4 == null ? a.name : '${a.name} (${a.last4})';
    final live = [
      for (final a in widget.accounts)
        if (!a.archived) a
    ];
    const decimal =
        TextInputType.numberWithOptions(decimal: true, signed: true);
    return Scaffold(
      appBar: AppBar(title: Text(t.t('sms.check'))),
      body: AnumPage(children: [
        AnumHeader(
            title: m.sender.isEmpty ? '-' : ltrValue(context, m.sender),
            subtitle: [
              _when(context, m.receivedAt),
              t.t('sms.status.${m.status}'),
              if (m.reason != null) t.label('sms.reason', m.reason!),
            ].join(' · ')),
        AnumSurface(
          child: Text(m.bodyMasked.isEmpty ? t.t('sms.bodyGone') : m.bodyMasked,
              key: const Key('sms-body')),
        ),
        Text(t.t('sms.fieldsHelp'), style: TextStyle(color: p.muted)),
        _choice(
            'kind',
            t.t('sms.field.kind'),
            _kind,
            {
              '': t.t('sms.pick'),
              for (final k in smsKinds) k: t.label('sms.kind', k),
            },
            (v) => setState(() => _kind = v)),
        _field('amount', t.t('sms.field.amount'), keyboard: decimal),
        _field('currency', t.t('sms.field.currency'),
            direction: TextDirection.ltr),
        _field('converted_amount', t.t('sms.field.converted'),
            keyboard: decimal),
        _choice(
            'account',
            t.t('sms.field.account'),
            _account,
            {
              '': t.t('sms.asRead'),
              for (final a in live) a.id: accountName(a),
            },
            (v) => setState(() => _account = v)),
        _choice(
            'counter',
            t.t('sms.field.counter'),
            _counter,
            {
              '': t.t('sms.noneChoice'),
              for (final a in live) a.id: accountName(a),
            },
            (v) => setState(() => _counter = v)),
        _field('merchant', t.t('sms.field.merchant')),
        _choice(
            'category',
            t.t('sms.field.category'),
            _category,
            {
              '': t.t('sms.automatic'),
              for (final c in fileableCategories) c: s.t('category.$c'),
            },
            (v) => setState(() => _category = v)),
        RecordsDateField(
          key: const Key('sms-field-occurred_on'),
          controller: _text['occurred_on']!,
          label: t.t('sms.field.date'),
        ),
        _field('reference', t.t('sms.field.reference'),
            direction: TextDirection.ltr),
        _field('interest', t.t('sms.field.interest'), keyboard: decimal),
        _field('fee', t.t('sms.field.fee'), keyboard: decimal),
        _field('last4', t.t('sms.field.last4'),
            keyboard: TextInputType.number, direction: TextDirection.ltr),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('sms-confirm-format'),
              contentPadding: EdgeInsets.zero,
              value: _confirmFormat,
              onChanged: (v) => setState(() => _confirmFormat = v),
              title: Text(t.t('sms.confirmFormat')),
            )),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('sms-teach'),
              contentPadding: EdgeInsets.zero,
              value: _teach,
              onChanged: m.bodyMasked.isEmpty
                  ? null
                  : (v) => setState(() => _teach = v),
              title: Text(t.t('sms.teach')),
            )),
        if (m.status == 'duplicate')
          Material(
              type: MaterialType.transparency,
              child: SwitchListTile(
                key: const Key('sms-allow-duplicate'),
                contentPadding: EdgeInsets.zero,
                value: _allowDuplicate,
                onChanged: (v) => setState(() => _allowDuplicate = v),
                title: Text(t.t('sms.allowDuplicate')),
              )),
        if (_error != null)
          Text(_error!,
              key: const Key('sms-error'), style: TextStyle(color: p.stop)),
        if (_note != null)
          Text(_note!,
              key: const Key('sms-note'), style: TextStyle(color: p.muted)),
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
          FilledButton.icon(
            key: const Key('sms-record'),
            onPressed: _busy ? null : () => _act('post'),
            icon: const Icon(Icons.check),
            label: Text(t.t(m.posted ? 'sms.recordCorrection' : 'sms.record')),
          ),
          if (widget.aiOn && m.bodyMasked.isNotEmpty)
            OutlinedButton.icon(
              key: const Key('sms-suggest'),
              onPressed: _busy ? null : () => _act('suggest'),
              icon: const Icon(Icons.auto_awesome_outlined),
              label: Text(t.t('sms.suggest')),
            ),
          if (m.posted)
            OutlinedButton.icon(
              key: const Key('sms-undo'),
              onPressed: _busy ? null : () => _act('reverse'),
              icon: const Icon(Icons.undo),
              label: Text(t.t('sms.undo')),
            )
          else
            OutlinedButton.icon(
              key: const Key('sms-dismiss'),
              onPressed: _busy ? null : () => _act('dismiss'),
              icon: const Icon(Icons.block),
              label: Text(t.t('sms.dismiss')),
            ),
        ]),
      ]),
    );
  }
}

// -- the ledger ---------------------------------------------------------------

/// "2026-10" moved by [delta] months.
String shiftMonth(String month, int delta) {
  final year = int.parse(month.substring(0, 4));
  final mon = int.parse(month.substring(5, 7));
  final moved = DateTime(year, mon + delta, 1);
  return '${moved.year.toString().padLeft(4, '0')}-${moved.month.toString().padLeft(2, '0')}';
}

/// Finance, Ledger on the phone (docs/finance-sms.md#ledger): the month's
/// figures per currency, the accounts, cards and loans with what a message
/// last said, spending by category and merchant, twelve months, the bills,
/// debts and goals, and the journal with its balanced postings. Read only.
class LedgerScreen extends StatefulWidget {
  const LedgerScreen({required this.repository, this.now, super.key});
  final FinanceMoreRepository repository;

  /// Today, for the first month shown; replaced in tests.
  final DateTime? now;

  @override
  State<LedgerScreen> createState() => _LedgerScreenState();
}

class _LedgerScreenState extends State<LedgerScreen> {
  late String _month;
  String _account = '', _currency = '', _category = '', _kind = '';
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  LedgerBoard? _board;
  List<FinanceAccount> _accounts = const [];
  late final RecordsList<LedgerEntry> _journal = RecordsList(
      (_, __, token) => widget.repository.ledgerEntries(token: token));
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    final now = widget.now ?? DateTime.now();
    _month =
        '${now.year.toString().padLeft(4, '0')}-${now.month.toString().padLeft(2, '0')}';
    _load();
    _journal.load();
    widget.repository.financeAccounts().then((a) {
      if (mounted) setState(() => _accounts = a);
    }, onError: (_) {});
  }

  @override
  void dispose() {
    _journal.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    setState(() {
      if (_board == null) _phase = RecordsPhase.loading;
    });
    try {
      final board = await widget.repository.ledger(
          month: _month,
          account: _account,
          currency: _currency,
          category: _category,
          kind: _kind);
      if (!mounted || generation != _generation) return;
      setState(() {
        _board = board;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _board = null;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _reload() => Future.wait([_load(), _journal.load()]);

  void _filter(void Function() change) {
    setState(change);
    _load();
  }

  Future<void> _openEntry(LedgerEntry e) async {
    final id = e.canonicalId;
    if (id == null) return;
    final t = FinanceToolsStrings.of(context);
    LedgerChain chain;
    try {
      chain = await widget.repository.ledgerChain(id);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
      return;
    }
    if (!mounted) return;
    await showRecordsSheet<void>(
      context,
      (sheet) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(t.t('ledger.chain'),
              style: Theme.of(sheet).textTheme.titleLarge),
          Text(
              chain.current == null
                  ? t.t('ledger.chainUndone')
                  : t.t('ledger.chainCurrent'),
              style: TextStyle(color: sheet.palette.muted)),
          for (final entry in chain.chain) _EntryTile(entry, names: _names),
        ],
      ),
    );
  }

  Map<String, String> get _names => {for (final a in _accounts) a.id: a.name};

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final s = FinanceStrings.of(context);
    final b = _board;
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(t.t('ledger.title'))),
      body: ListenableBuilder(
        listenable: _journal,
        builder: (context, _) => AnumPage(onRefresh: _reload, children: [
          Text(t.t('ledger.intro'), style: muted),
          Row(children: [
            IconButton(
              key: const Key('ledger-prev'),
              tooltip: t.t('ledger.prev'),
              onPressed: () => _filter(() => _month = shiftMonth(_month, -1)),
              icon: const Icon(Icons.chevron_left),
            ),
            Expanded(
              child: Text(ltrValue(context, _month),
                  key: const Key('ledger-month'),
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(
              key: const Key('ledger-next'),
              tooltip: t.t('ledger.next'),
              onPressed: () => _filter(() => _month = shiftMonth(_month, 1)),
              icon: const Icon(Icons.chevron_right),
            ),
          ]),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            _picker(
                'ledger-account',
                t.t('ledger.account'),
                _account,
                {
                  '': t.t('ledger.all'),
                  for (final a in _accounts) a.id: a.name,
                },
                (v) => _filter(() => _account = v)),
            _picker(
                'ledger-currency',
                t.t('ledger.currency'),
                _currency,
                {
                  '': t.t('ledger.all'),
                  for (final c in {
                    ...?b?.currencies,
                    if (_currency.isNotEmpty) _currency
                  })
                    c: c,
                },
                (v) => _filter(() => _currency = v)),
            _picker(
                'ledger-category',
                t.t('ledger.category'),
                _category,
                {
                  '': t.t('ledger.all'),
                  for (final c in fileableCategories) c: s.t('category.$c'),
                },
                (v) => _filter(() => _category = v)),
            _picker(
                'ledger-kind',
                t.t('ledger.kind'),
                _kind,
                {
                  '': t.t('ledger.all'),
                  for (final k in smsKinds) k: t.label('sms.kind', k),
                  'statement': t.t('ledger.kind.statement'),
                },
                (v) => _filter(() => _kind = v)),
          ]),
          if (_phase != RecordsPhase.ready || b == null)
            RecordsStateView(
                phase: _phase,
                failure: _failure,
                deniedHint: t.t('denied'),
                onRetry: _load)
          else
            ..._boardBody(context, b),
          AnumHeader(
              title: t.t('ledger.journal'), subtitle: t.t('ledger.journalSub')),
          ...recordsPagedBody<LedgerEntry>(
            _journal,
            deniedHint: t.t('denied'),
            empty: Text(t.t('ledger.none'), style: muted),
            row: (e) => InkWell(
              key: Key('ledger-entry-${e.id}'),
              onTap: e.canonicalId == null ? null : () => _openEntry(e),
              child: _EntryTile(e, names: _names),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _picker(String key, String label, String value,
      Map<String, String> options, ValueChanged<String> onChanged) {
    return ConstrainedBox(
      constraints: const BoxConstraints(minWidth: 140, maxWidth: 240),
      child: DropdownButtonFormField<String>(
        key: Key(key),
        initialValue: options.containsKey(value) ? value : '',
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final MapEntry(:key, :value) in options.entries)
            DropdownMenuItem(
                value: key,
                child: Text(value, overflow: TextOverflow.ellipsis)),
        ],
        onChanged: (v) {
          if (v != null && v != value) onChanged(v);
        },
      ),
    );
  }

  List<Widget> _boardBody(BuildContext context, LedgerBoard b) {
    final t = FinanceToolsStrings.of(context);
    final s = FinanceStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    Widget accounts(String title, List<LedgerAccountLine> rows,
            {bool owed = false}) =>
        RecordsSection(title: title, children: [
          if (rows.isEmpty)
            Text(t.t('ledger.none'), style: muted)
          else
            for (final a in rows)
              AnumRow(
                key: Key('ledger-account-${a.id}'),
                icon: owed
                    ? Icons.credit_card_outlined
                    : Icons.account_balance_outlined,
                title: a.name,
                subtitle: [
                  if (a.utilization != null)
                    t.t('ledger.utilization', {'n': a.utilization!}),
                  if (a.observed != null)
                    t.t('ledger.observed',
                        {'amount': formatAmount(a.observed!, a.currency)}),
                  if (a.difference != null &&
                      (double.tryParse(a.difference!) ?? 0) != 0)
                    t.t('ledger.difference',
                        {'amount': formatAmount(a.difference!, a.currency)}),
                ].join(' · '),
                detail: formatAmount(
                    owed && a.owed != null ? a.owed! : a.balance, a.currency),
              ),
        ]);
    Widget lines(String title, List<LedgerLine> rows,
            String Function(String) name) =>
        RecordsSection(title: title, children: [
          if (rows.isEmpty)
            Text(t.t('ledger.none'), style: muted)
          else
            for (final l in rows)
              RecordsField(
                  '${name(l.name)} (${t.t('ledger.count', {'n': l.count})})',
                  formatAmount(l.amount, l.currency)),
        ]);
    return [
      AnumPill(label: t.label('ledger.kindOf', b.ledgerKind)),
      RecordsSection(title: t.t('ledger.figures'), children: [
        for (final key in LedgerBoard.figureKeys)
          RecordsField(t.t('ledger.fig.$key'), formatAmounts(b.figures[key]!)),
      ]),
      RecordsSection(title: t.t('ledger.sync'), children: [
        RecordsField(
            t.t('sms.forwarding'), t.t(b.syncOn ? 'sms.on' : 'sms.off')),
        RecordsField(t.t('sms.devices'), '${b.devicesActive}'),
        RecordsField(t.t('sms.lastReceived'), _when(context, b.lastReceivedAt)),
        RecordsField(t.t('sms.lastError'),
            b.lastError == null ? '-' : t.label('sms.reason', b.lastError!)),
        RecordsField(t.t('sms.waiting'), '${b.reviewCount}'),
      ]),
      if (b.inTransit.isNotEmpty)
        Text(t.t('ledger.inTransit', {'amount': formatAmounts(b.inTransit)}),
            style: TextStyle(color: p.warn)),
      if (b.uncategorized > 0)
        Text(t.t('ledger.uncategorized', {'n': b.uncategorized}),
            style: TextStyle(color: p.warn)),
      if (b.entryCount == 0)
        AnumEmpty(
            icon: Icons.menu_book_outlined,
            title: t.t('ledger.empty'),
            message: t.t('ledger.emptyText')),
      accounts(t.t('ledger.balances'), b.accounts),
      accounts(t.t('ledger.cards'), b.cards, owed: true),
      accounts(t.t('ledger.loans'), b.loans, owed: true),
      lines(t.t('ledger.byCategory'), b.categories,
          (c) => s.maybe('category.$c') ?? c),
      lines(t.t('ledger.byMerchant'), b.merchants, (m) => m),
      RecordsSection(title: t.t('ledger.trend'), children: [
        if (b.trend.isEmpty)
          Text(t.t('ledger.none'), style: muted)
        else
          for (final row in b.trend)
            RecordsField(
                row.month.length == 4
                    ? t.t('ledger.year', {'year': row.month})
                    : ltrValue(context, row.month),
                t.t('ledger.trendLine', {
                  'income': formatAmount(row.income, row.currency),
                  'expenses': formatAmount(row.expenses, row.currency),
                  'net': formatAmount(row.net, row.currency),
                })),
      ]),
      RecordsSection(title: t.t('ledger.bills'), children: [
        if (b.bills.isEmpty)
          Text(t.t('ledger.none'), style: muted)
        else
          for (final bill in b.bills)
            RecordsField(
                [
                  bill.name,
                  if (bill.note != null) ltrValue(context, bill.note!)
                ].join(' · '),
                formatAmount(bill.amount, bill.currency)),
      ]),
      RecordsSection(title: t.t('ledger.debts'), children: [
        if (b.debts.isEmpty)
          Text(t.t('ledger.none'), style: muted)
        else
          for (final d in b.debts)
            RecordsField(
                '${d.name} · ${d.months == null ? t.t('ledger.debtNever') : t.t('ledger.debtMonths', {
                        'n': d.months!
                      })}',
                formatAmount(d.amount, d.currency)),
      ]),
      RecordsSection(title: t.t('ledger.goals'), children: [
        if (b.goals.isEmpty)
          Text(t.t('ledger.none'), style: muted)
        else
          for (final g in b.goals)
            RecordsField(
                '${g.name} · ${t.t('ledger.goalPercent', {
                      'n': g.percent ?? 0
                    })}',
                formatAmount(g.amount, g.currency)),
      ]),
    ];
  }
}

/// One journal entry with its postings (debits and credits).
class _EntryTile extends StatelessWidget {
  const _EntryTile(this.e, {required this.names});
  final LedgerEntry e;
  final Map<String, String> names;

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    // The web's `ledgerAccountLabel`: an own account by its name, the
    // transit and opening accounts in words, else "Expense: groceries".
    String account(LedgerPosting p) {
      final parts = p.ledgerAccount.split(':');
      final type = parts.first;
      final rest = parts.skip(1).join(':');
      if (type == 'asset' || type == 'liability') {
        return names[rest] ??
            (p.accountId == null ? null : names[p.accountId!]) ??
            t.t('ledger.acct.unknown');
      }
      if (type == 'suspense') return t.t('ledger.acct.suspense');
      if (type == 'equity') return t.t('ledger.acct.equity');
      final label = t.maybe('ledger.acct.$type');
      return label == null
          ? ltrValue(context, p.ledgerAccount)
          : '$label: ${rest.replaceAll('_', ' ')}';
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(e.description.isEmpty ? '-' : e.description,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        Text(
            [
              ltrValue(context, e.postedOn),
              t.label('sms.kind', e.kind),
              t.label('ledger.source', e.source),
              if (e.status == 'reversed') t.t('ledger.reversed'),
              if (e.reconciledAt != null) t.t('ledger.reconciled'),
            ].join(' · '),
            style: muted),
        if (e.sources.isNotEmpty)
          Text(
              t.t('ledger.sources', {
                'sources': e.sources
                    .map((k) => t.label('ledger.sourceKind', k))
                    .join(', ')
              }),
              style: muted),
        if (e.originalAmount != null && e.originalCurrency != null)
          Text(
              ltrValue(context,
                  '${e.originalCurrency} ${e.originalAmount} @ ${e.fxRate ?? '-'}'),
              style: muted),
        for (final p in e.postings)
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: Text(account(p))),
            const SizedBox(width: AnumSpacing.sm),
            Flexible(
              child: Text(
                  '${t.t(p.credit ? 'ledger.credit' : 'ledger.debit')} ${formatAmount(p.unsigned, p.currency)}',
                  textAlign: TextAlign.end),
            ),
          ]),
      ]),
    );
  }
}
