import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'books_controller.dart';
import 'books_data.dart';
import 'books_more_data.dart';
import 'books_more_strings.dart';
import 'books_review.dart';
import 'books_strings.dart';

/// The rest of the business books on the phone, reached from the Books
/// menu (docs/business-books.md#mobile): every adjustment with its state
/// and Void, the manual journal entries and their supporting documents,
/// the VAT filing settings (docs/business-tax.md#filing-deadlines) and the
/// receipts dismissed for bills with Restore. The API decides who may do
/// what; a refusal is shown in its own words.

String _money(BuildContext context, String amount, String currency) =>
    currency.isEmpty
        ? amount
        : formatMoneyText(amount, currency,
            locale: Localizations.localeOf(context));

enum BooksMorePage { adjustments, journal, tax, dismissed }

/// The Books app bar menu: the pages the person may open.
class BooksMoreMenu extends StatelessWidget {
  const BooksMoreMenu({required this.controller, super.key});
  final BooksController controller;

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    final account = controller.setup?.permissions.account ?? false;
    return PopupMenuButton<BooksMorePage>(
      key: const Key('books-more'),
      tooltip: m.t('more'),
      icon: const Icon(Icons.more_vert),
      onSelected: (page) => openBooksMore(context, controller, page),
      itemBuilder: (_) => [
        for (final page in BooksMorePage.values)
          if (page != BooksMorePage.dismissed || account)
            PopupMenuItem(
              key: Key('books-more-${page.name}'),
              value: page,
              child: Text(m.t('menu.${page.name}')),
            ),
      ],
    );
  }
}

Future<void> openBooksMore(
    BuildContext context, BooksController controller, BooksMorePage page) {
  final more = controller.more;
  if (more == null) return Future.value();
  return Navigator.of(context).push(MaterialPageRoute<void>(
    builder: (_) => switch (page) {
      BooksMorePage.adjustments =>
        AdjustmentsScreen(controller: controller, more: more),
      BooksMorePage.journal =>
        ManualEntriesScreen(controller: controller, more: more),
      BooksMorePage.tax =>
        TaxSettingsScreen(controller: controller, more: more),
      BooksMorePage.dismissed => DismissedReceiptsScreen(more: more),
    },
  ));
}

