import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../developer/developer_models.dart' show safeLink;
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'privacy_controller.dart';
import 'privacy_models.dart';

/// Settings -> Privacy (docs/privacy.md): what ANUM stores about you in this
/// workspace, how long it is kept, where messages go, and the two rights
/// actions. "Delete my data" is the in-app deletion path Google Play and the
/// App Store require.
class PrivacyScreen extends StatefulWidget {
  const PrivacyScreen(
      {required this.controller,
      this.openLink = openInBrowser,
      this.onOpenChatMemory,
      super.key});
  final PrivacyController controller;

  /// Leads to the "Remember things from my chats" switch (in Settings) when
  /// the caller turned it off; hidden when null.
  final VoidCallback? onOpenChatMemory;

  /// Opens the policy in the browser (url_launcher); tests pass a fake.
  final LinkOpener openLink;
  @override
  State<PrivacyScreen> createState() => _PrivacyScreenState();
}

class _PrivacyScreenState extends State<PrivacyScreen> {
  final _typed = TextEditingController();

  @override
  void initState() {
    super.initState();
    widget.controller.load();
    _typed.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _typed.dispose();
    super.dispose();
  }

  Future<void> _delete() async {
    final ok = await widget.controller.deleteMyData(_typed.text);
    if (ok) _typed.clear();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return Scaffold(
            appBar: AppBar(title: const Text('Privacy')),
            body: switch (c.phase) {
              PrivacyPhase.loading => const _Status(
                  icon: Icons.privacy_tip_outlined,
                  title: 'Loading your privacy summary',
                  loading: true),
              PrivacyPhase.permissionDenied => const _Status(
                  icon: Icons.lock_outline,
                  title: 'Permission required',
                  message: privacyPermissionMessage),
              PrivacyPhase.offline => _Status(
                  icon: Icons.cloud_off_outlined,
                  title: 'You are offline',
                  message: 'Reconnect to see and manage your data.',
                  retry: c.load),
              PrivacyPhase.error => _Status(
                  icon: Icons.error_outline,
                  title: 'Privacy summary unavailable',
                  message: c.loadError,
                  retry: c.load),
              PrivacyPhase.ready => _content(context, c, c.summary!),
            });
      });

  Widget _content(BuildContext context, PrivacyController c, PrivacySummary s) {
    final theme = Theme.of(context);
    final privateTotal = totalOf(s.private), sharedTotal = totalOf(s.shared);
    final ready = s.matches(_typed.text) && !c.deleting;
    return Center(
        child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
                padding: const EdgeInsetsDirectional.fromSTEB(AnumSpacing.md,
                    AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xl),
                children: [
                  _Heading('What ANUM stores about you'),
                  Text(
                      'Signed in as ${s.userId}${s.role == null ? '' : ' (${s.role})'} in ${s.workspaceId}. Other workspaces keep their own data.',
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  _Counts(title: 'Private to you', counts: s.private),
                  if (privateTotal == 0)
                    const Padding(
                        padding: EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(
                            'Nothing private is stored for you here yet.')),
                  _Counts(title: 'Shared with the workspace', counts: s.shared),
                  Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child:
                          Text(s.sharedNote, style: theme.textTheme.bodySmall)),
                  _Heading('How long it is kept, and where it goes'),
                  Card(
                      child: Column(children: [
                    for (final (label, value) in s.retention)
                      ListTile(
                          dense: true,
                          title: Text(label),
                          subtitle: Text(value)),
                  ])),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(s.providerSentence, style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(s.memorySentence,
                      key: const Key('privacy-memory'),
                      style: theme.textTheme.bodySmall),
                  if (s.offerChatMemorySwitch &&
                      widget.onOpenChatMemory != null)
                    Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: TextButton.icon(
                            onPressed: widget.onOpenChatMemory,
                            icon: const Icon(Icons.tune),
                            label: const Text(
                                'Change Remember things from my chats'))),
                  const SizedBox(height: AnumSpacing.xs),
                  _PolicyLink(url: s.policyUrl, open: widget.openLink),
                  _Heading('Download my data'),
                  Text(
                      'A JSON file with your profile in this workspace, conversations, private memories, voice transcripts, tasks you created, approvals you decided, notification preferences, your preferences, the list of files you uploaded, what ANUM learned from you, the mailboxes you connected with your email drafts, the WhatsApp messages you sent and the actions ANUM took for you. Up to ${formatBytes(s.exportMaxBytes)}.'),
                  const SizedBox(height: AnumSpacing.sm),
                  Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                          onPressed: c.exporting ? null : c.exportMyData,
                          icon: const Icon(Icons.download_outlined),
                          label: Text(c.exporting
                              ? 'Preparing download...'
                              : 'Download my data'))),
                  if (c.exportMessage != null)
                    _Message(text: c.exportMessage!, error: c.exportFailed),
                  _Heading('Delete my data'),
                  Text(
                      'Permanently deletes your conversations, private memories (and their search index entries), voice sessions and transcripts, notification preferences, what ANUM learned from you, the private mailboxes you connected (ANUM also withdraws its access at the provider) and the email drafts you never sent in this workspace, and resets your preferences (such as Remember things from my chats) in every workspace of this organization. This cannot be undone.'),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      'Kept: ${sharedTotal == 0 ? 'shared workspace records (none created by you)' : '$sharedTotal shared workspace ${sharedTotal == 1 ? 'record' : 'records'} you created or decided'}. A workspace owner can remove them.',
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  Text('Your account: ${s.accountNote}',
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.sm),
                  TextField(
                      key: const Key('privacy-confirm'),
                      controller: _typed,
                      enabled: !c.deleting,
                      autocorrect: false,
                      enableSuggestions: false,
                      textCapitalization: TextCapitalization.characters,
                      decoration: InputDecoration(
                          labelText: 'Type ${s.confirmation} to confirm',
                          helperText: privateTotal == 0
                              ? 'Nothing private is stored for you right now.'
                              : '$privateTotal private ${privateTotal == 1 ? 'item' : 'items'} will be deleted.')),
                  const SizedBox(height: AnumSpacing.sm),
                  Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                          style: FilledButton.styleFrom(
                              backgroundColor: theme.colorScheme.error,
                              foregroundColor: theme.colorScheme.onError),
                          onPressed: ready ? _delete : null,
                          icon: const Icon(Icons.delete_forever_outlined),
                          label: Text(
                              c.deleting ? 'Deleting...' : 'Delete my data'))),
                  if (c.deleteMessage != null)
                    _Message(text: c.deleteMessage!, error: c.deleteFailed),
                ])));
  }
}

