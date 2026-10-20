import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'developer_controller.dart';
import 'developer_models.dart';
import 'developer_screen.dart';

AnumTone projectTone(ProjectState state) => switch (state) {
      ProjectState.ready => AnumTone.ok,
      ProjectState.untested => AnumTone.warn,
      ProjectState.attention => AnumTone.stop,
      ProjectState.off => AnumTone.neutral,
    };

/// Developer › Projects (docs/developer-mode.md#projects): the GitHub
/// repositories the developer may change. Add, edit, test the connection,
/// install the workflow, turn off and delete. Tokens are sent once and never
/// shown again. Reads everything from [controller], so it follows changes.
class DeveloperProjectsScreen extends StatefulWidget {
  const DeveloperProjectsScreen({
    required this.controller,
    this.openLink = openInBrowser,
    super.key,
  });

  final DeveloperController controller;
  final LinkOpener openLink;

  @override
  State<DeveloperProjectsScreen> createState() =>
      _DeveloperProjectsScreenState();
}

class _DeveloperProjectsScreenState extends State<DeveloperProjectsScreen> {
  final _search = TextEditingController();
  String _query = '';

  DeveloperController get controller => widget.controller;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _edit(BuildContext context,
          [DevProject? project]) =>
      Navigator.push(
          context,
          MaterialPageRoute<void>(
              builder: (_) => DeveloperProjectFormScreen(
                  controller: controller, project: project)));

  Future<void> _confirmDelete(BuildContext context, DevProject project) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) {
        final l = AnumLocalizations.maybeOf(context);
        return AlertDialog(
          title: Text(l.f('dev.p.deleteTitle', {'name': project.name})),
          content: Text(l.t('dev.p.deleteText')),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: Text(l.t('da.keepIt'))),
            FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: context.palette.stop,
                    foregroundColor: context.palette.background),
                onPressed: () => Navigator.pop(context, true),
                child: Text(l.t('dev.p.delete'))),
          ],
        );
      },
    );
    if (ok ?? false) await controller.deleteProject(project);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
            title: Text(AnumLocalizations.maybeOf(context).t('dev.projects'))),
        body: ListenableBuilder(
          listenable: controller,
          builder: (context, _) {
            final p = context.palette;
            final l = AnumLocalizations.maybeOf(context);
            controller.l = l;
            final setup = controller.setup;
            final projects = controller.projects;
            final needle = _query.trim().toLowerCase();
            final shown = [
              for (final project in projects)
                if (needle.isEmpty ||
                    '${project.name} ${project.repository} ${project.description}'
                        .toLowerCase()
                        .contains(needle))
                  project
            ];
            final atLimit =
                setup != null && projects.length >= setup.maxProjects;
            return AnumPage(
              onRefresh: controller.reloadProjects,
              children: [
                AnumHeader(
                  eyebrow: l.t('dev.title'),
                  title: l.t('dev.projects'),
                  subtitle: l.t('dev.p.subtitle'),
                  trailing: IconButton(
                    key: const Key('developer-project-add'),
                    tooltip: l.t('dev.addProject'),
                    onPressed: atLimit ? null : () => unawaited(_edit(context)),
                    icon: const Icon(Icons.add),
                  ),
                ),
                if (atLimit)
                  Text(l.f('dev.p.max', {'max': setup.maxProjects}),
                      style: TextStyle(color: p.muted)),
                if (controller.notice != null)
                  DeveloperNotice(
                    message: controller.notice!,
                    failed: controller.noticeFailed,
                    onDismiss: controller.dismissNotice,
                  ),
                const _SecretsCard(),
                if (projects.length > 6)
                  TextField(
                    key: const Key('developer-project-search'),
                    controller: _search,
                    onChanged: (value) => setState(() => _query = value),
                    decoration: InputDecoration(
                        labelText: l.t('dev.p.search'),
                        prefixIcon: const Icon(Icons.search)),
                  ),
                if (projects.isEmpty)
                  AnumSurface(
                    key: const Key('developer-projects-empty'),
                    child: AnumEmpty(
                      icon: Icons.folder_outlined,
                      title: l.t('dev.noProjects'),
                      message: l.t('dev.p.emptyText'),
                      action: l.t('dev.addProject'),
                      onAction: () => unawaited(_edit(context)),
                    ),
                  )
                else if (shown.isEmpty)
                  Text(l.f('dev.p.noMatch', {'query': _query}),
                      style: TextStyle(color: p.muted))
                else
                  for (final project in shown)
                    DeveloperProjectCard(
                      project: project,
                      busy: controller.busy,
                      openLink: widget.openLink,
                      onTest: () => unawaited(controller.testProject(project)),
                      onInstall: () =>
                          unawaited(controller.installWorkflow(project)),
                      onEdit: () => unawaited(_edit(context, project)),
                      onToggle: () =>
                          unawaited(controller.toggleProject(project)),
                      onDelete: () =>
                          unawaited(_confirmDelete(context, project)),
                    ),
              ],
            );
          },
        ),
      );
}