/// A paged list's body: its state, empty view, rows and "Load more".
List<Widget> _listBody<T>(
  BuildContext context,
  RecordsList<T> list, {
  required String deniedHint,
  required Widget empty,
  required Widget Function(T item) row,
}) {
  if (list.phase != RecordsPhase.ready) {
    return [
      RecordsStateView(
          phase: list.phase,
          failure: list.failure,
          deniedHint: deniedHint,
          onRetry: list.load),
    ];
  }
  if (list.items.isEmpty) return [empty];
  return [
    AnumSurface(
      padding: const EdgeInsets.symmetric(
          horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
      child: Column(children: [for (final item in list.items) row(item)]),
    ),
    RecordsMore(list: list),
  ];
}

// -- adjustments -----------------------------------------------------------------

AnumTone _adjustmentTone(BooksAdjustment a) => switch (a.status) {
      'void' => AnumTone.neutral,
      'pending' => AnumTone.warn,
      _ => AnumTone.ok,
    };

/// Every adjustment, filtered by state and kind, paged. Owners and
/// accountants open one to void it (or reject a pending one) and attach
/// its supporting documents.
class AdjustmentsScreen extends StatefulWidget {
  const AdjustmentsScreen(
      {required this.controller, required this.more, super.key});
  final BooksController controller;
  final BooksMoreRepository more;

  @override
  State<AdjustmentsScreen> createState() => _AdjustmentsScreenState();
}

class _AdjustmentsScreenState extends State<AdjustmentsScreen> {
  String _status = '', _kind = '';
  late final RecordsList<BooksAdjustment> _list = RecordsList<BooksAdjustment>(
      (_, __, token) =>
          widget.more.adjustments(kind: _kind, status: _status, token: token));

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

  void _filter({String? status, String? kind}) {
    _status = status ?? _status;
    _kind = kind ?? _kind;
    _list.load(filter: '$_status|$_kind');
  }

  Future<void> _new() async {
    await openAdjustmentForm(context, widget.controller);
    if (mounted) await _list.load();
  }

  Future<void> _open(BooksAdjustment a) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => AdjustmentScreen(
          controller: widget.controller,
          adjustment: a,
          onChanged: (changed) =>
              _list.upsert(changed, (existing) => existing.id == changed.id)),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    final b = BooksStrings.of(context);
    final k = KitStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('adj.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('adj.intro'),
              style: TextStyle(color: context.palette.muted)),
          if (_account)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                key: const Key('adjustments-new'),
                onPressed: _new,
                icon: const Icon(Icons.add),
                label: Text(m.t('adj.new')),
              ),
            ),
          RecordsFilterChips(
            options: {
              for (final s in adjustmentStatuses) s: m.t('adj.filter.$s')
            },
            selected: _status,
            onSelected: (s) => _filter(status: s),
          ),
          KeyedSubtree(
              key: ValueKey('kind-$_kind'),
              child: DropdownButtonFormField<String>(
                key: const Key('adjustments-kind'),
                initialValue: _kind,
                isExpanded: true,
                decoration: InputDecoration(labelText: m.t('adj.kindFilter')),
                items: [
                  DropdownMenuItem(value: '', child: Text(m.t('adj.allKinds'))),
                  for (final kind in adjustmentKinds)
                    DropdownMenuItem(
                        value: kind,
                        child: Text(b.t('adj.kind.$kind'),
                            overflow: TextOverflow.ellipsis)),
                ],
                onChanged: (kind) => _filter(kind: kind ?? ''),
              )),
          ..._listBody<BooksAdjustment>(
            context,
            _list,
            deniedHint: m.t('adj.denied'),
            empty: _status.isNotEmpty || _kind.isNotEmpty
                ? AnumEmpty(
                    key: const Key('adjustments-no-match'),
                    title: k.t('noMatch'),
                    message: k.t('noMatchText'),
                    icon: Icons.search_off,
                    action: k.t('clearFilters'),
                    onAction: () => _filter(status: '', kind: ''))
                : AnumEmpty(
                    key: const Key('adjustments-empty'),
                    title: m.t('adj.empty'),
                    message: _account
                        ? m.t('adj.emptyText')
                        : m.t('adj.emptyMember'),
                    icon: Icons.edit_note,
                    action: _account ? m.t('adj.new') : m.t('refresh'),
                    onAction: _account ? _new : _list.load),
            row: (a) => AnumRow(
              key: Key('adjustment-${a.id}'),
              icon: a.voided
                  ? Icons.block
                  : a.pending
                      ? Icons.hourglass_top
                      : Icons.check_circle_outline,
              tone: _adjustmentTone(a),
              title: b.maybe('adj.kind.${a.kind}') ?? a.kind,
              subtitle: [
                m.t('adj.state.${a.status}'),
                recordsDate(context, a.entryDate),
                if (a.description.isNotEmpty) a.description,
              ].join(' · '),
              detail: _money(context, a.amount, a.currency),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(a),
            ),
          ),
        ]),
      ),
    );
  }
}

/// One adjustment: its figures and state, Void (or Reject while it waits),
/// Approve for another owner or accountant than its maker, and its
/// supporting documents.
class AdjustmentScreen extends StatefulWidget {
  const AdjustmentScreen(
      {required this.controller,
      required this.adjustment,
      this.onChanged,
      super.key});
  final BooksController controller;
  final BooksAdjustment adjustment;
  final void Function(BooksAdjustment changed)? onChanged;

  @override
  State<AdjustmentScreen> createState() => _AdjustmentScreenState();
}

class _AdjustmentScreenState extends State<AdjustmentScreen> {
  late BooksAdjustment _a = widget.adjustment;
  bool _busy = false;

  BooksController get c => widget.controller;

  BooksAdjustment _with(String status, {String reason = ''}) => BooksAdjustment(
        id: _a.id,
        kind: _a.kind,
        status: status,
        entryDate: _a.entryDate,
        currency: _a.currency,
        amount: _a.amount,
        description: _a.description,
        startMonth: _a.startMonth,
        months: _a.months,
        reverseOn: _a.reverseOn,
        voidReason: reason,
        createdBy: _a.createdBy,
      );

  void _changed(BooksAdjustment next) {
    setState(() => _a = next);
    widget.onChanged?.call(next);
    final reloads = <Future<void>>[c.loadReport()];
    if (c.setup?.permissions.account ?? false) reloads.add(c.review.load());
    Future.wait(reloads);
  }

