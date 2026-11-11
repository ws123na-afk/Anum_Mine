import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_strings.dart';
import 'finance_tools_data.dart';
import 'finance_tools_strings.dart';

/// Reconciliation on the phone, the owner's part
/// (docs/finance.md#reconciliation-sessions): starting a session from an
/// imported statement or typed balances, matching a statement row to a
/// record, a correcting entry approved with the payload hash it was shown,
/// and the reason a completed session is reopened with. Nothing here forces
/// a balance: a difference nobody explained keeps the session unresolved.

/// Starts a session: the account first (its imports are offered next), then
/// the statement's period and balances. Answers the new session.
Future<ReconDetail?> startReconciliation(
    BuildContext context, FinanceMoreRepository repository) async {
  final t = FinanceToolsStrings.of(context);
  final List<FinanceAccount> accounts;
  final List<StatementImport> imports;
  try {
    accounts = [
      for (final a in await repository.financeAccounts())
        if (!a.archived && a.kind != 'investment') a
    ];
    imports = (await repository.statementImports(limit: 100)).items;
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return null;
  }
  if (!context.mounted) return null;
  if (accounts.isEmpty) {
    await showDialog<void>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(t.t('rc.noAccounts')),
        content: Text(t.t('rc.noAccountsText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog),
              child: Text(t.t('close'))),
        ],
      ),
    );
    return null;
  }
  final account = await pickAccount(context, accounts, t.t('rc.pickAccount'));
  if (account == null || !context.mounted) return null;
  final owes = account.kind == 'card' || account.kind == 'loan';
  final card = account.kind == 'card';
  final mine = imports.where((i) => i.accountId == account.id).toList();
  final period = lastMonth(DateTime.now());
  ReconDetail? made;
  String? figure(RecordsValues v, String key, String label) {
    final typed = v[key] as String?;
    if (typed == null) return null;
    final parsed = parseAmount(ledgerFigure(account.kind, typed), signed: true);
    if (parsed == null) {
      throw FormatException(t.t('rc.badAmount', {'field': label}));
    }
    return parsed;
  }

  String? plain(RecordsValues v, String key, String label) {
    final typed = v[key] as String?;
    if (typed == null) return null;
    final parsed = parseAmount(typed, signed: false);
    if (parsed == null) {
      throw FormatException(t.t('rc.badAmount', {'field': label}));
    }
    return parsed;
  }

  final c = account.currency;
  final openingLabel =
      t.t(owes ? 'rc.openingOwed' : 'rc.opening', {'currency': c});
  final closingLabel =
      t.t(owes ? 'rc.closingOwed' : 'rc.closing', {'currency': c});
  await openRecordsForm(
    context,
    RecordsForm(
      title: t.t('rc.start'),
      intro: [
        account.name,
        t.t('rc.startIntro'),
        if (owes) t.t('rc.owedHelp'),
      ].join('\n\n'),
      submitLabel: t.t('rc.startButton'),
      fields: [
        if (mine.isNotEmpty)
          RecordsInput.choice('import', t.t('rc.statement'),
              options: {
                '': t.t('rc.typed'),
                for (final i in mine)
                  i.id: '${i.filename} (${i.createdAt.split('T').first})',
              },
              required: false,
              initial: '',
              help: t.t('rc.fromImport')),
        RecordsInput.date('start', t.t('rc.from'),
            initial: period.$1, help: t.t('rc.periodHelp')),
        RecordsInput.date('end', t.t('rc.to'), initial: period.$2),
        RecordsInput.text('opening', openingLabel),
        RecordsInput.text('closing', closingLabel),
        if (card) ...[
          RecordsInput.text('due', t.t('rc.due', {'currency': c})),
          RecordsInput.text('minimum', t.t('rc.minimum', {'currency': c})),
          RecordsInput.date('due_on', t.t('rc.dueOn')),
        ],
      ],
      onSubmit: (v) async {
        final importId = (v['import'] as String?) ?? '';
        final fromImport = importId.isNotEmpty;
        if (!fromImport && (v['start'] == null || v['end'] == null)) {
          throw FormatException(t.t('rc.periodNeeded'));
        }
        if (!fromImport && v['closing'] == null) {
          throw FormatException(t.t('rc.closingNeeded'));
        }
        made = await repository.startRecon({
          'account_id': account.id,
          'import_id': fromImport ? importId : null,
          'period_start': fromImport ? null : v['start'],
          'period_end': fromImport ? null : v['end'],
          'statement_opening': figure(v, 'opening', openingLabel),
          'statement_closing': figure(v, 'closing', closingLabel),
          if (card) ...{
            'amount_due': plain(v, 'due', t.t('rc.due', {'currency': c})),
            'minimum_due':
                plain(v, 'minimum', t.t('rc.minimum', {'currency': c})),
            'due_on': v['due_on'],
          },
        });
      },
    ),
  );
  return made;
}

/// The month before [today], first and last day (the web's `lastMonth`).
(String, String) lastMonth(DateTime today) {
  final first = DateTime(today.year, today.month - 1, 1);
  final last = DateTime(today.year, today.month, 0);
  return (isoDay(first), isoDay(last));
}

