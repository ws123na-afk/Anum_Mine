import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'admin_controller.dart';
import 'admin_models.dart';
import 'admin_repository.dart';

/// Owner screen: memberships and invitations from the live API. Whether the
/// caller may manage them is the API's answer (403), never a guess.
class MembersScreen extends StatefulWidget {
  const MembersScreen(
      {required this.controller,
      required this.repository,
      required this.currentWorkspaceId,
      super.key});
  final MembersController controller;
  final AdminRepository repository;
  final String currentWorkspaceId;

  @override
  State<MembersScreen> createState() => _MembersScreenState();
}

class _MembersScreenState extends State<MembersScreen> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  void _openAccept() => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => AcceptInvitationScreen(
              controller: AcceptInvitationController(widget.repository,
                  currentWorkspaceId: widget.currentWorkspaceId))));

  Future<void> _invite() => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _InviteSheet(controller: widget.controller));

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          return Scaffold(
            appBar: AppBar(title: const Text('Members'), actions: [
              IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.busy ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                eyebrow: 'Workspace access',
                title: 'Members and invitations',
                subtitle:
                    'Who can work in ${widget.currentWorkspaceId}, and with which role.',
                large: true,
              ),
              if (c.notice != null)
                AnumSurface(
                  accent: c.actionFailed ? p.stop : p.ok,
                  child: Text(c.notice!,
                      key: const Key('members-notice'),
                      style: TextStyle(color: p.text)),
                ),
              ...switch (c.phase) {
                AdminPhase.initial || AdminPhase.loading => [
                    const Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: Center(child: CircularProgressIndicator()))
                  ],
                AdminPhase.forbidden => [
                    AnumSurface(
                        accent: p.warn,
                        child: AnumEmpty(
                          icon: Icons.lock_outline,
                          title: 'Owner access required',
                          message:
                              'Listing members, changing roles and inviting people need the owner role in this workspace. '
                              'Ask a workspace owner, or accept an invitation you received.\n'
                              'The API answered: ${c.loadMessage}',
                        )),
                  ],
                AdminPhase.offline || AdminPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                          icon: c.phase == AdminPhase.offline
                              ? Icons.cloud_off_outlined
                              : Icons.error_outline,
                          title: 'Members could not be loaded',
                          message: c.loadMessage ?? 'Try again.',
                          action: 'Retry',
                          onAction: c.load,
                        )),
                  ],
                AdminPhase.ready => _ready(context, c),
              },
              AnumSurface(
                onTap: _openAccept,
                semanticLabel: 'Accept an invitation',
                child: const AnumRow(
                  icon: Icons.how_to_reg_outlined,
                  title: 'Accept an invitation',
                  subtitle: 'Paste a token or link you received.',
                  trailing: Icon(Icons.chevron_right),
                ),
              ),
            ]),
          );
        },
      );

  List<Widget> _ready(BuildContext context, MembersController c) {
    final p = context.palette;
    return [
      AnumMetricGrid(children: [
        AnumMetric(
            label: 'Active members',
            value: '${c.members.where((m) => m.active).length}',
            icon: Icons.group_outlined),
        AnumMetric(
            label: 'Owners',
            value: '${activeOwnerCount(c.members)}',
            icon: Icons.verified_user_outlined),
        AnumMetric(
            label: 'Pending invitations',
            value: '${c.pending.length}',
            icon: Icons.mark_email_unread_outlined),
        AnumMetric(
            label: 'Deactivated',
            value: '${c.members.where((m) => !m.active).length}',
            icon: Icons.block),
      ]),
      if (c.created != null)
        _TokenReveal(created: c.created!, onDone: c.dismissCreated),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(
            eyebrow: 'Memberships',
            title: c.members.length == 1
                ? '1 member'
                : '${c.members.length} members',
            trailing: FilledButton.tonalIcon(
                onPressed: c.busy ? null : _invite,
                icon: const Icon(Icons.person_add_alt_1_outlined),
                label: const Text('Invite')),
          ),
          const SizedBox(height: 6),
          if (c.members.isEmpty)
            const AnumEmpty(
                title: 'No members',
                message: 'This workspace has no memberships yet.'),
          for (final member in c.members)
            AnumRow(
              key: Key('member-${member.userId}'),
              icon: member.active
                  ? Icons.person_outline
                  : Icons.person_off_outlined,
              tone: member.active ? AnumTone.info : AnumTone.neutral,
              title: member.userId,
              subtitle:
                  '${roleLabel(member.role)} · ${member.active ? 'active' : 'deactivated'}',
              detail: isLastActiveOwner(c.members, member)
                  ? 'Last active owner: the API refuses to demote or deactivate them.'
                  : 'Joined ${relativeTime(member.createdAt)}',
              trailing: PopupMenuButton<String>(
                tooltip: 'Actions for ${member.userId}',
                enabled: !c.busy,
                onSelected: (value) {
                  if (value == 'deactivate' || value == 'reactivate') {
                    c.setActive(member, active: value == 'reactivate');
                  } else {
                    c.changeRole(member, value);
                  }
                },
                itemBuilder: (_) => [
                  for (final role in workspaceRoles)
                    if (role != member.role)
                      PopupMenuItem(
                          value: role, child: Text('Make ${roleLabel(role)}')),
                  const PopupMenuDivider(),
                  PopupMenuItem(
                    value: member.active ? 'deactivate' : 'reactivate',
                    child: Text(member.active ? 'Deactivate' : 'Reactivate',
                        style: TextStyle(color: member.active ? p.stop : null)),
                  ),
                ],
              ),
            ),
        ]),
      ),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(
              eyebrow: 'Invitations',
              title: c.pending.length == 1
                  ? '1 pending'
                  : '${c.pending.length} pending'),
          const SizedBox(height: 6),
          if (c.invitations.isEmpty)
            const AnumEmpty(
                icon: Icons.mail_outline,
                title: 'No invitations',
                message:
                    'Invite someone. Their invitation stays here until it is accepted, revoked or expires.'),
          for (final invitation in c.invitations)
            AnumRow(
              key: Key('invitation-${invitation.id}'),
              icon: Icons.mail_outline,
              tone: invitation.pending ? AnumTone.warn : AnumTone.neutral,
              title: invitation.invitee.isEmpty
                  ? 'Anyone with the token'
                  : invitation.invitee,
              subtitle:
                  '${roleLabel(invitation.role)} · ${invitation.status} · by ${invitation.createdByUserId}',
              detail: invitation.pending
                  ? expiryLabel(invitation.expiresAt)
                  : invitation.acceptedAt != null
                      ? 'Accepted ${exactTime(invitation.acceptedAt!)}'
                      : invitation.revokedAt != null
                          ? 'Revoked ${exactTime(invitation.revokedAt!)}'
                          : 'Expired ${exactTime(invitation.expiresAt)}',
              trailing: invitation.pending
                  ? TextButton(
                      onPressed: c.busy ? null : () => c.revoke(invitation),
                      style: TextButton.styleFrom(foregroundColor: p.stop),
                      child: const Text('Revoke'))
                  : AnumPill(label: invitation.status),
            ),
        ]),
      ),
    ];
  }
}