/// What each repository needs, by name only.
class _SecretsCard extends StatelessWidget {
  const _SecretsCard();

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.t('dev.p.needs'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        const SizedBox(height: AnumSpacing.xxs),
        Text(
            l.f('dev.p.needsText', {
              'secrets': githubSecretNames.join(l.t('dev.and')),
              'variables': githubVariableNames.join(l.t('dev.and')),
            }),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
      ]),
    );
  }
}

/// One project: its state, token, protected paths, last test, and actions.
class DeveloperProjectCard extends StatelessWidget {
  const DeveloperProjectCard({
    required this.project,
    required this.busy,
    required this.onTest,
    required this.onInstall,
    required this.onEdit,
    required this.onToggle,
    required this.onDelete,
    this.openLink = openInBrowser,
    super.key,
  });

  final DevProject project;
  final String? busy;
  final VoidCallback onTest, onInstall, onEdit, onToggle, onDelete;
  final LinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final state = projectState(project);
    final check = project.lastCheck;
    final idle = busy == null;
    return AnumSurface(
      key: Key('developer-project-${project.id}'),
      child: Semantics(
        container: true,
        label: l.f('dev.p.label', {'name': project.name}),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xxs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(project.name,
                    style: TextStyle(
                        color: p.text,
                        fontSize: AnumType.lgPlus,
                        fontWeight: FontWeight.w700)),
                AnumPill(
                    label: projectStateLabel(state, l),
                    tone: projectTone(state)),
              ]),
          const SizedBox(height: AnumSpacing.xxs),
          Directionality(
            textDirection: TextDirection.ltr,
            child: Text('${project.repository} · ${project.defaultBranch}',
                style: TextStyle(
                    color: p.muted,
                    fontSize: AnumType.xs,
                    fontFamily: 'monospace')),
          ),
          if (project.description.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(project.description, style: TextStyle(color: p.text)),
          ],
          const SizedBox(height: AnumSpacing.xs),
          _Fact(
              l.t('dev.p.token'),
              !project.tokenConfigured
                  ? l.t('dev.p.noToken')
                  : project.tokenSource == 'project'
                      ? l.t('dev.p.ownToken')
                      : l.t('dev.p.apiToken')),
          _Fact(
              l.t('dev.p.protected'),
              project.protectedPaths.isEmpty
                  ? l.t('dev.p.builtIn')
                  : project.protectedPaths.join(', ')),
          _Fact(
              l.t('dev.p.connection'),
              check == null
                  ? l.t('dev.p.untested')
                  : l.f('dev.p.tested', {
                      'when': relativeTime(check.checkedAt),
                      'message': check.message
                    })),
          for (final problem in project.problems)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xxs),
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.error_outline, size: 18, color: p.stop),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(child: Text(problem, style: TextStyle(color: p.stop))),
              ]),
            ),
          if (project.installPrUrl != null &&
              !(check?.workflowPresent ?? false)) ...[
            const SizedBox(height: AnumSpacing.xs),
            TextButton.icon(
              key: Key('developer-project-pr-${project.id}'),
              onPressed: () => unawaited(
                  openDeveloperLink(context, openLink, project.installPrUrl)),
              icon: const Icon(Icons.open_in_new, size: 18),
              label: Text(l.f('dev.p.workflowPr',
                  {'number': project.installPrNumber ?? ''})),
            ),
          ],
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            OutlinedButton.icon(
              key: Key('developer-project-test-${project.id}'),
              onPressed: idle && project.tokenConfigured ? onTest : null,
              icon: const Icon(Icons.wifi_tethering),
              label: Text(busy == 'project-test:${project.id}'
                  ? l.t('dev.p.testing')
                  : l.t('dev.p.test')),
            ),
            if (canInstallWorkflow(project))
              OutlinedButton.icon(
                key: Key('developer-project-install-${project.id}'),
                onPressed: idle ? onInstall : null,
                icon: const Icon(Icons.call_merge),
                label: Text(busy == 'project-install:${project.id}'
                    ? l.t('dev.p.opening')
                    : l.t('dev.p.install')),
              ),
            OutlinedButton.icon(
              key: Key('developer-project-edit-${project.id}'),
              onPressed: idle ? onEdit : null,
              icon: const Icon(Icons.edit_outlined),
              label: Text(l.t('da.edit')),
            ),
            OutlinedButton.icon(
              key: Key('developer-project-toggle-${project.id}'),
              onPressed: idle ? onToggle : null,
              icon: const Icon(Icons.power_settings_new),
              label: Text(
                  project.enabled ? l.t('dev.p.turnOff') : l.t('dev.p.turnOn')),
            ),
            OutlinedButton.icon(
              key: Key('developer-project-delete-${project.id}'),
              style: OutlinedButton.styleFrom(foregroundColor: p.stop),
              onPressed: idle ? onDelete : null,
              icon: const Icon(Icons.delete_outline),
              label: Text(l.t('da.delete')),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact(this.label, this.value);
  final String label, value;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label,
            style: TextStyle(
                color: p.faint,
                fontSize: AnumType.xs,
                fontWeight: FontWeight.w600)),
        Text(value, style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
      ]),
    );
  }
}

