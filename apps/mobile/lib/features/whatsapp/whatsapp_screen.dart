import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../business/ticket_from_message.dart';
import '../workspace/workspace_models.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_media_viewer.dart';
import 'whatsapp_models.dart';
import 'whatsapp_ops_controller.dart';
import 'whatsapp_setup_screen.dart';
import 'whatsapp_template_send.dart';
import 'whatsapp_templates_screen.dart';
import 'sales_mobile_screen.dart';
import 'whatsapp_repository.dart';

/// The WhatsApp inbox (docs/whatsapp.md): conversations with filter, search
/// and paging; a thread with the 24-hour window, the suggested reply (with
/// where it came from), a reply sent for approval, a template sent with its
/// parameters, attachments opened on the phone, and approve or reject for
/// owners. Templates and Setup open from the app bar.
class WhatsAppScreen extends StatefulWidget {
  const WhatsAppScreen({required this.controller, super.key});
  final WhatsAppController controller;

  @override
  State<WhatsAppScreen> createState() => _WhatsAppScreenState();
}

class _WhatsAppScreenState extends State<WhatsAppScreen> {
  final _search = TextEditingController();
  Timer? _poll;

  WhatsAppController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    _search.text = c.query;
    c.load();
    _poll = Timer.periodic(const Duration(seconds: 30), (_) {
      if (c.phase == WaPhase.ready) c.load();
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _search.dispose();
    super.dispose();
  }

  Future<void> _open(WaConversation conversation) => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => WhatsAppThreadScreen(
              controller: WhatsAppThreadController(
                  c.repository, conversation.id,
                  owner: c.owner ?? false,
                  onChanged: c.replace,
                  support: c.support),
              title: conversation.label)));

  Future<void> _openSetup() async {
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => WhatsAppSetupScreen(
                controller: WaSetupController(c.repository),
                apiBase: c.apiBase)));
    // Connecting or disconnecting changes what the inbox shows.
    if (mounted) await c.load();
  }

  Future<void> _openTemplates() => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => WhatsAppTemplatesScreen(
              controller: WaTemplatesController(c.repository),
              onOpenSetup: _openSetup)));

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('whatsapp')), actions: [
        IconButton(
            icon: const Icon(Icons.trending_up),
            tooltip: 'Sales CRM',
            onPressed: () {
              final repo = c.repository;
              if (repo is ApiWhatsAppRepository) {
                Navigator.push<void>(
                    context,
                    MaterialPageRoute(
                        builder: (_) => MobileSalesScreen(
                            repository: MobileSalesRepository(repo.api))));
              }
            }),
        IconButton(
            key: const Key('wa-open-templates'),
            onPressed: _openTemplates,
            tooltip: l.t('waOps.templates.title'),
            icon: const Icon(Icons.description_outlined)),
        IconButton(
            key: const Key('wa-open-setup'),
            onPressed: _openSetup,
            tooltip: l.t('waOps.setup.title'),
            icon: const Icon(Icons.settings_outlined)),
        IconButton(
            onPressed: c.load,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh))
      ]),
      body: ListenableBuilder(
          listenable: c,
          builder: (context, _) => AnumPage(onRefresh: c.load, children: [
                ..._body(context, l),
              ])),
    );
  }

  List<Widget> _body(BuildContext context, AnumLocalizations l) {
    switch (c.phase) {
      case WaPhase.initial:
      case WaPhase.loading:
        if (c.items.isEmpty) {
          return [
            Semantics(
                liveRegion: true,
                label: l.t('waLoading'),
                child: const Center(
                    child: Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: CircularProgressIndicator())))
          ];
        }
      case WaPhase.denied:
        return [
          AnumEmpty(
              icon: Icons.lock_outline,
              title: l.t('waDeniedTitle'),
              message: l.t('waDeniedText'))
        ];
      case WaPhase.notConnected:
        return [
          AnumEmpty(
              icon: Icons.power_outlined,
              title: l.t('waNotConnected'),
              message: c.account?.canManage ?? false
                  ? l.t('waOps.notConnectedOwner')
                  : l.t('waNotConnectedMember'),
              action: c.account?.canManage ?? false
                  ? l.t('waOps.setup.open')
                  : null,
              onAction: _openSetup)
        ];
      case WaPhase.error:
        return [
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('waLoadFailed'),
              message: c.error ?? '',
              action: l.t('retry'),
              onAction: c.load)
        ];
      case WaPhase.ready:
        break;
    }
    return [
      TextField(
          controller: _search,
          textInputAction: TextInputAction.search,
          onSubmitted: c.search,
          decoration: InputDecoration(
              labelText: l.t('waSearch'),
              prefixIcon: const Icon(Icons.search),
              suffixIcon: c.query.isEmpty
                  ? null
                  : IconButton(
                      tooltip: l.t('waClearSearch'),
                      onPressed: () {
                        _search.clear();
                        c.search('');
                      },
                      icon: const Icon(Icons.clear)))),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        for (final filter in WaFilter.values)
          ChoiceChip(
              label: Text(l.t('waFilter.${filter.api}')),
              selected: c.filter == filter,
              onSelected: (_) => c.setFilter(filter)),
      ]),
      if (c.items.isEmpty)
        AnumEmpty(
            icon: Icons.chat_bubble_outline,
            title: c.query.isNotEmpty || c.filter != WaFilter.all
                ? l.t('waNoMatch')
                : l.t('waEmpty'),
            message: c.query.isNotEmpty || c.filter != WaFilter.all
                ? l.t('waNoMatchText')
                : l.f('waEmptyText', {
                    'number': c.account?.displayPhoneNumber ?? '',
                  }),
            action: c.query.isNotEmpty || c.filter != WaFilter.all
                ? l.t('waShowAll')
                : null,
            onAction: () {
              _search.clear();
              c.filter = WaFilter.all;
              c.search('');
            })
      else
        AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.sm, vertical: AnumSpacing.xxs),
            child: Column(children: [
              for (final item in c.items) _ConversationRow(item, _open),
            ])),
      if (c.hasMore && c.items.isNotEmpty)
        Center(
            child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMore,
                child: Text(c.loadingMore ? l.t('waLoading') : l.t('waMore')))),
    ];
  }
}