class _TokenReveal extends StatelessWidget {
  const _TokenReveal({required this.created, required this.onDone});
  final CreatedInvitation created;
  final VoidCallback onDone;

  Future<void> _copy(BuildContext context) async {
    await Clipboard.setData(ClipboardData(text: created.token));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('Token copied.')));
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final invitation = created.invitation;
    return AnumSurface(
      accent: p.warn,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          eyebrow: 'Shown once',
          title: 'Send this to ${invitation.invitee}',
          trailing: AnumPill(
              label: expiryLabel(invitation.expiresAt), tone: AnumTone.warn),
        ),
        const SizedBox(height: 10),
        Text(
            'ANUM keeps only a hash of this token. Copy it now: it cannot be shown again. '
            'The invitee pastes it under Settings › Accept an invitation, signed in as themselves.',
            style: TextStyle(color: p.warn, fontSize: 13)),
        const SizedBox(height: 10),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
              color: p.background,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: p.line)),
          child: SelectableText(created.token,
              key: const Key('invitation-token'),
              textDirection: TextDirection.ltr,
              style: TextStyle(
                  color: p.text, fontFamily: 'monospace', fontSize: 13)),
        ),
        const SizedBox(height: 8),
        Text(
            '${roleLabel(invitation.role)} access to ${invitation.workspaceId} (workspace id, needed when accepting), valid until ${exactTime(invitation.expiresAt)}.',
            style: TextStyle(color: p.muted, fontSize: 12.5)),
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.icon(
              onPressed: () => _copy(context),
              icon: const Icon(Icons.copy),
              label: const Text('Copy token')),
          OutlinedButton(onPressed: onDone, child: const Text('Done')),
        ]),
      ]),
    );
  }
}

class _InviteSheet extends StatefulWidget {
  const _InviteSheet({required this.controller});
  final MembersController controller;
  @override
  State<_InviteSheet> createState() => _InviteSheetState();
}

class _InviteSheetState extends State<_InviteSheet> {
  final userId = TextEditingController();
  final email = TextEditingController();
  final ttl = TextEditingController(text: '$defaultInvitationTtlHours');
  String role = 'member';
  String? error;
  bool saving = false;

