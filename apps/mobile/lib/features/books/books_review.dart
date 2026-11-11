import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'books_controller.dart';
import 'books_data.dart';
import 'books_screen.dart' show openBill;
import 'books_extra_strings.dart';
import 'books_strings.dart';

/// The Accountant review and supporting documents of the business books on
/// the phone (docs/business-books.md#accountant-review,
/// #supporting-documents-and-evidence). The API decides who may act: the
/// maker of a draft never approves it, and only owners and accountants see
/// the review.

const _kinds = [
  '',
  'draft_bill',
  'adjustment_approval',
  'unconverted',
  'posting_issue',
  'allocation_review',
  'external',
];

/// Everything in these books that waits for an owner or an accountant,
/// with the close checklist of the previous and current month.
class ReviewView extends StatelessWidget {
  const ReviewView({required this.controller, super.key});
  final BooksController controller;

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final k = KitStrings.of(context);
    final list = controller.review;
    final summary = controller.reviewSummary;
    final muted = TextStyle(color: context.palette.muted);
    final children = <Widget>[
      AnumHeader(title: b.t('review.title'), subtitle: b.t('review.intro')),
      if (controller.setup?.permissions.account ?? false)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            key: const Key('adjustment-new'),
            onPressed: () => openAdjustmentForm(context, controller),
            icon: const Icon(Icons.add),
            label: Text(b.t('adj.new')),
          ),
        ),
      RecordsFilterChips(
        options: {
          for (final kind in _kinds)
            kind: [
              b.t('review.kind.${kind.isEmpty ? 'all' : kind}'),
              if (kind.isNotEmpty && (summary?.counts[kind] ?? 0) > 0)
                '(${summary!.counts[kind]})',
            ].join(' ')
        },
        selected: list.filter,
        onSelected: (kind) => list.load(filter: kind),
      ),
    ];
    if (list.phase != RecordsPhase.ready) {
      children.add(RecordsStateView(
          phase: list.phase,
          failure: list.failure,
          deniedHint: b.t('review.denied'),
          onRetry: list.load));
      return AnumPage(onRefresh: list.load, children: children);
    }
    if (summary != null) {
      if (summary.blocking > 0) {
        children.add(Text(b.t('review.blocking', {'n': summary.blocking}),
            key: const Key('review-blocking'),
            style: TextStyle(color: context.palette.warn)));
      }
      if (summary.toPost > 0) {
        children.add(
            Text(b.t('review.toPost', {'n': summary.toPost}), style: muted));
      }
      if (summary.unavailable.isNotEmpty) {
        children.add(Text(
            b.t('review.unavailable',
                {'names': summary.unavailable.join(', ')}),
            style: TextStyle(color: context.palette.warn)));
      }
    }
    if (list.items.isEmpty) {
      children.add(list.narrowed
          ? AnumEmpty(
              title: k.t('noMatch'),
              message: k.t('noMatchText'),
              icon: Icons.search_off,
              action: k.t('clearFilters'),
              onAction: () => list.load(filter: ''))
          : AnumEmpty(
              key: const Key('review-empty'),
              title: b.t('review.empty'),
              message: b.t('review.emptyText'),
              icon: Icons.task_alt,
              action: k.t('retry'),
              onAction: list.load));
    } else {
      for (final item in list.items) {
        children.add(_ReviewCard(item: item, controller: controller));
      }
      children.add(RecordsMore(list: list));
    }
    if (summary != null && summary.months.isNotEmpty) {
      children.add(RecordsSection(
        key: const Key('review-close'),
        title: b.t('review.close'),
        children: [
          for (final m in summary.months)
            AnumRow(
              icon: m.status == 'closed'
                  ? Icons.lock_outline
                  : m.ready
                      ? Icons.check_circle_outline
                      : Icons.pending_outlined,
              tone: m.status == 'closed' || m.ready
                  ? AnumTone.ok
                  : m.status == 'open'
                      ? AnumTone.warn
                      : null,
              title: m.status == 'closed'
                  ? b.t('review.month.closed', {'month': m.month})
                  : m.ready
                      ? b.t('review.month.ready', {'month': m.month})
                      : m.status == 'not_ended'
                          ? b.t('review.month.not_ended', {'month': m.month})
                          : b.t('review.month.open', {
                              'month': m.month,
                              'issues': m.issues,
                              'drafts': m.drafts,
                              'allocations': m.allocations,
                            }),
            ),
        ],
      ));
    }
    return AnumPage(onRefresh: list.load, children: children);
  }
}