class _ConversationRow extends StatelessWidget {
  const _ConversationRow(this.item, this.onOpen);
  final WaConversation item;
  final Future<void> Function(WaConversation) onOpen;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return Semantics(
      button: true,
      label: [
        item.label,
        if (item.unread > 0) l.f('waUnread', {'count': item.unread}),
        item.windowOpen ? l.t('waWindowOpenShort') : l.t('waTemplatesOnly'),
      ].join(', '),
      excludeSemantics: false,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => onOpen(item),
        child: Padding(
          padding: const EdgeInsets.symmetric(
              vertical: AnumSpacing.sm, horizontal: AnumSpacing.xxs),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(
                  child: Text(item.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          color: p.text,
                          fontWeight: FontWeight.w600,
                          fontSize: AnumType.base))),
              const SizedBox(width: AnumSpacing.xs),
              Text(relativeTime(item.lastMessageAt),
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            ]),
            const SizedBox(height: 2),
            Text(
                '${item.lastFromCustomer ? '' : '${l.t('waYou')} '}${item.preview}',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
            const SizedBox(height: AnumSpacing.xxs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (item.category != null)
                    AnumPill(
                        label: l.t('waCategory.${item.category}'),
                        tone: item.category == 'complaint'
                            ? AnumTone.stop
                            : AnumTone.info),
                  AnumPill(
                      label: item.windowOpen
                          ? l.t('waWindowOpenShort')
                          : l.t('waTemplatesOnly'),
                      tone: item.windowOpen ? AnumTone.ok : AnumTone.warn),
                  if (item.awaitingApproval > 0)
                    AnumPill(
                        label:
                            l.f('waAwaiting', {'count': item.awaitingApproval}),
                        tone: AnumTone.warn),
                  if (item.unread > 0) Badge(label: Text('${item.unread}')),
                ]),
          ]),
        ),
      ),
    );
  }
}

class WhatsAppThreadScreen extends StatefulWidget {
  const WhatsAppThreadScreen(
      {required this.controller, required this.title, super.key});
  final WhatsAppThreadController controller;
  final String title;

  @override
  State<WhatsAppThreadScreen> createState() => _WhatsAppThreadScreenState();
}

