import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'org_admin_models.dart';
import 'org_admin_repository.dart';
import 'org_admin_widgets.dart';

enum WorkspaceSettingsPhase { loading, ready, denied, error }

/// This workspace's name, status and lifecycle (docs/identity.md,
/// Workspace lifecycle): owners and organization administrators rename,
/// archive and restore it. What the caller may do is the API's answer
/// (`GET /workspaces/current/permissions`).
class WorkspaceSettingsController extends ChangeNotifier {
  WorkspaceSettingsController(this.repository, {this.onArchivedChange});
  final OrgAdminRepository repository;

  /// Told after every load, so the app can show the read-only banner.
  final ValueChanged<bool>? onArchivedChange;

  WorkspaceSettingsPhase phase = WorkspaceSettingsPhase.loading;
  String? error;
  WorkspaceInfo? workspace;
  WorkspacePermissions? permissions;

  /// Which change is in flight: `rename`, `archive` or `restore`.
  String? busy;

  /// The last change's result.
  ({String text, bool error})? flash;

  Future<void> load() async {
    phase = WorkspaceSettingsPhase.loading;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait<Object>(
          [repository.currentWorkspace(), repository.workspacePermissions()]);
      workspace = results[0] as WorkspaceInfo;
      permissions = results[1] as WorkspacePermissions;
      phase = WorkspaceSettingsPhase.ready;
      onArchivedChange?.call(workspace!.archived);
    } on ApiException catch (e) {
      phase = e.isPermissionDenied
          ? WorkspaceSettingsPhase.denied
          : WorkspaceSettingsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = WorkspaceSettingsPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  /// Runs one change, then reloads; returns whether it worked.
  Future<bool> act(String key, Future<String> Function() change,
      {required String Function(String message) notAllowed}) async {
    busy = key;
    flash = null;
    notifyListeners();
    var ok = false;
    try {
      flash = (text: await change(), error: false);
      ok = true;
    } on ApiException catch (e) {
      flash = (
        text: e.isPermissionDenied ? notAllowed(e.readable) : e.readable,
        error: true
      );
    }
    busy = null;
    if (ok) {
      final kept = flash;
      await load();
      flash = kept;
    }
    notifyListeners();
    return ok;
  }
}

class WorkspaceSettingsScreen extends StatefulWidget {
  const WorkspaceSettingsScreen({required this.controller, super.key});
  final WorkspaceSettingsController controller;
  @override
  State<WorkspaceSettingsScreen> createState() =>
      _WorkspaceSettingsScreenState();
}

class _WorkspaceSettingsScreenState extends State<WorkspaceSettingsScreen> {
  WorkspaceSettingsController get c => widget.controller;
  final _name = TextEditingController();
  String? _nameError;
  String? _shownName;

  @override
  void initState() {
    super.initState();
    c.addListener(_syncName);
    c.load();
  }

  @override
  void dispose() {
    c.removeListener(_syncName);
    _name.dispose();
    super.dispose();
  }

  /// A fresh name from the server replaces the field, once per change.
  void _syncName() {
    final name = c.workspace?.name;
    if (name != null && name != _shownName) {
      _shownName = name;
      _name.text = name;
    }
  }

  String _notAllowed(String message) =>
      context.anum.f('oa.ws.notAllowed', {'message': message});

  Future<void> _rename() async {
    final l = context.anum;
    final raw = _name.text;
    final name = cleanWorkspaceName(raw);
    if (name == null) {
      setState(() => _nameError =
          l.t(raw.trim().isEmpty ? 'oa.ws.nameRequired' : 'oa.ws.nameTooLong'));
      return;
    }
    setState(() => _nameError = null);
    await c.act('rename', () async {
      final next = await c.repository.renameWorkspace(name);
      return l.f('oa.ws.renamed', {'name': next.name});
    }, notAllowed: _notAllowed);
  }

  Future<void> _archive(WorkspaceInfo workspace) async {
    final l = context.anum;
    final typed = await showDialog<String>(
        context: context, builder: (_) => _ArchiveDialog(name: workspace.name));
    if (typed == null || !mounted) return;
    await c.act('archive', () async {
      await c.repository.archiveWorkspace(typed.trim());
      return l.f('oa.ws.archived', {'name': workspace.name});
    }, notAllowed: _notAllowed);
  }