class _ReviewCard extends StatelessWidget {
  const _ReviewCard({required this.item, required this.controller});
  final ReviewItem item;
  final BooksController controller;

  bool get _adjustment => item.kind == 'adjustment_approval';

  Future<void> _approve(BuildContext context) async {
    final b = BooksStrings.of(context);
    try {
      await controller.approve(item);
      if (context.mounted) {
        recordsToast(context,
            b.t(_adjustment ? 'review.adjApproved' : 'review.approved'));
      }
    } on Object catch (error) {
      if (context.mounted) {
        recordsToast(context, RecordsFailure.from(error).message);
      }
    }
  }

  Future<void> _reject(BuildContext context) async {
    final b = BooksStrings.of(context);
    final done = await openRecordsForm(
      context,
      RecordsForm(
        title: b.t('review.rejectTitle'),
        intro: b.t('review.rejectIntro'),
        submitLabel: b.t('review.reject'),
        fields: [
          RecordsInput.text('reason', b.t('voidReason'),
              required: true, maxLength: 300),
        ],
        onSubmit: (v) async {
          final reason = (v['reason'] as String?) ?? '';
          if (reason.length < 3) {
            throw FormatException(b.t('voidReason'));
          }
          await controller.reject(item, reason);
        },
      ),
    );
    if (done && context.mounted) recordsToast(context, b.t('review.rejected'));
  }

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final p = item.params;
    final values = <String, Object>{
      ...p,
      'vendor': p['vendor'] ?? '-',
      'currency': item.currency ?? '-',
      'count': p['count'] ?? '0',
      'first': p['first'] == null ? '-' : recordsDate(context, p['first']),
      'description': p['description'] ?? '-',
      'reason': p['reason'] ?? '-',
    };
    if (_adjustment) {
      final kind = p['adjustment_kind'] ?? '';
      values['kind'] = b.maybe('adj.kind.$kind') ?? kind;
    }
    final known = b.maybe('review.head.${item.kind}') != null;
    final head =
        known ? b.t('review.head.${item.kind}', values) : item.detected;
    final why = known ? b.t('review.why.${item.kind}', values) : null;
    final busy = controller.approving == item.key;
    return AnumSurface(
      key: Key('review-${item.key}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
          AnumPill(
              label: b.maybe('review.kind.${item.kind}') ??
                  b.t('review.kind.external')),
          AnumPill(
              label: b.t(item.blocking
                  ? 'review.severity.blocking'
                  : 'review.severity.warning'),
              tone: item.blocking ? AnumTone.stop : AnumTone.warn),
        ]),
        const SizedBox(height: AnumSpacing.xs),
        Text(head, style: Theme.of(context).textTheme.titleSmall),
        if (item.amount != null && item.currency != null)
          Text(
              formatMoneyText(item.amount!, item.currency!,
                  locale: Localizations.localeOf(context)),
              textDirection: TextDirection.ltr),
        if (item.occurredOn != null)
          Text(recordsDate(context, item.occurredOn), style: muted),
        if (why != null) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text(why, style: muted),
        ],
        if (_adjustment) ...[
          const SizedBox(height: AnumSpacing.xs),
          if (!item.canAct)
            Text(b.t('review.ownAdjustment'),
                key: Key('review-own-${item.key}'),
                style: TextStyle(color: context.palette.warn))
          else
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton(
                    key: Key('review-approve-${item.key}'),
                    onPressed: busy || controller.approving != null
                        ? null
                        : () => _approve(context),
                    child: Text(b.t('review.approve')),
                  ),
                  OutlinedButton(
                    key: Key('review-reject-${item.key}'),
                    onPressed: busy || controller.approving != null
                        ? null
                        : () => _reject(context),
                    child: Text(b.t('review.reject')),
                  ),
                ]),
        ],
        if (item.kind == 'receipt_match')
          _ReceiptActions(item: item, controller: controller),
        if (item.kind == 'draft_bill') ...[
          const SizedBox(height: AnumSpacing.xs),
          if (!item.canAct)
            Text(b.t('review.ownDraft'),
                key: Key('review-own-${item.key}'),
                style: TextStyle(color: context.palette.warn)),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
            if (item.canAct && item.action == 'approve')
              FilledButton(
                key: Key('review-approve-${item.key}'),
                onPressed: busy || controller.approving != null
                    ? null
                    : () => _approve(context),
                child: Text(b.t('review.approve')),
              ),
            if (item.refId != null)
              OutlinedButton(
                key: Key('review-open-${item.key}'),
                onPressed: () => openBill(context, controller, item.refId!),
                child: Text(b.t('review.openBill')),
              ),
          ]),
        ],
      ]),
    );
  }
}

