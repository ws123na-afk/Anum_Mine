import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../workspace/workspace_models.dart';
import 'email_controller.dart';
import 'email_models.dart';

/// The unified inbox (docs/email.md): every connected mailbox in one list,
/// search and unread filter, and the message with its drafts. Sending waits
/// for an owner's approval of the exact message, shown with its payload hash.
class InboxScreen extends StatefulWidget {
  const InboxScreen({super.key, required this.controller});
  final EmailController controller;

  @override
  State<InboxScreen> createState() => _InboxScreenState();
}

class _InboxScreenState extends State<InboxScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    _search.text = widget.controller.query;
    Future.microtask(widget.controller.load);
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('inbox')),
        actions: [
          IconButton(
            onPressed: c.phase == InboxPhase.loading ? null : c.load,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 880),
              child: _body(context, l, c),
            ),
          ),
        ),
      ),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l, EmailController c) {
    if (c.phase == InboxPhase.loading && c.messages.isEmpty) {
      return Center(
        child: Semantics(
          label: l.t('inboxLoading'),
          child: const CircularProgressIndicator(),
        ),
      );
    }
    if (c.phase == InboxPhase.denied) {
      return _Message(
        icon: Icons.lock_outline,
        title: l.t('inboxDeniedTitle'),
        text: l.t('inboxDeniedText'),
      );
    }
    if (c.phase == InboxPhase.error) {
      return _Message(
        icon: Icons.error_outline,
        title: l.t('inboxError'),
        text: c.error ?? '',
        action: FilledButton.icon(
          onPressed: c.load,
          icon: const Icon(Icons.refresh),
          label: Text(l.t('retry')),
        ),
      );
    }
    if (c.phase == InboxPhase.ready && c.accounts.isEmpty) {
      return _Message(
        icon: Icons.mail_outline,
        title: l.t('inboxNoAccountsTitle'),
        text: l.t('inboxNoAccountsText'),
      );
    }
    return RefreshIndicator(
      onRefresh: c.load,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          Text(
            l.f('inboxAccounts', {
              'list': c.accounts
                  .map(
                    (a) => a.needsReconnect
                        ? '${a.address} (${l.t('inboxReconnect')})'
                        : a.address,
                  )
                  .join(', '),
            }),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: (value) => c.search(value),
            decoration: InputDecoration(
              labelText: l.t('inboxSearch'),
              prefixIcon: const Icon(Icons.search),
            ),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              FilterChip(
                label: Text(l.t('inboxUnread')),
                selected: c.unreadOnly,
                onSelected: (value) => c.search(_search.text, unread: value),
              ),
              if (c.hasFilters)
                ActionChip(
                  label: Text(l.t('inboxClear')),
                  onPressed: () {
                    _search.clear();
                    c.search('', unread: false);
                  },
                ),
            ],
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.messages.isEmpty)
            _Message(
              icon: Icons.inbox_outlined,
              title:
                  c.hasFilters ? l.t('inboxNoMatch') : l.t('inboxEmptyTitle'),
              text: c.hasFilters
                  ? l.t('inboxNoMatchText')
                  : l.t('inboxEmptyText'),
              inList: true,
            ),
          for (final message in c.messages)
            _MessageTile(
              message: message,
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) =>
                      EmailThreadScreen(controller: c, messageId: message.id),
                ),
              ),
            ),
          if (c.nextCursor != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.sm),
              child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMore,
                child: Text(l.t('inboxMore')),
              ),
            ),
        ],
      ),
    );
  }
}

