import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'chat_cards.dart';

/// The outcome of a chat question that ran as a task
/// (docs/conversations.md#tools-in-chat): results with links into Inbox,
/// WhatsApp, Files and Tasks, a pending approval with its payload hash and
/// Approve/Reject, or why it failed. Mail, WhatsApp and web text is shown as
/// plain text only.
class ChatCardView extends StatelessWidget {
  const ChatCardView({
    required this.card,
    this.live = false,
    this.deciding = false,
    this.error,
    this.onDecide,
    this.onOpen,
    this.openLink,
    super.key,
  });

  final ChatCard card;

  /// The reply is still streaming (a running card shows progress, not
  /// "still running in the background").
  final bool live;

  /// Approve or Reject is being recorded.
  final bool deciding;

  /// Why the last decision failed, already in words.
  final String? error;
  final void Function({required bool approve})? onDecide;
  final ValueChanged<ChatTarget>? onOpen;
  final Future<bool> Function(Uri uri)? openLink;

  static const _icons = {
    ChatCardCategory.email: Icons.inbox_outlined,
    ChatCardCategory.whatsapp: Icons.chat_outlined,
    ChatCardCategory.web: Icons.public,
    ChatCardCategory.files: Icons.description_outlined,
    ChatCardCategory.action: Icons.shield_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final category = card.category.name;
    final title = switch (card.state) {
      ChatCardState.approval => l.t('chatCard.approval.title'),
      ChatCardState.failed => l.t('chatCard.failed.title'),
      ChatCardState.denied => l.t('chatCard.denied.title'),
      _ => l.t('chatCard.title.$category'),
    };
    final note = TextStyle(color: p.muted, fontSize: 13);
    final faint = TextStyle(color: p.faint, fontSize: 12);
    final approval = card.approval;
    final accent = switch (card.state) {
      ChatCardState.approval => p.warn,
      ChatCardState.failed || ChatCardState.denied => p.stop,
      _ => null,
    };
    final children = <Widget>[
      Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
        Icon(_icons[card.category], color: p.violet, size: 20),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(
          child: Semantics(
            header: true,
            child: Text(title,
                style: TextStyle(
                    color: p.text, fontWeight: FontWeight.w700, fontSize: 15)),
          ),
        ),
        if (card.taskId != null && onOpen != null)
          TextButton(
            onPressed: () =>
                onOpen!(ChatTarget(ChatTargetKind.task, card.taskId)),
            child: Text(l.t('chatCard.openTask')),
          ),
      ]),
      if (card.state == ChatCardState.running) ...[
        Semantics(
          liveRegion: true,
          child: AnumPill(
              label: l.t('chatCard.running.$category'),
              tone: AnumTone.info,
              icon: Icons.hourglass_top),
        ),
        if (!live) Text(l.t('chatCard.stillRunning'), style: note),
      ],
      if (card.state == ChatCardState.results) ..._results(context, l, note),
      if (card.state == ChatCardState.approval && approval != null)
        ..._approval(context, l, approval, note, faint),
      if (card.state == ChatCardState.failed) ...[
        Text(card.summary, style: TextStyle(color: p.stop, fontSize: 13.5)),
        if (approval != null) Text(_decided(l, approval.status), style: note),
      ],
      if (card.state == ChatCardState.cancelled)
        Text(l.t('chatCard.cancelled'), style: note),
      if (card.state == ChatCardState.denied) Text(card.summary, style: note),
      if (card.taskId != null && card.state != ChatCardState.running)
        Row(children: [
          Icon(Icons.check, size: 14, color: p.faint),
          const SizedBox(width: AnumSpacing.xxs),
          Flexible(child: Text(l.t('chatCard.ranAsTask'), style: faint)),
        ]),
    ];
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xs),
      child: AnumSurface(
        key: Key('chat-card-${card.state.name}'),
        accent: accent,
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var i = 0; i < children.length; i++) ...[
              if (i > 0) const SizedBox(height: AnumSpacing.xs),
              children[i],
            ],
          ],
        ),
      ),
    );
  }

  List<Widget> _results(
      BuildContext context, AnumLocalizations l, TextStyle note) {
    final view = switch (card.category) {
      ChatCardCategory.email => (ChatTargetKind.inbox, 'chatCard.openInbox'),
      ChatCardCategory.whatsapp => (
          ChatTargetKind.whatsappInbox,
          'chatCard.openWhatsApp'
        ),
      ChatCardCategory.files => (ChatTargetKind.files, 'chatCard.openFiles'),
      _ => null,
    };
    final quoted = card.category == ChatCardCategory.email ||
        card.category == ChatCardCategory.whatsapp ||
        card.category == ChatCardCategory.web;
    return [
      if (card.items.isEmpty)
        Text(l.t('chatCard.empty.${card.category.name}'), style: note)
      else
        for (final item in card.items)
          _ItemTile(item: item, onOpen: onOpen, openLink: openLink),
      if (card.total > card.items.length)
        Text(
            l.f('chatCard.more',
                {'shown': card.items.length, 'total': card.total}),
            style: note),
      if (quoted && card.items.isNotEmpty)
        Text(l.t('chatCard.untrusted'),
            style: TextStyle(color: context.palette.faint, fontSize: 12)),
      if (view != null && onOpen != null)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton(
            onPressed: () => onOpen!(ChatTarget(view.$1)),
            child: Text(l.t(view.$2)),
          ),
        ),
      if (card.approval case final approval? when !approval.pending)
        Text(_decided(l, approval.status), style: note),
    ];
  }

  List<Widget> _approval(BuildContext context, AnumLocalizations l,
      ChatCardApproval approval, TextStyle note, TextStyle faint) {
    final p = context.palette;
    final risk = switch (approval.riskLevel) {
      'low' ||
      'medium' ||
      'high' =>
        l.t('chatCard.approval.risk.${approval.riskLevel}'),
      final other => other,
    };
    final mono =
        TextStyle(color: p.text, fontSize: 13, fontFamily: 'monospace');
    Widget fact(String label, Widget value) => Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [Text(label, style: faint), value],
        );
    return [
      Text(l.t('chatCard.approval.intro'), style: note),
      fact(l.t('chatCard.approval.action'), Text(approval.action, style: mono)),
      fact(
          l.t('chatCard.approval.risk'),
          AnumPill(
              label: risk,
              tone: approval.riskLevel == 'high'
                  ? AnumTone.stop
                  : AnumTone.warn)),
      if (approval.target != null)
        fact(l.t('chatCard.approval.target'),
            Text(approval.target!, style: mono)),
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.t('chatCard.approval.hash'), style: faint),
        Text(approval.payloadHash ?? '—',
            key: const Key('chat-card-hash'), style: mono),
      ]),
      if (approval.arguments.isNotEmpty)
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('chatCard.approval.arguments'), style: faint),
          for (final entry in approval.arguments.entries)
            Text('${entry.key}: ${entry.value}',
                style: TextStyle(color: p.text, fontSize: 13)),
        ]),
      if (approval.expiresAt != null)
        Text(
            l.f('chatCard.approval.expires',
                {'time': _when(context, approval.expiresAt!)}),
            style: faint),
      if (!approval.pending) Text(_decided(l, approval.status), style: note),
      if (approval.pending && approval.canDecide && onDecide != null)
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton.icon(
            key: const Key('chat-card-approve'),
            onPressed: deciding || approval.payloadHash == null
                ? null
                : () => onDecide!(approve: true),
            icon: const Icon(Icons.verified_user_outlined, size: 18),
            label: Text(l.t('chatCard.approve')),
          ),
          OutlinedButton.icon(
            key: const Key('chat-card-reject'),
            onPressed: deciding ? null : () => onDecide!(approve: false),
            icon: const Icon(Icons.close, size: 18),
            label: Text(l.t('chatCard.reject')),
          ),
        ]),
      if (deciding)
        Semantics(
            liveRegion: true,
            child: Text(l.t('chatCard.deciding'), style: note)),
      if (approval.pending && !approval.canDecide) ...[
        Text(l.t('chatCard.approval.cannotDecide'), style: note),
        if (onOpen != null)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton(
              onPressed: () =>
                  onOpen!(const ChatTarget(ChatTargetKind.approvals)),
              child: Text(l.t('chatCard.openApprovals')),
            ),
          ),
      ],
      if (approval.status == 'approved')
        Text(l.t('chatCard.approval.waitingMore'), style: note),
      if (error != null)
        Semantics(
          liveRegion: true,
          child: Text(
              error == 'cannotDecide'
                  ? l.t('chatCard.approval.cannotDecide')
                  : error!,
              style: TextStyle(color: p.stop, fontSize: 13)),
        ),
    ];
  }

  static String _when(BuildContext context, DateTime value) {
    final m = MaterialLocalizations.of(context);
    final local = value.toLocal();
    return '${m.formatMediumDate(local)} '
        '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
  }

  static String _decided(AnumLocalizations l, String status) =>
      switch (status) {
        'approved' ||
        'rejected' ||
        'expired' =>
          l.t('chatCard.approval.status.$status'),
        final other => other,
      };
}

