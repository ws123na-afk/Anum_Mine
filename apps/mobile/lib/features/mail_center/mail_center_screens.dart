import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../workspace/workspace_models.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';
import 'mail_directory.dart';
import 'mail_directory_models.dart';
import 'mail_outgoing.dart';
import 'mail_assist.dart';

/// The Email Center on the phone (docs/email.md#email-center): mailboxes,
/// folders, a folder's messages with paging and search, one message as plain
/// text, and writing with send through an owner's approval.
const _maxWidth = 880.0;

Widget mailPage(Widget child) => SafeArea(
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: _maxWidth),
          child: child,
        ),
      ),
    );

/// A full-page state: loading, denied, offline, error or empty.
class MailState extends StatelessWidget {
  const MailState({
    super.key,
    required this.icon,
    required this.title,
    this.text,
    this.action,
    this.loading = false,
  });
  final IconData icon;
  final String title;
  final String? text;
  final Widget? action;
  final bool loading;

  @override
  Widget build(BuildContext context) => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AnumSpacing.lg),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Semantics(
              liveRegion: true,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (loading)
                    const CircularProgressIndicator()
                  else
                    Icon(icon, size: 36),
                  const SizedBox(height: AnumSpacing.sm),
                  Text(title,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleMedium),
                  if (text != null && text!.isNotEmpty) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(text!, textAlign: TextAlign.center),
                  ],
                  if (action != null) ...[
                    const SizedBox(height: AnumSpacing.md),
                    action!,
                  ],
                ],
              ),
            ),
          ),
        ),
      );
}

/// The state widget for a phase that is not `ready`, or null when it is.
Widget? stateFor(
  BuildContext context,
  MailPhase phase,
  String? error, {
  required VoidCallback onRetry,
  bool hasData = false,
}) {
  final l = AnumLocalizations.maybeOf(context);
  final retry = FilledButton.icon(
    onPressed: onRetry,
    icon: const Icon(Icons.refresh),
    label: Text(l.t('mc.retry')),
  );
  return switch (phase) {
    MailPhase.idle || MailPhase.loading when !hasData => MailState(
        icon: Icons.hourglass_top, title: l.t('mc.loading'), loading: true),
    MailPhase.denied =>
      MailState(icon: Icons.lock_outline, title: l.t('mc.denied')),
    MailPhase.offline => MailState(
        icon: Icons.cloud_off_outlined,
        title: l.t('mc.offline'),
        text: error,
        action: retry),
    MailPhase.error => MailState(
        icon: Icons.error_outline,
        title: l.t('mc.errorTitle'),
        text: error,
        action: retry),
    _ => null,
  };
}

/// Shows the controller's notices as snack bars while [child] is on screen.
class MailNotices extends StatefulWidget {
  const MailNotices({super.key, required this.controller, required this.child});
  final MailNoticeSource controller;
  final Widget child;

  @override
  State<MailNotices> createState() => _MailNoticesState();
}

class _MailNoticesState extends State<MailNotices> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_say);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_say);
    super.dispose();
  }

  void _say() {
    if (!mounted || widget.controller.notice == null) return;
    if (ModalRoute.of(context)?.isCurrent == false) return;
    final error = widget.controller.noticeIsError;
    final text = widget.controller.takeNotice()!;
    final l = AnumLocalizations.maybeOf(context);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(l.t(text)),
      backgroundColor: error ? Theme.of(context).colorScheme.error : null,
    ));
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

String mailWhen(BuildContext context, DateTime value) {
  final local = value.toLocal();
  final m = MaterialLocalizations.of(context);
  final now = DateTime.now();
  final today = now.year == local.year &&
      now.month == local.month &&
      now.day == local.day;
  return today
      ? m.formatTimeOfDay(TimeOfDay.fromDateTime(local))
      : m.formatShortDate(local);
}

// ============================================================================
// Mailboxes
// ============================================================================

class MailCenterScreen extends StatefulWidget {
  const MailCenterScreen({super.key, required this.controller});
  final MailCenterController controller;

  @override
  State<MailCenterScreen> createState() => _MailCenterScreenState();
}