  Future<void> _void() async {
    final m = BooksMoreStrings.of(context);
    final pending = _a.pending;
    final done = await openRecordsForm(
      context,
      RecordsForm(
        key: const Key('adjustment-void-form'),
        title: m.t(pending ? 'adj.rejectTitle' : 'adj.voidTitle'),
        intro: m.t(pending ? 'adj.rejectIntro' : 'adj.voidIntro'),
        submitLabel: m.t(pending ? 'adj.reject' : 'adj.void'),
        fields: [
          RecordsInput.text('reason', m.t('adj.reason'),
              required: true, maxLength: 300),
        ],
        onSubmit: (v) async {
          final reason = (v['reason'] as String?) ?? '';
          if (reason.length < 3) throw FormatException(m.t('adj.reasonShort'));
          await c.repository.voidAdjustment(_a.id, reason);
          _changed(_with('void', reason: reason));
        },
      ),
    );
    if (done && mounted) {
      recordsToast(context, m.t(pending ? 'adj.rejected' : 'adj.voided'));
    }
  }

  Future<void> _approve() async {
    final m = BooksMoreStrings.of(context);
    setState(() => _busy = true);
    try {
      await c.repository.approveAdjustment(_a.id);
      _changed(_with('active'));
      if (mounted) recordsToast(context, m.t('adj.approved'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    final b = BooksStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final a = _a;
    final account = c.setup?.permissions.account ?? false;
    final own = a.createdBy != null && a.createdBy == c.setup?.userId;
    final kind = b.maybe('adj.kind.${a.kind}') ?? a.kind;
    return Scaffold(
      appBar: AppBar(title: Text(kind)),
      body: AnumPage(children: [
        RecordsSection(
          key: Key('adjustment-detail-${a.id}'),
          title: kind,
          children: [
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: AnumPill(
                  key: const Key('adjustment-state'),
                  label: m.t('adj.state.${a.status}'),
                  tone: _adjustmentTone(a)),
            ),
            const SizedBox(height: AnumSpacing.xs),
            RecordsField(
                m.t('adj.amount'), _money(context, a.amount, a.currency)),
            RecordsField(m.t('adj.date'), recordsDate(context, a.entryDate)),
            RecordsField(m.t('adj.description'), a.description),
            if (a.months != null && a.startMonth != null)
              Text(
                  m.t('adj.spread', {
                    'months': a.months!,
                    'start': recordsDate(context, a.startMonth),
                  }),
                  style: muted),
            if (a.reverseOn != null)
              Text(
                  m.t('adj.reverses',
                      {'day': recordsDate(context, a.reverseOn)}),
                  style: muted),
            if (a.voided)
              Text(m.t('adj.voidedLine', {'reason': a.voidReason}),
                  key: const Key('adjustment-void-reason'), style: muted),
            if (!a.voided) ...[
              const SizedBox(height: AnumSpacing.xs),
              if (!account)
                Text(m.t('adj.accountantsOnly'),
                    key: const Key('adjustment-accountants-only'), style: muted)
              else ...[
                if (a.pending && own)
                  Text(m.t('adj.own'),
                      key: const Key('adjustment-own'),
                      style: TextStyle(color: context.palette.warn)),
                Wrap(
                    spacing: AnumSpacing.sm,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      if (a.pending && !own)
                        FilledButton(
                          key: const Key('adjustment-approve'),
                          onPressed: _busy ? null : _approve,
                          child: Text(m.t('adj.approve')),
                        ),
                      OutlinedButton.icon(
                        key: const Key('adjustment-void'),
                        onPressed: _busy ? null : _void,
                        icon: const Icon(Icons.block),
                        label: Text(m.t(a.pending ? 'adj.reject' : 'adj.void')),
                      ),
                    ]),
              ],
            ],
          ],
        ),
        EvidenceSection(
            repository: c.repository,
            sourceType: 'adjustment',
            sourceId: a.id,
            missingText: m.t('adj.missingText')),
      ]),
    );
  }
}

// -- manual journal entries ----------------------------------------------------------

/// The manual journal entries, newest first, paged; each opens with its
/// lines and supporting documents.
class ManualEntriesScreen extends StatefulWidget {
  const ManualEntriesScreen(
      {required this.controller, required this.more, super.key});
  final BooksController controller;
  final BooksMoreRepository more;

