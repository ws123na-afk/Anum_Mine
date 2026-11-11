import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/infrastructure/picked_file_size.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_recon_actions.dart' show pickAccount;
import 'finance_strings.dart';
import 'finance_tools_data.dart';
import 'finance_tools_strings.dart';

/// Finance, statement import on the phone (docs/finance-statements.md,
/// docs/finance.md#statement-matching): a CSV export or the bank's PDF
/// statement, picked with the system file picker and held in memory only
/// (nothing is written to shared storage; the picker's own cache copy is
/// cleared once read). The preview the web shows comes first: the layout and
/// columns, the date format, the balance check, each row's match against
/// what is already recorded and the person's choice per row. Nothing is
/// saved until Import, and an import can be undone from the list.

/// The largest CSV and PDF the API reads.
const csvMaxBytes = 2000000, pdfMaxBytes = 10000000;

/// A picked statement: its name and bytes, or null when cancelled.
typedef StatementFilePicker = Future<({String name, Uint8List bytes})?>
    Function();

/// The system picker, limited to CSV and PDF. The file is read into memory
/// and the picker's temporary copy is removed.
Future<({String name, Uint8List bytes})?> pickStatementFile() async {
  final file = await FilePicker.pickFile(
      type: FileType.custom, allowedExtensions: const ['csv', 'pdf']);
  if (file == null) return null;
  try {
    final size = await pickedFileSize(file);
    if (size != null && size > pdfMaxBytes) {
      throw const FormatException('too_large');
    }
    return (name: file.name, bytes: await file.readAsBytes());
  } finally {
    try {
      await FilePicker.clearTemporaryFiles();
    } on Object {
      // Best effort: the copy is in the app's own cache, never shared.
    }
  }
}

bool _isPdf(String name, Uint8List bytes) =>
    name.toLowerCase().endsWith('.pdf') ||
    (bytes.length > 4 &&
        bytes[0] == 0x25 &&
        bytes[1] == 0x50 &&
        bytes[2] == 0x44 &&
        bytes[3] == 0x46);

/// The result of an import, in one dialog.
Future<void> showImportResult(
    BuildContext context, StatementImport done, String currency) async {
  final t = FinanceToolsStrings.of(context);
  await showDialog<void>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(t.t('imp.doneTitle')),
      content: SingleChildScrollView(
        child: Column(
          key: const Key('import-result'),
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(t.t('imp.done', {
              'imported': done.importedCount,
              'duplicates': done.duplicateCount,
              'errors': done.errorCount,
            })),
            if (done.reconciled > 0 || done.heldForReview > 0)
              Text(t.t('imp.doneMatched', {
                'matched': done.reconciled,
                'held': done.heldForReview,
              })),
            if (done.transfers > 0)
              Text(t.t('imp.doneTransfers', {'n': done.transfers})),
            if (done.billsPaid > 0)
              Text(t.t('imp.doneBills', {'n': done.billsPaid})),
            if (done.skipped > 0 || done.dropped > 0)
              Text(t.t('imp.doneLeft', {'n': done.skipped + done.dropped})),
            if (done.check != null) Text(t.t('imp.check.${done.check}')),
          ],
        ),
      ),
      actions: [
        FilledButton(
            onPressed: () => Navigator.pop(dialog), child: Text(t.t('close'))),
      ],
    ),
  );
}

/// The import page: Import a statement, and the earlier imports with Undo.
class StatementImportScreen extends StatefulWidget {
  const StatementImportScreen(
      {required this.repository, this.pickFile = pickStatementFile, super.key});
  final FinanceMoreRepository repository;
  final StatementFilePicker pickFile;

  @override
  State<StatementImportScreen> createState() => _StatementImportScreenState();
}