class _MailCenterScreenState extends State<MailCenterScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(widget.controller.loadSetup);
  }

  void _openAccount(MailAccount account) {
    widget.controller.openAccount(account);
    Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => MailFoldersScreen(controller: widget.controller)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('mc.title')),
        actions: [
          ListenableBuilder(
            listenable: c,
            builder: (context, _) => (c.setup?.available ?? false)
                ? IconButton(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                          builder: (_) =>
                              MailContactsScreen(controller: c.directory)),
                    ),
                    tooltip: l.t('mc.c.title'),
                    icon: const Icon(Icons.contacts_outlined),
                  )
                : const SizedBox.shrink(),
          ),
          IconButton(
            onPressed: c.setupPhase == MailPhase.loading ? null : c.loadSetup,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: MailNotices(
        controller: c,
        child: ListenableBuilder(
          listenable: c,
          builder: (context, _) => mailPage(_body(context, l, c)),
        ),
      ),
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailCenterController c) {
    final state = stateFor(context, c.setupPhase, c.setupError,
        onRetry: c.loadSetup, hasData: c.setup != null);
    if (state != null) return state;
    final setup = c.setup!;
    if (!setup.enabled) {
      return MailState(icon: Icons.power_settings_new, title: l.t('mc.off'));
    }
    if (!setup.configured) {
      return MailState(icon: Icons.dns_outlined, title: l.t('mc.notSetUp'));
    }
    if (!setup.tenantAllowed) {
      return MailState(icon: Icons.block, title: l.t('mc.notAllowed'));
    }
    if (setup.accounts.isEmpty) {
      return MailState(
        icon: Icons.mail_outline,
        title: l.t('mc.noMailboxTitle'),
        text:
            setup.canAct ? l.t('mc.noMailboxText') : l.t('mc.noMailboxViewer'),
        action: setup.canAct
            ? FilledButton.icon(
                onPressed: c.busy == null ? () => _connect(context) : null,
                icon: const Icon(Icons.add),
                label: Text(l.t('mc.connect')),
              )
            : null,
      );
    }
    return RefreshIndicator(
      onRefresh: c.loadSetup,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          Text(l.t('mc.subtitle'),
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: AnumSpacing.sm),
          for (final (title, list) in [
            (l.t('mc.yourMailboxes'), c.ownAccounts),
            (l.t('mc.sharedWithYou'), c.sharedAccounts),
          ])
            if (list.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.only(
                    top: AnumSpacing.xs, bottom: AnumSpacing.xs),
                child: Semantics(
                  header: true,
                  child: Text(title,
                      style: Theme.of(context).textTheme.titleSmall),
                ),
              ),
              for (final account in list)
                _AccountCard(
                  controller: c,
                  account: account,
                  onOpen: () => _openAccount(account),
                  onPassword: () => _password(context, account),
                  onRemove: () => _remove(context, account),
                  onLeave: () => _leave(context, account),
                  onSettings: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                        builder: (_) => MailAccountSettingsScreen(
                            controller: c.directory, account: account)),
                  ).then((_) => c.loadSetup()),
                ),
            ],
          if (setup.canAct)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                onPressed: c.busy == null ? () => _connect(context) : null,
                icon: const Icon(Icons.add),
                label: Text(l.t('mc.connectAnother')),
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _connect(BuildContext context) => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) => _CredentialsSheet(
          controller: widget.controller,
          domains: widget.controller.setup?.allowedDomains ?? const [],
        ),
      );

  Future<void> _password(BuildContext context, MailAccount account) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) => _CredentialsSheet(
          controller: widget.controller,
          domains: const [],
          account: account,
        ),
      );

  Future<void> _remove(BuildContext context, MailAccount account) async {
    final l = AnumLocalizations.maybeOf(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('mc.removeTitle')),
        content: Text(l.t('mc.removeText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.remove'))),
        ],
      ),
    );
    if (yes == true) await widget.controller.disconnect(account);
  }

  Future<void> _leave(BuildContext context, MailAccount account) async {
    final l = AnumLocalizations.maybeOf(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('mc.leaveTitle')),
        content: Text(l.f('mc.leaveText', {'address': account.address})),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.leave'))),
        ],
      ),
    );
    if (yes == true) await widget.controller.leave(account);
  }
}