  @override
  State<ManualEntriesScreen> createState() => _ManualEntriesScreenState();
}

class _ManualEntriesScreenState extends State<ManualEntriesScreen> {
  String _base = '';
  late final RecordsList<ManualEntry> _list =
      RecordsList<ManualEntry>((_, __, token) async {
    final chunk = await widget.more.manualEntries(token: token);
    if (chunk.baseCurrency.isNotEmpty) _base = chunk.baseCurrency;
    return chunk;
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

  void _open(ManualEntry entry) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => ManualEntryScreen(
              repository: widget.controller.repository,
              entry: entry,
              currency: _base)));

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('je.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('je.intro'), style: TextStyle(color: context.palette.muted)),
          ..._listBody<ManualEntry>(
            context,
            _list,
            deniedHint: m.t('je.denied'),
            empty: AnumEmpty(
                key: const Key('journal-empty'),
                title: m.t('je.empty'),
                message: m.t('je.emptyText'),
                icon: Icons.menu_book_outlined,
                action: m.t('refresh'),
                onAction: _list.load),
            row: (entry) => AnumRow(
              key: Key('journal-${entry.id}'),
              icon: entry.reversed ? Icons.undo : Icons.receipt_outlined,
              tone: entry.reversed ? AnumTone.neutral : AnumTone.ok,
              title: entry.description,
              subtitle: [
                recordsDate(context, entry.entryDate),
                m.t(entry.reversed ? 'je.reversed' : 'je.posted'),
                if (entry.evidenceCount > 0)
                  m.t('je.docs', {'n': entry.evidenceCount}),
              ].join(' · '),
              detail: _money(context, entry.total, _base),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(entry),
            ),
          ),
        ]),
      ),
    );
  }
}

/// One manual entry: its lines and its supporting documents.
class ManualEntryScreen extends StatelessWidget {
  const ManualEntryScreen(
      {required this.repository,
      required this.entry,
      required this.currency,
      super.key});
  final BooksRepository repository;
  final ManualEntry entry;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('je.title'))),
      body: AnumPage(children: [
        RecordsSection(
          key: Key('journal-detail-${entry.id}'),
          title: entry.description,
          children: [
            Text(
                [
                  recordsDate(context, entry.entryDate),
                  m.t(entry.reversed ? 'je.reversed' : 'je.posted'),
                ].join(' · '),
                style: muted),
            RecordsField(
                m.t('je.total'), _money(context, entry.total, currency)),
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
                header: true,
                child: Text(m.t('je.lines'),
                    style: Theme.of(context).textTheme.titleSmall)),
            for (final line in entry.lines)
              AnumRow(
                title: line.account.isEmpty ? '-' : line.account,
                subtitle: m.t(line.isDebit ? 'je.debit' : 'je.credit'),
                detail: _money(
                    context, line.isDebit ? line.debit : line.credit, currency),
              ),
          ],
        ),
        EvidenceSection(
            repository: repository, sourceType: 'journal', sourceId: entry.id),
      ]),
    );
  }
}

// -- tax settings ----------------------------------------------------------------------

/// The VAT filing settings: jurisdiction, filing period, the filing country
/// of another country, the owner's own due day, and the line that says
/// which deadline the Accountant review will use. The registration and
/// ZATCA fields are sent back as saved.
class TaxSettingsScreen extends StatefulWidget {
  const TaxSettingsScreen(
      {required this.controller, required this.more, super.key});
  final BooksController controller;
  final BooksMoreRepository more;

  @override
  State<TaxSettingsScreen> createState() => _TaxSettingsScreenState();
}