class _MessageTile extends StatelessWidget {
  const _MessageTile({required this.message, required this.onTap});
  final EmailMessage message;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final high = message.priority == 'high';
    return Card(
      margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
      child: ListTile(
        onTap: onTap,
        leading: Icon(
          message.isRead ? Icons.drafts_outlined : Icons.mark_email_unread,
          semanticLabel: message.isRead ? null : l.t('inboxUnread'),
        ),
        title: Text(
          message.sender,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            fontWeight: message.isRead ? FontWeight.normal : FontWeight.bold,
          ),
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              message.subject.isEmpty ? l.t('inboxNoSubject') : message.subject,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            Text(
              message.summary ?? message.snippet,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            if (high)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xxs),
                child: Text(
                  l.t('inboxHighPriority'),
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.text,
    this.action,
    this.inList = false,
  });
  final IconData icon;
  final String title, text;
  final Widget? action;
  final bool inList;

  @override
  Widget build(BuildContext context) {
    final content = Padding(
      padding: const EdgeInsets.all(AnumSpacing.lg),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 32),
          const SizedBox(height: AnumSpacing.sm),
          Text(
            title,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: AnumSpacing.xs),
          Text(text, textAlign: TextAlign.center),
          if (action != null) ...[
            const SizedBox(height: AnumSpacing.md),
            action!,
          ],
        ],
      ),
    );
    return inList
        ? content
        : Center(child: SingleChildScrollView(child: content));
  }
}

/// One message: its plain text, AI summary, drafts and the approve-to-send
/// card.
class EmailThreadScreen extends StatefulWidget {
  const EmailThreadScreen({
    super.key,
    required this.controller,
    required this.messageId,
  });
  final EmailController controller;
  final String messageId;

  @override
  State<EmailThreadScreen> createState() => _EmailThreadScreenState();
}

class _EmailThreadScreenState extends State<EmailThreadScreen> {
  final _instructions = TextEditingController();