class _AccountCard extends StatelessWidget {
  const _AccountCard({
    required this.controller,
    required this.account,
    required this.onOpen,
    required this.onPassword,
    required this.onRemove,
    required this.onLeave,
    required this.onSettings,
  });
  final MailCenterController controller;
  final MailAccount account;
  final VoidCallback onOpen, onPassword, onRemove, onLeave, onSettings;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final scheme = Theme.of(context).colorScheme;
    final checked = account.lastCheckedAt;
    final canAct = controller.setup?.canAct ?? false;
    return Card(
      margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            ListTile(
              contentPadding: EdgeInsets.zero,
              onTap: onOpen,
              leading: const Icon(Icons.inbox_outlined),
              title: Text(account.displayName ?? account.address,
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text(
                [
                  if (account.displayName != null) account.address,
                  if (!account.isOwn) l.t('mc.access.${account.access}'),
                  if (account.isOwn && account.sharedWith > 0)
                    l.f('mc.sharedCount', {'count': account.sharedWith}),
                  l.t('mc.status.${account.status}'),
                  checked == null
                      ? l.t('mc.neverChecked')
                      : l.f('mc.lastChecked',
                          {'time': mailWhen(context, checked)}),
                ].join(' · '),
              ),
              trailing: const Icon(Icons.chevron_right),
            ),
            if (account.needsPassword)
              Text(l.t('mc.passwordRefused'),
                  style: TextStyle(color: scheme.error)),
            if (!account.healthy &&
                !account.needsPassword &&
                account.lastError != null)
              Text(account.lastError!, style: TextStyle(color: scheme.error)),
            if (canAct && !account.isOwn)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton.icon(
                  onPressed: controller.busy == null ? onLeave : null,
                  icon: const Icon(Icons.logout),
                  label: Text(l.t('mc.leave')),
                ),
              ),
            if (canAct && account.isOwn)
              Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  if (account.needsPassword)
                    FilledButton.icon(
                      onPressed: controller.busy == null ? onPassword : null,
                      icon: const Icon(Icons.key),
                      label: Text(l.t('mc.enterPassword')),
                    ),
                  OutlinedButton.icon(
                    onPressed: controller.busy == null
                        ? () => controller.check(account)
                        : null,
                    icon: const Icon(Icons.sync),
                    label: Text(l.t('mc.check')),
                  ),
                  OutlinedButton.icon(
                    onPressed: controller.busy == null ? onSettings : null,
                    icon: const Icon(Icons.manage_accounts_outlined),
                    label: Text(l.t('mc.settings')),
                  ),
                  TextButton.icon(
                    onPressed: controller.busy == null ? onRemove : null,
                    icon: const Icon(Icons.link_off),
                    label: Text(l.t('mc.remove')),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

/// Connect a mailbox, or enter a refused password again ([account] set).
class _CredentialsSheet extends StatefulWidget {
  const _CredentialsSheet(
      {required this.controller, required this.domains, this.account});
  final MailCenterController controller;
  final List<String> domains;
  final MailAccount? account;

  @override
  State<_CredentialsSheet> createState() => _CredentialsSheetState();
}

class _CredentialsSheetState extends State<_CredentialsSheet> {
  final _form = GlobalKey<FormState>();
  final _address = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();

  @override
  void dispose() {
    _address.dispose();
    _password.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final c = widget.controller;
    final account = widget.account;
    final ok = account == null
        ? await c.connect(_address.text, _password.text,
            displayName: _name.text)
        : await c.updatePassword(account, _password.text);
    _password.clear();
    if (ok && mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final account = widget.account;
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom,
          left: AnumSpacing.md,
          right: AnumSpacing.md,
          top: AnumSpacing.md),
      child: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => Form(
          key: _form,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  account == null ? l.t('mc.connect') : l.t('mc.enterPassword'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: AnumSpacing.sm),
                if (account == null) ...[
                  TextFormField(
                    controller: _address,
                    keyboardType: TextInputType.emailAddress,
                    autofillHints: const [AutofillHints.email],
                    textInputAction: TextInputAction.next,
                    decoration: InputDecoration(
                      labelText: l.t('mc.address'),
                      helperText: widget.domains.isEmpty
                          ? null
                          : l.f('mc.allowedDomains',
                              {'domains': widget.domains.join(', ')}),
                    ),
                    validator: (value) => RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
                            .hasMatch((value ?? '').trim())
                        ? null
                        : l.t('mc.addressInvalid'),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                ] else
                  Text(account.address),
                TextFormField(
                  controller: _password,
                  obscureText: true,
                  enableSuggestions: false,
                  autocorrect: false,
                  autofillHints: const [AutofillHints.password],
                  decoration: InputDecoration(
                    labelText: l.t('mc.appPassword'),
                    helperText: l.t('mc.appPasswordHint'),
                    helperMaxLines: 3,
                  ),
                  validator: (value) =>
                      (value ?? '').isEmpty ? l.t('mc.passwordRequired') : null,
                ),
                if (account == null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  TextFormField(
                    controller: _name,
                    maxLength: 200,
                    decoration:
                        InputDecoration(labelText: l.t('mc.displayName')),
                  ),
                ],
                const SizedBox(height: AnumSpacing.sm),
                Wrap(
                  alignment: WrapAlignment.end,
                  spacing: AnumSpacing.xs,
                  children: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: Text(l.t('mc.cancel'))),
                    FilledButton(
                      onPressed:
                          widget.controller.busy == null ? _submit : null,
                      child: Text(
                          account == null ? l.t('mc.connect') : l.t('mc.save')),
                    ),
                  ],
                ),
                const SizedBox(height: AnumSpacing.md),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ============================================================================
// Folders and drafts
// ============================================================================

class MailFoldersScreen extends StatelessWidget {
  const MailFoldersScreen({super.key, required this.controller});
  final MailCenterController controller;

  Future<void> _write(BuildContext context) async {
    if (await controller.startDraft('new') && context.mounted) {
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => MailComposeScreen(controller: controller)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final account = c.account;
        final canAct = c.canWrite && c.foldersPhase == MailPhase.ready;
        return Scaffold(
          appBar: AppBar(
            title: Text(account?.address ?? l.t('mc.folders'),
                overflow: TextOverflow.ellipsis),
            actions: [
              IconButton(
                onPressed:
                    c.foldersPhase == MailPhase.loading ? null : c.loadFolders,
                tooltip: l.t('refresh'),
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          floatingActionButton: canAct
              ? FloatingActionButton.extended(
                  onPressed: c.busy == null ? () => _write(context) : null,
                  icon: const Icon(Icons.edit_outlined),
                  label: Text(l.t('mc.write')),
                )
              : null,
          body:
              MailNotices(controller: c, child: mailPage(_body(context, l, c))),
        );
      },
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailCenterController c) {
    final account = c.account;
    if (account != null && account.needsPassword) {
      return MailState(
        icon: Icons.key_off_outlined,
        title: l.t('mc.passwordRefused'),
        text: account.isOwn ? null : l.t('mc.passwordRefusedShared'),
        action: account.isOwn
            ? FilledButton(
                onPressed: () => Navigator.pop(context),
                child: Text(l.t('mc.enterPassword')),
              )
            : null,
      );
    }
    final state = stateFor(context, c.foldersPhase, c.foldersError,
        onRetry: c.loadFolders, hasData: c.folders.isNotEmpty);
    if (state != null) return state;
    return RefreshIndicator(
      onRefresh: c.loadFolders,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.md, AnumSpacing.md, AnumSpacing.md, 96),
        children: [
          if (account != null && !account.isOwn)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: Text(l.t('mc.sharedNote.${account.access}'),
                  style: Theme.of(context).textTheme.bodySmall),
            ),
          Text(l.t('mc.folders'),
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AnumSpacing.xs),
          if (c.folders.isEmpty) Text(l.t('mc.foldersEmpty')),
          for (final folder in c.folders)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: ListTile(
                leading: Icon(_folderIcon(folder.role)),
                title: Text(folder.name,
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(l.f('mc.totalCount', {'count': folder.total})),
                trailing: folder.unread > 0 && folder.role != 'trash'
                    ? Semantics(
                        label: l.f('mc.unreadCount', {'count': folder.unread}),
                        child: Badge(
                          label: Text('${folder.unread}'),
                          largeSize: 22,
                        ),
                      )
                    : null,
                onTap: () {
                  c.openFolder(folder);
                  Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                        builder: (_) => MailListScreen(controller: c)),
                  );
                },
              ),
            ),
          if (account != null && account.canWrite)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: ListTile(
                leading: const Icon(Icons.schedule_send_outlined),
                title: Text(l.t('mc.s.title')),
                subtitle: Text(l.t('mc.s.subtitle')),
                onTap: () {
                  c.loadScheduled();
                  Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                        builder: (_) => MailScheduledScreen(controller: c)),
                  );
                },
              ),
            ),
          const SizedBox(height: AnumSpacing.md),
          Text(l.t('mc.writtenHere'),
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AnumSpacing.xs),
          if (c.drafts.isEmpty) Text(l.t('mc.noDrafts')),
          for (final draft in c.drafts)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: ListTile(
                leading: Icon(mailDraftIcon(draft.status)),
                title: Text(
                  draft.subject.isEmpty ? l.t('mc.noSubject') : draft.subject,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  [
                    mailDraftStatus(context, draft),
                    if (draft.to.isNotEmpty) draft.to.join(', '),
                  ].join(' · '),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () {
                  c.editDraft(draft);
                  Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                        builder: (_) => MailComposeScreen(controller: c)),
                  );
                },
              ),
            ),
        ],
      ),
    );
  }
}