class _ItemTile extends StatelessWidget {
  const _ItemTile({required this.item, this.onOpen, this.openLink});
  final ChatCardItem item;
  final ValueChanged<ChatTarget>? onOpen;
  final Future<bool> Function(Uri uri)? openLink;

  static const _badges = {
    'unread',
    'priority_high',
    'needs_reply',
    'window_closed'
  };

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final title = item.title.isNotEmpty
        ? item.title
        : item.kind == 'email'
            ? l.t('chatCard.noSubject')
            : (item.id ?? '');
    final meta = [
      if (item.subtitle != null) item.subtitle!,
      if (item.at != null)
        MaterialLocalizations.of(context)
            .formatShortMonthDay(item.at!.toLocal()),
    ].join(' · ');
    final badges = item.badges.where(_badges.contains).toList();
    final link = item.webLink;
    final VoidCallback? onTap = link != null && openLink != null
        ? () => openLink!(link)
        : item.id != null && item.kind != 'web' && onOpen != null
            ? () => onOpen!(ChatTarget(
                switch (item.kind) {
                  'email' => ChatTargetKind.email,
                  'whatsapp' => ChatTargetKind.whatsapp,
                  'memory' => ChatTargetKind.memory,
                  _ => ChatTargetKind.file,
                },
                item.id))
            : null;
    final label = l.f(
        'chatCard.openItem.${item.kind == 'file' || item.kind == 'memory' || item.kind == 'whatsapp' || item.kind == 'web' ? item.kind : 'email'}',
        {'title': title});
    return Semantics(
      button: onTap != null,
      label: onTap != null ? label : null,
      child: Material(
        color: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: p.line),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 48),
            child: Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.sm, vertical: AnumSpacing.xs),
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                                color: p.text,
                                fontWeight: FontWeight.w600,
                                fontSize: 14)),
                        if (meta.isNotEmpty)
                          Text(meta,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(color: p.faint, fontSize: 12)),
                        if (item.detail != null)
                          Text(item.detail!,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(color: p.muted, fontSize: 13)),
                        if (badges.isNotEmpty) ...[
                          const SizedBox(height: AnumSpacing.xxs),
                          Wrap(
                              spacing: AnumSpacing.xxs,
                              runSpacing: AnumSpacing.xxs,
                              children: [
                                for (final badge in badges)
                                  AnumPill(
                                      label: l.t('chatCard.badge.$badge'),
                                      tone: badge == 'priority_high' ||
                                              badge == 'needs_reply'
                                          ? AnumTone.warn
                                          : AnumTone.neutral),
                              ]),
                        ],
                      ]),
                ),
                if (onTap != null)
                  Padding(
                    padding:
                        const EdgeInsetsDirectional.only(start: AnumSpacing.xs),
                    child: Icon(
                        link != null ? Icons.open_in_new : Icons.chevron_right,
                        size: 18,
                        color: p.faint),
                  ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
