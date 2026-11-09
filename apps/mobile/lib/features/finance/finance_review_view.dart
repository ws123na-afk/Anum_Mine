import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'finance_controller.dart';
import 'finance_models.dart';
import 'finance_personal_view.dart' show MoneyLinkOpener, financeWebLink;
import 'finance_review_models.dart';
import 'finance_review_strings.dart';
import 'finance_strings.dart';

/// The review side of Finance on the phone (docs/finance.md): filing an
/// uncategorized row, the suggestion queue, the last statement import and
/// reconciliation status. The phone shows what matters and acts on it;
/// rules, matching rows and completing a reconciliation stay on the web.

/// Lays [parts] out one under the other with the shared spacing.
Widget _stack(List<Widget> parts) => Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < parts.length; i++) ...[
          parts[i],
          if (i < parts.length - 1) const SizedBox(height: AnumSpacing.md),
        ],
      ],
    );

/// Loading, permission denied or failed for one card, or null when ready.
Widget? _waiting(FinanceLoad state, bool hasData, String? error,
    VoidCallback retry, FinanceStrings f,
    {required String loading, required String failed}) {
  if ((state == FinanceLoad.idle || state == FinanceLoad.loading) && !hasData) {
    return AnumFeedback(kind: AnumFeedbackKind.loading, message: loading);
  }
  return switch (state) {
    FinanceLoad.denied =>
      AnumFeedback(kind: AnumFeedbackKind.permission, message: f.t('denied')),
    FinanceLoad.error => AnumFeedback(
        kind: AnumFeedbackKind.error, message: failed, onRetry: retry),
    _ => null,
  };
}

/// A web Finance page ([fragment], for example `finance-reconciliation`) as
/// a button that opens the browser, or [fallback] as plain text when the
/// build does not know the web app's address.
class FinanceWebLink extends StatelessWidget {
  const FinanceWebLink(
      {required this.appUrl,
      required this.openLink,
      required this.fragment,
      required this.label,
      this.fallback,
      super.key});
  final String appUrl, fragment, label;

  /// Shown when there is no link; nothing when null.
  final String? fallback;
  final MoneyLinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final link = financeWebLink(appUrl, fragment);
    if (link == null) {
      final text = fallback;
      return text == null
          ? const SizedBox.shrink()
          : Text(text, style: TextStyle(color: context.palette.muted));
    }
    final failed = FinanceReviewStrings.of(context).t('web.linkFailed');
    return Align(
      alignment: AlignmentDirectional.centerStart,
      child: TextButton.icon(
        onPressed: () async {
          final messenger = ScaffoldMessenger.maybeOf(context);
          if (!await openLink(link)) {
            messenger?.showSnackBar(SnackBar(content: Text(failed)));
          }
        },
        icon: const Icon(Icons.open_in_new),
        label: Text(label),
      ),
    );
  }
}

// -- transactions: the uncategorized notice and filing -----------------------

/// "N uncategorized, not counted" with Show them, or, while the list shows
/// only those rows, a line saying so with Show all.
class UncategorizedNotice extends StatelessWidget {
  const UncategorizedNotice({required this.controller, super.key});
  final FinanceController controller;

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final c = controller;
    if (c.typeFilter != null) {
      return Wrap(
        spacing: AnumSpacing.sm,
        runSpacing: AnumSpacing.xs,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(s.t('tx.filterOn'),
              style: TextStyle(color: context.palette.muted)),
          TextButton(
              key: const Key('tx-show-all'),
              onPressed: () => c.showUncategorized(false),
              child: Text(s.t('tx.showAll'))),
        ],
      );
    }
    if (c.uncategorized <= 0) return const SizedBox.shrink();
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.warning_amber_outlined, color: context.palette.warn),
          const SizedBox(width: AnumSpacing.sm),
          Expanded(
              child: Text(s.t('tx.uncategorized', {'n': c.uncategorized}))),
        ]),
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: TextButton(
              key: const Key('tx-show-uncategorized'),
              onPressed: () => c.showUncategorized(true),
              child: Text(s.t('tx.showThem'))),
        ),
      ]),
    );
  }
}