String mailDraftStatus(BuildContext context, MailDraft draft) {
  final l = AnumLocalizations.maybeOf(context);
  final sendAt = draft.scheduledFor;
  if (sendAt != null) {
    return l.f(
        draft.status == 'scheduled'
            ? 'mc.d.scheduledApproved'
            : 'mc.d.scheduled',
        {'time': mailDateTime(context, sendAt)});
  }
  return l.t('mc.d.${draft.status}');
}

/// A date and time in the person's own time zone and format.
String mailDateTime(BuildContext context, DateTime value) {
  final m = MaterialLocalizations.of(context);
  final local = value.toLocal();
  return '${m.formatShortDate(local)} '
      '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local), alwaysUse24HourFormat: MediaQuery.alwaysUse24HourFormatOf(context))}';
}

IconData _folderIcon(String? role) => switch (role) {
      'inbox' => Icons.inbox_outlined,
      'sent' => Icons.send_outlined,
      'drafts' => Icons.drafts_outlined,
      'archive' => Icons.archive_outlined,
      'junk' => Icons.report_gmailerrorred_outlined,
      'trash' => Icons.delete_outline,
      _ => Icons.folder_outlined,
    };

IconData mailDraftIcon(String status) => switch (status) {
      'sent' => Icons.check_circle_outline,
      'pending_approval' => Icons.hourglass_top,
      'scheduled' => Icons.schedule_send_outlined,
      'failed' => Icons.error_outline,
      'unknown' => Icons.help_outline,
      'sending' => Icons.outbox_outlined,
      _ => Icons.edit_note,
    };

// ============================================================================
// A folder's messages
// ============================================================================

class MailListScreen extends StatefulWidget {
  const MailListScreen({super.key, required this.controller});
  final MailCenterController controller;

  @override
  State<MailListScreen> createState() => _MailListScreenState();
}

class _MailListScreenState extends State<MailListScreen> {
  final _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _open(MailMessage message) {
    widget.controller.read(message);
    Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => MailMessageScreen(controller: widget.controller)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(c.folder?.name ?? '', overflow: TextOverflow.ellipsis),
          actions: [
            IconButton(
              onPressed:
                  c.listPhase == MailPhase.loading ? null : c.loadMessages,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: MailNotices(controller: c, child: mailPage(_body(context, l, c))),
      ),
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailCenterController c) {
    final state = stateFor(context, c.listPhase, c.listError,
        onRetry: c.loadMessages, hasData: c.messages.isNotEmpty);
    if (state != null) return state;
    return RefreshIndicator(
      onRefresh: c.loadMessages,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          TextField(
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: (value) => c.search(value),
            decoration: InputDecoration(
              labelText: l.t('mc.search'),
              prefixIcon: const Icon(Icons.search),
            ),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              FilterChip(
                label: Text(l.t('mc.unread')),
                selected: c.unreadOnly,
                onSelected: (value) => c.search(_search.text, unread: value),
              ),
              FilterChip(
                label: Text(l.t('mc.starred')),
                selected: c.flaggedOnly,
                onSelected: (value) => c.search(_search.text, flagged: value),
              ),
              if (c.hasFilters)
                ActionChip(
                  label: Text(l.t('mc.clear')),
                  onPressed: () {
                    _search.clear();
                    c.search('', unread: false, flagged: false);
                  },
                ),
            ],
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.listPhase == MailPhase.loading) const LinearProgressIndicator(),
          if (c.messages.isEmpty && c.listPhase == MailPhase.ready)
            MailState(
              icon: c.hasFilters ? Icons.search_off : Icons.inbox_outlined,
              title: c.hasFilters ? l.t('mc.noMatch') : l.t('mc.emptyFolder'),
              action: c.hasFilters
                  ? OutlinedButton(
                      onPressed: () {
                        _search.clear();
                        c.search('', unread: false, flagged: false);
                      },
                      child: Text(l.t('mc.clear')),
                    )
                  : null,
            ),
          if (c.aiAvailable && c.messages.isNotEmpty)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                child: OutlinedButton.icon(
                  onPressed: c.busy == null ? c.labelPage : null,
                  icon: c.busy == 'label'
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.label_outline),
                  label: Text(l.f('mc.ai.labelAll', {
                    'count': c.messages
                        .where((m) => !c.labels.containsKey(m.uid))
                        .take(25)
                        .length
                  })),
                ),
              ),
            ),
          for (final message in c.messages)
            _MessageTile(
                message: message,
                label: c.labels[message.uid],
                onTap: () => _open(message)),
          if (c.messages.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Text(
                l.f('mc.shown', {'shown': c.messages.length, 'total': c.total}),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          if (c.nextBefore != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.sm),
              child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMore,
                child: c.loadingMore
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(l.t('mc.loadMore')),
              ),
            ),
        ],
      ),
    );
  }
}

