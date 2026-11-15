import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import '../vault/vault_transfer.dart' show VaultFilePicker, pickVaultFile;
import 'finance_core_models.dart';
import 'finance_core_repository.dart';
import 'finance_correct_strings.dart';
import 'finance_models.dart';
import 'finance_review_models.dart';
import 'finance_review_strings.dart';
import 'finance_rules_screens.dart' show RulePreviewBlock;
import 'finance_strings.dart';
import 'finance_tx_edit.dart';

/// A transaction's corrections on the phone, the same as the web drawer
/// (docs/finance.md#transaction-detail): the category with its subcategory,
/// the type, "Use this for later payments to this merchant" (a learned
/// merchant), "Remember this category" (a learned phrase), Apply to similar
/// with the preview and Undo, the AI category (shown, never saved), money in
/// corrected to money out after a confirmation, a duplicate copy deleted,
/// and the date, amount and description edited ([TransactionEditScreen]).
/// Only a typed or imported row is corrected here; an SMS or Apple Pay row
/// is corrected under iPhone SMS, Review, so the audit trail keeps it.

/// The categories that are not spending: money in corrected to money out
/// needs a spending category.
const _notSpending = {'income', 'cashback', 'refunds', 'transfer'};

/// The web's room-rent words: an incoming row with them is probably a
/// payment recorded the wrong way round.
final _roomRent = RegExp(
    r'room\s*rent|rent\s*room|bed\s*space|accommodation\s*rent|إيجار\s*غرفة|ايجار\s*غرفة',
    caseSensitive: false);

class TransactionCorrectionPanel extends StatefulWidget {
  const TransactionCorrectionPanel(
      {required this.repository,
      required this.detail,
      required this.onSaved,
      this.onOpen,
      this.onDeleted,
      this.pickFile = pickVaultFile,
      super.key});
  final FinanceCoreRepository repository;
  final RegisterDetail detail;

  /// Picks the receipt file (the system picker; a fake in tests).
  final VaultFilePicker pickFile;

  /// Reloads the detail (and the lists behind it) after a change.
  final Future<void> Function() onSaved;

  /// Opens another transaction (the duplicate's match).
  final void Function(String id)? onOpen;

  /// After this copy was deleted; the screen closes.
  final VoidCallback? onDeleted;

  @override
  State<TransactionCorrectionPanel> createState() =>
      _TransactionCorrectionPanelState();
}