class _StatementImportScreenState extends State<StatementImportScreen> {
  late final RecordsList<StatementImport> _imports = RecordsList(
      (_, __, token) => widget.repository.statementImports(token: token));
  Map<String, FinanceAccount> _accounts = const {};
  String? _error;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _imports.dispose();
    super.dispose();
  }

  Future<void> _reload() async {
    await _imports.load();
    try {
      final accounts = await widget.repository.financeAccounts();
      if (mounted) {
        setState(() => _accounts = {for (final a in accounts) a.id: a});
      }
    } on Object {
      // The list still shows; names fall back to the file.
    }
  }

  Future<void> _start() async {
    final t = FinanceToolsStrings.of(context);
    setState(() => _error = null);
    final live = [
      for (final a in _accounts.values)
        if (!a.archived) a
    ];
    if (live.isEmpty) {
      setState(() => _error = t.t('imp.needAccount'));
      return;
    }
    final account = await pickAccount(context, live, t.t('imp.pickAccount'));
    if (account == null || !mounted) return;
    ({String name, Uint8List bytes})? file;
    try {
      file = await widget.pickFile();
    } on FormatException {
      setState(
          () => _error = t.t('imp.tooLarge', {'mb': pdfMaxBytes ~/ 1000000}));
      return;
    } on Object {
      setState(() => _error = t.t('imp.openFailed'));
      return;
    }
    if (file == null || !mounted) return;
    if (file.bytes.isEmpty) {
      setState(() => _error = t.t('imp.empty'));
      return;
    }
    final pdf = _isPdf(file.name, file.bytes);
    final name = file.name.toLowerCase();
    if (!pdf && !name.endsWith('.csv')) {
      setState(() => _error = t.t('imp.type'));
      return;
    }
    if (file.bytes.length > (pdf ? pdfMaxBytes : csvMaxBytes)) {
      setState(() => _error = t.t('imp.tooLarge',
          {'mb': (pdf ? pdfMaxBytes : csvMaxBytes) ~/ 1000000}));
      return;
    }
    String? content;
    if (!pdf) {
      try {
        content = utf8.decode(file.bytes);
      } on FormatException {
        setState(() => _error = t.t('imp.utf8'));
        return;
      }
    }
    final done = await Navigator.of(context).push(MaterialPageRoute<bool>(
      builder: (_) => pdf
          ? PdfImportScreen(
              repository: widget.repository,
              account: account,
              filename: file!.name,
              bytes: file.bytes)
          : CsvImportScreen(
              repository: widget.repository,
              account: account,
              filename: file!.name,
              content: content!),
    ));
    if (done == true && mounted) await _reload();
  }

  Future<void> _undo(StatementImport i) async {
    final t = FinanceToolsStrings.of(context);
    final ok = await confirmRecords(context,
        title: t.t('imp.undoTitle'),
        message:
            t.t('imp.undoText', {'file': i.filename, 'n': i.importedCount}),
        action: t.t('imp.undo'));
    if (!ok || !mounted) return;
    try {
      await widget.repository.undoImport(i.id);
      if (!mounted) return;
      recordsToast(context, t.t('imp.undone', {'file': i.filename}));
      await _reload();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(t.t('imp.title'))),
      body: ListenableBuilder(
        listenable: _imports,
        builder: (context, _) => AnumPage(onRefresh: _reload, children: [
          Text(
              t.t('imp.intro', {
                'mb': csvMaxBytes ~/ 1000000,
                'pdfMb': pdfMaxBytes ~/ 1000000
              }),
              style: muted),
          Text(t.t('imp.privacy'), style: muted),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('import-start'),
              onPressed: _start,
              icon: const Icon(Icons.upload_file),
              label: Text(t.t('imp.start')),
            ),
          ),
          if (_error != null)
            Text(_error!,
                key: const Key('import-error'),
                style: TextStyle(color: context.palette.stop)),
          AnumHeader(title: t.t('imp.history')),
          ...recordsPagedBody<StatementImport>(
            _imports,
            deniedHint: t.t('denied'),
            empty: AnumEmpty(
                icon: Icons.upload_file,
                title: t.t('imp.historyEmpty'),
                message: t.t('imp.historyEmptyText')),
            row: (i) => RecordsRowAction(
              row: AnumRow(
                key: Key('import-${i.id}'),
                icon: i.filename.toLowerCase().endsWith('.pdf')
                    ? Icons.picture_as_pdf_outlined
                    : Icons.table_chart_outlined,
                tone: i.check == 'differs' ? AnumTone.warn : null,
                title: i.filename,
                subtitle: [
                  _accounts[i.accountId]?.name ?? '',
                  if (i.createdAt.isNotEmpty)
                    recordsDateTime(context, i.createdAt),
                ].where((x) => x.isNotEmpty).join(' · '),
                detail: [
                  t.t('imp.counts', {
                    'imported': i.importedCount,
                    'duplicates': i.duplicateCount,
                    'errors': i.errorCount,
                  }),
                  if (i.reconciled > 0 || i.heldForReview > 0)
                    t.t('imp.doneMatched',
                        {'matched': i.reconciled, 'held': i.heldForReview}),
                  if (i.notInLedger > 0)
                    t.t('imp.notInLedger', {'n': i.notInLedger}),
                ].join(' · '),
              ),
              action: TextButton.icon(
                key: Key('import-undo-${i.id}'),
                onPressed: () => _undo(i),
                icon: const Icon(Icons.undo),
                label: Text(t.t('imp.undo')),
              ),
            ),
          ),
        ]),
      ),
    );
  }
}

/// The rows of a preview a page at a time, with a search: a statement can
/// hold thousands of rows.
class _RowPager extends StatelessWidget {
  const _RowPager(
      {required this.count,
      required this.shown,
      required this.onMore,
      required this.moreKey});
  final int count, shown;
  final VoidCallback onMore;
  final Key moreKey;

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    if (shown >= count) return const SizedBox.shrink();
    return Align(
      alignment: AlignmentDirectional.center,
      child: OutlinedButton.icon(
        key: moreKey,
        onPressed: onMore,
        icon: const Icon(Icons.expand_more),
        label: Text(t.t('imp.moreRows', {'shown': shown, 'total': count})),
      ),
    );
  }
}