class _MessageTile extends StatelessWidget {
  const _MessageTile({required this.message, required this.onTap, this.label});
  final MailMessage message;
  final MailLabel? label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final bold = !message.seen;
    return Card(
      margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
      child: ListTile(
        onTap: onTap,
        leading: Icon(
          message.seen ? Icons.drafts_outlined : Icons.mark_email_unread,
          semanticLabel: message.seen ? null : l.t('mc.unread'),
        ),
        title: Text(
          message.sender?.label ?? l.t('mc.unknownSender'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontWeight: bold ? FontWeight.bold : null),
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              message.subject.isEmpty ? l.t('mc.noSubject') : message.subject,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            if (label != null) MailLabelChips(label: label!),
          ],
        ),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(mailWhen(context, message.date),
                style: Theme.of(context).textTheme.bodySmall),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (message.maybeAttachments)
                  const Icon(Icons.attach_file, size: 16),
                if (message.flagged)
                  Icon(Icons.star,
                      size: 16,
                      semanticLabel: l.t('mc.starred'),
                      color: Theme.of(context).colorScheme.tertiary),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ============================================================================
// One message
// ============================================================================

class MailMessageScreen extends StatelessWidget {
  const MailMessageScreen({super.key, required this.controller});
  final MailCenterController controller;

  Future<void> _draft(BuildContext context, String kind) async {
    final source = controller.detail?.message;
    if (source == null) return;
    if (await controller.startDraft(kind, source: source) && context.mounted) {
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => MailComposeScreen(controller: controller)),
      );
    }
  }

  Future<void> _move(BuildContext context, String role) async {
    final message = controller.detail?.message;
    if (message == null) return;
    if (await controller.move(message, role) && context.mounted) {
      Navigator.pop(context);
    }
  }