/// The type label of a transaction, or null when the API sent none.
String? typeLabel(FinanceReviewStrings s, String? type) =>
    type == null ? null : s.maybe('type.$type') ?? type;

/// Opens the filing dialog for [row]; true when it was saved.
Future<bool> fileTransaction(
    BuildContext context, FinanceController c, FinanceTransaction row) async {
  final saved = await showDialog<String>(
    context: context,
    builder: (_) => _FileDialog(controller: c, row: row),
  );
  if (saved == null || !context.mounted) return false;
  final s = FinanceReviewStrings.of(context);
  ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(
      content: Text(s.t('tx.filed', {'type': typeLabel(s, saved) ?? saved}))));
  return true;
}

class _FileDialog extends StatefulWidget {
  const _FileDialog({required this.controller, required this.row});
  final FinanceController controller;
  final FinanceTransaction row;

  @override
  State<_FileDialog> createState() => _FileDialogState();
}

class _FileDialogState extends State<_FileDialog> {
  late String? _type = widget.row.moneyIn ? 'income' : 'expense';
  String? _category;
  String? _error;
  bool _busy = false;

  Future<void> _save() async {
    final type = _type;
    if (type == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    final failed = await widget.controller
        .fileTransaction(widget.row, type: type, category: _category);
    if (!mounted) return;
    if (failed != null) {
      setState(() {
        _busy = false;
        _error = failed;
      });
      return;
    }
    Navigator.of(context).pop(type);
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final f = FinanceStrings.of(context);
    final row = widget.row;
    return AlertDialog(
      title: Text(s.t('tx.fileTitle')),
      scrollable: true,
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(row.description, textDirection: TextDirection.ltr),
          Text(formatAmount(row.amount, row.currency),
              textDirection: TextDirection.ltr,
              style: TextStyle(color: context.palette.muted)),
          const SizedBox(height: AnumSpacing.sm),
          Text(s.t('tx.fileIntro')),
          const SizedBox(height: AnumSpacing.md),
          DropdownButtonFormField<String>(
            key: const Key('file-type'),
            initialValue: _type,
            isExpanded: true,
            decoration: InputDecoration(labelText: s.t('tx.type')),
            items: [
              for (final type in fileableTypes)
                DropdownMenuItem(
                    value: type,
                    child: Text(s.t('type.$type'),
                        overflow: TextOverflow.ellipsis)),
            ],
            onChanged: _busy ? null : (v) => setState(() => _type = v),
          ),
          const SizedBox(height: AnumSpacing.sm),
          DropdownButtonFormField<String?>(
            key: const Key('file-category'),
            initialValue: _category,
            isExpanded: true,
            decoration: InputDecoration(labelText: s.t('tx.category')),
            items: [
              DropdownMenuItem<String?>(
                  value: null,
                  child: Text(s.t('tx.categoryAuto'),
                      overflow: TextOverflow.ellipsis)),
              for (final category in fileableCategories)
                DropdownMenuItem<String?>(
                    value: category,
                    child: Text(f.t('category.$category'),
                        overflow: TextOverflow.ellipsis)),
            ],
            onChanged: _busy ? null : (v) => setState(() => _category = v),
          ),
          if (_error != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(_error!,
                key: const Key('file-error'),
                style: TextStyle(color: context.palette.stop)),
          ],
        ],
      ),
      actions: [
        TextButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(),
            child: Text(s.t('tx.cancel'))),
        FilledButton(
            key: const Key('file-save'),
            onPressed: _busy || _type == null ? null : _save,
            child: Text(s.t('tx.save'))),
      ],
    );
  }
}

// -- suggestions ---------------------------------------------------------------

