import 'package:flutter/material.dart';

import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_tools_data.dart' show StatementImport;

/// What a statement import did with each row (docs/finance.md#import-history),
/// the same as the web's Import history rows: added, matched to a record,
/// already recorded, held for review, left out by the person or not read,
/// with the reason; a row that stands for several recorded payments
/// (docs/finance.md#one-row-for-several-recorded-payments) can be unmatched,
/// and then waits in Review.

/// One statement row and what happened to it (`FinanceRowDisposition`).
class ImportRowDisposition {
  const ImportRowDisposition(
      {required this.line,
      required this.description,
      required this.disposition,
      this.postedOn,
      this.amount,
      this.reason,
      this.message,
      this.splitGroup,
      this.splitRecords = const []});
  final int line;
  final String description, disposition;
  final String? postedOn, amount, reason, message, splitGroup;
  final List<String> splitRecords;

  factory ImportRowDisposition.fromJson(JsonReader r) => ImportRowDisposition(
        line: r.optInt('line') ?? 0,
        description: r.optString('description') ?? '',
        disposition: r.optString('disposition') ?? 'imported',
        postedOn: r.optString('posted_on'),
        amount: r.json['amount'] == null ? null : '${r.json['amount']}',
        reason: r.optString('reason'),
        message: r.optString('message'),
        splitGroup: r.optString('split_group'),
        splitRecords: r.optStrings('split_records') ?? const [],
      );
}

extension FinanceImportRowsApi on FinanceMoreRepository {
  /// One page of an import's rows; [disposition] filters (`not_in_ledger`:
  /// held or left out). Answers the rows and how many there are.
  Future<(List<ImportRowDisposition>, int)> importRows(String importId,
      {String? disposition, int offset = 0, int limit = 100}) async {
    final r = JsonReader.of(await api.request(
        'GET',
        '${FinanceMoreRepository.base}/imports/${Uri.encodeComponent(importId)}${recordsQuery({
              'disposition': disposition,
              'limit': limit,
              'offset': offset,
            })}'));
    return (
      r.optObjects('rows', ImportRowDisposition.fromJson),
      r.optInt('rows_total') ?? 0
    );
  }

  /// Undoes one row matched to several recorded payments: each is detached
  /// and the row waits in Review.
  Future<void> undoSplitMatch(String importId, String groupId) => api.request(
      'POST',
      '${FinanceMoreRepository.base}/imports/${Uri.encodeComponent(importId)}/splits/${Uri.encodeComponent(groupId)}/undo');
}

/// The filters the rows offer, with the counts the import stored; empty
/// groups are left out (all and not in the ledger stay), as on the web.
List<(String, int?)> importRowFilters(StatementImport i) => [
      ('all', null),
      ('not_in_ledger', i.notInLedger),
      if (i.heldForReview > 0) ('held_for_review', i.heldForReview),
      if (i.skipped > 0) ('unticked', i.skipped),
      if (i.reconciled > 0) ('attached_to_existing', i.reconciled),
      if (i.importedCount > 0) ('imported', i.importedCount),
      if (i.duplicateCount > 0) ('duplicate_skipped', i.duplicateCount),
      if (i.dropped > 0) ('dropped_by_reader', i.dropped),
    ];

class ImportRowsScreen extends StatefulWidget {
  const ImportRowsScreen(
      {required this.repository,
      required this.item,
      required this.currency,
      super.key});
  final FinanceMoreRepository repository;
  final StatementImport item;

  /// The account's currency (the rows carry amounts only).
  final String currency;

  @override
  State<ImportRowsScreen> createState() => _ImportRowsScreenState();
}

