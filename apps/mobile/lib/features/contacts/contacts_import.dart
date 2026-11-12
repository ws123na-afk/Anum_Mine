import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart' show JsonMap;
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../../src/infrastructure/picked_file_size.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'contacts_repository.dart';

/// Importing people on the phone (docs/contacts.md#on-the-phone): pick a CSV,
/// Excel (.xlsx) or vCard (.vcf) file, see the API's preview as a summary
/// (rows, new, already in the directory, possible duplicates, repeated, cannot
/// be imported, what applying would add, update and skip, the columns ANUM
/// chose and the first problems), and import it with ANUM's suggestion for
/// every row. The same two calls as the web (`/import/preview`, then
/// `/import/apply` with the preview's mapping and fingerprint), owners only on
/// the server. Choosing other columns or another action for one row is a desk
/// task: the phone says so and points to the web.

/// The largest CSV or vCard text and the largest Excel workbook the API reads.
const importMaxTextBytes = 4000000, importMaxWorkbookBytes = 2000000;

/// The file's format from its name: `csv` (also .txt), `xlsx` or `vcf`.
String? importFormatOf(String name) {
  final dot = name.lastIndexOf('.');
  final extension = dot < 0 ? '' : name.substring(dot + 1).toLowerCase();
  return switch (extension) {
    'csv' || 'txt' => 'csv',
    'xlsx' => 'xlsx',
    'vcf' || 'vcard' => 'vcf',
    _ => null,
  };
}

/// A file read for the import: its text (CSV, vCard) or the workbook's bytes
/// as base64 (Excel), with the body that carries it.
class ImportFile {
  const ImportFile(
      {required this.name, required this.format, required this.content});
  final String name, format, content;

  JsonMap body() => switch (format) {
        'xlsx' => {'format': 'xlsx', 'file_base64': content, 'file_name': name},
        'vcf' => {'format': 'vcf', 'csv': content, 'file_name': name},
        _ => {'csv': content, 'file_name': name},
      };
}

/// The picked file as an [ImportFile], or a [FormatException] whose message
/// is a code: `type`, `empty`, `too_large` or `not_text`.
ImportFile readImportFile(String name, Uint8List bytes) {
  final format = importFormatOf(name);
  if (format == null) throw const FormatException('type');
  if (bytes.isEmpty) throw const FormatException('empty');
  final limit = format == 'xlsx' ? importMaxWorkbookBytes : importMaxTextBytes;
  if (bytes.length > limit) throw const FormatException('too_large');
  if (format == 'xlsx') {
    return ImportFile(name: name, format: format, content: base64Encode(bytes));
  }
  try {
    return ImportFile(name: name, format: format, content: utf8.decode(bytes));
  } on FormatException {
    throw const FormatException('not_text');
  }
}

typedef ImportFilePicker = Future<({String name, Uint8List bytes})?> Function();

