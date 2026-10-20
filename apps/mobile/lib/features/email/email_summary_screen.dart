import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'email_controller.dart';
import 'email_models.dart';
import 'email_summary.dart';
import 'inbox_screen.dart';

/// The email summary (docs/email.md): how many unread and urgent messages
/// there are, the All / Urgent / Replied / Other tabs with sender, subject
/// and priority, and the way into the full inbox.
class EmailSummaryScreen extends StatefulWidget {
  const EmailSummaryScreen(
      {required this.controller, required this.inbox, super.key});
  final EmailSummaryController controller;

  /// Opens a message with its drafts and the approve-to-send card.
  final EmailController inbox;

  @override
  State<EmailSummaryScreen> createState() => _EmailSummaryScreenState();
}

class _EmailSummaryScreenState extends State<EmailSummaryScreen> {
  EmailSummaryController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    Future.microtask(c.load);
  }

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  String _tabLabel(AnumLocalizations l, SummaryTab tab) => switch (tab) {
        SummaryTab.all => l.t('emailTabAll'),
        SummaryTab.urgent => c.counts.urgentUnread > 0
            ? l.f('emailTabUrgentCount', {'count': c.counts.urgentUnread})
            : l.t('emailTabUrgent'),
        SummaryTab.replied => l.t('emailTabReplied'),
        SummaryTab.other => l.t('emailTabOther'),
      };

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(l.t('emailSummary')),
          actions: [
            IconButton(
                tooltip: l.t('refresh'),
                onPressed: c.load,
                icon: const Icon(Icons.refresh)),
          ],
        ),
        body: _body(context, l),
      ),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l) {
    if (c.phase == SummaryPhase.idle ||
        c.phase == SummaryPhase.loading && c.messages.isEmpty) {
      return Center(
          child: Semantics(
              label: l.t('inboxLoading'),
              child: const CircularProgressIndicator()));
    }
    if (c.phase == SummaryPhase.denied) {
      return AnumPage(children: [
        AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('inboxDeniedTitle'),
            message: l.t('inboxDeniedText')),
      ]);
    }
    if (c.phase == SummaryPhase.error) {
      return AnumPage(children: [
        AnumEmpty(
          icon: Icons.error_outline,
          title: l.t('inboxError'),
          message: l.f('reportsErrorHint', {'message': c.error ?? ''}),
          action: l.t('retry'),
          onAction: c.load,
        ),
      ]);
    }
    if (c.counts.accounts == 0) {
      return AnumPage(children: [
        AnumEmpty(
            icon: Icons.mail_outline,
            title: l.t('inboxNoAccountsTitle'),
            message: l.t('inboxNoAccountsText')),
      ]);
    }
    final p = context.palette;
    return AnumPage(onRefresh: c.load, children: [
      Semantics(
        header: true,
        child: AnumHeader(
          title: l.t('emailSummary'),
          subtitle: l.f('emailSummaryCounts', {
            'urgent': c.counts.urgentUnread,
            'unread': c.counts.unread,
          }),
        ),
      ),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (final tab in SummaryTab.values)
            Padding(
              padding: const EdgeInsetsDirectional.only(end: AnumSpacing.xs),
              child: ChoiceChip(
                label: Text(_tabLabel(l, tab)),
                selected: tab == c.tab,
                onSelected: (_) => c.select(tab),
              ),
            ),
        ]),
      ),
      if (c.messages.isEmpty)
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.mark_email_read_outlined,
            title: l.t('emailTabEmpty_${c.tab.name}'),
            message: l.t('emailTabEmptyText'),
            action: c.tab == SummaryTab.all ? null : l.t('emailTabShowAll'),
            onAction:
                c.tab == SummaryTab.all ? null : () => c.select(SummaryTab.all),
          ),
        )
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (var i = 0; i < c.messages.length; i++) ...[
              if (i > 0) Divider(height: 1, color: p.line),
              _SummaryRow(
                message: c.messages[i],
                onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => EmailThreadScreen(
                      controller: widget.inbox, messageId: c.messages[i].id),
                )),
              ),
            ],
          ]),
        ),
      if (c.nextCursor != null)
        OutlinedButton(
          onPressed: c.loadingMore ? null : c.loadMore,
          child: Text(l.t('inboxMore')),
        ),
      OutlinedButton.icon(
        onPressed: () => Navigator.of(context).pushNamed('/inbox'),
        icon: const Icon(Icons.inbox_outlined),
        label: Text(l.t('emailViewAll')),
      ),
    ]);
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.message, required this.onTap});
  final EmailMessage message;
  final VoidCallback onTap;

  static String initials(String name) {
    final words = name
        .replaceAll(RegExp(r'[<>"@].*'), '')
        .trim()
        .split(RegExp(r'[\s._-]+'))
        .where((w) => w.isNotEmpty)
        .take(2);
    final letters = [for (final w in words) String.fromCharCode(w.runes.first)];
    return letters.isEmpty ? '?' : letters.join().toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final priority = message.priority;
    final pill = switch (priority) {
      'high' => AnumPill(label: l.t('emailPriorityHigh'), tone: AnumTone.stop),
      'normal' =>
        AnumPill(label: l.t('emailPriorityMedium'), tone: AnumTone.warn),
      'low' => AnumPill(label: l.t('emailPriorityLow'), tone: AnumTone.neutral),
      _ => null,
    };
    final subject =
        message.subject.isEmpty ? l.t('inboxNoSubject') : message.subject;
    return Semantics(
      button: true,
      label: [
        message.sender,
        subject,
        if (!message.isRead) l.t('inboxUnread'),
        if (pill != null) pill.label,
        relativeTime(message.receivedAt),
      ].join(', '),
      excludeSemantics: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.sm),
          child: Row(children: [
            CircleAvatar(
              radius: 20,
              backgroundColor: p.surface3,
              child: Text(initials(message.sender),
                  style: TextStyle(
                      color: p.sky,
                      fontWeight: FontWeight.w700,
                      fontSize: AnumType.sm)),
            ),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(message.sender,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            color: p.text,
                            fontSize: AnumType.mdPlus,
                            fontWeight: message.isRead
                                ? FontWeight.w500
                                : FontWeight.w700)),
                    Text(subject,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style:
                            TextStyle(color: p.muted, fontSize: AnumType.sm)),
                    Text(relativeTime(message.receivedAt),
                        style: TextStyle(
                            color: p.faint, fontSize: AnumType.xxsPlus)),
                  ]),
            ),
            if (pill != null) ...[
              const SizedBox(width: AnumSpacing.xs),
              Flexible(child: pill),
            ],
            Icon(Directionality.of(context) == TextDirection.rtl
                ? Icons.chevron_left
                : Icons.chevron_right),
          ]),
        ),
      ),
    );
  }
}