class _ImportRowsScreenState extends State<ImportRowsScreen> {
  static const _page = 100;
  late String _filter = widget.item.notInLedger > 0 ? 'not_in_ledger' : 'all';
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  final List<ImportRowDisposition> _rows = [];
  int _total = 0;
  bool _more = false, _busy = false;
  String? _notice;
  bool _noticeError = false;
  bool _changed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool append = false}) async {
    setState(() {
      if (!append) _phase = RecordsPhase.loading;
      _more = append;
    });
    try {
      final (rows, total) = await widget.repository.importRows(widget.item.id,
          disposition: _filter == 'all' ? null : _filter,
          offset: append ? _rows.length : 0,
          limit: _page);
      if (!append) _rows.clear();
      _rows.addAll(rows);
      _total = total;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() => _more = false);
  }

  Future<void> _undo(String group) async {
    final s = FinanceImportRowsStrings.of(context);
    final ok = await confirmRecords(context,
        title: s.t('split.undoTitle'),
        message: s.t('split.undoText'),
        action: s.t('split.undo'));
    if (!ok || !mounted) return;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await widget.repository.undoSplitMatch(widget.item.id, group);
      _notice = s.t('split.undone');
      _noticeError = false;
      _changed = true;
      await _load();
    } on Object catch (error) {
      _notice = RecordsFailure.from(error).message;
      _noticeError = true;
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceImportRowsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final item = widget.item;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_changed);
      },
      child: Scaffold(
        appBar: AppBar(title: Text(s.t('title', {'file': item.filename}))),
        body: AnumPage(onRefresh: _load, children: [
          Text(s.t('intro'), style: muted),
          if (item.check == 'tied' && item.notInLedger > 0)
            Text(s.count('tiedMissing', item.notInLedger),
                key: const Key('rows-tied-missing'),
                style: TextStyle(color: context.palette.warn)),
          if (item.heldForReview > 0)
            Text(s.count('heldWhere', item.heldForReview), style: muted),
          DropdownButtonFormField<String>(
            key: const Key('rows-filter'),
            initialValue: _filter,
            isExpanded: true,
            decoration: InputDecoration(labelText: s.t('show')),
            items: [
              for (final (id, count) in importRowFilters(item))
                DropdownMenuItem(
                    value: id,
                    child: Text(
                        count == null
                            ? s.t('filter.$id')
                            : '${s.t('filter.$id')} ($count)',
                        overflow: TextOverflow.ellipsis)),
            ],
            onChanged: _busy
                ? null
                : (v) {
                    if (v == null || v == _filter) return;
                    _filter = v;
                    _load();
                  },
          ),
          if (_notice != null)
            Text(_notice!,
                key: const Key('rows-notice'),
                style: TextStyle(
                    color: _noticeError
                        ? context.palette.stop
                        : context.palette.muted)),
          ...switch (_phase) {
            RecordsPhase.loading => [
                AnumFeedback(
                    kind: AnumFeedbackKind.loading, message: s.t('loading'))
              ],
            RecordsPhase.denied => [
                AnumFeedback(
                    kind: AnumFeedbackKind.permission, message: s.t('denied'))
              ],
            RecordsPhase.error => [
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message:
                        s.t('failed', {'message': _failure?.message ?? ''}),
                    onRetry: _load)
              ],
            RecordsPhase.ready when _rows.isEmpty => [
                AnumEmpty(
                    key: const Key('rows-empty'),
                    icon: Icons.table_rows_outlined,
                    title: s.t(_filter == 'all'
                        ? 'noneStoredTitle'
                        : 'filter.$_filter'),
                    message: s.t(_filter == 'all' ? 'noneStored' : 'none'))
              ],
            RecordsPhase.ready => [
                AnumSurface(
                  padding: const EdgeInsets.symmetric(
                      horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
                  child: Column(children: [
                    for (final (index, r) in _rows.indexed)
                      _RowTile(
                          key: Key('row-$index'),
                          row: r,
                          currency: widget.currency,
                          busy: _busy,
                          onUndo: _undo),
                  ]),
                ),
                if (_rows.length < _total)
                  OutlinedButton(
                      key: const Key('rows-more'),
                      onPressed: _more ? null : () => _load(append: true),
                      child: Text(s.t(
                          'more', {'shown': _rows.length, 'total': _total}))),
              ],
          },
        ]),
      ),
    );
  }
}

class _RowTile extends StatelessWidget {
  const _RowTile(
      {required this.row,
      required this.currency,
      required this.busy,
      required this.onUndo,
      super.key});
  final ImportRowDisposition row;
  final String currency;
  final bool busy;
  final void Function(String group) onUndo;

  @override
  Widget build(BuildContext context) {
    final s = FinanceImportRowsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final group = row.splitGroup;
    final title = row.description.isNotEmpty
        ? row.description
        : (row.message ?? '').isNotEmpty
            ? row.message!
            : '-';
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      AnumRow(
        icon: switch (row.disposition) {
          'imported' => Icons.add_circle_outline,
          'attached_to_existing' => Icons.link,
          'held_for_review' => Icons.hourglass_empty,
          'duplicate_skipped' => Icons.copy_all_outlined,
          _ => Icons.remove_circle_outline,
        },
        tone: row.disposition == 'held_for_review' ||
                row.disposition == 'unticked'
            ? AnumTone.warn
            : null,
        title: title,
        subtitle: [
          if (row.line > 0) s.t('line', {'n': row.line}),
          ltrValue(context, row.postedOn ?? '-'),
          row.amount == null
              ? '-'
              : ltrValue(context, formatAmount(row.amount!, currency)),
        ].join(' · '),
        detail: [
          s.maybe('kind.${row.disposition}') ?? row.disposition,
          if (row.reason case final reason?)
            s.maybe('reason.$reason') ?? reason,
        ].join(' · '),
      ),
      if (row.disposition == 'dropped_by_reader' &&
          (row.message ?? '').isNotEmpty &&
          row.description.isNotEmpty)
        Text(row.message!, style: muted),
      if (group != null && row.disposition == 'attached_to_existing')
        Wrap(
          spacing: AnumSpacing.sm,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(s.count('split.records', row.splitRecords.length),
                style: muted),
            TextButton.icon(
                key: Key('rows-undo-$group'),
                onPressed: busy ? null : () => onUndo(group),
                icon: const Icon(Icons.undo),
                label: Text(s.t('split.undo'))),
          ],
        ),
    ]);
  }
}