/// The supporting documents of one record: what is attached (from the
/// Document vault), whether it is missing one, attach a workspace document
/// and remove one. The vault's own rules apply on the server.
class EvidenceSection extends StatefulWidget {
  const EvidenceSection({
    required this.repository,
    required this.sourceType,
    required this.sourceId,
    this.onChanged,
    this.missingText,
    super.key,
  });
  final BooksRepository repository;
  final String sourceType, sourceId;
  final void Function(bool missing)? onChanged;

  /// Why this record needs a document, when it is not a bill.
  final String? missingText;

  @override
  State<EvidenceSection> createState() => _EvidenceSectionState();
}

class _EvidenceSectionState extends State<EvidenceSection> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<EvidenceDoc> _docs = const [];
  bool _missing = false, _canAttach = false, _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final page = await widget.repository
          .evidence(widget.sourceType, [widget.sourceId]);
      final entry = page.items[widget.sourceId];
      _docs = entry?.documents ?? const [];
      _missing = entry?.missing ?? false;
      _canAttach = page.canAttach;
      _failure = null;
      _phase = RecordsPhase.ready;
      widget.onChanged?.call(_missing);
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _change(Future<void> Function() action, String done) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted) recordsToast(context, done);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
    if (mounted) setState(() => _busy = false);
    await _load();
  }

  Future<void> _attach() async {
    final b = BooksStrings.of(context);
    final picked = await showRecordsSheet<AttachableDoc>(
        context, (_) => _DocumentPicker(repository: widget.repository));
    if (picked == null || !mounted) return;
    await _change(
        () => widget.repository
            .attach(widget.sourceType, widget.sourceId, picked.id),
        b.t('evidence.attached'));
  }

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final List<Widget> body = switch (_phase) {
      RecordsPhase.loading => [
          AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: KitStrings.of(context).t('loading'))
        ],
      RecordsPhase.denied => [
          Text(b.t('evidence.denied'),
              key: const Key('evidence-denied'), style: muted)
        ],
      RecordsPhase.error => [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message:
                  b.t('evidence.failed', {'message': _failure?.message ?? ''}),
              onRetry: _load)
        ],
      RecordsPhase.ready => [
          if (_missing) ...[
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: AnumPill(
                  key: const Key('bill-evidence-missing'),
                  icon: Icons.report_gmailerrorred_outlined,
                  label: b.t('evidence.missing'),
                  tone: AnumTone.warn),
            ),
            Text(
                widget.missingText ??
                    b.maybe('evidence.missingText.${widget.sourceType}') ??
                    b.t('evidence.missingText'),
                style: muted),
          ],
          if (_docs.isEmpty)
            Text(b.t('evidence.none'), style: muted)
          else
            for (final doc in _docs)
              AnumRow(
                key: Key('evidence-${doc.linkId}'),
                icon: Icons.description_outlined,
                title: doc.title,
                subtitle: [
                  if (doc.category.isNotEmpty) doc.category,
                  if (doc.addedAt != null) recordsDate(context, doc.addedAt),
                ].join(' · '),
                trailing: _canAttach
                    ? IconButton(
                        key: Key('evidence-remove-${doc.linkId}'),
                        tooltip: b.t('evidence.remove', {'title': doc.title}),
                        icon: const Icon(Icons.link_off),
                        onPressed: _busy
                            ? null
                            : () => _change(
                                () => widget.repository.detach(
                                    widget.sourceType,
                                    widget.sourceId,
                                    doc.linkId),
                                widget.sourceType == 'bill'
                                    ? b.t('evidence.removed')
                                    : b.t('evidence.removedOther')),
                      )
                    : null,
              ),
          if (_canAttach)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                key: const Key('evidence-attach'),
                onPressed: _busy ? null : _attach,
                icon: const Icon(Icons.attach_file),
                label: Text(b.t('evidence.attach')),
              ),
            )
          else
            Text(b.t('evidence.cannotAttach'), style: muted),
        ],
    };
    return RecordsSection(
      key: const Key('evidence-section'),
      title: b.t('evidence.title'),
      children: body,
    );
  }
}

