import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'lifecycle_controller.dart';
import 'lifecycle_models.dart';
import 'lifecycle_strings.dart';

/// A download link opens in the browser when it is https (or plain http to
/// this device during local development); otherwise it is copied.
bool openableLink(Uri uri) {
  if (uri.host.isEmpty) return false;
  if (uri.scheme == 'https') return true;
  return uri.scheme == 'http' &&
      const {'localhost', '127.0.0.1', '10.0.2.2', '::1'}.contains(uri.host);
}

/// Settings -> Data export and deletion (docs/organisation-lifecycle.md):
/// export the whole organisation, schedule or cancel deleting it, and
/// schedule or cancel deleting this workspace. Everyone sees the facts; the
/// controls show only for the people the API allows.
class LifecycleScreen extends StatefulWidget {
  const LifecycleScreen(
      {required this.controller, this.openLink = openInBrowser, super.key});
  final LifecycleController controller;

  /// Opens a download link in the browser (url_launcher); tests pass a fake.
  final LinkOpener openLink;

  @override
  State<LifecycleScreen> createState() => _LifecycleScreenState();
}

class _LifecycleScreenState extends State<LifecycleScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // The controller writes its result sentences in the app's language.
    widget.controller.strings = LifecycleStrings.of(context);
  }

  @override
  void dispose() {
    widget.controller.dispose();
    super.dispose();
  }

  void _snack(String text) {
    ScaffoldMessenger.maybeOf(context)
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _download(LifecycleJob job, {required bool open}) async {
    final link = await widget.controller.exportLink(job.id);
    if (link == null || !mounted) return;
    final text = link.url.toString();
    if (open && openableLink(link.url) && await widget.openLink(link.url)) {
      return;
    }
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    final s = LifecycleStrings.of(context);
    final when = formatWhen(link.expiresAt, s);
    _snack(open
        ? s.t('openFailedCopied', {'when': when})
        : s.t('linkCopied', {'when': when}));
  }

  Future<void> _schedule(
      {required String title,
      required String expected,
      required String what,
      required String fieldLabel,
      required String confirmLabel,
      required Future<bool> Function(String) schedule,
      required int coolingOffHours}) async {
    final typed = await showDialog<String>(
        context: context,
        builder: (_) => _ConfirmDeletionDialog(
            title: title,
            expected: expected,
            what: what,
            fieldLabel: fieldLabel,
            confirmLabel: confirmLabel,
            coolingOffHours: coolingOffHours));
    if (typed == null) return;
    await schedule(typed);
  }

  Future<void> _cancel(String title, Future<bool> Function() cancel) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (context) {
          final s = LifecycleStrings.of(context);
          return AlertDialog(
              title: Text(title),
              content: Text(s.t('cancelText')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: Text(s.t('keepScheduled'))),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: Text(s.t('cancelDeletion'))),
              ]);
        });
    if (ok ?? false) await cancel();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final s = LifecycleStrings.of(context);

        return Scaffold(
            appBar: AppBar(title: Text(s.t('title'))),
            body: switch (c.phase) {
              LifecyclePhase.loading => _Status(
                  icon: Icons.inventory_2_outlined,
                  title: s.t('loading'),
                  loading: true),
              LifecyclePhase.permissionDenied => _Status(
                  icon: Icons.lock_outline,
                  title: s.t('permissionTitle'),
                  message: s.t('permission')),
              LifecyclePhase.offline => _Status(
                  icon: Icons.cloud_off_outlined,
                  title: s.t('offlineTitle'),
                  message: s.t('offlineText'),
                  retry: c.load),
              LifecyclePhase.error => _Status(
                  icon: Icons.error_outline,
                  title: s.t('unavailable'),
                  message: c.loadError,
                  retry: c.load),
              LifecyclePhase.ready => _content(context, c, c.summary!),
            });
      });

  Widget _content(
      BuildContext context, LifecycleController c, LifecycleSummary s) {
    final theme = Theme.of(context);
    final small = theme.textTheme.bodySmall;
    final t = LifecycleStrings.of(context);
    final busy = c.busy != null;
    final orgDeletion = s.organisationDeletion;
    final wsDeletion = s.workspaceDeletion;
    final orgDeleting = orgDeletion != null &&
        (orgDeletion.isScheduled || orgDeletion.isPurging);
    return RefreshIndicator(
        onRefresh: c.refresh,
        child: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 720),
                child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsetsDirectional.fromSTEB(
                        AnumSpacing.md,
                        AnumSpacing.sm,
                        AnumSpacing.md,
                        AnumSpacing.xl),
                    children: [
                      Text(t.t('lead'), style: small),
                      // Organisation export.
                      _Heading(t.t('exportHeading')),
                      Text(t.t('exportText', {
                        'retention': formatHours(s.exportRetentionHours, t),
                        'ttl': _ttl(s.exportLinkTtlSeconds, t)
                      })),
                      const SizedBox(height: AnumSpacing.sm),
                      if (!s.canManageOrganisation)
                        _Rights(t.t('orgRights'))
                      else ...[
                        Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: FilledButton.icon(
                                key: const Key('lifecycle-request-export'),
                                onPressed:
                                    busy || s.exportInProgress || orgDeleting
                                        ? null
                                        : c.requestExport,
                                icon: const Icon(Icons.archive_outlined),
                                label: Text(t.t(c.busy == LifecycleAction.export
                                    ? 'requesting'
                                    : 'requestExport')))),
                        if (s.exportInProgress)
                          Padding(
                              padding:
                                  const EdgeInsets.only(top: AnumSpacing.xs),
                              child:
                                  Text(t.t('exportInProgress'), style: small)),
                        if (orgDeleting)
                          Padding(
                              padding:
                                  const EdgeInsets.only(top: AnumSpacing.xs),
                              child: Text(t.t('exportBlocked'), style: small)),
                        _MessageFor(c, LifecycleAction.export),
                        _MessageFor(c, LifecycleAction.link),
                        const SizedBox(height: AnumSpacing.sm),
                        if (s.exports.isEmpty)
                          _Empty(t.t('noExports'))
                        else
                          for (final job in s.exports)
                            _ExportCard(
                                job: job,
                                linkBusy: c.busy == LifecycleAction.link &&
                                    c.linkJobId == job.id,
                                disabled: busy,
                                onOpen: () => _download(job, open: true),
                                onCopy: () => _download(job, open: false)),
                      ],
                      // Organisation deletion.
                      _Heading(t.t('orgHeading')),
                      Text(t.t('orgText',
                          {'period': formatHours(s.coolingOffHours, t)})),
                      const SizedBox(height: AnumSpacing.sm),
                      _DeletionState(
                          job: orgDeletion,
                          what: 'organisation',
                          canAct: s.canManageOrganisation,
                          busy: busy,
                          onCancel: () => _cancel(t.t('cancelOrgTitle'),
                              c.cancelOrganisationDeletion)),
                      if (!s.canManageOrganisation)
                        _Rights(t.t('orgRights'))
                      else if (!orgDeleting &&
                          s.organisationConfirmation != null)
                        Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: _DangerButton(
                                key: const Key('lifecycle-delete-organisation'),
                                label: t.t(c.busy ==
                                        LifecycleAction.organisationDeletion
                                    ? 'scheduling'
                                    : 'deleteOrg'),
                                onPressed: busy
                                    ? null
                                    : () => _schedule(
                                        title: t.t('deleteOrgTitle'),
                                        expected: s.organisationConfirmation!,
                                        what: t.t('deleteOrgWhat'),
                                        fieldLabel: t.t('deleteOrgField'),
                                        confirmLabel: t.t('deleteOrgConfirm'),
                                        coolingOffHours: s.coolingOffHours,
                                        schedule:
                                            c.scheduleOrganisationDeletion))),
                      _MessageFor(c, LifecycleAction.organisationDeletion),
                      // Workspace deletion.
                      _Heading(t.t('wsHeading')),
                      Text(t.t('wsText',
                          {'period': formatHours(s.coolingOffHours, t)})),
                      const SizedBox(height: AnumSpacing.sm),
                      _DeletionState(
                          job: wsDeletion,
                          what: 'workspace',
                          canAct: s.canDeleteWorkspace,
                          busy: busy,
                          onCancel: () => _cancel(
                              t.t('cancelWsTitle'), c.cancelWorkspaceDeletion)),
                      if (!s.canDeleteWorkspace)
                        _Rights(t.t('wsRights'))
                      else if (!(wsDeletion != null &&
                              (wsDeletion.isScheduled ||
                                  wsDeletion.isPurging)) &&
                          s.workspaceConfirmation != null)
                        Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: _DangerButton(
                                key: const Key('lifecycle-delete-workspace'),
                                label: t.t(
                                    c.busy == LifecycleAction.workspaceDeletion
                                        ? 'scheduling'
                                        : 'deleteWs'),
                                onPressed: busy
                                    ? null
                                    : () => _schedule(
                                        title: t.t('deleteWsTitle'),
                                        expected: s.workspaceConfirmation!,
                                        what: t.t('deleteWsWhat'),
                                        fieldLabel: t.t('deleteWsField'),
                                        confirmLabel: t.t('deleteWsConfirm'),
                                        coolingOffHours: s.coolingOffHours,
                                        schedule:
                                            c.scheduleWorkspaceDeletion))),
                      _MessageFor(c, LifecycleAction.workspaceDeletion),
                    ]))));
  }

  static String _ttl(int seconds, LifecycleStrings t) {
    if (seconds >= 3600 && seconds % 3600 == 0) {
      return formatHours(seconds ~/ 3600, t);
    }
    return t.count('minute', (seconds / 60).ceil());
  }
}