class _TransactionCorrectionPanelState
    extends State<TransactionCorrectionPanel> {
  late String _category;
  bool _categoryTouched = false;
  String? _type;
  String _sub = '';
  bool _learnMerchant = true;
  bool _remember = false;
  final _phrase = TextEditingController();
  String? _fromAi;
  List<SubcategoryChoice> _subs = const [];
  bool _busy = false;
  String? _message;
  bool _messageError = false;
  String? _undoBatch;
  bool _uploading = false;

  RegisterRow get _row => widget.detail.row;

  @override
  void initState() {
    super.initState();
    _reset();
    widget.repository.subcategoryChoices().then((subs) {
      if (mounted) setState(() => _subs = subs);
    }, onError: (Object _) {
      // Subcategories are optional: without them the list is not offered.
    });
  }

  @override
  void didUpdateWidget(TransactionCorrectionPanel old) {
    super.didUpdateWidget(old);
    if (!identical(old.detail, widget.detail)) _reset();
  }

  @override
  void dispose() {
    _phrase.dispose();
    super.dispose();
  }

  void _reset() {
    final d = widget.detail;
    final row = d.row;
    _category =
        row.categorySource == 'none' ? d.suggestedCategory : row.category;
    if (!fileableCategories.contains(_category)) _category = 'other';
    _categoryTouched = false;
    _type = fileableTypes.contains(row.transactionType)
        ? row.transactionType
        : null;
    _sub = row.subcategoryId ?? '';
    _remember = false;
    _learnMerchant = true;
    _phrase.text = d.rememberPhrase;
  }

  List<SubcategoryChoice> _subsFor(String category) => [
        for (final s in _subs)
          if (s.parent == category && (!s.archived || s.id == _sub)) s
      ];

  bool get _unchanged =>
      _category == _row.category &&
      _row.categorySource == 'manual' &&
      !_remember &&
      (_type == null || _type == _row.transactionType) &&
      _sub == (_row.subcategoryId ?? '');

  Future<void> _run(Future<String> Function() call) async {
    final c = FinanceCorrectStrings.of(context);
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      _message = await call();
      _messageError = false;
    } on Object catch (error) {
      _message = c.t('failed', {'message': RecordsFailure.from(error).message});
      _messageError = true;
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _save() async {
    final c = FinanceCorrectStrings.of(context);
    final f = FinanceStrings.of(context);
    final rs = FinanceReviewStrings.of(context);
    final row = _row;
    final phrase = _phrase.text.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (_remember) {
      if (phrase.length < 4 || !phrase.contains(RegExp(r'[^\W\d_]'))) {
        setState(() {
          _message = c.t('rememberShort');
          _messageError = true;
        });
        return;
      }
      if (!row.description.toLowerCase().contains(phrase.toLowerCase())) {
        setState(() {
          _message = c.t('rememberMissing');
          _messageError = true;
        });
        return;
      }
    }
    final typeChanged = _type != null && _type != row.transactionType;
    final subChanged = _sub != (row.subcategoryId ?? '');
    // A type picked on its own files the row under that type's category on
    // the server; the category is sent only when chosen (as on the web).
    final categoryChanged =
        _categoryTouched || _remember || (!typeChanged && !subChanged);
    final category = _category, type = _type;
    await _run(() async {
      await widget.repository.saveCorrection(row.id,
          category: categoryChanged ? category : null,
          type: typeChanged ? type : null,
          subcategorySet: subChanged || (categoryChanged && _sub.isNotEmpty),
          subcategoryId: _sub.isEmpty ? null : _sub,
          remember: _remember ? phrase : null,
          learnMerchant: !_remember && _learnMerchant,
          fromAi: _fromAi != null && categoryChanged && _fromAi == category);
      _fromAi = null;
      _undoBatch = null;
      await widget.onSaved();
      return typeChanged && !categoryChanged
          ? c.t('typeSaved', {'type': rs.t('type.$type')})
          : c.t('categorySaved', {'category': f.t('category.$category')});
    });
  }

  Future<void> _askAi() async {
    final c = FinanceCorrectStrings.of(context);
    final f = FinanceStrings.of(context);
    await _run(() async {
      final found = await widget.repository.aiSuggestion(_row.id);
      final category = found.category;
      if (category == null || !fileableCategories.contains(category)) {
        return c.t('aiNone');
      }
      _category = category;
      _categoryTouched = true;
      _fromAi = category;
      _sub = '';
      return c.t('aiResult', {'category': f.t('category.$category')});
    });
  }

  Future<void> _moneyOut() async {
    final c = FinanceCorrectStrings.of(context);
    final row = _row;
    if (_notSpending.contains(_category)) {
      setState(() {
        _message = c.t('outNeedsCategory');
        _messageError = true;
      });
      return;
    }
    final ok = await confirmRecords(context,
        title: c.t('confirmOutTitle'),
        message: c.t('confirmOutText', {
          'amount': ltrValue(context, formatAmount(row.amount, row.currency))
        }),
        action: c.t('correctOut'));
    if (!ok || !mounted) return;
    final category = _category;
    await _run(() async {
      await widget.repository.correctToMoneyOut(row.id, row.amount, category);
      await widget.onSaved();
      return c.t('outDone');
    });
  }

  Future<void> _deleteCopy() async {
    final c = FinanceCorrectStrings.of(context);
    final row = _row;
    final ok = await confirmRecords(context,
        title: c.t('deleteTitle'),
        message: c.t('deleteText', {
          'description': row.description,
          'amount': ltrValue(context, formatAmount(row.amount, row.currency)),
        }),
        action: c.t('delete'));
    if (!ok || !mounted) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await widget.repository.deleteTransaction(row.id);
      await widget.onSaved();
      widget.onDeleted?.call();
    } on Object catch (error) {
      _message = c.t('failed', {'message': RecordsFailure.from(error).message});
      _messageError = true;
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _similar() async {
    final done = await Navigator.of(context).push<SimilarOutcome>(
        MaterialPageRoute(
            builder: (_) => SimilarScreen(
                repository: widget.repository, transactionId: _row.id)));
    if (done == null || !mounted) return;
    setState(() {
      _message = done.text;
      _messageError = false;
      _undoBatch = done.batchId;
    });
    await widget.onSaved();
  }

  Future<void> _attach() async {
    final c = FinanceCorrectStrings.of(context);
    final canonical = _row.canonicalId;
    if (canonical == null) return;
    final picked =
        await widget.pickFile(const ['.pdf', '.jpg', '.jpeg', '.png']);
    if (picked == null || !mounted) return;
    final (path, name, _) = picked;
    setState(() => _uploading = true);
    await _run(() async {
      await widget.repository.attachReceipt(
          path: path,
          name: name,
          canonicalId: canonical,
          description: _row.description);
      await widget.onSaved();
      return c.t('receiptAttached');
    });
    if (mounted) setState(() => _uploading = false);
  }

  Future<void> _edit() async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => TransactionEditScreen(
            repository: widget.repository, row: _row)));
    if (saved != true || !mounted) return;
    final e = TransactionEditStrings.of(context);
    setState(() {
      _message = e.t('saved');
      _messageError = false;
    });
    await widget.onSaved();
  }

  Future<void> _undo(String batchId) async {
    final c = FinanceCorrectStrings.of(context);
    await _run(() async {
      final restored = await widget.repository.undoBatch(batchId);
      _undoBatch = null;
      await widget.onSaved();
      return c.count('undone', restored);
    });
  }

  @override
  Widget build(BuildContext context) {
    final c = FinanceCorrectStrings.of(context);
    final f = FinanceStrings.of(context);
    final rs = FinanceReviewStrings.of(context);
    final arabic = AnumLocalizations.maybeOf(context).isArabic;
    final muted = TextStyle(color: context.palette.muted);
    final d = widget.detail;
    final row = d.row;
    final editable = row.editable;
    final incoming = (double.tryParse(row.amount) ?? 0) > 0;
    final subs = _subsFor(_category);
    final dup = d.duplicate;
    String typeName(String t) => rs.maybe('type.$t') ?? t;

    final banners = <Widget>[
      if (row.transactionType == 'uncategorized')
        _Banner(
            key: const Key('tx-uncategorized'),
            icon: Icons.warning_amber_outlined,
            tone: context.palette.warn,
            title: c.t('uncategorizedTitle'),
            text: c.t('uncategorizedText')),
      if (row.typeReason == 'refund_suggested')
        _Banner(
            icon: Icons.receipt_long_outlined,
            tone: context.palette.sky,
            text: c.t('refundSuggested')),
      if (row.linksNeedReview > 0)
        _Banner(
            icon: Icons.link_off,
            tone: context.palette.warn,
            text: c.count('linksReview', row.linksNeedReview)),
      if (incoming &&
          (_roomRent.hasMatch(row.description) || _category == 'room_rent'))
        _Banner(
            key: const Key('tx-direction'),
            icon: Icons.swap_vert,
            tone: context.palette.warn,
            title: c.t('directionTitle'),
            text: c.t('directionText')),
      if (dup != null)
        _Banner(
            key: const Key('tx-duplicate'),
            icon: Icons.copy_all_outlined,
            tone: context.palette.warn,
            title: c.t('duplicate'),
            text: c.t('duplicateText', {
              'amount':
                  ltrValue(context, formatAmount(dup.amount, dup.currency)),
              'date': ltrValue(context, dup.postedOn),
              'description': dup.description,
            }),
            actions: [
              if (widget.onOpen != null)
                TextButton(
                    key: const Key('tx-view-match'),
                    onPressed: _busy ? null : () => widget.onOpen!(dup.id),
                    child: Text(c.t('viewMatch'))),
              TextButton(
                  key: const Key('tx-delete-copy'),
                  style: TextButton.styleFrom(
                      foregroundColor: context.palette.stop),
                  onPressed: _busy ? null : _deleteCopy,
                  child: Text(c.t('deleteCopy'))),
            ]),
    ];

    return AnumSurface(
      key: const Key('tx-correct'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            title: c.t('title'),
            subtitle: c.t('suggested', {
              'category': f.t('category.${d.suggestedCategory}'),
              'by': c.maybe('suggestedBy.${d.suggestedBy}') ?? d.suggestedBy,
            })),
        if (row.transactionType != null)
          Text(
              [
                c.t('type', {'type': typeName(row.transactionType!)}),
                if (row.typeSource case final by?) c.maybe('typeBy.$by') ?? by,
              ].join(' · '),
              key: const Key('tx-type-line'),
              style: muted),
        for (final b in banners) ...[
          const SizedBox(height: AnumSpacing.sm),
          b,
        ],
        const SizedBox(height: AnumSpacing.sm),
        if (!editable)
          Text(c.t('smsOrigin'), key: const Key('tx-sms-origin'), style: muted)
        else ...[
          DropdownButtonFormField<String>(
            key: const Key('tx-category'),
            initialValue: _category,
            isExpanded: true,
            decoration: InputDecoration(labelText: c.t('category')),
            items: [
              for (final cat in fileableCategories)
                DropdownMenuItem(
                    value: cat,
                    child: Text(
                        cat == d.suggestedCategory
                            ? c.t('suggestedOption',
                                {'category': f.t('category.$cat')})
                            : f.t('category.$cat'),
                        overflow: TextOverflow.ellipsis))
            ],
            onChanged: _busy
                ? null
                : (v) => setState(() {
                      if (v == null) return;
                      _category = v;
                      _categoryTouched = true;
                      _sub = '';
                    }),
          ),
          if (subs.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            DropdownButtonFormField<String>(
              key: ValueKey('tx-subcategory-$_category'),
              initialValue: subs.any((s) => s.id == _sub) ? _sub : '',
              isExpanded: true,
              decoration: InputDecoration(labelText: c.t('subcategory')),
              items: [
                DropdownMenuItem(
                    value: '', child: Text(c.t('subcategoryNone'))),
                for (final s in subs)
                  DropdownMenuItem(
                      value: s.id,
                      child: Text(s.label(arabic),
                          overflow: TextOverflow.ellipsis)),
              ],
              onChanged: _busy ? null : (v) => setState(() => _sub = v ?? ''),
            ),
          ],
          const SizedBox(height: AnumSpacing.sm),
          DropdownButtonFormField<String>(
            key: const Key('tx-type'),
            initialValue: _type,
            isExpanded: true,
            hint: const Text('-'),
            decoration: InputDecoration(labelText: c.t('chooseType')),
            items: [
              for (final t in fileableTypes)
                DropdownMenuItem(
                    value: t,
                    child: Text(typeName(t), overflow: TextOverflow.ellipsis)),
            ],
            onChanged: _busy ? null : (v) => setState(() => _type = v),
          ),
          if (!_remember)
            CheckboxListTile(
              key: const Key('tx-learn'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _learnMerchant,
              title: Text(c.t('learn')),
              subtitle: Text(c.t('learnHint'), style: muted),
              onChanged: _busy
                  ? null
                  : (v) => setState(() => _learnMerchant = v ?? false),
            ),
          CheckboxListTile(
            key: const Key('tx-remember'),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _remember,
            title: Text(c.t('remember')),
            onChanged:
                _busy ? null : (v) => setState(() => _remember = v ?? false),
          ),
          if (_remember)
            TextField(
              key: const Key('tx-remember-phrase'),
              controller: _phrase,
              maxLength: 80,
              enabled: !_busy,
              decoration: InputDecoration(
                  labelText: c.t('rememberPhrase'),
                  helperText: c.t('rememberHint'),
                  helperMaxLines: 3),
            ),
        ],
        if (_message != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(_message!,
              key: const Key('tx-correct-message'),
              style: TextStyle(
                  color: _messageError
                      ? context.palette.stop
                      : context.palette.muted)),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xs,
          children: [
            if (editable)
              FilledButton.icon(
                key: const Key('tx-category-save'),
                onPressed: _busy || _unchanged ? null : _save,
                icon: const Icon(Icons.sell_outlined),
                label: Text(_busy
                    ? c.t('saving')
                    : _type != null && _type != row.transactionType
                        ? c.t('save')
                        : c.t('saveCategory')),
              ),
            if (_undoBatch case final batch?)
              OutlinedButton.icon(
                  key: const Key('tx-undo-similar'),
                  onPressed: _busy ? null : () => _undo(batch),
                  icon: const Icon(Icons.undo),
                  label: Text(c.t('undo'))),
            if (editable && row.categorySource == 'manual')
              OutlinedButton.icon(
                  key: const Key('tx-similar'),
                  onPressed: _busy ? null : _similar,
                  icon: const Icon(Icons.auto_fix_high_outlined),
                  label: Text(c.t('similar'))),
            if (editable && row.transactionType == 'uncategorized')
              OutlinedButton.icon(
                  key: const Key('tx-ask-ai'),
                  onPressed: _busy ? null : _askAi,
                  icon: const Icon(Icons.lightbulb_outline),
                  label: Text(c.t('ai'))),
            if (editable)
              OutlinedButton.icon(
                  key: const Key('tx-edit'),
                  onPressed: _busy ? null : _edit,
                  icon: const Icon(Icons.edit_outlined),
                  label: Text(TransactionEditStrings.of(context).t('open'))),
            if (editable && incoming)
              OutlinedButton.icon(
                  key: const Key('tx-money-out'),
                  onPressed: _busy ? null : _moneyOut,
                  icon: const Icon(Icons.north_east),
                  label: Text(c.t('correctOut'))),
          ],
        ),
        const Divider(height: AnumSpacing.lg),
        Text(c.t('receipt'), style: Theme.of(context).textTheme.titleSmall),
        Text(
            d.receipts > 0
                ? c.count('receipts', d.receipts)
                : row.canonicalId != null
                    ? c.t('receiptHint')
                    : c.t('receiptNeedsSave'),
            key: const Key('tx-receipts'),
            style: muted),
        if (row.canonicalId != null && widget.repository.uploads != null)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
                key: const Key('tx-attach'),
                onPressed: _busy ? null : _attach,
                icon: const Icon(Icons.attach_file),
                label: Text(_uploading ? c.t('attaching') : c.t('attach'))),
          ),
      ]),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner(
      {required this.icon,
      required this.tone,
      required this.text,
      this.title,
      this.actions = const [],
      super.key});
  final IconData icon;
  final Color tone;
  final String text;
  final String? title;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Color.alphaBlend(
            tone.withValues(alpha: 0.08), context.palette.surface1),
        borderRadius: BorderRadius.circular(AnumRadius.md),
        border: Border.all(color: tone.withValues(alpha: 0.24)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: tone, size: 20),
          const SizedBox(width: AnumSpacing.sm),
          Expanded(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (title != null)
                    Text(title!, style: Theme.of(context).textTheme.titleSmall),
                  Text(text),
                  if (actions.isNotEmpty)
                    Wrap(spacing: AnumSpacing.xs, children: actions),
                ]),
          ),
        ]),
      ),
    );
  }
}