/// Workspace documents of the vault, searchable and paged; picking one
/// pops it.
class _DocumentPicker extends StatefulWidget {
  const _DocumentPicker({required this.repository});
  final BooksRepository repository;

  @override
  State<_DocumentPicker> createState() => _DocumentPickerState();
}

class _DocumentPickerState extends State<_DocumentPicker> {
  late final RecordsList<AttachableDoc> _list = RecordsList<AttachableDoc>(
      (query, _, token) =>
          widget.repository.documents(query: query, token: token));

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

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    return ListenableBuilder(
      listenable: _list,
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            header: true,
            child: Text(b.t('evidence.pickTitle'),
                style: Theme.of(context).textTheme.titleLarge),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Text(b.t('evidence.pickIntro'),
              style: TextStyle(color: context.palette.muted)),
          const SizedBox(height: AnumSpacing.sm),
          RecordsSearchField(
              initial: _list.query,
              hint: b.t('evidence.pickSearch'),
              onSearch: (q) => _list.load(query: q)),
          const SizedBox(height: AnumSpacing.sm),
          if (_list.phase != RecordsPhase.ready)
            RecordsStateView(
                phase: _list.phase,
                failure: _list.failure,
                deniedHint: b.t('evidence.denied'),
                onRetry: _list.load)
          else if (_list.items.isEmpty)
            AnumEmpty(
                key: const Key('picker-empty'),
                icon: Icons.folder_open_outlined,
                title: b.t('evidence.pickEmpty'),
                message: b.t('evidence.pickEmptyText'))
          else ...[
            for (final doc in _list.items)
              AnumRow(
                key: Key('pick-${doc.id}'),
                icon: Icons.description_outlined,
                title: doc.title,
                subtitle: doc.category,
                onTap: () => Navigator.of(context).pop(doc),
              ),
            RecordsMore(list: _list),
          ],
        ],
      ),
    );
  }
}