/// English and Arabic strings of an import's rows; the terms follow the web
/// catalog `financeImportRows`.
class FinanceImportRowsStrings extends ModuleStrings {
  const FinanceImportRowsStrings(bool arabic) : super(arabic, _en, _ar);
  static FinanceImportRowsStrings of(BuildContext context) =>
      FinanceImportRowsStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  String count(String key, int n) {
    final form = arabic
        ? '$key.${AnumLocalizations.arabicPluralForm(n)}'
        : '$key.${n == 1 ? 'one' : 'other'}';
    return t(maybe(form) == null ? '$key.other' : form, {'n': n});
  }

  static const _en = <String, String>{
    'title': 'Rows of {file}',
    'intro':
        'What the import did with each row of the statement. Rows held for review or left out are not in your ledger.',
    'tiedMissing.one':
        'The statement balances, but {n} row is not in the ledger, so the account is not marked reconciled. Find it below.',
    'tiedMissing.other':
        'The statement balances, but {n} rows are not in the ledger, so the account is not marked reconciled. Find them below.',
    'heldWhere.one':
        '{n} row waits in More in Finance, iPhone SMS, Review: confirm it there to record it, or dismiss it.',
    'heldWhere.other':
        '{n} rows wait in More in Finance, iPhone SMS, Review: confirm them there to record them, or dismiss them.',
    'show': 'Show',
    'loading': 'Loading the rows…',
    'failed': 'The rows could not be loaded: {message}',
    'denied':
        'You cannot see this import. Ask the workspace owner for Finance access.',
    'none': 'No rows of this kind in this import.',
    'noneStoredTitle': 'No rows kept for this import',
    'noneStored':
        'This import was made before ANUM kept what happened to each row. Import the statement again to see it: rows already recorded are not added twice.',
    'more': 'Show more ({shown} of {total})',
    'line': 'Line {n}',
    'filter.all': 'All rows',
    'filter.not_in_ledger': 'Not in the ledger',
    'filter.held_for_review': 'Held for review',
    'filter.unticked': 'Left out by you',
    'filter.attached_to_existing': 'Matched to a record',
    'filter.imported': 'Added',
    'filter.duplicate_skipped': 'Already recorded',
    'filter.dropped_by_reader': 'Not read',
    'kind.imported': 'Added',
    'kind.held_for_review': 'Held for review',
    'kind.attached_to_existing': 'Matched to a record',
    'kind.duplicate_skipped': 'Already recorded',
    'kind.unticked': 'Left out by you',
    'kind.dropped_by_reader': 'Not read',
    'reason.two_candidates': 'more than one recorded payment fits',
    'reason.amount_date_only': 'same amount and date, no merchant to compare',
    'reason.waiting_review': 'already waiting in Review',
    'reason.imported_before': 'imported from a statement before',
    'reason.reconciled_before': 'a statement confirmed it before',
    'reason.same_statement_row':
        'the same day, amount and merchant from another statement',
    'reason.already_in_ledger': 'stored by another import at the same moment',
    'reason.same_amount_merchant': 'same amount and merchant',
    'reason.same_reference': 'same bank reference',
    'reason.same_original_amount': 'same foreign amount',
    'reason.unreadable': 'the row could not be read',
    'reason.outside_period': 'dated outside the statement period',
    'reason.unattached_line': 'a line no row could take',
    'reason.split_match': 'stands for several recorded payments together',
    'reason.split_changed':
        'the payments you picked changed since the preview, so none were attached',
    'reason.split_undone': 'the match with several payments was undone',
    'split.records.one': 'Attached to {n} recorded payment',
    'split.records.other': 'Attached to {n} recorded payments',
    'split.undo': 'Undo the match',
    'split.undoTitle': 'Undo this match?',
    'split.undoText':
        'Every recorded payment this row stood for is detached from it, and the row waits in Review, where you can import it as its own payment or leave it out.',
    'split.undone': 'Undone. The row waits in Review.',
  };