class _WhatsAppThreadScreenState extends State<WhatsAppThreadScreen> {
  final _reply = TextEditingController();
  Timer? _poll;
  WhatsAppThreadController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load(first: true);
    _poll = Timer.periodic(const Duration(seconds: 15), (_) {
      if (c.busy == null) c.load();
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _reply.dispose();
    c.dispose();
    super.dispose();
  }

  String _window(AnumLocalizations l, WaConversation conversation) {
    final left = conversation.windowLeft(DateTime.now());
    if (left == null) return l.t('waWindowClosed');
    return left.inHours > 0
        ? l.f('waWindowHours', {'count': left.inHours})
        : l.f('waWindowMinutes', {'count': left.inMinutes.clamp(1, 59)});
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title), actions: [
        if (c.support case final support?)
          IconButton(
              key: const Key('wa-create-ticket'),
              tooltip: l.t('biz.fromMessage.action'),
              icon: const Icon(Icons.confirmation_number_outlined),
              onPressed: () => createTicketFromMessage(context,
                  support: support,
                  source: 'whatsapp',
                  refId: c.conversationId)),
      ]),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final conversation = c.conversation;
          if (conversation == null) {
            return AnumPage(children: [
              if (c.loading)
                const Center(child: CircularProgressIndicator())
              else
                AnumEmpty(
                    icon: Icons.error_outline,
                    title: l.t('waThreadFailed'),
                    message: c.error ?? '',
                    action: l.t('retry'),
                    onAction: () => c.load(first: true)),
            ]);
          }
          final open = conversation.windowLeft(DateTime.now()) != null;
          return AnumPage(onRefresh: c.load, children: [
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text('+${conversation.waId}',
                      textDirection: TextDirection.ltr,
                      style: TextStyle(color: p.muted)),
                  Semantics(
                      liveRegion: true,
                      child: AnumPill(
                          icon: Icons.schedule,
                          label: _window(l, conversation),
                          tone: open ? AnumTone.ok : AnumTone.warn)),
                ]),
            _SuggestionCard(
                controller: c,
                canUse: open,
                onUse: (text) => setState(() => _reply.text = text)),
            if (c.budgetExceeded)
              Text(l.t('waBudget'), style: TextStyle(color: p.warn)),
            if (c.hasMore)
              Center(
                  child: OutlinedButton(
                      onPressed: c.busy == null ? c.older : null,
                      child: Text(l.t('waOlder')))),
            if (c.messages.isEmpty)
              Text(l.t('waNoMessages'), style: TextStyle(color: p.muted)),
            for (final message in c.messages)
              _Bubble(message: message, controller: c),
            if (c.notice != null || c.noticeKey != null)
              Semantics(
                  liveRegion: true,
                  child: Text(
                      c.noticeKey != null ? l.t(c.noticeKey!) : c.notice!,
                      style: TextStyle(color: c.noticeFailed ? p.stop : p.ok))),
            if (open)
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                TextField(
                    controller: _reply,
                    minLines: 2,
                    maxLines: 6,
                    maxLength: 4096,
                    decoration: InputDecoration(
                        labelText: l.t('waReply'),
                        helperText: l.t('waApprovalNote'))),
                const SizedBox(height: AnumSpacing.xs),
                Align(
                    alignment: AlignmentDirectional.centerEnd,
                    child: FilledButton.icon(
                        onPressed: c.busy == 'send'
                            ? null
                            : () async {
                                if (await c.send(_reply.text)) _reply.clear();
                              },
                        icon: const Icon(Icons.send),
                        label: Text(c.busy == 'send'
                            ? l.t('waSending')
                            : l.t('waSendForApproval')))),
              ])
            else
              AnumSurface(
                  accent: p.warn,
                  child: Text(l.t('waOps.closedExplain'),
                      style: TextStyle(color: p.text))),
            Align(
                alignment: AlignmentDirectional.centerStart,
                child: (open ? OutlinedButton.icon : FilledButton.icon)(
                    key: const Key('wa-send-template-open'),
                    onPressed: c.busy == null
                        ? () => showWaTemplateSend(context, c)
                        : null,
                    icon: const Icon(Icons.description_outlined),
                    label: Text(l.t('waOps.send.open')))),
          ]);
        },
      ),
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard(
      {required this.controller, required this.canUse, required this.onUse});
  final WhatsAppThreadController controller;
  final bool canUse;
  final ValueChanged<String> onUse;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final suggestion = controller.suggestion;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Icon(Icons.auto_awesome, size: 18, color: p.violet),
              Text(l.t('waSuggested'),
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
              TextButton(
                  onPressed:
                      controller.busy == 'draft' ? null : controller.draft,
                  child: Text(controller.busy == 'draft'
                      ? l.t('waThinking')
                      : suggestion == null
                          ? l.t('waSuggest')
                          : l.t('waSuggestAgain'))),
            ]),
        if (suggestion == null)
          Text(l.t('waNoSuggestion'), style: TextStyle(color: p.muted))
        else ...[
          if (suggestion.summary.isNotEmpty)
            Text('${l.t('waCustomerWants')} ${suggestion.summary}',
                style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
          const SizedBox(height: AnumSpacing.xs),
          Text(suggestion.text, style: TextStyle(color: p.text)),
          const SizedBox(height: AnumSpacing.xs),
          Text(
              suggestion.fromModel
                  ? l.f('waFromModel', {
                      'model': [suggestion.provider, suggestion.model]
                          .whereType<String>()
                          .join(' / '),
                      'count': suggestion.untrustedMessages,
                    })
                  : l.t('waFromRules'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          const SizedBox(height: AnumSpacing.xs),
          FilledButton.tonal(
              onPressed: canUse ? () => onUse(suggestion.text) : null,
              child: Text(l.t('waUseDraft'))),
        ],
      ]),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message, required this.controller});
  final WaMessage message;
  final WhatsAppThreadController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final approval = message.approval;
    final pending =
        approval != null && approvable(approval, DateTime.now().toUtc());
    final media = message.media;
    final body = message.body.isNotEmpty
        ? message.body
        : media != null
            ? (media.caption ?? '')
            : l.f('waUnsupported', {'kind': message.kind});
    final text = Theme.of(context).textTheme;
    return Align(
      alignment: message.inbound
          ? AlignmentDirectional.centerStart
          : AlignmentDirectional.centerEnd,
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
        child: Semantics(
          label: message.inbound ? l.t('waFromCustomer') : l.t('waFromYou'),
          child: DecoratedBox(
            decoration: BoxDecoration(
                color: message.inbound
                    ? p.surface2
                    : p.violet.withValues(alpha: 0.18),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: p.edge)),
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.sm),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (message.templateName != null)
                      Text(l.f('waTemplate', {'name': message.templateName!}),
                          style: TextStyle(
                              color: p.violet, fontSize: AnumType.xs)),
                    if (media != null)
                      _MediaRow(message: message, controller: controller),
                    if (body.isNotEmpty)
                      Text(body,
                          style: text.bodyMedium?.copyWith(color: p.text)),
                    const SizedBox(height: AnumSpacing.xxs),
                    Wrap(spacing: AnumSpacing.xs, children: [
                      Text(exactTime(message.timestamp),
                          style: TextStyle(
                              color: p.faint, fontSize: AnumType.xxsPlus)),
                      if (!message.inbound)
                        Text(l.t('waStatus.${message.status}'),
                            style: TextStyle(
                                color: message.status == 'failed' ||
                                        message.status == 'not_sent'
                                    ? p.stop
                                    : p.muted,
                                fontSize: AnumType.xxsPlus)),
                    ]),
                    if (message.error != null)
                      Text(message.error!,
                          style: TextStyle(
                              color: p.stop, fontSize: AnumType.xsPlus)),
                    if (pending) _ApprovalPanel(message, approval, controller),
                  ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _ApprovalPanel extends StatelessWidget {
  const _ApprovalPanel(this.message, this.approval, this.controller);
  final WaMessage message;
  final WorkspaceApproval approval;
  final WhatsAppThreadController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final hash = approval.payloadHash ?? '';
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.t('waApproveTitle'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        if (approval.target != null)
          Text(l.f('waTarget', {'target': approval.target!}),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        Text(
            l.f('waHash', {
              'hash': hash.length > 18
                  ? '${hash.substring(0, 12)}…${hash.substring(hash.length - 6)}'
                  : hash
            }),
            style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        const SizedBox(height: AnumSpacing.xs),
        if (controller.owner)
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton(
                onPressed: controller.busy == null
                    ? () => controller.decide(message, approve: true)
                    : null,
                child: Text(l.t('waApproveSend'))),
            OutlinedButton(
                onPressed: controller.busy == null
                    ? () => controller.decide(message, approve: false)
                    : null,
                child: Text(l.t('waReject'))),
          ])
        else
          Text(l.t('waOwnerApproves'),
              style: TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
      ]),
    );
  }
}