/// The suggestion queue: what was found, why, the money, how sure, and
/// Accept or Dismiss where the phone can decide. Rules and automation are
/// edited on the web (docs/finance.md#suggestions).
class SuggestionsView extends StatelessWidget {
  const SuggestionsView(
      {required this.controller,
      required this.appUrl,
      required this.openLink,
      required this.onOpenTransactions,
      super.key});
  final FinanceController controller;
  final String appUrl;
  final MoneyLinkOpener openLink;
  final VoidCallback onOpenTransactions;

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final f = FinanceStrings.of(context);
    final c = controller;
    final muted = TextStyle(color: context.palette.muted);
    final waiting = _waiting(
        c.suggestionsState,
        c.suggestionsState == FinanceLoad.ready,
        c.suggestionsError,
        c.loadSuggestions,
        f,
        loading: s.t('sug.loading'),
        failed: s.t('sug.failed', {'message': c.suggestionsError ?? ''}));
    if (waiting != null) return waiting;
    return _stack([
      Text(s.t('sug.intro'), style: muted),
      if (c.suggestionsUnavailable.isNotEmpty)
        Text(
            s.t('sug.unavailable',
                {'names': c.suggestionsUnavailable.join(', ')}),
            style: TextStyle(color: context.palette.warn)),
      if (c.suggestions.isEmpty)
        AnumEmpty(
          title: s.t('sug.empty'),
          message: s.t('sug.emptyText'),
          icon: Icons.inbox_outlined,
          action: s.t('sug.emptyAction'),
          onAction: onOpenTransactions,
        )
      else
        for (final item in c.suggestions)
          _SuggestionCard(
              item: item, controller: c, appUrl: appUrl, openLink: openLink),
      if (c.suggestionsError != null && c.suggestions.isNotEmpty)
        Text(s.t('sug.moreFailed', {'message': c.suggestionsError!}),
            style: TextStyle(color: context.palette.stop)),
      if (c.hasMoreSuggestions)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton(
            onPressed: c.loadingMoreSuggestions ? null : c.loadMoreSuggestions,
            child: Text(s.t('sug.more',
                {'shown': c.suggestions.length, 'total': c.suggestionsTotal})),
          ),
        ),
      Text(s.t('sug.rulesOnWeb'), style: muted),
      FinanceWebLink(
          appUrl: appUrl,
          openLink: openLink,
          fragment: 'finance-suggestions',
          label: s.t('web.finish')),
    ]);
  }
}

class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard(
      {required this.item,
      required this.controller,
      required this.appUrl,
      required this.openLink});
  final Suggestion item;
  final FinanceController controller;
  final String appUrl;
  final MoneyLinkOpener openLink;

  Map<String, Object> _vars(FinanceReviewStrings s, FinanceStrings f) {
    final p = item.params;
    String category(String? value) =>
        value == null ? '' : f.maybe('category.$value') ?? value;
    return {
      ...p,
      'category': category(p['category']),
      'current': category(p['current']),
      if (item.kind == 'reconciliation')
        'exception': s.maybe('reconKind.${p['exception']}') ?? '',
    };
  }

  String _head(FinanceReviewStrings s, FinanceStrings f) =>
      s.t('sug.head.${item.kind}', _vars(s, f));

  String _why(FinanceReviewStrings s, FinanceStrings f) {
    if (item.kind == 'external') return item.why;
    if (item.kind == 'reconciliation') {
      final p = item.params;
      if (p['exception'] == 'missing_period') {
        return s
            .t('sug.why.reconciliation.missing', {'month': p['month'] ?? ''});
      }
      return s.t(
          p['severity'] == 'blocking'
              ? 'sug.why.reconciliation.blocking'
              : 'sug.why.reconciliation.warning',
          {'start': p['start'] ?? '', 'end': p['end'] ?? ''});
    }
    return s.maybe('sug.why.${item.kind}') == null
        ? item.why
        : s.t('sug.why.${item.kind}', _vars(s, f));
  }

  Future<void> _decide(BuildContext context, bool accept) async {
    final s = FinanceReviewStrings.of(context);
    final messenger = ScaffoldMessenger.maybeOf(context);
    final result = await controller.decideSuggestion(item, accept: accept);
    final text = result.error ??
        (!accept
            ? s.t('sug.dismissed')
            : result.changed != null
                ? s.t('sug.accepted', {'n': result.changed!})
                : s.t('sug.acknowledged'));
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final f = FinanceStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final busy = controller.deciding != null;
    final mode = item.acceptMode;
    final percent = item.percent;
    final description = item.params['description'];
    final href = item.href?.replaceFirst('#', '');
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            AnumPill(label: s.maybe('sug.kind.${item.kind}') ?? item.kind),
            if (percent != null)
              AnumPill(
                  label: s.t('sug.confidence', {'percent': percent}),
                  tone: percent >= 80 ? AnumTone.ok : AnumTone.warn),
            if (item.occurredOn != null)
              Text(item.occurredOn!,
                  textDirection: TextDirection.ltr, style: muted),
          ],
        ),
        const SizedBox(height: AnumSpacing.xs),
        Text(_head(s, f), style: Theme.of(context).textTheme.titleSmall),
        if (description != null && description.isNotEmpty)
          Text(description, textDirection: TextDirection.ltr, style: muted),
        const SizedBox(height: AnumSpacing.xxs),
        Text(_why(s, f)),
        if (item.effects.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text(
              item.effects
                  .map((e) => formatAmount(e.signed, e.currency))
                  .join(' · '),
              textDirection: TextDirection.ltr,
              style: Theme.of(context).textTheme.titleSmall),
        ],
        const SizedBox(height: AnumSpacing.xs),
        Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xs,
          children: [
            if (mode != 'none')
              FilledButton(
                key: Key('sug-accept-${item.key}'),
                onPressed: busy ? null : () => _decide(context, true),
                child: Text(s.t(mode == 'apply' ? 'sug.accept' : 'sug.seen')),
              ),
            OutlinedButton(
              key: Key('sug-dismiss-${item.key}'),
              onPressed: busy ? null : () => _decide(context, false),
              child: Text(s.t('sug.dismiss')),
            ),
          ],
        ),
        if (mode == 'none')
          FinanceWebLink(
              appUrl: appUrl,
              openLink: openLink,
              fragment:
                  href == null || href.isEmpty ? 'finance-suggestions' : href,
              label: s.t('web.finish'),
              fallback: s.t('sug.openOnWeb')),
      ]),
    );
  }
}