class _TaxSettingsScreenState extends State<TaxSettingsScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  TaxOverview? _overview;
  TaxFilingForm _form = const TaxFilingForm(
      jurisdiction: 'OTHER', frequency: 'quarterly', cycleMonth: 1);
  final _country = TextEditingController(), _due = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _country.dispose();
    _due.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final overview = await widget.more.tax();
      _overview = overview;
      _form = overview.form(booksCountry: widget.controller.setup?.country);
      _country.text = _form.country;
      _due.text = _form.dueDays;
      _failure = null;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _save() async {
    final m = BooksMoreStrings.of(context);
    final problem = _form.problem();
    if (problem != null) {
      setState(() => _error = m.t(problem));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.more.saveTax(_overview!.body(_form));
      if (!mounted) return;
      recordsToast(context, m.t('tax.saved'));
      await _load();
    } on Object catch (error) {
      if (mounted) setState(() => _error = RecordsFailure.from(error).message);
    }
    if (mounted) setState(() => _busy = false);
  }

  String _deadline(BooksMoreStrings m) {
    final o = _overview!;
    return switch (_form.source(presets: o.presets, noVat: o.noVat)) {
      DeadlineSource.owner =>
        m.ownerDeadline(int.tryParse(_form.dueDays.trim()) ?? 0),
      DeadlineSource.preset when _form.presetRule() != null =>
        m.t('deadline.preset', {'rule': m.t(_form.presetRule()!)}),
      DeadlineSource.noVat => m.t('deadline.noVat'),
      _ => m.t('deadline.none'),
    };
  }

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final overview = _overview;
    final List<Widget> children;
    if (_phase != RecordsPhase.ready || overview == null) {
      children = [
        RecordsStateView(
            phase: _phase,
            failure: _failure,
            deniedHint: m.t('tax.denied'),
            onRetry: _load),
      ];
    } else if (overview.settings == null && !overview.canEdit) {
      children = [
        AnumEmpty(
          key: const Key('tax-not-set'),
          icon: Icons.account_balance_outlined,
          title: m.t('tax.notSet'),
          message: m.t('tax.notSetMember'),
          action: m.t('refresh'),
          onAction: _load,
        ),
      ];
    } else {
      final edit = overview.canEdit;
      void set(TaxFilingForm next) => setState(() {
            _form = next;
            _error = null;
          });
      children = [
        if (overview.settings == null)
          AnumSurface(
            accent: context.palette.warn,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(m.t('tax.notSet'),
                    style: Theme.of(context).textTheme.titleSmall),
                Text(m.t('tax.notSetText'), key: const Key('tax-not-set-text')),
              ],
            ),
          ),
        RecordsSection(title: m.t('tax.title'), children: [
          DropdownButtonFormField<String>(
            key: const Key('tax-jurisdiction'),
            initialValue: _form.jurisdiction,
            isExpanded: true,
            decoration: InputDecoration(labelText: m.t('tax.jurisdiction')),
            items: [
              for (final j in overview.jurisdictions)
                DropdownMenuItem(
                    value: j, child: Text(m.maybe('tax.j.$j') ?? j)),
            ],
            onChanged:
                edit ? (j) => set(_form.copyWith(jurisdiction: j)) : null,
          ),
          const SizedBox(height: AnumSpacing.sm),
          DropdownButtonFormField<String>(
            key: const Key('tax-frequency'),
            initialValue: _form.frequency,
            isExpanded: true,
            decoration: InputDecoration(labelText: m.t('tax.frequency')),
            items: [
              for (final f in overview.frequencies)
                DropdownMenuItem(
                    value: f, child: Text(m.maybe('tax.f.$f') ?? f)),
            ],
            onChanged: edit ? (f) => set(_form.copyWith(frequency: f)) : null,
          ),
          if (_form.frequency == 'quarterly') ...[
            const SizedBox(height: AnumSpacing.sm),
            DropdownButtonFormField<int>(
              key: const Key('tax-cycle'),
              initialValue: _form.cycleMonth,
              isExpanded: true,
              decoration: InputDecoration(
                  labelText: m.t('tax.cycle'),
                  helperText: m.t('tax.cycleHelp'),
                  helperMaxLines: 3),
              items: [
                for (final n in const [1, 2, 3])
                  DropdownMenuItem(value: n, child: Text('$n')),
              ],
              onChanged:
                  edit ? (n) => set(_form.copyWith(cycleMonth: n ?? 1)) : null,
            ),
          ],
          if (_form.jurisdiction == 'OTHER') ...[
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('tax-country'),
              controller: _country,
              enabled: edit,
              maxLength: 2,
              textCapitalization: TextCapitalization.characters,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                  labelText: m.t('tax.country'),
                  helperText: m.t('tax.countryHelp'),
                  helperMaxLines: 4),
              onChanged: (v) => set(_form.copyWith(country: v)),
            ),
          ],
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('tax-due'),
            controller: _due,
            enabled: edit,
            maxLength: 3,
            keyboardType: TextInputType.number,
            textDirection: TextDirection.ltr,
            decoration: InputDecoration(
                labelText: m.t('tax.dueDays'),
                helperText: m.t('tax.dueDaysHelp'),
                helperMaxLines: 4),
            onChanged: (v) => set(_form.copyWith(dueDays: v)),
          ),
        ]),
        RecordsSection(
          key: const Key('tax-deadline'),
          title: m.t('tax.deadlineTitle'),
          children: [
            Semantics(
              liveRegion: true,
              child: Text(_deadline(m), key: const Key('tax-deadline-line')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Text(m.t('deadline.portal'), style: muted),
          ],
        ),
        Text(m.t('tax.kept'), style: muted),
        if (_error != null)
          Text(_error!,
              key: const Key('tax-error'),
              style: TextStyle(color: context.palette.stop)),
        if (edit)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('tax-save'),
              onPressed: _busy ? null : _save,
              icon: const Icon(Icons.save_outlined),
              label: Text(m.t('tax.save')),
            ),
          )
        else
          Text(m.t('tax.readOnly'),
              key: const Key('tax-read-only'),
              style: TextStyle(color: context.palette.warn)),
      ];
    }
    return Scaffold(
      appBar: AppBar(title: Text(m.t('tax.title'))),
      body: AnumPage(onRefresh: _load, children: [
        Text(m.t('tax.intro'), style: muted),
        ...children,
      ]),
    );
  }
}