  Future<void> _restore() async {
    final l = context.anum;
    await c.act('restore', () async {
      await c.repository.restoreWorkspace();
      return l.t('oa.ws.restored');
    }, notAllowed: _notAllowed);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('oa.ws.title')), actions: [
          IconButton(
              onPressed:
                  c.phase == WorkspaceSettingsPhase.loading ? null : c.load,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh)),
        ]),
        body: switch (c.phase) {
          WorkspaceSettingsPhase.loading when c.workspace == null =>
            AdminLoading(label: l.t('oa.ws.loading')),
          WorkspaceSettingsPhase.denied => AdminStateView(
              icon: Icons.lock_outline,
              title: l.t('oa.ws.deniedTitle'),
              message: l.t('oa.ws.deniedText'),
              detail: l.f('oa.apiSaid', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          WorkspaceSettingsPhase.error => AdminStateView(
              icon: Icons.error_outline,
              title: l.t('oa.ws.errorTitle'),
              message: l.f('oa.errorHint', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          _ => _ready(context, l, c.workspace!, c.permissions!),
        },
      ),
    );
  }

  Widget _ready(BuildContext context, AnumLocalizations l,
      WorkspaceInfo workspace, WorkspacePermissions permissions) {
    final text = Theme.of(context).textTheme;
    final archived = workspace.archived;
    final canManage = permissions.canManageWorkspace;
    final role = l.t(
        const {'owner', 'member', 'viewer'}.contains(permissions.role)
            ? 'oa.ws.role.${permissions.role}'
            : 'oa.ws.role.none');
    final busy = c.busy != null;
    return AnumPage(onRefresh: c.load, children: [
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(
            spacing: AnumSpacing.sm,
            runSpacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(workspace.name, style: text.titleLarge),
              AnumPill(
                  label: l.t(archived
                      ? 'oa.ws.status.archived'
                      : 'oa.ws.status.active'),
                  tone: archived ? AnumTone.warn : AnumTone.ok),
            ],
          ),
          const SizedBox(height: AnumSpacing.xxs),
          Text(
              l.f(
                  permissions.organizationAdmin
                      ? 'oa.ws.yourRoleAdmin'
                      : 'oa.ws.yourRole',
                  {'role': role}),
              style: text.bodySmall),
          const SizedBox(height: AnumSpacing.sm),
          _fact(context, l.t('oa.ws.id'), workspace.id, ltr: true),
          _fact(context, l.t('oa.ws.organization'), workspace.tenantId,
              ltr: true),
          _fact(context, l.t('oa.ws.createdAt'),
              adminDate(context, workspace.createdAt)),
        ]),
      ),
      if (archived)
        AnumSurface(
          accent: anumToneColor(context, AnumTone.warn),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.archive_outlined),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Text(workspace.archivedBy == null
                  ? l.f('oa.ws.archivedAt',
                      {'when': adminDate(context, workspace.archivedAt)})
                  : l.f('oa.ws.archivedBy', {
                      'when': adminDate(context, workspace.archivedAt),
                      'who': workspace.archivedBy!
                    })),
            ),
          ]),
        ),
      if (c.flash != null)
        AdminFlash(text: c.flash!.text, error: c.flash!.error),
      if (!canManage)
        AnumSurface(
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.lock_outline),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(child: Text(l.t('oa.ws.readOnly'))),
          ]),
        ),
      if (canManage && !archived)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(l.t('oa.ws.rename'), style: text.titleMedium),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('ws-name'),
              controller: _name,
              maxLength: 160,
              enabled: !busy,
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) => _rename(),
              decoration: InputDecoration(
                  labelText: l.t('oa.ws.nameLabel'), errorText: _nameError),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                  key: const Key('ws-save-name'),
                  onPressed: busy || _name.text.trim() == workspace.name
                      ? null
                      : _rename,
                  icon: const Icon(Icons.edit_outlined),
                  label: Text(l.t('oa.ws.saveName'))),
            ),
          ]),
        ),
      if (canManage)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(l.t(archived ? 'oa.ws.restore' : 'oa.ws.archive'),
                style: text.titleMedium),
            const SizedBox(height: AnumSpacing.xs),
            Text(l.t(archived ? 'oa.ws.restoreText' : 'oa.ws.archiveText'),
                style: text.bodyMedium),
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: archived
                  ? FilledButton.icon(
                      key: const Key('ws-restore'),
                      onPressed: busy ? null : _restore,
                      icon: const Icon(Icons.unarchive_outlined),
                      label: Text(l.t('oa.ws.restoreButton')))
                  : OutlinedButton.icon(
                      key: const Key('ws-archive'),
                      style: OutlinedButton.styleFrom(
                          foregroundColor: Theme.of(context).colorScheme.error),
                      onPressed: busy ? null : () => _archive(workspace),
                      icon: const Icon(Icons.archive_outlined),
                      label: Text(l.t('oa.ws.archiveButton'))),
            ),
          ]),
        ),
    ]);
  }

  Widget _fact(BuildContext context, String label, String value,
          {bool ltr = false}) =>
      Padding(
        padding: const EdgeInsets.only(top: AnumSpacing.xxs),
        child: Wrap(spacing: AnumSpacing.xs, children: [
          Text('$label:', style: Theme.of(context).textTheme.bodySmall),
          SelectableText(value,
              textDirection: ltr ? TextDirection.ltr : null,
              style: Theme.of(context).textTheme.bodyMedium),
        ]),
      );
}

/// Asks for the workspace's current name before archiving it.
class _ArchiveDialog extends StatefulWidget {
  const _ArchiveDialog({required this.name});
  final String name;
  @override
  State<_ArchiveDialog> createState() => _ArchiveDialogState();
}

class _ArchiveDialogState extends State<_ArchiveDialog> {
  final _typed = TextEditingController();

  @override
  void dispose() {
    _typed.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final ok = archiveConfirmed(_typed.text, widget.name);
    return AlertDialog(
      title: Text(l.f('oa.ws.archiveConfirmTitle', {'name': widget.name})),
      scrollable: true,
      content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l.f('oa.ws.archiveConfirmText', {'name': widget.name})),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('ws-archive-name'),
              controller: _typed,
              autofocus: true,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                  labelText: l.t('oa.ws.nameLabel'),
                  errorText: _typed.text.isNotEmpty && !ok
                      ? l.t('oa.ws.nameMismatch')
                      : null),
            ),
          ]),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: Text(l.t('oa.cancel'))),
        FilledButton(
            key: const Key('ws-archive-confirm'),
            style: FilledButton.styleFrom(
                backgroundColor: Theme.of(context).colorScheme.error,
                foregroundColor: Theme.of(context).colorScheme.onError),
            onPressed: ok ? () => Navigator.of(context).pop(_typed.text) : null,
            child: Text(l.t('oa.ws.archiveButton'))),
      ],
    );
  }
}