/// The system picker, limited to CSV, Excel and vCard files. The file is read
/// into memory and the picker's temporary copy is removed.
Future<({String name, Uint8List bytes})?> pickImportFile() async {
  final file = await FilePicker.pickFile(
      type: FileType.custom,
      allowedExtensions: const ['csv', 'txt', 'xlsx', 'vcf']);
  if (file == null) return null;
  try {
    final size = await pickedFileSize(file);
    if (size != null && size > importMaxTextBytes) {
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

/// One row that cannot be imported, with the API's first reason.
class ImportProblem {
  const ImportProblem(this.row, this.text);
  final int row;
  final String text;
}

/// The preview as the phone shows it.
class ImportPlan {
  const ImportPlan({
    required this.columns,
    required this.mapping,
    required this.fingerprint,
    required this.counts,
    required this.create,
    required this.update,
    required this.skip,
    required this.problems,
    required this.needsColumns,
  });

  factory ImportPlan.fromJson(JsonReader j) {
    final counts = j.object('counts');
    final rows = j.objects('rows', (row) => row);
    var create = 0, update = 0, skip = 0;
    final problems = <ImportProblem>[];
    for (final row in rows) {
      switch (row.string('action')) {
        case 'create':
          create++;
        case 'update':
          update++;
        default:
          skip++;
      }
      final reasons = row.optStrings('problems') ?? const <String>[];
      if (row.string('status') == 'invalid' && problems.length < 5) {
        problems.add(ImportProblem(
            row.integer('row'), reasons.isEmpty ? '-' : reasons.first));
      }
    }
    final mapping = j.optMap('mapping') ?? const <String, Object?>{};
    return ImportPlan(
      columns: j.strings('columns'),
      mapping: mapping,
      fingerprint: j.string('fingerprint'),
      counts: {
        for (final key in const [
          'rows',
          'new',
          'match',
          'possible_duplicate',
          'duplicate_in_file',
          'invalid'
        ])
          key: counts.integer(key)
      },
      create: create,
      update: update,
      skip: skip,
      problems: problems,
      needsColumns: rows.isEmpty && (j.optBool('suggested') ?? false),
    );
  }

  final List<String> columns;
  final JsonMap mapping;
  final String fingerprint;
  final Map<String, int> counts;
  final int create, update, skip;
  final List<ImportProblem> problems;

  /// ANUM could not tell which column holds what: the columns are chosen on
  /// the web.
  final bool needsColumns;

  /// The columns ANUM chose, as "Name: Full name" lines.
  List<(String, String)> chosen() {
    final out = <(String, String)>[];
    void add(String field, Object? value) {
      if (value is String && value.isNotEmpty) out.add((field, value));
      if (value is List) {
        final names = value.whereType<String>().where((v) => v.isNotEmpty);
        if (names.isNotEmpty) out.add((field, names.join(', ')));
      }
    }

    for (final field in const [
      'name',
      'organisation',
      'phones',
      'emails',
      'notes',
      'do_not_contact'
    ]) {
      add(field, mapping[field]);
    }
    return out;
  }
}

/// What the import did.
class ImportOutcome {
  const ImportOutcome(
      this.created, this.updated, this.skipped, this.marks, this.proposals);
  factory ImportOutcome.fromJson(JsonReader j) => ImportOutcome(
      j.integer('created'),
      j.integer('updated'),
      j.integer('skipped'),
      j.integer('marks_added'),
      j.integer('proposals_created'));
  final int created, updated, skipped, marks, proposals;
}

enum ImportPhase { idle, reading, ready, importing, done }

/// Pick, preview and import, every state said.
class ContactsImportController extends ChangeNotifier {
  ContactsImportController(this.repository, {ImportFilePicker? picker})
      : picker = picker ?? pickImportFile;
  final ContactsRepository repository;
  final ImportFilePicker picker;

  ImportPhase phase = ImportPhase.idle;
  ImportFile? file;
  ImportPlan? plan;
  ImportOutcome? outcome;

  /// A message code (`type`, `empty`, `too_large`, `not_text`) or the API's
  /// own words, and whether the server refused the caller.
  String? error;
  bool errorIsCode = false, denied = false;
  bool _disposed = false;

  Future<void> choose() async {
    if (phase == ImportPhase.reading || phase == ImportPhase.importing) return;
    _clear();
    final ({String name, Uint8List bytes})? picked;
    try {
      picked = await picker();
    } on FormatException catch (e) {
      _fail(e.message, code: true);
      return;
    } on Object catch (e) {
      _fail(readableError(e));
      return;
    }
    if (picked == null) {
      _notify();
      return;
    }
    try {
      file = readImportFile(picked.name, picked.bytes);
    } on FormatException catch (e) {
      _fail(e.message, code: true);
      return;
    }
    phase = ImportPhase.reading;
    _notify();
    try {
      plan = await repository.previewImport(file!);
      phase = ImportPhase.ready;
    } on ApiException catch (e) {
      denied = e.isPermissionDenied;
      _fail(e.readable);
      return;
    } on Object catch (e) {
      _fail(readableError(e));
      return;
    }
    _notify();
  }

  Future<void> apply() async {
    final current = file, preview = plan;
    if (current == null || preview == null || phase != ImportPhase.ready) {
      return;
    }
    phase = ImportPhase.importing;
    error = null;
    _notify();
    try {
      outcome = await repository.applyImport(current, preview);
      phase = ImportPhase.done;
      file = null;
      plan = null;
    } on ApiException catch (e) {
      phase = ImportPhase.ready;
      denied = e.isPermissionDenied;
      error = e.readable;
      errorIsCode = false;
    } on Object catch (e) {
      phase = ImportPhase.ready;
      error = readableError(e);
      errorIsCode = false;
    }
    _notify();
  }

  void _clear() {
    file = null;
    plan = null;
    outcome = null;
    error = null;
    errorIsCode = denied = false;
    phase = ImportPhase.idle;
  }

  void _fail(String message, {bool code = false}) {
    phase = ImportPhase.idle;
    file = null;
    plan = null;
    error = message;
    errorIsCode = code;
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

class ContactsImportScreen extends StatelessWidget {
  const ContactsImportScreen({required this.controller, super.key});
  final ContactsImportController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('contacts.import.title'))),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(AnumSpacing.md),
            children: [
              for (final child in _children(context, l))
                Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 760),
                    child: Padding(
                        padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                        child: child),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _children(BuildContext context, AnumLocalizations l) {
    final c = controller;
    final text = Theme.of(context).textTheme;
    final busy =
        c.phase == ImportPhase.reading || c.phase == ImportPhase.importing;
    final plan = c.plan;
    return [
      Text(l.t('contacts.import.intro'), style: text.bodyMedium),
      Text(l.t('contacts.import.limits'), style: text.bodySmall),
      FilledButton.icon(
        key: const Key('contacts-import-choose'),
        onPressed: busy ? null : () => unawaited(c.choose()),
        icon: const Icon(Icons.upload_file_outlined),
        label: Text(l.t(c.file == null
            ? 'contacts.import.choose'
            : 'contacts.import.another')),
      ),
      if (c.file != null)
        Text(l.f('contacts.import.file', {'name': c.file!.name}),
            key: const Key('contacts-import-file'), style: text.bodyMedium),
      if (c.phase == ImportPhase.reading)
        Semantics(
          liveRegion: true,
          child: Row(children: [
            const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(child: Text(l.t('contacts.import.reading'))),
          ]),
        ),
      if (c.error != null)
        Semantics(
          liveRegion: true,
          child: AnumSurface(
            key: const Key('contacts-import-error'),
            child: Text(
              c.denied
                  ? l.t('contacts.import.owners')
                  : c.errorIsCode
                      ? l.t('contacts.import.err.${c.error}')
                      : l.f('contacts.import.failed', {'message': c.error!}),
              style: TextStyle(color: context.palette.stop),
            ),
          ),
        ),
      if (c.outcome != null)
        Semantics(
          liveRegion: true,
          child: AnumSurface(
            key: const Key('contacts-import-done'),
            child: Text(l.f('contacts.import.done', {
              'created': c.outcome!.created,
              'updated': c.outcome!.updated,
              'skipped': c.outcome!.skipped,
              'marks': c.outcome!.marks,
              'proposals': c.outcome!.proposals,
            })),
          ),
        ),
      if (plan != null) ..._plan(context, l, plan, busy),
    ];
  }

  List<Widget> _plan(
      BuildContext context, AnumLocalizations l, ImportPlan plan, bool busy) {
    final text = Theme.of(context).textTheme;
    if (plan.needsColumns) {
      return [
        AnumEmpty(
          key: const Key('contacts-import-columns'),
          icon: Icons.view_column_outlined,
          title: l.t('contacts.import.columnsTitle'),
          message: l.f('contacts.import.columnsWeb',
              {'columns': plan.columns.join(', ')}),
        ),
      ];
    }
    final counts = plan.counts;
    return [
      Text(l.f('contacts.import.summary', {'rows': counts['rows'] ?? 0}),
          style: text.titleMedium),
      AnumSurface(
        key: const Key('contacts-import-counts'),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final status in const [
              'new',
              'match',
              'possible_duplicate',
              'duplicate_in_file',
              'invalid'
            ])
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(children: [
                  Expanded(child: Text(l.t('contacts.import.status.$status'))),
                  Text('${counts[status] ?? 0}', style: text.titleSmall),
                ]),
              ),
          ],
        ),
      ),
      Text(l.f('contacts.import.totals',
          {'create': plan.create, 'update': plan.update, 'skip': plan.skip})),
      Text(l.t('contacts.import.columns'), style: text.titleSmall),
      for (final (field, column) in plan.chosen())
        Text(
            l.f('contacts.import.columnLine', {
              'field': l.t('contacts.import.field.$field'),
              'column': column
            }),
            style: text.bodySmall),
      if (plan.problems.isNotEmpty) ...[
        Text(l.t('contacts.import.problems'), style: text.titleSmall),
        for (final problem in plan.problems)
          Text(
              l.f('contacts.import.problemLine',
                  {'row': problem.row, 'problem': problem.text}),
              style: text.bodySmall),
      ],
      Text(l.t('contacts.import.webForMore'), style: text.bodySmall),
      FilledButton.icon(
        key: const Key('contacts-import-apply'),
        onPressed: busy || plan.create + plan.update == 0
            ? null
            : () => unawaited(_confirm(context, l, plan)),
        icon: const Icon(Icons.playlist_add_check),
        label: Text(l.t(controller.phase == ImportPhase.importing
            ? 'contacts.import.applying'
            : 'contacts.import.apply')),
      ),
    ];
  }

  Future<void> _confirm(
      BuildContext context, AnumLocalizations l, ImportPlan plan) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('contacts.import.confirmTitle')),
        content: Text(l.f('contacts.import.confirmText',
            {'create': plan.create, 'update': plan.update, 'skip': plan.skip})),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(l.t('contacts.dnc.cancel'))),
          FilledButton(
              key: const Key('contacts-import-confirm'),
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(l.t('contacts.import.apply'))),
        ],
      ),
    );
    if (yes == true) await controller.apply();
  }
}