  Future<void> _deleteForGood(BuildContext context) async {
    final l = AnumLocalizations.maybeOf(context);
    final message = controller.detail?.message;
    if (message == null) return;
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('mc.deleteForGoodTitle')),
        content: Text(l.t('mc.deleteForGoodText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.askToDelete'))),
        ],
      ),
    );
    if (yes == true) await controller.deleteForGood(message);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final detail = c.detail;
        final canAct = c.canWrite;
        final role = c.folder?.role;
        final canDelete = c.ownsAccount && (role == 'trash' || role == 'junk');
        return Scaffold(
          appBar: AppBar(
            title: Text(l.t('mc.compose')),
            actions: [
              if (detail != null && canAct) ...[
                IconButton(
                  onPressed: c.busy == null
                      ? () => c.toggleFlag(detail.message)
                      : null,
                  tooltip: detail.message.flagged
                      ? l.t('mc.unstar')
                      : l.t('mc.star'),
                  icon: Icon(
                      detail.message.flagged ? Icons.star : Icons.star_border),
                ),
                PopupMenuButton<String>(
                  tooltip: l.t('mc.more'),
                  enabled: c.busy == null,
                  onSelected: (value) => switch (value) {
                    'unread' => c.markUnread(detail.message),
                    'delete' => _deleteForGood(context),
                    _ => _move(context, value),
                  },
                  itemBuilder: (_) => [
                    PopupMenuItem(
                        value: 'unread', child: Text(l.t('mc.markUnread'))),
                    if (role != 'archive' &&
                        c.folderWithRole('archive') != null)
                      PopupMenuItem(
                          value: 'archive', child: Text(l.t('mc.archive'))),
                    if (role != 'junk' && c.folderWithRole('junk') != null)
                      PopupMenuItem(value: 'junk', child: Text(l.t('mc.junk'))),
                    if (role != 'inbox' && c.folderWithRole('inbox') != null)
                      PopupMenuItem(
                          value: 'inbox', child: Text(l.t('mc.toInbox'))),
                    if (role != 'trash' && c.folderWithRole('trash') != null)
                      PopupMenuItem(
                          value: 'trash', child: Text(l.t('mc.trash'))),
                    if (canDelete)
                      PopupMenuItem(
                          value: 'delete',
                          child: Text(l.t('mc.deleteForGood'))),
                  ],
                ),
              ],
            ],
          ),
          body:
              MailNotices(controller: c, child: mailPage(_body(context, l, c))),
        );
      },
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailCenterController c) {
    final detail = c.detail;
    final state = stateFor(context, c.detailPhase, c.detailError,
        onRetry: c.retryRead, hasData: detail != null);
    if (state != null) return state;
    if (detail == null) return const SizedBox.shrink();
    final message = detail.message;
    final theme = Theme.of(context);
    final canAct = c.canWrite;
    final deleteApproval = c.deleteApproval;
    String people(List<MailPerson> list) => list
        .map((p) => p.name.isEmpty ? p.address : '${p.name} <${p.address}>')
        .join(', ');
    return ListView(
      padding: const EdgeInsets.all(AnumSpacing.md),
      children: [
        Semantics(
          header: true,
          child: SelectableText(
            message.subject.isEmpty ? l.t('mc.noSubject') : message.subject,
            style: theme.textTheme.titleLarge,
          ),
        ),
        const SizedBox(height: AnumSpacing.xs),
        if (message.sender != null)
          Text(l.f('mc.from', {
            'from': people([message.sender!])
          })),
        if (message.to.isNotEmpty)
          Text(l.f('mc.to', {'to': people(message.to)})),
        if (message.cc.isNotEmpty)
          Text(l.f('mc.cc', {'cc': people(message.cc)})),
        if (detail.replyTo.isNotEmpty)
          Text(l.f('mc.replyTo', {'to': people(detail.replyTo)})),
        Text(MaterialLocalizations.of(context)
            .formatFullDate(message.date.toLocal())),
        const SizedBox(height: AnumSpacing.sm),
        if (canAct)
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              FilledButton.icon(
                onPressed:
                    c.busy == null ? () => _draft(context, 'reply') : null,
                icon: const Icon(Icons.reply),
                label: Text(l.t('mc.reply')),
              ),
              OutlinedButton.icon(
                onPressed:
                    c.busy == null ? () => _draft(context, 'reply_all') : null,
                icon: const Icon(Icons.reply_all),
                label: Text(l.t('mc.replyAll')),
              ),
              OutlinedButton.icon(
                onPressed:
                    c.busy == null ? () => _draft(context, 'forward') : null,
                icon: const Icon(Icons.forward),
                label: Text(l.t('mc.forward')),
              ),
            ],
          ),
        if (deleteApproval != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          MailApprovalCard(
            controller: c,
            approval: deleteApproval,
            title: l.t('mc.deleteApprovalTitle'),
            lines: [
              l.f('mc.deleteApprovalCount', {
                'count':
                    (deleteApproval.arguments['uids'] as List?)?.length ?? 1,
                'folder': c.folder?.name ?? message.folder,
              }),
            ],
            approveLabel: l.t('mc.approveDelete'),
            onApprove: () async {
              if (await c.approveDelete() && context.mounted) {
                Navigator.pop(context);
              }
            },
          ),
        ],
        if (c.aiAvailable || c.ai?.reason != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          MailAssistPanel(controller: c),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Text(l.t('mc.untrusted'), style: theme.textTheme.bodySmall),
        if (detail.remoteImagesBlocked > 0)
          Text(l.f('mc.remoteBlocked', {'count': detail.remoteImagesBlocked}),
              style: theme.textTheme.bodySmall),
        if (detail.inlineImagesHidden > 0)
          Text(l.f('mc.inlineHidden', {'count': detail.inlineImagesHidden}),
              style: theme.textTheme.bodySmall),
        if (detail.textFromHtml)
          Text(l.t('mc.htmlOnly'), style: theme.textTheme.bodySmall),
        const SizedBox(height: AnumSpacing.xs),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(AnumSpacing.md),
            child: SelectableText(
              detail.text.trim().isEmpty ? l.t('mc.emptyBody') : detail.text,
            ),
          ),
        ),
        if (detail.textTruncated)
          Text(l.t('mc.truncated'), style: theme.textTheme.bodySmall),
        if (detail.attachments.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('mc.attachments'), style: theme.textTheme.titleSmall),
          for (final file in detail.attachments)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.attach_file),
              title: Text(
                  file.filename.isEmpty ? file.contentType : file.filename,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
              subtitle: Text(file.downloadable
                  ? mailSize(file.size)
                  : '${mailSize(file.size)} · ${l.t('mc.tooLarge')}'),
              trailing: file.downloadable
                  ? IconButton(
                      tooltip: l.f('mc.download', {'name': file.saveName}),
                      onPressed: c.busy == null
                          ? () => c.downloadReceived(file)
                          : null,
                      icon: c.busy == 'download'
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.download_outlined),
                    )
                  : null,
            ),
          Text(l.t('mc.attachmentsNote'), style: theme.textTheme.bodySmall),
        ],
      ],
    );
  }
}

String mailSize(int bytes) {
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  if (bytes >= 1024) return '${(bytes / 1024).round()} KB';
  return '$bytes B';
}

// ============================================================================
// Writing and sending
// ============================================================================

class MailComposeScreen extends StatefulWidget {
  const MailComposeScreen({super.key, required this.controller});
  final MailCenterController controller;

  @override
  State<MailComposeScreen> createState() => _MailComposeScreenState();
}

class _MailComposeScreenState extends State<MailComposeScreen> {
  final _form = GlobalKey<FormState>();
  late final MailDraft? _initial = widget.controller.draft;
  late final _to = TextEditingController(text: _initial?.to.join(', '));
  late final _cc = TextEditingController(text: _initial?.cc.join(', '));
  late final _bcc = TextEditingController(text: _initial?.bcc.join(', '));
  late final _subject = TextEditingController(text: _initial?.subject);
  late final _body = TextEditingController(text: _initial?.body);
  late bool _showCopies =
      (_initial?.cc.isNotEmpty ?? false) || (_initial?.bcc.isNotEmpty ?? false);