class _ExportCard extends StatelessWidget {
  const _ExportCard(
      {required this.job,
      required this.linkBusy,
      required this.disabled,
      required this.onOpen,
      required this.onCopy});
  final LifecycleJob job;
  final bool linkBusy, disabled;
  final VoidCallback onOpen, onCopy;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final small = theme.textTheme.bodySmall;
    final t = LifecycleStrings.of(context);
    final (icon, color) = switch (job.status) {
      'ready' => (Icons.check_circle_outline, scheme.primary),
      'failed' => (Icons.error_outline, scheme.error),
      'expired' => (Icons.history_toggle_off, scheme.onSurfaceVariant),
      _ => (Icons.hourglass_top, scheme.onSurfaceVariant),
    };
    final details = <String>[
      t.t('requested', {'when': formatWhen(job.requestedAt, t)}),
      if (job.archiveBytes != null)
        t.t('size', {'size': formatArchiveSize(job.archiveBytes)}),
      if (job.rowCount != null) t.t('rows', {'n': job.rowCount!}),
      if (job.fileCount != null) t.t('files', {'n': job.fileCount!}),
      if (job.isReady && job.expiresAt != null)
        t.t('keptUntil', {'when': formatWhen(job.expiresAt, t)}),
    ];
    return Card(
        key: ValueKey('lifecycle-export-${job.id}'),
        margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
        child: Padding(
            padding: const EdgeInsets.all(AnumSpacing.sm),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(icon, color: color, size: 22),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: Text(job.statusLabelIn(t),
                        style: theme.textTheme.titleSmall)),
                if (job.inProgress)
                  const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2)),
              ]),
              const SizedBox(height: AnumSpacing.xxs),
              Text(details.join(' · '), style: small),
              if (job.isFailed && job.error != null) ...[
                const SizedBox(height: AnumSpacing.xxs),
                Text(job.error!, style: small?.copyWith(color: scheme.error)),
              ],
              if (job.status == 'expired')
                Text(t.t('archiveDeleted'), style: small),
              if (job.isReady) ...[
                const SizedBox(height: AnumSpacing.xs),
                Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      FilledButton.tonalIcon(
                          onPressed: disabled ? null : onOpen,
                          icon: linkBusy
                              ? const SizedBox.square(
                                  dimension: 16,
                                  child:
                                      CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.open_in_new),
                          label: Text(t.t('openLink'))),
                      OutlinedButton.icon(
                          onPressed: disabled ? null : onCopy,
                          icon: const Icon(Icons.copy),
                          label: Text(t.t('copyLink'))),
                    ]),
              ],
            ])));
  }
}