/// The balance check in words.
Widget balanceCheckText(
    BuildContext context, BalanceCheck check, String currency, int rows) {
  final t = FinanceToolsStrings.of(context);
  final p = context.palette;
  String money(String? v) => v == null ? '-' : formatAmount(v, currency);
  final text = check.ties
      ? t.t('imp.check.matchedText', {
          'opening': money(check.opening),
          'n': rows,
          'total': money(check.rowsTotal),
          'closing': money(check.closing),
        })
      : check.differs
          ? t.t('imp.check.mismatchText', {
              'opening': money(check.opening),
              'total': money(check.rowsTotal),
              'expected': money(check.expected),
              'closing': money(check.closing),
              'difference': money(check.difference),
            })
          : t.t('imp.check.unknownText');
  return Column(
    key: const Key('import-check'),
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text(text,
          style: TextStyle(
              color: check.ties
                  ? p.ok
                  : check.differs
                      ? p.warn
                      : p.muted)),
      if (check.breaks.isNotEmpty)
        Text(t.t('imp.check.breaks', {'lines': check.breaks.join(', ')}),
            style: TextStyle(color: p.warn)),
    ],
  );
}

/// One row's match against what is already recorded, and its switches.
class _MatchControls extends StatelessWidget {
  const _MatchControls(
      {required this.match,
      required this.choice,
      required this.onChanged,
      required this.rowKey});
  final RowMatch? match;
  final RowChoice choice;
  final VoidCallback onChanged;
  final String rowKey;

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final m = match;
    if (m == null) return const SizedBox.shrink();
    final muted = TextStyle(color: context.palette.muted);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(
          [
            t.label('imp.match', m.status),
            if (m.reason.isNotEmpty) m.reason,
            if (m.recordText != null) m.recordText!,
          ].join(' · '),
          style: muted),
      if (m.billName != null)
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: Key('row-bill-$rowKey'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: choice.bill ?? true,
              onChanged: (v) {
                choice.bill = v ? null : false;
                onChanged();
              },
              title: Text(t.t('imp.bill', {'bill': m.billName!})),
            )),
      if (m.transferAccount != null)
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: Key('row-transfer-$rowKey'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: choice.transfer ?? m.transferStrong,
              onChanged: (v) {
                choice.transfer = v == m.transferStrong ? null : v;
                onChanged();
              },
              title: Text(t.t('imp.transfer', {'account': m.transferAccount!})),
            )),
      if (m.status == 'matched_existing' && m.amountDifference != null)
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: Key('row-correct-$rowKey'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: choice.correct ?? true,
              onChanged: (v) {
                choice.correct = v ? null : false;
                onChanged();
              },
              title: Text(t.t('imp.correct')),
            )),
    ]);
  }
}

// -- CSV ------------------------------------------------------------------------

class CsvImportScreen extends StatefulWidget {
  const CsvImportScreen(
      {required this.repository,
      required this.account,
      required this.filename,
      required this.content,
      super.key});
  final FinanceMoreRepository repository;
  final FinanceAccount account;
  final String filename, content;

  @override
  State<CsvImportScreen> createState() => _CsvImportScreenState();
}