/// Records a journal adjustment from the phone (owners and accountants).
/// The form says beforehand whether it is posted at once or waits for a
/// second approval (docs/business-books.md#adjustment-approval); the API
/// decides, converting with the business's own rates.
Future<void> openAdjustmentForm(
    BuildContext context, BooksController controller) async {
  final b = BooksStrings.of(context);
  final setup = controller.setup;
  final base = setup?.baseCurrency ?? '';
  final threshold = setup?.approvalThreshold;
  final String note;
  if (setup?.permissions.manage ?? false) {
    note = b.t('adj.ownerNote');
  } else if (threshold != null) {
    note = b.t('adj.approvalNote', {
      'amount': formatMoneyText(threshold, base,
          locale: Localizations.localeOf(context)),
    });
  } else {
    note = b.t('adj.noApproval');
  }
  final key = newIdempotencyKey('adj');
  BooksAdjustment? saved;
  final done = await openRecordsForm(
    context,
    RecordsForm(
      key: const Key('adjustment-form'),
      title: b.t('adj.new'),
      intro: '$note ${b.t('adj.trail')}',
      submitLabel: b.t('adj.record'),
      fields: [
        RecordsInput.choice('kind', b.t('adj.kind'),
            options: {for (final k in adjustmentKinds) k: b.t('adj.kind.$k')},
            initial: 'accrual'),
        RecordsInput.date('entry_date', b.t('adj.date'),
            required: true, initial: isoDay(DateTime.now())),
        RecordsInput.text('currency', b.t('adj.currency'),
            required: true, maxLength: 3, initial: base),
        RecordsInput.number('amount', b.t('adj.amount'), required: true),
        RecordsInput.text('description', b.t('adj.why'),
            required: true, maxLength: 300),
        RecordsInput.date('start_month', b.t('adj.start'),
            help: b.t('adj.startHelp')),
        RecordsInput.number('months', b.t('adj.months')),
        RecordsInput.date('reverse_on', b.t('adj.reverse')),
      ],
      onSubmit: (v) async {
        final kind = v['kind'] as String? ?? 'accrual';
        final amount = v['amount'] as String?;
        if (amount == null || (double.tryParse(amount) ?? 0) <= 0) {
          throw FormatException(b.t('adj.badAmount'));
        }
        final currency = (v['currency'] as String? ?? '').toUpperCase();
        if (!RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
          throw FormatException(b.t('adj.badCurrency'));
        }
        final description = v['description'] as String? ?? '';
        if (description.length < 3) {
          throw FormatException(b.t('adj.badDescription'));
        }
        final spread = adjustmentSpreads(kind);
        final start = v['start_month'] as String?;
        final months = int.tryParse(v['months'] as String? ?? '');
        if (spread && (start == null || months == null || months < 1)) {
          throw FormatException(b.t('adj.needSpread'));
        }
        saved = await controller.recordAdjustment({
          'kind': kind,
          'entry_date': v['entry_date'],
          'currency': currency,
          'amount': amount,
          'description': description,
          'start_month': spread ? '${start!.substring(0, 8)}01' : null,
          'months': spread ? months : null,
          'reverse_on': kind == 'accrual' ? v['reverse_on'] : null,
        }, idempotencyKey: key);
      },
    ),
  );
  if (done && context.mounted) {
    recordsToast(
        context, b.t((saved?.pending ?? false) ? 'adj.pending' : 'adj.posted'));
  }
}

/// Attach or Dismiss on a receipt proposed for a bill
/// (docs/business-books.md#receipts-to-bills): Attach for whoever may add
/// supporting documents, Dismiss for owners and accountants, each refusal
/// in the API's words, and the review reloads after either.
class _ReceiptActions extends StatefulWidget {
  const _ReceiptActions({required this.item, required this.controller});
  final ReviewItem item;
  final BooksController controller;

  @override
  State<_ReceiptActions> createState() => _ReceiptActionsState();
}

class _ReceiptActionsState extends State<_ReceiptActions> {
  bool _busy = false;

  Future<void> _run(bool attach) async {
    final x = BooksExtraStrings.of(context);
    final more = widget.controller.more;
    final bill = widget.item.params['bill_id'] ?? widget.item.refId;
    final document = widget.item.params['document_id'];
    if (more == null || bill == null || document == null) return;
    if (!attach &&
        !await confirmRecords(context,
            title: x.t('rcpt.dismissTitle'),
            message: x.t('rcpt.dismissText'),
            action: x.t('rcpt.dismiss'))) {
      return;
    }
    setState(() => _busy = true);
    try {
      if (attach) {
        await more.acceptReceipt(bill, document);
      } else {
        await more.dismissReceipt(bill, document);
      }
      if (!mounted) return;
      recordsToast(context, x.t(attach ? 'rcpt.attached' : 'rcpt.dismissed'));
      await widget.controller.review.load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final x = BooksExtraStrings.of(context);
    final b = BooksStrings.of(context);
    final account = widget.controller.setup?.permissions.account ?? false;
    final key = widget.item.key;
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xs),
      child:
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
        FilledButton(
          key: Key('review-attach-$key'),
          onPressed: _busy ? null : () => _run(true),
          child: Text(x.t('rcpt.attach')),
        ),
        if (account)
          OutlinedButton(
            key: Key('review-dismiss-$key'),
            onPressed: _busy ? null : () => _run(false),
            child: Text(x.t('rcpt.dismiss')),
          ),
        if (widget.item.refId != null)
          TextButton(
            key: Key('review-open-$key'),
            onPressed: () =>
                openBill(context, widget.controller, widget.item.refId!),
            child: Text(b.t('review.openBill')),
          ),
      ]),
    );
  }
}