  @override
  void dispose() {
    userId.dispose();
    email.dispose();
    ttl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final (draft, problem) = validateInvitation(
        role: role, userId: userId.text, email: email.text, ttlHours: ttl.text);
    if (draft == null) {
      setState(() => error = problem);
      return;
    }
    setState(() {
      saving = true;
      error = null;
    });
    final ok = await widget.controller.invite(draft);
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() {
        saving = false;
        error = widget.controller.notice;
      });
    }
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsetsDirectional.fromSTEB(AnumSpacing.md, 0,
            AnumSpacing.md, MediaQuery.viewInsetsOf(context).bottom + 24),
        child: SingleChildScrollView(
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Invite someone',
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 4),
                const Text(
                    'Bind it to their user id (sign-in subject), verified email, or both. Only that person can accept it.'),
                const SizedBox(height: AnumSpacing.md),
                TextField(
                    controller: userId,
                    decoration: const InputDecoration(labelText: 'User id')),
                const SizedBox(height: AnumSpacing.sm),
                TextField(
                    controller: email,
                    keyboardType: TextInputType.emailAddress,
                    decoration: const InputDecoration(labelText: 'Email')),
                const SizedBox(height: AnumSpacing.sm),
                DropdownButtonFormField<String>(
                  initialValue: role,
                  decoration: const InputDecoration(labelText: 'Role'),
                  items: [
                    for (final r in workspaceRoles)
                      DropdownMenuItem(value: r, child: Text(roleLabel(r)))
                  ],
                  onChanged:
                      saving ? null : (v) => setState(() => role = v ?? role),
                ),
                const SizedBox(height: AnumSpacing.sm),
                TextField(
                    controller: ttl,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                        labelText: 'Expires after (hours)')),
                if (error != null) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Text(error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
                ],
                const SizedBox(height: AnumSpacing.md),
                FilledButton.icon(
                    onPressed: saving ? null : _submit,
                    icon: const Icon(Icons.forward_to_inbox_outlined),
                    label: Text(saving ? 'Creating...' : 'Create invitation')),
              ]),
        ),
      );
}

/// For invitees: paste a token or a web invitation link. Works without a
/// membership in the target workspace.
class AcceptInvitationScreen extends StatefulWidget {
  const AcceptInvitationScreen({required this.controller, super.key});
  final AcceptInvitationController controller;
  @override
  State<AcceptInvitationScreen> createState() => _AcceptState();
}

class _AcceptState extends State<AcceptInvitationScreen> {
  final input = TextEditingController();
  final workspace = TextEditingController();

  @override
  void dispose() {
    input.dispose();
    workspace.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text;
    if (text == null) return;
    input.text = text.trim();
    _sync(input.text);
  }

  void _sync(String value) {
    final parsed = parseInvitationInput(value);
    if (parsed?.workspaceId != null) workspace.text = parsed!.workspaceId!;
    setState(() {});
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          return Scaffold(
            appBar: AppBar(title: const Text('Accept an invitation')),
            body: AnumPage(children: [
              const AnumHeader(
                eyebrow: 'For invitees',
                title: 'Join a workspace',
                subtitle:
                    'Paste the token or link you received. You must be signed in as the person it was issued to.',
                large: true,
              ),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        controller: input,
                        onChanged: _sync,
                        autocorrect: false,
                        enableSuggestions: false,
                        textDirection: TextDirection.ltr,
                        decoration: InputDecoration(
                          labelText: 'Invitation token or link',
                          hintText: 'anum_inv_…',
                          suffixIcon: IconButton(
                              tooltip: 'Paste',
                              onPressed: _paste,
                              icon: const Icon(Icons.content_paste)),
                        ),
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      TextField(
                        controller: workspace,
                        autocorrect: false,
                        textDirection: TextDirection.ltr,
                        decoration: InputDecoration(
                            labelText: 'Workspace',
                            helperText:
                                'Leave empty to join ${c.currentWorkspaceId}.'),
                      ),
                      if (c.message != null) ...[
                        const SizedBox(height: AnumSpacing.sm),
                        Text(c.message!,
                            key: const Key('accept-message'),
                            style: TextStyle(color: c.failed ? p.stop : p.ok)),
                      ],
                      const SizedBox(height: AnumSpacing.md),
                      FilledButton.icon(
                        onPressed: c.busy || input.text.trim().isEmpty
                            ? null
                            : () => c.accept(input.text,
                                workspaceId: workspace.text),
                        icon: const Icon(Icons.how_to_reg_outlined),
                        label:
                            Text(c.busy ? 'Accepting...' : 'Accept invitation'),
                      ),
                    ]),
              ),
            ]),
          );
        },
      );
}