  @override
  void dispose() {
    for (final c in [_to, _cc, _bcc, _subject, _body]) {
      c.dispose();
    }
    super.dispose();
  }

  MailDraftEdit get _edit => MailDraftEdit(
        to: MailDraftEdit.split(_to.text),
        cc: MailDraftEdit.split(_cc.text),
        bcc: MailDraftEdit.split(_bcc.text),
        subject: _subject.text,
        body: _body.text,
      );

  static final _address = RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$');

  String? _checkAddresses(String? text, {bool required = false}) {
    final l = AnumLocalizations.maybeOf(context);
    final list = MailDraftEdit.split(text ?? '');
    if (required && _edit.to.isEmpty) return l.t('mc.needsRecipient');
    final bad = [
      for (final a in list)
        if (!_address.hasMatch(a)) a
    ];
    if (bad.isNotEmpty) return l.f('mc.badAddresses', {'list': bad.join(', ')});
    final max = widget.controller.setup?.maxRecipients ?? 50;
    final edit = _edit;
    if (edit.to.length + edit.cc.length + edit.bcc.length > max) {
      return l.f('mc.tooMany', {'max': max});
    }
    return null;
  }

  Future<void> _send() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    await widget.controller.askToSend(_edit);
  }

  Future<void> _delete(MailDraft draft) async {
    final l = AnumLocalizations.maybeOf(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('mc.deleteDraftTitle')),
        content: Text(l.t('mc.deleteDraftText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.delete'))),
        ],
      ),
    );
    if (yes == true && await widget.controller.deleteDraft(draft) && mounted) {
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final draft = c.draft;
        return Scaffold(
          appBar: AppBar(
            title: Text(l.t('mc.compose')),
            actions: [
              if (draft != null && draft.status != 'sending' && c.canWrite)
                IconButton(
                  onPressed: c.busy == null ? () => _delete(draft) : null,
                  tooltip: l.t('mc.deleteDraft'),
                  icon: const Icon(Icons.delete_outline),
                ),
            ],
          ),
          body: MailNotices(
            controller: c,
            child: mailPage(draft == null
                ? MailState(
                    icon: Icons.error_outline, title: l.t('mc.errorTitle'))
                : _content(context, l, c, draft)),
          ),
        );
      },
    );
  }

  Future<void> _pickSender(String? identityId) async {
    final c = widget.controller;
    if (await c.setIdentity(_edit, identityId)) {
      final body = c.draft?.body;
      if (body != null && mounted) setState(() => _body.text = body);
    }
  }

  Future<void> _schedule(MailDraft draft) async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final c = widget.controller;
    final when = await pickSendTime(context, c, initial: draft.sendAt);
    if (when == null || !mounted) return;
    await c.askToSend(_edit, sendAt: when);
  }

  Widget _content(BuildContext context, AnumLocalizations l,
      MailCenterController c, MailDraft draft) {
    final theme = Theme.of(context);
    final editable = draft.editable && draft.status != 'sending' && c.canWrite;
    final account = c.account;
    final identities = c.identities;
    final approval = draft.approval;
    final scheduledFor = draft.scheduledFor;
    final busy = c.busy != null || c.uploads.isNotEmpty;
    return Form(
      key: _form,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          if (identities.isNotEmpty && account != null)
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: identities.any((i) => i.id == draft.identityId)
                  ? draft.identityId
                  : '',
              decoration: InputDecoration(labelText: l.t('mc.fromField')),
              items: [
                for (final identity in identities)
                  DropdownMenuItem(
                    value: identity.id,
                    child:
                        Text(identity.label, overflow: TextOverflow.ellipsis),
                  ),
                DropdownMenuItem(
                  value: '',
                  child: Text(account.address, overflow: TextOverflow.ellipsis),
                ),
              ],
              onChanged: !editable || busy
                  ? null
                  : (value) => _pickSender(
                      value == null || value.isEmpty ? null : value),
            )
          else
            Text(
                l.f('mc.from',
                    {'from': draft.fromAddress ?? account?.address ?? ''}),
                style: theme.textTheme.bodySmall),
          _StatusLine(draft: draft),
          const SizedBox(height: AnumSpacing.xs),
          MailRecipientField(
            controller: _to,
            enabled: editable,
            label: l.t('mc.toField'),
            helperText: l.t('mc.addressesHint'),
            suggest: c.directory.suggest,
            suffixIcon: IconButton(
              tooltip: '${l.t('mc.ccField')} / ${l.t('mc.bccField')}',
              onPressed: () => setState(() => _showCopies = !_showCopies),
              icon: Icon(_showCopies ? Icons.expand_less : Icons.expand_more),
            ),
            validator: (v) => _checkAddresses(v, required: true),
          ),
          if (_showCopies) ...[
            const SizedBox(height: AnumSpacing.xs),
            MailRecipientField(
              controller: _cc,
              enabled: editable,
              label: l.t('mc.ccField'),
              suggest: c.directory.suggest,
              validator: _checkAddresses,
            ),
            const SizedBox(height: AnumSpacing.xs),
            MailRecipientField(
              controller: _bcc,
              enabled: editable,
              label: l.t('mc.bccField'),
              suggest: c.directory.suggest,
              validator: _checkAddresses,
            ),
          ],
          const SizedBox(height: AnumSpacing.xs),
          TextFormField(
            controller: _subject,
            enabled: editable,
            maxLength: 500,
            decoration: InputDecoration(labelText: l.t('mc.subject')),
          ),
          TextFormField(
            controller: _body,
            enabled: editable,
            minLines: 8,
            maxLines: 20,
            keyboardType: TextInputType.multiline,
            decoration: InputDecoration(
                labelText: l.t('mc.body'), alignLabelWithHint: true),
          ),
          const SizedBox(height: AnumSpacing.xs),
          MailAttachmentsSection(
              controller: c, draft: draft, editable: editable),
          if (draft.approvalNote != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Text(draft.approvalNote!),
            ),
          const SizedBox(height: AnumSpacing.sm),
          if (draft.waiting && approval != null)
            sendApprovalCard(context, c, draft, approval),
          if (scheduledFor != null && editable)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  onPressed: busy ? null : () => c.cancelSchedule(draft),
                  icon: const Icon(Icons.cancel_schedule_send_outlined),
                  label: Text(l.t('mc.s.cancel')),
                ),
              ),
            ),
          if (editable)
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                if (!draft.waiting && !draft.scheduled)
                  FilledButton.icon(
                    onPressed: busy ? null : _send,
                    icon: c.busy == 'send'
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.send),
                    label: Text(l.t('mc.askToSend')),
                  ),
                if (!draft.scheduled)
                  OutlinedButton.icon(
                    onPressed: busy ? null : () => _schedule(draft),
                    icon: c.busy == 'schedule'
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.schedule_send_outlined),
                    label: Text(l.t('mc.s.schedule')),
                  ),
                OutlinedButton.icon(
                  onPressed: busy
                      ? null
                      : () {
                          if (_form.currentState?.validate() ?? false) {
                            c.saveDraft(_edit);
                          }
                        },
                  icon: const Icon(Icons.save_outlined),
                  label: Text(l.t('mc.saveDraft')),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.draft});
  final MailDraft draft;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final scheme = Theme.of(context).colorScheme;
    final (String? text, Color? color) = switch (draft.status) {
      'sent' => (
          draft.savedToSent == false
              ? l.t('mc.d.sentNoCopy')
              : l.t('mc.d.sentText'),
          null
        ),
      'unknown' => (l.t('mc.d.unknownText'), scheme.error),
      'failed' => (
          l.f('mc.d.failedText', {'reason': draft.error ?? ''}),
          scheme.error
        ),
      'draft' || 'pending_approval' || 'sending' || 'scheduled' => (
          mailDraftStatus(context, draft),
          null
        ),
      _ => (null, null),
    };
    if (text == null) return const SizedBox.shrink();
    return Semantics(
      liveRegion: true,
      child: Padding(
        padding: const EdgeInsets.only(top: AnumSpacing.xs),
        child: Text(text,
            style: TextStyle(color: color, fontWeight: FontWeight.w600)),
      ),
    );
  }
}