/// What Apply to similar did: the notice and the batch Undo reverses (null
/// when only the rule was saved).
class SimilarOutcome {
  const SimilarOutcome(this.text, this.batchId);
  final String text;
  final String? batchId;
}

/// Apply to similar (docs/finance.md#rules-and-learning): the rule this
/// correction suggests, every account or only this one, and the rows it
/// would change. Save rule only, or Save and apply as one batch that can be
/// undone; nothing changes before the owner saves.
class SimilarScreen extends StatefulWidget {
  const SimilarScreen(
      {required this.repository, required this.transactionId, super.key});
  final FinanceCoreRepository repository;
  final String transactionId;

  @override
  State<SimilarScreen> createState() => _SimilarScreenState();
}

class _SimilarScreenState extends State<SimilarScreen> {
  bool _thisAccount = false;
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  SimilarProposal? _value;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _phase = RecordsPhase.loading;
      _error = null;
    });
    try {
      _value = await widget.repository
          .similar(widget.transactionId, thisAccount: _thisAccount);
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _save(SimilarProposal p, bool apply) async {
    final c = FinanceCorrectStrings.of(context);
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final done = await widget.repository.saveSimilar(p, apply: apply);
      if (!mounted) return;
      Navigator.of(context).pop(done.batchId == null
          ? SimilarOutcome(c.t('savedOnly'), null)
          : SimilarOutcome(c.t('applied', {'n': done.changed}), done.batchId));
      return;
    } on Object catch (error) {
      _error = c.t('failed', {'message': RecordsFailure.from(error).message});
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = FinanceCorrectStrings.of(context);
    final f = FinanceStrings.of(context);
    final v = _value;
    return Scaffold(
      appBar: AppBar(title: Text(c.t('similarTitle'))),
      body: AnumPage(children: [
        Text(c.t('similarIntro'),
            style: TextStyle(color: context.palette.muted)),
        Text(c.t('scope'), style: Theme.of(context).textTheme.titleSmall),
        SegmentedButton<bool>(
          key: const Key('similar-scope'),
          segments: [
            ButtonSegment(value: false, label: Text(c.t('scope.all'))),
            ButtonSegment(value: true, label: Text(c.t('scope.account'))),
          ],
          selected: {_thisAccount},
          onSelectionChanged: _busy || _phase == RecordsPhase.loading
              ? null
              : (s) {
                  _thisAccount = s.first;
                  _load();
                },
        ),
        ...switch (_phase) {
          RecordsPhase.loading => [
              AnumFeedback(
                  kind: AnumFeedbackKind.loading, message: c.t('loading'))
            ],
          RecordsPhase.denied => [
              AnumFeedback(
                  kind: AnumFeedbackKind.permission, message: c.t('denied'))
            ],
          RecordsPhase.error => [
              AnumFeedback(
                  key: const Key('similar-error'),
                  kind: AnumFeedbackKind.error,
                  message:
                      c.t('loadFailed', {'message': _failure?.message ?? ''}),
                  onRetry: _load)
            ],
          RecordsPhase.ready when v != null => [
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(
                          c.t('rule', {
                            'match': v.content.summary.isEmpty
                                ? '-'
                                : v.content.summary,
                            'direction': c.t('dir.${const {
                              'in',
                              'out'
                            }.contains(v.content.match.direction) ? v.content.match.direction : 'any'}'),
                            'category': f.t('category.${v.content.category}'),
                          }),
                          key: const Key('similar-rule')),
                      const SizedBox(height: AnumSpacing.sm),
                      RulePreviewBlock(v.preview),
                    ]),
              ),
              if (_error != null)
                Text(_error!,
                    key: const Key('similar-save-error'),
                    style: TextStyle(color: context.palette.stop)),
              Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                children: [
                  if (v.preview.changes > 0)
                    FilledButton(
                        key: const Key('similar-apply'),
                        onPressed: _busy ? null : () => _save(v, true),
                        child: Text(v.preview.changes == 1
                            ? c.t('saveApply.one')
                            : c.t('saveApply', {'n': v.preview.changes}))),
                  OutlinedButton(
                      key: const Key('similar-save-only'),
                      onPressed: _busy ? null : () => _save(v, false),
                      child: Text(c.t('saveOnly'))),
                  TextButton(
                      onPressed:
                          _busy ? null : () => Navigator.of(context).pop(),
                      child: Text(c.t('cancel'))),
                ],
              ),
            ],
          RecordsPhase.ready => const [],
        },
      ]),
    );
  }
}