class _CsvImportScreenState extends State<CsvImportScreen> {
  static const _page = 25;
  ImportFormats? _formats;
  CsvPreview? _preview;
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  String _preset = 'auto', _dateFormat = '';
  Map<String, String>? _mapping;
  final _opening = TextEditingController(), _closing = TextEditingController();
  final _search = TextEditingController();
  final _choices = <int, RowChoice>{};
  int _shown = _page;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.repository.importFormats().then((f) {
      if (mounted) setState(() => _formats = f);
    }, onError: (_) {});
    _read();
  }

  @override
  void dispose() {
    _opening.dispose();
    _closing.dispose();
    _search.dispose();
    super.dispose();
  }

  Map<String, Object?> _body() {
    String? amount(TextEditingController c) =>
        c.text.trim().isEmpty ? null : parseAmount(c.text.trim());
    return {
      'account_id': widget.account.id,
      'filename': widget.filename,
      'content': widget.content,
      'preset': _mapping != null ? 'custom' : _preset,
      if (_dateFormat.isNotEmpty) 'date_format': _dateFormat,
      if (_mapping != null) 'mapping': _mapping,
      'opening_balance': amount(_opening),
      'closing_balance': amount(_closing),
    };
  }

  Future<void> _read() async {
    for (final c in [_opening, _closing]) {
      if (c.text.trim().isNotEmpty && parseAmount(c.text.trim()) == null) {
        final t = FinanceToolsStrings.of(context);
        setState(() => _error = t.t('imp.badBalance'));
        return;
      }
    }
    setState(() {
      _phase = _preview == null ? RecordsPhase.loading : _phase;
      _busy = true;
      _error = null;
    });
    try {
      final preview = await widget.repository.previewCsv(_body());
      if (!mounted) return;
      setState(() {
        _preview = preview;
        _phase = RecordsPhase.ready;
        _dateFormat = preview.dateFormat;
        _choices.clear();
        _shown = _page;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        if (_preview == null) {
          _failure = failure;
          _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
        } else {
          _error = failure.message;
        }
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  int get _importing {
    final p = _preview;
    if (p == null) return 0;
    return p.rows.where((r) => rowImported(r.match, _choices[r.line])).length;
  }

  Future<void> _import() async {
    final t = FinanceToolsStrings.of(context);
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final done = await widget.repository.importCsv({
        ..._body(),
        'decisions': [
          for (final MapEntry(:key, :value) in _choices.entries)
            if (!value.isEmpty) {'line': key, ...value.wire()}
        ],
      });
      if (!mounted) return;
      await showImportResult(context, done, widget.account.currency);
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _error =
            t.t('imp.failed', {'message': RecordsFailure.from(error).message}));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _columns(CsvPreview p) async {
    final t = FinanceToolsStrings.of(context);
    final draft = Map<String, String>.from(p.mapping);
    final saved = await showRecordsSheet<bool>(
      context,
      (sheet) => StatefulBuilder(
        builder: (sheet, setSheet) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(t.t('imp.columns'),
                style: Theme.of(sheet).textTheme.titleLarge),
            Text(t.t('imp.columnsHelp'),
                style: TextStyle(color: sheet.palette.muted)),
            for (final field in csvMappingFields)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: DropdownButtonFormField<String>(
                  key: Key('csv-map-$field'),
                  initialValue: draft[field] ?? '',
                  isExpanded: true,
                  decoration: InputDecoration(labelText: t.t('imp.col.$field')),
                  items: [
                    DropdownMenuItem(
                        value: '', child: Text(t.t('imp.noColumn'))),
                    for (final h in p.headers)
                      DropdownMenuItem(
                          value: h,
                          child: Text(h, overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setSheet(() {
                    if (v == null || v.isEmpty) {
                      draft.remove(field);
                    } else {
                      draft[field] = v;
                    }
                  }),
                ),
              ),
            const SizedBox(height: AnumSpacing.md),
            FilledButton(
              key: const Key('csv-map-apply'),
              onPressed: () {
                final money = draft.containsKey('amount') ||
                    (draft.containsKey('debit') && draft.containsKey('credit'));
                if (!draft.containsKey('date') ||
                    !draft.containsKey('description') ||
                    !money) {
                  ScaffoldMessenger.maybeOf(sheet)?.showSnackBar(
                      SnackBar(content: Text(t.t('imp.columnsNeeded'))));
                  return;
                }
                Navigator.pop(sheet, true);
              },
              child: Text(t.t('imp.readAgain')),
            ),
          ],
        ),
      ),
    );
    if (saved == true && mounted) {
      setState(() => _mapping = draft);
      await _read();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final p = _preview;
    return Scaffold(
      appBar: AppBar(title: Text(t.t('imp.csvTitle'))),
      body: AnumPage(onRefresh: _read, children: [
        AnumHeader(title: widget.filename, subtitle: widget.account.name),
        if (_phase != RecordsPhase.ready || p == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: t.t('denied'),
              onRetry: _read)
        else
          ..._ready(context, p),
      ]),
    );
  }

  List<Widget> _ready(BuildContext context, CsvPreview p) {
    final t = FinanceToolsStrings.of(context);
    final s = FinanceStrings.of(context);
    final c = p.currency;
    final muted = TextStyle(color: context.palette.muted);
    final query = _search.text.trim().toLowerCase();
    final rows = query.isEmpty
        ? p.rows
        : p.rows
            .where((r) => r.description.toLowerCase().contains(query))
            .toList();
    final formats = _formats;
    return [
      Text(
          t.t('imp.previewText', {
            'rows': p.rowCount,
            'valid': p.validCount,
            'fresh': p.newCount,
            'duplicates': p.duplicateCount,
            'errors': p.errorCount,
          }),
          key: const Key('csv-summary')),
      Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
        if (formats != null)
          ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 160, maxWidth: 260),
            child: DropdownButtonFormField<String>(
              key: const Key('csv-layout'),
              initialValue: _mapping != null
                  ? 'custom'
                  : formats.presets.containsKey(_preset)
                      ? _preset
                      : 'auto',
              isExpanded: true,
              decoration: InputDecoration(labelText: t.t('imp.layout')),
              items: [
                DropdownMenuItem(value: 'auto', child: Text(t.t('imp.detect'))),
                if (_mapping != null)
                  DropdownMenuItem(
                      value: 'custom', child: Text(t.t('imp.custom'))),
                for (final MapEntry(:key, :value) in formats.presets.entries)
                  DropdownMenuItem(
                      value: key,
                      child: Text(value, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) {
                if (v == null || v == 'custom') return;
                setState(() {
                  _preset = v;
                  _mapping = null;
                });
                _read();
              },
            ),
          ),
        if (p.dateFormats.isNotEmpty)
          ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 140, maxWidth: 220),
            child: DropdownButtonFormField<String>(
              key: const Key('csv-date-format'),
              initialValue:
                  p.dateFormats.contains(_dateFormat) ? _dateFormat : null,
              isExpanded: true,
              decoration: InputDecoration(labelText: t.t('imp.dateFormat')),
              items: [
                for (final f in p.dateFormats)
                  DropdownMenuItem(value: f, child: Text(ltrValue(context, f))),
              ],
              onChanged: (v) {
                if (v == null || v == _dateFormat) return;
                setState(() => _dateFormat = v);
                _read();
              },
            ),
          ),
        OutlinedButton.icon(
          key: const Key('csv-columns'),
          onPressed: _busy ? null : () => _columns(p),
          icon: const Icon(Icons.view_column_outlined),
          label: Text(t.t('imp.columns')),
        ),
      ]),
      if (p.dateAmbiguous)
        Text(t.t('imp.ambiguous', {'format': ltrValue(context, p.dateFormat)}),
            style: TextStyle(color: context.palette.warn)),
      RecordsSection(title: t.t('imp.check'), children: [
        Text(t.t('imp.balancesHelp'), style: muted),
        TextField(
          key: const Key('csv-opening'),
          controller: _opening,
          keyboardType: const TextInputType.numberWithOptions(
              decimal: true, signed: true),
          decoration:
              InputDecoration(labelText: t.t('imp.opening', {'currency': c})),
        ),
        TextField(
          key: const Key('csv-closing'),
          controller: _closing,
          keyboardType: const TextInputType.numberWithOptions(
              decimal: true, signed: true),
          decoration:
              InputDecoration(labelText: t.t('imp.closing', {'currency': c})),
        ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton(
            key: const Key('csv-recheck'),
            onPressed: _busy ? null : _read,
            child: Text(t.t('imp.recheck')),
          ),
        ),
        balanceCheckText(context, p.check, c, p.validCount),
      ]),
      if (p.errors.isNotEmpty)
        RecordsSection(
            title: t.t('imp.errors', {'n': p.errorCount}),
            children: [
              for (final (line, message) in p.errors)
                Text(t.t('imp.errorLine', {'line': line, 'message': message})),
            ]),
      AnumHeader(
          title: t.t('imp.rows'),
          subtitle: t.t('imp.rowsSub', {'n': _importing})),
      if (p.rows.length > 10)
        TextField(
          key: const Key('csv-search'),
          controller: _search,
          decoration: InputDecoration(
              labelText: t.t('imp.search'),
              prefixIcon: const Icon(Icons.search)),
          onChanged: (_) => setState(() => _shown = _page),
        ),
      if (p.rows.isEmpty)
        Text(t.t('imp.noRows'), style: muted)
      else if (rows.isEmpty)
        Text(t.t('imp.noMatch'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final r in rows.take(_shown)) _csvRow(context, r, s, c),
          ]),
        ),
      _RowPager(
          count: rows.length,
          shown: _shown,
          moreKey: const Key('csv-more'),
          onMore: () => setState(() => _shown += _page)),
      if (_error != null)
        Text(_error!,
            key: const Key('csv-error'),
            style: TextStyle(color: context.palette.stop)),
      if (_importing == 0 && p.rows.isNotEmpty)
        Text(t.t('imp.nothingNew'), style: muted),
      FilledButton.icon(
        key: const Key('csv-import'),
        onPressed: _busy || _importing == 0 ? null : _import,
        icon: const Icon(Icons.download_done),
        label: Text(t.t('imp.confirm', {'n': _importing})),
      ),
    ];
  }

  Widget _csvRow(BuildContext context, CsvRow r, FinanceStrings s, String c) {
    final t = FinanceToolsStrings.of(context);
    final choice = _choices.putIfAbsent(r.line, RowChoice.new);
    final on = rowImported(r.match, choice);
    return Padding(
      key: Key('csv-row-${r.line}'),
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Material(
            type: MaterialType.transparency,
            child: CheckboxListTile(
              key: Key('csv-include-${r.line}'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: on,
              onChanged: (v) => setState(() {
                choice.import = v == r.match.defaultImport ? null : v;
              }),
              title: Text(r.description.isEmpty ? '-' : r.description),
              subtitle: Text([
                ltrValue(context, r.postedOn),
                formatAmount(r.amount, c),
                s.maybe('category.${r.category}') ?? r.category,
                t.t('imp.line', {'line': r.line}),
              ].join(' · ')),
            )),
        if (r.warning != null)
          Text(r.warning!, style: TextStyle(color: context.palette.warn)),
        _MatchControls(
            match: r.match,
            choice: choice,
            rowKey: '${r.line}',
            onChanged: () => setState(() {})),
      ]),
    );
  }
}