  @override
  void initState() {
    super.initState();
    Future.microtask(() => widget.controller.open(widget.messageId));
    widget.controller.addListener(_announce);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_announce);
    _instructions.dispose();
    super.dispose();
  }

  void _announce() {
    final text = widget.controller.notice;
    if (text == null || !mounted) return;
    widget.controller.clearNotice();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('inboxMessage'))),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final detail = c.detail;
          if (c.detailLoading && detail == null) {
            return const Center(child: CircularProgressIndicator());
          }
          if (detail == null) {
            return _Message(
              icon: Icons.error_outline,
              title: l.t('inboxError'),
              text: c.detailError ?? '',
              action: FilledButton(
                onPressed: () => c.open(widget.messageId),
                child: Text(l.t('retry')),
              ),
            );
          }
          final message = detail.message;
          return SafeArea(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 880),
                child: ListView(
                  padding: const EdgeInsets.all(AnumSpacing.md),
                  children: [
                    Text(
                      message.subject.isEmpty
                          ? l.t('inboxNoSubject')
                          : message.subject,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    Text(
                      l.f('inboxFrom', {
                        'from': message.fromName == null
                            ? message.fromAddress
                            : '${message.fromName} <${message.fromAddress}>',
                      }),
                    ),
                    Text(
                      MaterialLocalizations.of(context)
                          .formatFullDate(message.receivedAt.toLocal()),
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    Wrap(
                      spacing: AnumSpacing.xs,
                      children: [
                        OutlinedButton.icon(
                          onPressed: c.busy == null ? c.toggleRead : null,
                          icon: Icon(
                            message.isRead
                                ? Icons.mark_email_unread_outlined
                                : Icons.mark_email_read_outlined,
                          ),
                          label: Text(
                            message.isRead
                                ? l.t('inboxMarkUnread')
                                : l.t('inboxMarkRead'),
                          ),
                        ),
                      ],
                    ),
                    if (message.summary != null) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(AnumSpacing.sm),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                l.t('inboxSummary'),
                                style: Theme.of(context).textTheme.titleSmall,
                              ),
                              Text(message.summary!),
                            ],
                          ),
                        ),
                      ),
                    ],
                    const SizedBox(height: AnumSpacing.sm),
                    Text(
                      l.t('inboxUntrusted'),
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(AnumSpacing.md),
                        child: detail.bodyError != null
                            ? Text(
                                l.f('inboxBodyFailed', {
                                  'reason': detail.bodyError!,
                                }),
                              )
                            : SelectableText(
                                detail.body?.isNotEmpty == true
                                    ? detail.body!
                                    : l.t('inboxEmptyBody'),
                              ),
                      ),
                    ),
                    if (detail.truncated) Text(l.t('inboxTruncated')),
                    if (detail.attachments.isNotEmpty)
                      Text(
                        l.f('inboxAttachments', {
                          'names': detail.attachments.join(', '),
                        }),
                      ),
                    const SizedBox(height: AnumSpacing.md),
                    Text(
                      l.t('inboxReply'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    TextField(
                      controller: _instructions,
                      maxLength: 1000,
                      decoration: InputDecoration(
                        labelText: l.t('inboxInstructions'),
                      ),
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                        onPressed: c.busy == null
                            ? () =>
                                c.draftReply(instructions: _instructions.text)
                            : null,
                        icon: const Icon(Icons.auto_awesome),
                        label: Text(
                          c.busy == 'draft'
                              ? l.t('inboxDrafting')
                              : l.t('inboxDraftWithAi'),
                        ),
                      ),
                    ),
                    const SizedBox(height: AnumSpacing.sm),
                    for (final draft in detail.drafts)
                      DraftCard(
                        key: ValueKey(draft.id),
                        controller: c,
                        draft: draft,
                      ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// A reply draft with its provenance, editable until it is sent, and the
/// owner's approve-to-send card when an approval is waiting.
class DraftCard extends StatefulWidget {
  const DraftCard({super.key, required this.controller, required this.draft});
  final EmailController controller;
  final EmailDraft draft;

  @override
  State<DraftCard> createState() => _DraftCardState();
}

class _DraftCardState extends State<DraftCard>
    with AutomaticKeepAliveClientMixin {
  // An edited reply survives being scrolled out of the lazily built list.
  @override
  bool get wantKeepAlive => true;

  late final TextEditingController _body = TextEditingController(
    text: widget.draft.body,
  );

  @override
  void dispose() {
    _body.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    final draft = widget.draft;
    final approval = draft.approval;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              draft.byModel
                  ? l.f('inboxByModel', {'model': draft.modelName ?? 'AI'})
                  : l.t('inboxByPerson'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
            Text(l.f('inboxTo', {'to': draft.to.join(', ')})),
            const SizedBox(height: AnumSpacing.xs),
            if (draft.sent)
              Text(
                l.t('inboxSent'),
                style: const TextStyle(fontWeight: FontWeight.w600),
              )
            else ...[
              TextField(
                controller: _body,
                minLines: 4,
                maxLines: 12,
                maxLength: 20000,
                decoration: InputDecoration(labelText: l.t('inboxDraftBody')),
              ),
              if (draft.approvalNote != null) Text(draft.approvalNote!),
              if (draft.error != null)
                Text(
                  draft.error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              if (approval != null && approval.status == 'pending')
                _ApprovalCard(controller: c, approval: approval)
              else
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: FilledButton.icon(
                    onPressed: c.busy == null
                        ? () => c.askToSend(draft, editedBody: _body.text)
                        : null,
                    icon: const Icon(Icons.send),
                    label: Text(l.t('inboxAskToSend')),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ApprovalCard extends StatelessWidget {
  const _ApprovalCard({required this.controller, required this.approval});
  final EmailController controller;
  final WorkspaceApproval approval;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final hash = approval.payloadHash ?? '';
    final to = approval.arguments['to'];
    return Semantics(
      container: true,
      label: l.t('inboxApproval'),
      child: Container(
        margin: const EdgeInsets.only(top: AnumSpacing.xs),
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).colorScheme.tertiary),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              l.t('inboxApprovalTitle'),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            Text(
              l.f('inboxFrom', {'from': '${approval.arguments['from'] ?? ''}'}),
            ),
            Text(l.f('inboxTo', {'to': to is List ? to.join(', ') : '$to'})),
            Text(
              l.f('inboxHash', {
                'hash': hash.length > 18
                    ? '${hash.substring(0, 12)}…${hash.substring(hash.length - 6)}'
                    : hash,
              }),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                onPressed: controller.busy == null && hash.isNotEmpty
                    ? () => controller.approve(approval)
                    : null,
                icon: const Icon(Icons.verified_outlined),
                label: Text(l.t('inboxApprove')),
              ),
            ),
            Text(
              l.t('inboxApproveOwners'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