class _Counts extends StatelessWidget {
  const _Counts({required this.title, required this.counts});
  final String title;
  final List<PrivacyCount> counts;
  @override
  Widget build(BuildContext context) => Card(
      margin: const EdgeInsets.only(top: AnumSpacing.xs),
      child: Column(children: [
        ListTile(
            title: Text(title, style: Theme.of(context).textTheme.titleSmall)),
        for (final item in counts)
          ListTile(
              dense: true,
              title: Text(item.label),
              trailing: Text('${item.value}',
                  style: Theme.of(context).textTheme.titleSmall)),
      ]));
}

class _PolicyLink extends StatelessWidget {
  const _PolicyLink({required this.url, required this.open});
  final String? url;
  final LinkOpener open;

  Future<void> _copy(BuildContext context, String link, String note) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    await Clipboard.setData(ClipboardData(text: link));
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(note)));
  }

  /// Opens an https policy in the browser; otherwise (or when no browser
  /// opens it) copies the link and says so.
  Future<void> _open(BuildContext context, String link) async {
    final uri = safeLink(link);
    final opened = uri != null && await open(uri);
    if (!opened && context.mounted) {
      await _copy(
          context,
          link,
          uri == null
              ? 'The policy link is not an https address, so it was copied instead of opened.'
              : 'The browser could not open the policy. The link was copied; paste it into a browser.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final link = url;
    if (link == null) {
      return const Text(
          'Your organization has not published a privacy policy link yet. Ask the workspace owner for it.');
    }
    return Card(
        child: ListTile(
            leading: const Icon(Icons.policy_outlined),
            title: const Text('Read the privacy policy'),
            subtitle: Text(link, textDirection: TextDirection.ltr),
            onTap: () => _open(context, link),
            trailing: IconButton(
                tooltip: 'Copy privacy policy link',
                icon: const Icon(Icons.copy),
                onPressed: () =>
                    _copy(context, link, 'Privacy policy link copied.'))));
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.text, required this.error});
  final String text;
  final bool error;
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
        padding: const EdgeInsets.only(top: AnumSpacing.sm),
        child: Semantics(
            liveRegion: true,
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(error ? Icons.error_outline : Icons.check_circle_outline,
                  size: 20, color: error ? scheme.error : scheme.primary),
              const SizedBox(width: AnumSpacing.xs),
              Expanded(child: Text(text)),
            ])));
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsetsDirectional.fromSTEB(4, 20, 4, 8),
      child: Semantics(
          header: true,
          child: Text(text, style: Theme.of(context).textTheme.titleMedium)));
}

class _Status extends StatelessWidget {
  const _Status(
      {required this.icon,
      required this.title,
      this.message,
      this.retry,
      this.loading = false});
  final IconData icon;
  final String title;
  final String? message;
  final VoidCallback? retry;
  final bool loading;
  @override
  Widget build(BuildContext context) => Center(
      child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.lg),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                if (loading)
                  const CircularProgressIndicator()
                else
                  Icon(icon, size: 42),
                const SizedBox(height: AnumSpacing.md),
                Text(title,
                    style: Theme.of(context).textTheme.titleLarge,
                    textAlign: TextAlign.center),
                if (message != null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  Text(message!, textAlign: TextAlign.center)
                ],
                if (retry != null) ...[
                  const SizedBox(height: AnumSpacing.md),
                  OutlinedButton.icon(
                      onPressed: retry,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Retry'))
                ]
              ]))));
}