// -- PDF ------------------------------------------------------------------------

class PdfImportScreen extends StatefulWidget {
  const PdfImportScreen(
      {required this.repository,
      required this.account,
      required this.filename,
      required this.bytes,
      super.key});
  final FinanceMoreRepository repository;
  final FinanceAccount account;
  final String filename;
  final Uint8List bytes;

  @override
  State<PdfImportScreen> createState() => _PdfImportScreenState();
}

class _PdfImportScreenState extends State<PdfImportScreen> {
  static const _page = 25;
  PdfStatement? _view;
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  String? _password;
  List<String>? _roles;
  String _dateFormat = '';
  final _opening = TextEditingController(), _closing = TextEditingController();
  final _search = TextEditingController();
  bool _accept = false, _remember = false, _busy = false;
  int _shown = _page;
  String? _error;

  @override
  void initState() {
    super.initState();
    _read();
  }

  @override
  void dispose() {
    _opening.dispose();
    _closing.dispose();
    _search.dispose();
    super.dispose();
  }

  Future<void> _read({bool again = false}) async {
    setState(() {
      if (_view == null) _phase = RecordsPhase.loading;
      _busy = true;
      _error = null;
    });
    try {
      final view = await widget.repository.readPdf(
          accountId: widget.account.id,
          filename: widget.filename,
          bytes: widget.bytes,
          password: _password,
          roles: again ? _roles : null,
          dateFormat: again ? _dateFormat : null);
      if (!mounted) return;
      setState(() {
        _view = view;
        _phase = RecordsPhase.ready;
        _roles = [for (final c in view.columns) c.role];
        _dateFormat = view.dateFormat;
        _opening.text = view.opening ?? '';
        _closing.text = view.closing ?? '';
        _accept = false;
        _shown = _page;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.code == 'password_required' || error.code == 'password_wrong') {
        final password =
            await _askPassword(wrong: error.code == 'password_wrong');
        if (password != null && mounted) {
          _password = password;
          await _read(again: again);
          return;
        }
      }
      _fail(error);
    } on Object catch (error) {
      if (mounted) _fail(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _fail(Object error) {
    final failure = RecordsFailure.from(error);
    setState(() {
      if (_view == null) {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      } else {
        _error = failure.message;
      }
    });
  }

  Future<String?> _askPassword({required bool wrong}) async {
    final t = FinanceToolsStrings.of(context);
    final field = TextEditingController();
    String? error;
    final typed = await showDialog<String>(
      context: context,
      builder: (dialog) => StatefulBuilder(
        builder: (dialog, setDialog) => AlertDialog(
          title: Text(t.t('pdf.passwordTitle')),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text(t.t(wrong ? 'pdf.passwordWrong' : 'pdf.passwordText')),
              TextField(
                key: const Key('pdf-password'),
                controller: field,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                    labelText: t.t('pdf.password'), errorText: error),
              ),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(dialog),
                child: Text(t.t('cancel'))),
            FilledButton(
              key: const Key('pdf-password-open'),
              onPressed: () {
                if (field.text.isEmpty) {
                  setDialog(() => error = t.t('pdf.passwordEmpty'));
                  return;
                }
                Navigator.pop(dialog, field.text);
              },
              child: Text(t.t('pdf.passwordOpen')),
            ),
          ],
        ),
      ),
    );
    // The dialog's field is left to the collector: disposing it here would
    // race the dialog's closing animation.
    return typed;
  }

  BalanceCheck get _check =>
      liveCheck(_view?.rows ?? const [], _opening.text, _closing.text);

  int get _problems =>
      _view?.rows
          .where((r) => r.include && r.problemIn(_view!.currency) != null)
          .length ??
      0;

  int get _included => _view?.rows.where((r) => r.include).length ?? 0;

  bool get _rolesChanged {
    final view = _view, roles = _roles;
    if (view == null || roles == null) return false;
    for (var i = 0; i < view.columns.length && i < roles.length; i++) {
      if (view.columns[i].role != roles[i]) return true;
    }
    return false;
  }

  Future<void> _editRow(PdfRow row) async {
    final edited = await showRecordsSheet<(String, String, String)>(
        context, (sheet) => _PdfRowEditor(row));
    if (edited != null && mounted) {
      setState(() {
        row.postedOn = edited.$1;
        row.description = edited.$2;
        row.amount = parseAmount(edited.$3) ?? edited.$3;
      });
    }
  }

  Future<void> _import(PdfStatement view) async {
    final t = FinanceToolsStrings.of(context);
    if (_problems > 0) {
      setState(() => _error = t.t('pdf.problems', {'n': _problems}));
      return;
    }
    final check = _check;
    if (check.differs && !_accept) {
      setState(() => _error = t.t('pdf.acceptNeeded'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final done = await widget.repository.importPdf(view.importBody(
          accountId: widget.account.id,
          opening: _opening.text,
          closing: _closing.text,
          acceptMismatch: _accept,
          roles: _rolesChanged ? _roles : null,
          remember: _remember));
      if (!mounted) return;
      await showImportResult(context, done, view.currency);
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _error =
            t.t('imp.failed', {'message': RecordsFailure.from(error).message}));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reread() async {
    final t = FinanceToolsStrings.of(context);
    final roles = _roles;
    if (roles == null) return;
    final problem = checkRoles(roles);
    if (problem != null) {
      setState(() => _error = t.t(problem));
      return;
    }
    await _read(again: true);
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final view = _view;
    return Scaffold(
      appBar: AppBar(title: Text(t.t('pdf.title'))),
      body: AnumPage(children: [
        AnumHeader(title: widget.filename, subtitle: widget.account.name),
        Text(t.t('imp.privacy'),
            style: TextStyle(color: context.palette.muted)),
        if (_phase == RecordsPhase.loading && view == null)
          AnumFeedback(
              kind: AnumFeedbackKind.loading, message: t.t('pdf.reading'))
        else if (_phase != RecordsPhase.ready || view == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: t.t('denied'),
              onRetry: _read)
        else
          ..._ready(context, view),
      ]),
    );
  }

  List<Widget> _ready(BuildContext context, PdfStatement v) {
    final t = FinanceToolsStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final c = v.currency;
    final query = _search.text.trim().toLowerCase();
    final rows = query.isEmpty
        ? v.rows
        : v.rows
            .where((r) =>
                r.description.toLowerCase().contains(query) ||
                (r.reference ?? '').toLowerCase().contains(query))
            .toList();
    final roles = _roles ?? const <String>[];
    final check = _check;
    return [
      RecordsSection(title: t.t('pdf.summary'), children: [
        RecordsField(t.t('pdf.source'), t.t('pdf.source.${v.source}')),
        RecordsField(t.t('pdf.pages'), '${v.pageCount}'),
        if (v.periodStart != null && v.periodEnd != null)
          RecordsField(
              t.t('pdf.period'),
              t.t('pdf.periodValue', {
                'start': ltrValue(context, v.periodStart!),
                'end': ltrValue(context, v.periodEnd!)
              })),
        if (v.last4 != null)
          RecordsField(t.t('pdf.accountNumber'),
              t.t('pdf.ending', {'last4': ltrValue(context, v.last4!)})),
        RecordsField(t.t('pdf.rowsFound'), '${v.rows.length}'),
      ]),
      if (v.last4Matches == false)
        Text(
            t.t('pdf.last4Differs', {
              'statement': v.last4 ?? '-',
              'account': widget.account.name,
              'own': widget.account.last4 ?? '-',
            }),
            key: const Key('pdf-last4-warning'),
            style: TextStyle(color: p.warn)),
      if (v.rows.isEmpty)
        Text(t.t('pdf.noRows'), style: TextStyle(color: p.warn)),
      if (v.dateAmbiguous)
        Text(t.t('imp.ambiguous', {'format': ltrValue(context, v.dateFormat)}),
            style: TextStyle(color: p.warn)),
      for (final w in v.warnings) Text(w, style: TextStyle(color: p.warn)),
      if (v.skippedPages > 0)
        Text(t.t('pdf.skippedPages', {'n': v.skippedPages}), style: muted),
      if (v.columns.isNotEmpty)
        RecordsSection(title: t.t('pdf.columns'), children: [
          Text(t.t('pdf.columnsHelp'), style: muted),
          for (var i = 0; i < v.columns.length && i < roles.length; i++)
            DropdownButtonFormField<String>(
              key: Key('pdf-role-$i'),
              initialValue: pdfRoles.contains(roles[i]) ? roles[i] : 'skip',
              isExpanded: true,
              decoration: InputDecoration(
                  labelText: t.t('pdf.column', {
                'label':
                    v.columns[i].label.isEmpty ? '${i + 1}' : v.columns[i].label
              })),
              items: [
                for (final r in pdfRoles)
                  DropdownMenuItem(value: r, child: Text(t.t('pdf.role.$r'))),
              ],
              onChanged: (r) {
                if (r == null) return;
                setState(() => _roles = [...roles]..[i] = r);
              },
            ),
          if (v.dateFormats.isNotEmpty)
            DropdownButtonFormField<String>(
              key: const Key('pdf-date-format'),
              initialValue:
                  v.dateFormats.contains(_dateFormat) ? _dateFormat : null,
              isExpanded: true,
              decoration: InputDecoration(labelText: t.t('imp.dateFormat')),
              items: [
                for (final f in v.dateFormats)
                  DropdownMenuItem(value: f, child: Text(ltrValue(context, f))),
              ],
              onChanged: (f) {
                if (f != null) setState(() => _dateFormat = f);
              },
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('pdf-reread'),
              onPressed: _busy ? null : _reread,
              icon: const Icon(Icons.refresh),
              label: Text(t.t('imp.readAgain')),
            ),
          ),
          if (v.fingerprint != null && _rolesChanged)
            Material(
                type: MaterialType.transparency,
                child: SwitchListTile(
                  key: const Key('pdf-remember'),
                  contentPadding: EdgeInsets.zero,
                  value: _remember,
                  onChanged: (on) => setState(() => _remember = on),
                  title: Text(t.t('pdf.remember')),
                )),
        ]),
      RecordsSection(title: t.t('imp.check'), children: [
        TextField(
          key: const Key('pdf-opening'),
          controller: _opening,
          keyboardType: const TextInputType.numberWithOptions(
              decimal: true, signed: true),
          decoration:
              InputDecoration(labelText: t.t('imp.opening', {'currency': c})),
          onChanged: (_) => setState(() {}),
        ),
        TextField(
          key: const Key('pdf-closing'),
          controller: _closing,
          keyboardType: const TextInputType.numberWithOptions(
              decimal: true, signed: true),
          decoration:
              InputDecoration(labelText: t.t('imp.closing', {'currency': c})),
          onChanged: (_) => setState(() {}),
        ),
        balanceCheckText(context, check, c, _included),
        if (check.differs)
          Material(
              type: MaterialType.transparency,
              child: SwitchListTile(
                key: const Key('pdf-accept'),
                contentPadding: EdgeInsets.zero,
                value: _accept,
                onChanged: (on) => setState(() => _accept = on),
                title: Text(t.t('pdf.accept')),
              )),
      ]),
      if (v.dropped.isNotEmpty)
        RecordsSection(
            title: t.t('pdf.dropped', {'n': v.dropped.length}),
            children: [
              Text(t.t('pdf.droppedHelp'), style: muted),
              for (final (page, reason, text) in v.dropped)
                Text('${t.t('pdf.page', {
                      'page': page
                    })} · ${t.label('pdf.dropped', reason)}: $text'),
            ]),
      AnumHeader(
          title: t.t('imp.rows'),
          subtitle: t.t('imp.rowsSub', {'n': _included})),
      if (v.rows.length > 10)
        TextField(
          key: const Key('pdf-search'),
          controller: _search,
          decoration: InputDecoration(
              labelText: t.t('imp.search'),
              prefixIcon: const Icon(Icons.search)),
          onChanged: (_) => setState(() => _shown = _page),
        ),
      if (v.rows.isNotEmpty && rows.isEmpty)
        Text(t.t('imp.noMatch'), style: muted)
      else if (rows.isNotEmpty)
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final r in rows.take(_shown)) _pdfRow(context, r, c),
          ]),
        ),
      _RowPager(
          count: rows.length,
          shown: _shown,
          moreKey: const Key('pdf-more'),
          onMore: () => setState(() => _shown += _page)),
      if (_problems > 0)
        Text(t.t('pdf.problems', {'n': _problems}),
            style: TextStyle(color: p.warn)),
      if (_error != null)
        Text(_error!,
            key: const Key('pdf-error'), style: TextStyle(color: p.stop)),
      FilledButton.icon(
        key: const Key('pdf-import'),
        onPressed: _busy || _included == 0 ? null : () => _import(v),
        icon: const Icon(Icons.download_done),
        label: Text(t.t('imp.confirm', {'n': _included})),
      ),
    ];
  }

  Widget _pdfRow(BuildContext context, PdfRow r, String c) {
    final t = FinanceToolsStrings.of(context);
    final p = context.palette;
    final problem = r.problemIn(c);
    final amount = double.tryParse(r.amount) == null
        ? (r.amount.isEmpty ? '-' : r.amount)
        : formatAmount(r.amount, r.currency ?? c);
    return Padding(
      key: Key('pdf-row-${r.key}'),
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Material(
            type: MaterialType.transparency,
            child: CheckboxListTile(
              key: Key('pdf-include-${r.key}'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: r.include,
              onChanged: (on) => setState(() => r.include = on ?? false),
              title: Text(r.description.isEmpty ? '-' : r.description),
              subtitle: Text([
                r.postedOn.isEmpty ? '-' : ltrValue(context, r.postedOn),
                amount,
                t.t('pdf.page', {'page': r.page}),
                if (r.reference != null)
                  t.t('pdf.reference',
                      {'reference': ltrValue(context, r.reference!)}),
              ].join(' · ')),
              secondary: IconButton(
                key: Key('pdf-edit-${r.key}'),
                tooltip: t.t('pdf.editRow', {'line': r.line}),
                icon: const Icon(Icons.edit_outlined),
                onPressed: () => _editRow(r),
              ),
            )),
        for (final flag in r.flags)
          if (t.maybe('pdf.flag.$flag') != null)
            Text(t.t('pdf.flag.$flag'), style: TextStyle(color: p.warn)),
        if (r.confidence < 0.6)
          Text(t.t('pdf.lowConfidence'), style: TextStyle(color: p.warn)),
        if (r.include && problem != null)
          Text(t.t(problem), style: TextStyle(color: p.stop)),
        if (r.include)
          _MatchControls(
              match: r.match,
              choice: r.choice,
              rowKey: r.key,
              onChanged: () => setState(() {})),
      ]),
    );
  }
}