/// The open deletion: a banner while scheduled (with the date and, for
/// those allowed, Cancel deletion), purging or failed.
class _DeletionState extends StatelessWidget {
  const _DeletionState(
      {required this.job,
      required this.what,
      required this.canAct,
      required this.busy,
      required this.onCancel});
  final LifecycleJob? job;
  final String what;
  final bool canAct, busy;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    final job = this.job;
    if (job == null || !(job.isScheduled || job.isPurging || job.isFailed)) {
      return const SizedBox.shrink();
    }
    final scheme = Theme.of(context).colorScheme;
    final t = LifecycleStrings.of(context);
    final noun = t.t('what.$what');
    final (title, body) = switch (job.status) {
      'scheduled' => (
          t.t('banner.scheduled',
              {'what': noun, 'when': formatWhen(job.purgeAfter, t)}),
          t.t('banner.scheduledBody', {
                'when': formatWhen(job.requestedAt, t),
                'by': job.requestedBy == null
                    ? ''
                    : t.t('banner.by', {'who': job.requestedBy!})
              }) +
              (canAct ? '' : t.t('banner.askOwner'))
        ),
      'purging' => (
          t.t('banner.purging', {'what': noun}),
          t.t('banner.purgingBody',
              {'when': formatWhen(job.startedAt ?? job.purgeAfter, t)})
        ),
      _ => (
          t.t('banner.failed', {'what': noun}),
          t.t('banner.failedBody',
              {'error': job.error ?? t.t('banner.noReason')})
        ),
    };
    return Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
        child: Semantics(
            liveRegion: true,
            container: true,
            child: Container(
                key: Key('lifecycle-$what-banner'),
                width: double.infinity,
                padding: const EdgeInsets.all(AnumSpacing.sm),
                decoration: BoxDecoration(
                    color: scheme.errorContainer,
                    borderRadius: BorderRadius.circular(12)),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(
                                job.isPurging
                                    ? Icons.delete_sweep_outlined
                                    : Icons.warning_amber_outlined,
                                color: scheme.onErrorContainer),
                            const SizedBox(width: AnumSpacing.xs),
                            Expanded(
                                child: Text(title,
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleSmall
                                        ?.copyWith(
                                            color: scheme.onErrorContainer))),
                          ]),
                      const SizedBox(height: AnumSpacing.xxs),
                      Text(body,
                          style: TextStyle(color: scheme.onErrorContainer)),
                      if (job.isScheduled && canAct) ...[
                        const SizedBox(height: AnumSpacing.xs),
                        FilledButton.icon(
                            key: Key('lifecycle-cancel-$what'),
                            onPressed: busy ? null : onCancel,
                            icon: const Icon(Icons.undo),
                            label: Text(t.t('cancelDeletion'))),
                      ],
                    ]))));
  }
}