// -- the last statement import -----------------------------------------------

/// The newest statement import's counts, read only: imported, held,
/// attached to a recorded payment and not in the ledger. Rows not in the
/// ledger are finished on the web.
class LastImportCard extends StatelessWidget {
  const LastImportCard(
      {required this.controller,
      required this.appUrl,
      required this.openLink,
      super.key});
  final FinanceController controller;
  final String appUrl;
  final MoneyLinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final f = FinanceStrings.of(context);
    final c = controller;
    final muted = TextStyle(color: context.palette.muted);
    final waiting = _waiting(c.importState, c.importState == FinanceLoad.ready,
        c.importError, c.loadLastImport, f,
        loading: s.t('import.loading'),
        failed: s.t('import.failed', {'message': c.importError ?? ''}));
    if (waiting != null) return waiting;
    final last = c.lastImport;
    if (last == null) {
      return AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('import.none')),
          Text(s.t('import.noneText'), style: muted),
          FinanceWebLink(
              appUrl: appUrl,
              openLink: openLink,
              fragment: 'finance-import',
              label: s.t('web.finish')),
        ]),
      );
    }
    final account =
        c.accounts.where((a) => a.id == last.accountId).firstOrNull?.name;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          eyebrow: s.t('import.title'),
          title: last.filename,
          subtitle: [
            if (last.createdAt.length >= 10) last.createdAt.substring(0, 10),
            if (account != null) account,
          ].join(' · '),
        ),
        AnumRow(
            icon: Icons.download_done_outlined,
            tone: AnumTone.ok,
            title: s.t('import.imported'),
            detail: '${last.imported}'),
        AnumRow(
            icon: Icons.link,
            title: s.t('import.attached'),
            detail: '${last.attached}'),
        AnumRow(
            icon: Icons.hourglass_empty,
            tone: last.held > 0 ? AnumTone.warn : null,
            title: s.t('import.held'),
            detail: '${last.held}'),
        AnumRow(
            icon: Icons.report_gmailerrorred_outlined,
            tone: last.notInLedger > 0 ? AnumTone.warn : null,
            title: s.t('import.notInLedger'),
            detail: '${last.notInLedger}'),
        if (last.status != null)
          Text(s.maybe('import.status.${last.status}') ?? '', style: muted),
        if (last.notInLedger > 0) ...[
          Text(s.t('import.notInLedgerText'),
              style: TextStyle(color: context.palette.warn)),
          FinanceWebLink(
              appUrl: appUrl,
              openLink: openLink,
              fragment: 'finance-connections',
              label: s.t('web.finish')),
        ] else
          Text(s.t('import.allIn'), style: muted),
      ]),
    );
  }
}