/// Add a project, or edit one. The token field is write-only: an existing
/// token is kept unless a new one is typed or removal is ticked.
class DeveloperProjectFormScreen extends StatefulWidget {
  const DeveloperProjectFormScreen(
      {required this.controller, this.project, super.key});
  final DeveloperController controller;
  final DevProject? project;

  @override
  State<DeveloperProjectFormScreen> createState() =>
      _DeveloperProjectFormScreenState();
}

class _DeveloperProjectFormScreenState
    extends State<DeveloperProjectFormScreen> {
  late final ProjectDraft _start = widget.project == null
      ? const ProjectDraft()
      : ProjectDraft.from(widget.project!);
  late final _name = TextEditingController(text: _start.name);
  late final _repository = TextEditingController(text: _start.repository);
  late final _branch = TextEditingController(text: _start.defaultBranch);
  late final _description = TextEditingController(text: _start.description);
  late final _paths = TextEditingController(text: _start.protectedPaths);
  late final _workflow = TextEditingController(text: _start.workflowFile);
  final _token = TextEditingController();
  late bool _enabled = _start.enabled;
  late String _tokenSource = _start.tokenSource;
  bool _clearToken = false;
  String? _error;
  String? _errorField;
  bool _saving = false;

  @override
  void dispose() {
    for (final c in [
      _name,
      _repository,
      _branch,
      _description,
      _paths,
      _workflow,
      _token
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final draft = ProjectDraft(
      name: _name.text,
      repository: _repository.text,
      defaultBranch: _branch.text,
      description: _description.text,
      protectedPaths: _paths.text,
      enabled: _enabled,
      tokenSource: _tokenSource,
      token: _token.text,
      clearToken: _clearToken,
      workflowFile: _workflow.text,
    );
    final invalid = projectPayload(draft);
    if (invalid is InvalidProject) {
      setState(() {
        _error = invalid.error;
        _errorField = invalid.field;
      });
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
      _errorField = null;
    });
    final error =
        await widget.controller.saveProject(draft, existing: widget.project);
    if (!mounted) return;
    if (error == null) {
      Navigator.pop(context);
    } else {
      setState(() {
        _saving = false;
        _error = error;
      });
    }
  }

  String? _fieldError(String field) => _errorField == field ? _error : null;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    widget.controller.l = l;
    final existing = widget.project;
    final storedToken = existing != null &&
        existing.tokenConfigured &&
        existing.tokenSource == 'project';
    final environmentToken =
        widget.controller.setup?.environmentTokenConfigured ?? false;
    return Scaffold(
      appBar: AppBar(
          title: Text(existing == null
              ? l.t('dev.addProject')
              : l.f('dev.p.editTitle', {'name': existing.name}))),
      body: AnumPage(children: [
        TextField(
          key: const Key('developer-project-name'),
          controller: _name,
          maxLength: 60,
          autofocus: existing == null,
          decoration: InputDecoration(
              labelText: l.t('dev.p.name'),
              helperText: l.t('dev.p.nameHelp'),
              helperMaxLines: 3,
              errorText: _fieldError('name'),
              errorMaxLines: 3),
        ),
        TextField(
          textDirection: TextDirection.ltr,
          key: const Key('developer-project-repository'),
          controller: _repository,
          autocorrect: false,
          keyboardType: TextInputType.url,
          decoration: InputDecoration(
              labelText: l.t('dev.p.repository'),
              hintText: 'owner/repo',
              helperText: l.t('dev.p.repositoryHelp'),
              helperMaxLines: 3,
              errorText: _fieldError('repository'),
              errorMaxLines: 3),
        ),
        TextField(
          textDirection: TextDirection.ltr,
          key: const Key('developer-project-branch'),
          controller: _branch,
          autocorrect: false,
          decoration: InputDecoration(
              labelText: l.t('dev.p.branch'),
              errorText: _fieldError('branch'),
              errorMaxLines: 3),
        ),
        TextField(
          key: const Key('developer-project-description'),
          controller: _description,
          maxLength: 500,
          minLines: 1,
          maxLines: 4,
          decoration: InputDecoration(
              labelText: l.t('dev.p.description'),
              helperText: l.t('dev.p.descriptionHelp'),
              helperMaxLines: 4),
        ),
        TextField(
          textDirection: TextDirection.ltr,
          key: const Key('developer-project-paths'),
          controller: _paths,
          autocorrect: false,
          minLines: 1,
          maxLines: 5,
          decoration: InputDecoration(
              labelText: l.t('dev.p.protectedOptional'),
              hintText: 'docs/legal/',
              helperText: l.t('dev.p.protectedHelp'),
              helperMaxLines: 4,
              errorText: _fieldError('protected'),
              errorMaxLines: 4),
        ),
        TextField(
          textDirection: TextDirection.ltr,
          key: const Key('developer-project-workflow'),
          controller: _workflow,
          autocorrect: false,
          decoration: InputDecoration(
              labelText: l.t('dev.p.workflow'),
              errorText: _fieldError('workflow'),
              errorMaxLines: 3),
        ),
        AnumSurface(
          child: Material(
            type: MaterialType.transparency,
            child: RadioGroup<String>(
              groupValue: _tokenSource,
              onChanged: (value) =>
                  setState(() => _tokenSource = value ?? 'project'),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(l.t('dev.p.token'),
                        style: TextStyle(
                            color: p.text, fontWeight: FontWeight.w700)),
                    RadioListTile<String>(
                      key: const Key('developer-project-token-project'),
                      contentPadding: EdgeInsets.zero,
                      value: 'project',
                      title: Text(l.t('dev.p.tokenProject')),
                    ),
                    RadioListTile<String>(
                      key: const Key('developer-project-token-environment'),
                      contentPadding: EdgeInsets.zero,
                      value: 'environment',
                      title: Text(l.t('dev.p.tokenEnvironment')),
                    ),
                    if (_tokenSource == 'environment' && !environmentToken)
                      Text(l.t('dev.p.envMissing'),
                          style:
                              TextStyle(color: p.muted, fontSize: AnumType.sm)),
                    if (_tokenSource == 'project') ...[
                      TextField(
                        textDirection: TextDirection.ltr,
                        key: const Key('developer-project-token'),
                        controller: _token,
                        obscureText: true,
                        autocorrect: false,
                        enableSuggestions: false,
                        decoration: InputDecoration(
                            labelText: l.t('dev.p.fineGrained'),
                            hintText: storedToken ? l.t('dev.p.stored') : null,
                            helperText: storedToken
                                ? l.t('dev.p.storedHelp')
                                : l.t('dev.p.tokenHelp'),
                            helperMaxLines: 5,
                            errorText: _fieldError('token'),
                            errorMaxLines: 3),
                      ),
                      if (storedToken)
                        CheckboxListTile(
                          key: const Key('developer-project-clear-token'),
                          contentPadding: EdgeInsets.zero,
                          controlAffinity: ListTileControlAffinity.leading,
                          value: _clearToken,
                          onChanged: (value) =>
                              setState(() => _clearToken = value ?? false),
                          title: Text(l.t('dev.p.removeToken')),
                        ),
                    ],
                  ]),
            ),
          ),
        ),
        Material(
          type: MaterialType.transparency,
          child: SwitchListTile(
            key: const Key('developer-project-enabled'),
            contentPadding: EdgeInsets.zero,
            value: _enabled,
            onChanged: (value) => setState(() => _enabled = value),
            title: Text(l.t('dev.p.accept')),
          ),
        ),
        if (_error != null && _errorField == null)
          Semantics(
            liveRegion: true,
            child: Text(_error!,
                key: const Key('developer-project-error'),
                style: TextStyle(color: p.stop)),
          ),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton.icon(
            key: const Key('developer-project-save'),
            onPressed: _saving ? null : () => unawaited(_save()),
            icon: const Icon(Icons.check),
            label: Text(_saving ? l.t('da.saving') : l.t('dev.p.save')),
          ),
          OutlinedButton(
            onPressed: _saving ? null : () => Navigator.maybePop(context),
            child: Text(l.t('da.cancel')),
          ),
        ]),
      ]),
    );
  }
}