/// An attachment on a message: what it is and an Open action. Images open in
/// a viewer; other types go to the platform's save dialog.
class _MediaRow extends StatelessWidget {
  const _MediaRow({required this.message, required this.controller});
  final WaMessage message;
  final WhatsAppThreadController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final media = message.media!;
    final kind = const {'image', 'document', 'audio', 'video', 'sticker'}
            .contains(message.kind)
        ? l.t('waOps.media.${message.kind}')
        : l.t('waOps.media.file');
    final name = (media.filename ?? '').trim();
    final label = name.isEmpty ? kind : '$kind: $name';
    final fetching = controller.busy == 'media:${message.id}';
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
      child: Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Icon(
                switch (message.kind) {
                  'image' || 'sticker' => Icons.image_outlined,
                  'audio' => Icons.audiotrack_outlined,
                  'video' => Icons.videocam_outlined,
                  _ => Icons.insert_drive_file_outlined,
                },
                size: 18),
            Text(label,
                style: Theme.of(context).textTheme.bodySmall, softWrap: true),
            Semantics(
              button: true,
              label: l.f('waOps.media.openLabel', {'kind': kind}),
              excludeSemantics: true,
              child: OutlinedButton(
                  key: Key('wa-media-open-${message.id}'),
                  onPressed: controller.busy == null
                      ? () => openWaMedia(context, controller, message)
                      : null,
                  child: Text(fetching
                      ? l.t('waOps.media.opening')
                      : l.t('waOps.media.open'))),
            ),
          ]),
    );
  }
}