// -- dismissed receipts ----------------------------------------------------------------

/// Receipts dismissed for bills, newest first, paged, each with Restore
/// (owners and accountants with the Document vault).
class DismissedReceiptsScreen extends StatefulWidget {
  const DismissedReceiptsScreen({required this.more, super.key});
  final BooksMoreRepository more;

  @override
  State<DismissedReceiptsScreen> createState() =>
      _DismissedReceiptsScreenState();
}

class _DismissedReceiptsScreenState extends State<DismissedReceiptsScreen> {
  late final RecordsList<ReceiptDismissal> _list =
      RecordsList<ReceiptDismissal>(
          (_, __, token) => widget.more.dismissedReceipts(token: token));
  String? _restoring;

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

  String _document(BooksMoreStrings m, ReceiptDismissal row) =>
      row.documentFound && row.documentTitle.isNotEmpty
          ? row.documentTitle
          : m.t('dis.documentGone');

  Future<void> _restore(ReceiptDismissal row) async {
    final m = BooksMoreStrings.of(context);
    setState(() => _restoring = row.id);
    try {
      await widget.more.restoreReceipt(row.id);
      _list.remove((existing) => existing.id == row.id);
      if (mounted) recordsToast(context, m.t('dis.restored'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
    if (mounted) setState(() => _restoring = null);
  }

  @override
  Widget build(BuildContext context) {
    final m = BooksMoreStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('dis.title'))),
      body: ListenableBuilder(
        listenable: _list,
        builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
          Text(m.t('dis.help'), style: TextStyle(color: context.palette.muted)),
          ..._listBody<ReceiptDismissal>(
            context,
            _list,
            deniedHint: m.t('dis.denied'),
            empty: AnumEmpty(
                key: const Key('dismissed-empty'),
                title: m.t('dis.empty'),
                message: m.t('dis.emptyText'),
                icon: Icons.receipt_long_outlined,
                action: m.t('refresh'),
                onAction: _list.load),
            row: (row) {
              final document = _document(m, row);
              return AnumRow(
                key: Key('dismissed-${row.id}'),
                icon: Icons.receipt_long_outlined,
                title: row.billFound
                    ? m.t('dis.row', {
                        'document': document,
                        'reference':
                            row.billReference.isEmpty ? '-' : row.billReference,
                        'vendor': row.vendor.isEmpty ? '-' : row.vendor,
                      })
                    : '$document, ${m.t('dis.billGone')}',
                subtitle: [
                  m.t('dis.on',
                      {'date': recordsDate(context, row.dismissedAt)}),
                  if (row.amount != null && row.currency != null)
                    _money(context, row.amount!, row.currency!),
                ].join(' · '),
                trailing: TextButton(
                  key: Key('restore-${row.id}'),
                  onPressed: _restoring == null ? () => _restore(row) : null,
                  child: Semantics(
                    label: m.t('dis.restoreLabel', {'document': document}),
                    excludeSemantics: true,
                    child: Text(m.t('dis.restore')),
                  ),
                ),
              );
            },
          ),
        ]),
      ),
    );
  }
}