/// A sheet listing [accounts]: the one picked, or null.
Future<FinanceAccount?> pickAccount(
    BuildContext context, List<FinanceAccount> accounts, String title) {
  final s = FinanceStrings.of(context);
  return showRecordsSheet<FinanceAccount>(
    context,
    (sheet) => Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(title, style: Theme.of(sheet).textTheme.titleLarge),
        const SizedBox(height: AnumSpacing.sm),
        for (final a in accounts)
          AnumRow(
            key: Key('pick-account-${a.id}'),
            icon: a.kind == 'card'
                ? Icons.credit_card_outlined
                : Icons.account_balance_outlined,
            title: a.name,
            subtitle: [
              s.t('kind.${a.kind}'),
              a.currency,
              if (a.last4 != null) s.t('endingIn', {'last4': a.last4!}),
            ].join(' · '),
            onTap: () => Navigator.pop(sheet, a),
          ),
      ],
    ),
  );
}

/// The match picker: the session's records (or statement rows) with the
/// same amount. True once matched.
Future<bool> matchReconRow(BuildContext context,
    FinanceMoreRepository repository, ReconDetail d, ReconIssue row) async {
  final t = FinanceToolsStrings.of(context);
  final List<ReconIssue> options;
  try {
    options =
        reconMatchCandidates(row, await repository.reconAllIssues(d.row.id));
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return false;
  }
  if (!context.mounted) return false;
  final fromStatement = row.statementRef != null;
  final amount =
      row.amount == null ? '-' : formatAmount(row.amount!, d.row.currency);
  final picked = await showRecordsSheet<ReconIssue>(
    context,
    (sheet) => Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(t.t('rc.matchTitle'), style: Theme.of(sheet).textTheme.titleLarge),
        const SizedBox(height: AnumSpacing.xs),
        Text(t.t(fromStatement ? 'rc.matchStatement' : 'rc.matchLedger',
            {'amount': amount})),
        const SizedBox(height: AnumSpacing.sm),
        if (options.isEmpty)
          Text(t.t('rc.matchNone'),
              style: TextStyle(color: sheet.palette.muted))
        else
          for (final o in options)
            AnumRow(
              key: Key('recon-match-${o.id}'),
              icon: Icons.link,
              title: o.description ?? '-',
              subtitle: [
                if (o.postedOn != null) ltrValue(sheet, o.postedOn!),
                if (o.amount != null) formatAmount(o.amount!, d.row.currency),
              ].join(' · '),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.pop(sheet, o),
            ),
      ],
    ),
  );
  if (picked == null || !context.mounted) return false;
  try {
    await repository.resolveRecon(d.row.id, {
      'kind': 'match',
      'statement_ref': fromStatement ? row.statementRef : picked.statementRef,
      'transaction_id':
          fromStatement ? picked.transactionId : row.transactionId,
    });
    return true;
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return false;
  }
}

/// The correcting entry for a statement row the books do not hold: its
/// journal lines and the payload hash, then Approve and post. True once
/// posted. A changed entry (409) is shown again for a fresh look.
Future<bool> correctReconRow(BuildContext context,
    FinanceMoreRepository repository, ReconDetail d, ReconIssue row) async {
  final t = FinanceToolsStrings.of(context);
  final s = FinanceStrings.of(context);
  final ref = row.statementRef;
  if (ref == null) return false;
  ReconCorrection preview;
  try {
    preview = await repository.previewCorrection(d.row.id, ref);
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return false;
  }
  if (!context.mounted) return false;
  final ok = await showDialog<bool>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(t.t('rc.correctTitle')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(t.t('rc.correctText')),
            const SizedBox(height: AnumSpacing.sm),
            Text(preview.description,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text([
              ltrValue(dialog, preview.postedOn),
              formatAmount(preview.amount, preview.currency),
              s.t('category.${preview.category}'),
            ].join(' · ')),
            const SizedBox(height: AnumSpacing.sm),
            Text(t.t('rc.legs'), style: Theme.of(dialog).textTheme.titleSmall),
            for (final leg in preview.legs)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text(
                    '${ltrValue(dialog, leg.ledgerAccount)}: ${formatAmount(leg.amount, leg.currency)}'),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Text(
                t.t('rc.hash', {
                  'hash': ltrValue(dialog, preview.payloadHash.substring(0, 12))
                }),
                style: TextStyle(color: dialog.palette.muted)),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(t.t('cancel'))),
        FilledButton(
            key: const Key('recon-correct-post'),
            onPressed: () => Navigator.pop(dialog, true),
            child: Text(t.t('rc.correctPost'))),
      ],
    ),
  );
  if (ok != true || !context.mounted) return false;
  try {
    await repository.postCorrection(d.row.id, preview);
    return true;
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return false;
  }
}

/// Asks why a completed session is reopened (at least 3 characters).
Future<String?> askReopenReason(BuildContext context) async {
  final t = FinanceToolsStrings.of(context);
  String? reason;
  await openRecordsForm(
    context,
    RecordsForm(
      title: t.t('rc.reopen'),
      intro: t.t('rc.reopenText'),
      submitLabel: t.t('rc.reopen'),
      fields: [
        RecordsInput.text('reason', t.t('rc.reason'),
            required: true, maxLength: 200),
      ],
      onSubmit: (v) async {
        final text = ((v['reason'] as String?) ?? '').trim();
        if (text.length < 3) throw FormatException(t.t('rc.reasonNeeded'));
        reason = text;
      },
    ),
  );
  return reason;
}