class _ConfirmDeletionDialog extends StatefulWidget {
  const _ConfirmDeletionDialog(
      {required this.title,
      required this.expected,
      required this.what,
      required this.fieldLabel,
      required this.confirmLabel,
      required this.coolingOffHours});
  final String title, expected, what, fieldLabel, confirmLabel;
  final int coolingOffHours;

  @override
  State<_ConfirmDeletionDialog> createState() => _ConfirmDeletionDialogState();
}

class _ConfirmDeletionDialogState extends State<_ConfirmDeletionDialog> {
  final _typed = TextEditingController();

  @override
  void initState() {
    super.initState();
    _typed.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _typed.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final t = LifecycleStrings.of(context);
    final ready = confirmationMatches(widget.expected, _typed.text);
    return AlertDialog(
        scrollable: true,
        title: Text(widget.title),
        content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(t.t('dialog.text', {
                'what': widget.what,
                'period': formatHours(widget.coolingOffHours, t)
              })),
              const SizedBox(height: AnumSpacing.sm),
              Text(t.t('dialog.typeExactly'), style: theme.textTheme.bodySmall),
              SelectableText(widget.expected,
                  key: const Key('lifecycle-expected'),
                  style: theme.textTheme.titleSmall),
              const SizedBox(height: AnumSpacing.xs),
              TextField(
                  key: const Key('lifecycle-confirm'),
                  controller: _typed,
                  autofocus: true,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: InputDecoration(
                      labelText: widget.fieldLabel,
                      helperText: _typed.text.isEmpty || ready
                          ? null
                          : t.t('dialog.mismatch'),
                      helperMaxLines: 3)),
            ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: Text(t.t('dialog.keep'))),
          FilledButton(
              key: const Key('lifecycle-confirm-button'),
              style: FilledButton.styleFrom(
                  backgroundColor: theme.colorScheme.error,
                  foregroundColor: theme.colorScheme.onError),
              onPressed:
                  ready ? () => Navigator.pop(context, _typed.text) : null,
              child: Text(widget.confirmLabel)),
        ]);
  }
}

class _DangerButton extends StatelessWidget {
  const _DangerButton({required this.label, this.onPressed, super.key});
  final String label;
  final VoidCallback? onPressed;
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return FilledButton.icon(
        style: FilledButton.styleFrom(
            backgroundColor: scheme.error, foregroundColor: scheme.onError),
        onPressed: onPressed,
        icon: const Icon(Icons.delete_forever_outlined),
        label: Text(label));
  }
}

class _Rights extends StatelessWidget {
  const _Rights(this.text);
  final String text;
  @override
  Widget build(BuildContext context) =>
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Icon(Icons.lock_outline, size: 20),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(
            child: Text(text, style: Theme.of(context).textTheme.bodySmall)),
      ]);
}

class _Empty extends StatelessWidget {
  const _Empty(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Card(
      child: ListTile(
          leading: const Icon(Icons.inventory_2_outlined), title: Text(text)));
}

class _MessageFor extends StatelessWidget {
  const _MessageFor(this.controller, this.action);
  final LifecycleController controller;
  final LifecycleAction action;
  @override
  Widget build(BuildContext context) {
    final message = controller.messages[action];
    if (message == null) return const SizedBox.shrink();
    final (text, error) = message;
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
      child: SingleChildScrollView(
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
                          label: Text(LifecycleStrings.of(context).t('retry')))
                    ]
                  ])))));
}
