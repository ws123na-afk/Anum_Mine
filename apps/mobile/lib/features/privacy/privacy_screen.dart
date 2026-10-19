import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../developer/developer_models.dart' show safeLink;
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'privacy_controller.dart';
import 'privacy_models.dart';
import 'privacy_strings.dart';

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
  void didChangeDependencies() {
    super.didChangeDependencies();
    // The controller writes its result sentences in the app's language.
    widget.controller.strings = PrivacyStrings.of(context);
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
        final p = PrivacyStrings.of(context);
        return Scaffold(
            appBar: AppBar(title: Text(p.t('title'))),
            body: switch (c.phase) {
              PrivacyPhase.loading => _Status(
                  icon: Icons.privacy_tip_outlined,
                  title: p.t('loading'),
                  loading: true),
              PrivacyPhase.permissionDenied => _Status(
                  icon: Icons.lock_outline,
                  title: p.t('permissionTitle'),
                  message: p.t('permission')),
              PrivacyPhase.offline => _Status(
                  icon: Icons.cloud_off_outlined,
                  title: p.t('offlineTitle'),
                  message: p.t('offlineText'),
                  retry: c.load),
              PrivacyPhase.error => _Status(
                  icon: Icons.error_outline,
                  title: p.t('unavailable'),
                  message: c.loadError,
                  retry: c.load),
              PrivacyPhase.ready => _content(context, c, c.summary!),
            });
      });

  Widget _content(BuildContext context, PrivacyController c, PrivacySummary s) {
    final theme = Theme.of(context);
    final p = PrivacyStrings.of(context);
    final privateTotal = totalOf(s.private), sharedTotal = totalOf(s.shared);
    final ready = s.matches(_typed.text) && !c.deleting;
    return Center(
        child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
                padding: const EdgeInsetsDirectional.fromSTEB(AnumSpacing.md,
                    AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xl),
                children: [
                  _Heading(p.t('storesHeading')),
                  Text(
                      p.t('signedIn', {
                        'user': s.userId,
                        'role': s.role == null
                            ? ''
                            : p.t('role', {'role': s.role!}),
                        'workspace': s.workspaceId
                      }),
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  _Counts(
                      title: p.t('privateTitle'),
                      group: 'private',
                      counts: s.private),
                  if (privateTotal == 0)
                    Padding(
                        padding: const EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(p.t('nothingPrivate'))),
                  _Counts(
                      title: p.t('sharedTitle'),
                      group: 'shared',
                      counts: s.shared),
                  Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child:
                          Text(s.sharedNote, style: theme.textTheme.bodySmall)),
                  _Heading(p.t('keptHeading')),
                  Card(
                      child: Column(children: [
                    for (final (label, value) in s.retentionIn(p))
                      ListTile(
                          dense: true,
                          title: Text(label),
                          subtitle: Text(value)),
                  ])),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(s.providerSentenceIn(p),
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(s.memorySentenceIn(p),
                      key: const Key('privacy-memory'),
                      style: theme.textTheme.bodySmall),
                  if (s.offerChatMemorySwitch &&
                      widget.onOpenChatMemory != null)
                    Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: TextButton.icon(
                            onPressed: widget.onOpenChatMemory,
                            icon: const Icon(Icons.tune),
                            label: Text(p.t('changeMemory')))),
                  const SizedBox(height: AnumSpacing.xs),
                  _PolicyLink(url: s.policyUrl, open: widget.openLink),
                  _Heading(p.t('downloadHeading')),
                  Text(p.t(
                      'downloadText', {'max': formatBytes(s.exportMaxBytes)})),
                  const SizedBox(height: AnumSpacing.sm),
                  Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                          onPressed: c.exporting ? null : c.exportMyData,
                          icon: const Icon(Icons.download_outlined),
                          label: Text(
                              p.t(c.exporting ? 'preparing' : 'download')))),
                  if (c.exportMessage != null)
                    _Message(text: c.exportMessage!, error: c.exportFailed),
                  _Heading(p.t('deleteHeading')),
                  Text(p.t('deleteText')),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      sharedTotal == 0
                          ? p.t('keptNone')
                          : p.count('kept', sharedTotal),
                      style: theme.textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(p.t('account', {'note': s.accountNote}),
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
                          labelText:
                              p.t('typeToConfirm', {'phrase': s.confirmation}),
                          helperText: privateTotal == 0
                              ? p.t('nothingNow')
                              : p.count('willDelete', privateTotal))),
                  const SizedBox(height: AnumSpacing.sm),
                  Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                          style: FilledButton.styleFrom(
                              backgroundColor: theme.colorScheme.error,
                              foregroundColor: theme.colorScheme.onError),
                          onPressed: ready ? _delete : null,
                          icon: const Icon(Icons.delete_forever_outlined),
                          label:
                              Text(p.t(c.deleting ? 'deleting' : 'delete')))),
                  if (c.deleteMessage != null)
                    _Message(text: c.deleteMessage!, error: c.deleteFailed),
                ])));
  }
}

class _Counts extends StatelessWidget {
  const _Counts(
      {required this.title, required this.group, required this.counts});
  final String title;

  /// `private` or `shared`: the catalog prefix of the count labels.
  final String group;
  final List<PrivacyCount> counts;
  @override
  Widget build(BuildContext context) {
    final p = PrivacyStrings.of(context);
    String label(PrivacyCount item) {
      final key = '$group.${item.key}';
      return PrivacyStrings.en.containsKey(key) ? p.t(key) : item.label;
    }

    return Card(
        margin: const EdgeInsets.only(top: AnumSpacing.xs),
        child: Column(children: [
          ListTile(
              title:
                  Text(title, style: Theme.of(context).textTheme.titleSmall)),
          for (final item in counts)
            ListTile(
                dense: true,
                title: Text(label(item)),
                trailing: Text('${item.value}',
                    style: Theme.of(context).textTheme.titleSmall)),
        ]));
  }
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
      final p = PrivacyStrings.of(context);
      await _copy(context, link,
          p.t(uri == null ? 'policyNotHttps' : 'policyBrowserFailed'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final link = url;
    final p = PrivacyStrings.of(context);
    if (link == null) return Text(p.t('noPolicy'));
    return Card(
        child: ListTile(
            leading: const Icon(Icons.policy_outlined),
            title: Text(p.t('readPolicy')),
            subtitle: Text(link, textDirection: TextDirection.ltr),
            onTap: () => _open(context, link),
            trailing: IconButton(
                tooltip: p.t('copyPolicy'),
                icon: const Icon(Icons.copy),
                onPressed: () => _copy(context, link, p.t('policyCopied')))));
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
                      label: Text(PrivacyStrings.of(context).t('retry')))
                ]
              ]))));
}