// -- reconciliation ------------------------------------------------------------

/// Reconciliation status per bank and card account, read only: reconciled
/// through, the open session with its unexplained difference, months with
/// no statement and the next step. Finished on the web.
class ReconciliationCard extends StatelessWidget {
  const ReconciliationCard(
      {required this.controller,
      required this.appUrl,
      required this.openLink,
      super.key});
  final FinanceController controller;
  final String appUrl;
  final MoneyLinkOpener openLink;

  String _next(FinanceReviewStrings s, ReconHealth h) {
    final month = h.nextMonth ?? '';
    return switch (h.nextStep) {
      'import_statement' => s.t(
          h.nextMonth == null ? 'recon.next.importAny' : 'recon.next.import',
          {'month': month}),
      'reconcile' => s.t('recon.next.reconcile', {'month': month}),
      'finish_reconciliation' => s.t('recon.next.finish', {'month': month}),
      'review_changes' => s.t('recon.next.review', {'month': month}),
      'up_to_date' => s.t('recon.next.upToDate'),
      _ => s.t('recon.next.none'),
    };
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final f = FinanceStrings.of(context);
    final c = controller;
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final waiting = _waiting(
        c.reconState, c.recon != null, c.reconError, c.loadReconciliation, f,
        loading: s.t('recon.loading'),
        failed: s.t('recon.failed', {'message': c.reconError ?? ''}));
    if (waiting != null) return waiting;
    final accounts = c.recon!.reconcilable;
    final web = FinanceWebLink(
        appUrl: appUrl,
        openLink: openLink,
        fragment: 'finance-reconciliation',
        label: s.t('web.finish'),
        fallback: s.t('recon.onWeb'));
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(title: s.t('recon.title'), subtitle: s.t('recon.intro')),
        if (accounts.isEmpty) ...[
          Text(s.t('recon.empty'),
              style: Theme.of(context).textTheme.titleSmall),
          Text(s.t('recon.emptyText'), style: muted),
        ] else
          for (final h in accounts) ...[
            const Divider(height: AnumSpacing.lg),
            _ReconAccount(h: h, open: c.recon!.openFor(h), next: _next(s, h)),
          ],
        const SizedBox(height: AnumSpacing.xs),
        web,
      ]),
    );
  }
}

class _ReconAccount extends StatelessWidget {
  const _ReconAccount(
      {required this.h, required this.open, required this.next});
  final ReconHealth h;
  final ReconSession? open;
  final String next;

  @override
  Widget build(BuildContext context) {
    final s = FinanceReviewStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final session = open;
    final attention = h.nextStep == 'review_changes' ||
        h.nextStep == 'finish_reconciliation' ||
        h.missingMonths.isNotEmpty;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      AnumRow(
        icon: h.kind == 'card'
            ? Icons.credit_card_outlined
            : Icons.account_balance_outlined,
        tone: attention ? AnumTone.warn : AnumTone.ok,
        title: h.accountName,
        subtitle: h.reconciledThrough == null
            ? s.t('recon.never')
            : s.t('recon.through', {'date': h.reconciledThrough!}),
      ),
      if (session != null) ...[
        Text(s.t('recon.open',
            {'start': session.periodStart, 'end': session.periodEnd})),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          children: [
            AnumPill(
              label: session.canComplete
                  ? s.t('recon.balanced')
                  : s.t('recon.unexplained', {
                      'amount':
                          formatAmount(session.unexplained, session.currency)
                    }),
              tone: session.canComplete ? AnumTone.ok : AnumTone.warn,
            ),
            if (session.exceptions > 0)
              AnumPill(
                  label: s.t('recon.items', {'n': session.exceptions}),
                  tone:
                      session.blocking > 0 ? AnumTone.stop : AnumTone.neutral),
          ],
        ),
      ],
      if (h.missingMonths.isNotEmpty)
        Text(
            s.t('recon.missing',
                {'months': h.missingMonths.take(4).join(', ')}),
            style: TextStyle(color: p.warn)),
      Text(next, style: muted),
    ]);
  }
}