/// One PDF row's date, description and amount, edited before import. Owns
/// its fields so they live as long as the sheet.
class _PdfRowEditor extends StatefulWidget {
  const _PdfRowEditor(this.row);
  final PdfRow row;

  @override
  State<_PdfRowEditor> createState() => _PdfRowEditorState();
}

class _PdfRowEditorState extends State<_PdfRowEditor> {
  late final _date = TextEditingController(text: widget.row.postedOn);
  late final _description = TextEditingController(text: widget.row.description);
  late final _amount = TextEditingController(text: widget.row.amount);

  @override
  void dispose() {
    _date.dispose();
    _description.dispose();
    _amount.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(t.t('pdf.editRow', {'line': widget.row.line}),
            style: Theme.of(context).textTheme.titleLarge),
        RecordsDateField(
            key: const Key('pdf-edit-date'),
            controller: _date,
            label: t.t('pdf.date')),
        TextField(
            key: const Key('pdf-edit-description'),
            controller: _description,
            decoration: InputDecoration(labelText: t.t('pdf.description'))),
        TextField(
            key: const Key('pdf-edit-amount'),
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(
                decimal: true, signed: true),
            decoration: InputDecoration(
                labelText: t.t('pdf.amount'),
                helperText: t.t('pdf.amountHelp'))),
        const SizedBox(height: AnumSpacing.md),
        FilledButton(
            key: const Key('pdf-edit-save'),
            onPressed: () => Navigator.pop(context, (
                  _date.text.trim(),
                  _description.text.trim(),
                  _amount.text.trim()
                )),
            child: Text(t.t('save'))),
      ],
    );
  }
}