  static const _ar = <String, String>{
    'title': 'صفوف {file}',
    'intro':
        'ما فعله الاستيراد بكل صف من الكشف. الصفوف المحجوزة للمراجعة أو المستبعدة ليست في دفترك.',
    'tiedMissing.one':
        'الكشف متوازن، لكن صفًا واحدًا ليس في الدفتر، لذلك لا يُعلَّم الحساب مطابقًا. تجده أدناه.',
    'tiedMissing.other':
        'الكشف متوازن، لكن صفوفًا ({n}) ليست في الدفتر، لذلك لا يُعلَّم الحساب مطابقًا. تجدها أدناه.',
    'heldWhere.one':
        'صف واحد ينتظر في المزيد في المالية، رسائل الآيفون، المراجعة: أكّده هناك لتسجيله أو تجاهله.',
    'heldWhere.other':
        'صفوف ({n}) تنتظر في المزيد في المالية، رسائل الآيفون، المراجعة: أكّدها هناك لتسجيلها أو تجاهلها.',
    'show': 'اعرض',
    'loading': 'جارٍ تحميل الصفوف…',
    'failed': 'تعذر تحميل الصفوف: {message}',
    'denied':
        'لا يمكنك رؤية هذا الاستيراد. اطلب صلاحية المالية من مالك مساحة العمل.',
    'none': 'لا صفوف من هذا النوع في هذا الاستيراد.',
    'noneStoredTitle': 'لم تُحفظ صفوف لهذا الاستيراد',
    'noneStored':
        'تم هذا الاستيراد قبل أن يحفظ ANUM ما حدث لكل صف. استورد الكشف مجددًا لترى ذلك: الصفوف المسجلة لا تضاف مرتين.',
    'more': 'اعرض المزيد ({shown} من {total})',
    'line': 'السطر {n}',
    'filter.all': 'كل الصفوف',
    'filter.not_in_ledger': 'خارج الدفتر',
    'filter.held_for_review': 'محجوزة للمراجعة',
    'filter.unticked': 'استبعدتها أنت',
    'filter.attached_to_existing': 'طوبقت مع سجل',
    'filter.imported': 'أضيفت',
    'filter.duplicate_skipped': 'مسجلة من قبل',
    'filter.dropped_by_reader': 'لم تُقرأ',
    'kind.imported': 'أضيف',
    'kind.held_for_review': 'محجوز للمراجعة',
    'kind.attached_to_existing': 'طوبق مع سجل',
    'kind.duplicate_skipped': 'مسجل من قبل',
    'kind.unticked': 'استبعدته أنت',
    'kind.dropped_by_reader': 'لم يُقرأ',
    'reason.two_candidates': 'أكثر من دفعة مسجلة تناسبه',
    'reason.amount_date_only': 'المبلغ والتاريخ نفسهما بلا اسم تاجر للمقارنة',
    'reason.waiting_review': 'ينتظر في المراجعة من قبل',
    'reason.imported_before': 'استورد من كشف سابق',
    'reason.reconciled_before': 'أكده كشف سابق',
    'reason.same_statement_row': 'اليوم والمبلغ والتاجر نفسها من كشف آخر',
    'reason.already_in_ledger': 'سجله استيراد آخر في اللحظة نفسها',
    'reason.same_amount_merchant': 'المبلغ والتاجر نفسهما',
    'reason.same_reference': 'مرجع البنك نفسه',
    'reason.same_original_amount': 'المبلغ الأجنبي نفسه',
    'reason.unreadable': 'تعذرت قراءة الصف',
    'reason.outside_period': 'تاريخه خارج فترة الكشف',
    'reason.unattached_line': 'سطر لم يأخذه أي صف',
    'reason.split_match': 'يمثل عدة دفعات مسجلة معا',
    'reason.split_changed':
        'تغيرت الدفعات التي اخترتها منذ المعاينة، لذلك لم يُربط أي منها',
    'reason.split_undone': 'أُلغيت المطابقة مع عدة دفعات',
    'split.records.one': 'مربوط بدفعة مسجلة واحدة',
    'split.records.other': 'مربوط بدفعات مسجلة ({n})',
    'split.undo': 'إلغاء المطابقة',
    'split.undoTitle': 'هل تريد إلغاء هذه المطابقة؟',
    'split.undoText':
        'تُفصل كل الدفعات المسجلة التي كان هذا الصف يمثلها، وينتظر الصف في المراجعة، حيث يمكنك استيراده دفعة مستقلة أو تركه.',
    'split.undone': 'أُلغيت. ينتظر الصف في المراجعة.',
  };
}