/// An approval waiting for an owner: what it does, the full payload hash and
/// its expiry. Approving sends the hash back, so only what is shown runs.
class MailApprovalCard extends StatelessWidget {
  const MailApprovalCard({
    super.key,
    required this.controller,
    required this.approval,
    required this.title,
    required this.lines,
    required this.approveLabel,
    required this.onApprove,
  });
  final MailCenterController controller;
  final WorkspaceApproval approval;
  final String title, approveLabel;
  final List<String> lines;
  final VoidCallback onApprove;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final hash = approval.payloadHash ?? '';
    final expires = approval.expiresAt;
    return Semantics(
      container: true,
      label: title,
      child: Container(
        margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
          border: Border.all(color: Theme.of(context).colorScheme.tertiary),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
            for (final line in lines) Text(line),
            SelectableText(l.f('mc.approvalHash', {'hash': hash})),
            if (expires != null)
              Text(l.f('mc.approvalExpires', {
                'time': MaterialLocalizations.of(context)
                    .formatFullDate(expires.toLocal())
              })),
            const SizedBox(height: AnumSpacing.xs),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                onPressed: controller.busy == null && hash.isNotEmpty
                    ? onApprove
                    : null,
                icon: const Icon(Icons.verified_outlined),
                label: Text(approveLabel),
              ),
            ),
            Text(l.t('mc.approveOwners'),
                style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

/// The send approval of a draft: sender, recipients, the time when it is
/// scheduled and how many files go with it.
Widget sendApprovalCard(BuildContext context, MailCenterController c,
    MailDraft draft, WorkspaceApproval approval) {
  final l = AnumLocalizations.maybeOf(context);
  final to = approval.arguments['to'];
  final sendAt = DateTime.tryParse('${approval.arguments['send_at'] ?? ''}');
  final files = approval.arguments['attachments'];
  final count = files is List ? files.length : draft.attachments.length;
  return MailApprovalCard(
    controller: c,
    approval: approval,
    title: l.t('mc.approvalTitle'),
    lines: [
      l.f('mc.approvalFrom', {'from': '${approval.arguments['from'] ?? ''}'}),
      l.f('mc.approvalTo', {'to': to is List ? to.join(', ') : '${to ?? ''}'}),
      if (sendAt != null)
        l.f('mc.approvalWhen', {'time': mailDateTime(context, sendAt)}),
      if (count > 0) l.f('mc.approvalFiles', {'count': count}),
    ],
    approveLabel:
        sendAt != null ? l.t('mc.approveSchedule') : l.t('mc.approve'),
    onApprove: () => c.approve(approval),
  );
}
