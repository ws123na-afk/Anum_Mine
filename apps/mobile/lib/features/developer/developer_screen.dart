import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_models.dart' show roleLabel;
import '../workspace/approvals_screen.dart'
    show ApprovalArgumentsTable, expiryLabel;
import '../workspace/workspace_models.dart';
import 'developer_controller.dart';
import 'developer_models.dart';
import 'developer_projects_screen.dart';

/// Opens an https link outside the app; false when it could not.
typedef LinkOpener = Future<bool> Function(Uri uri);

/// Pull request and run links open in the browser, never in a web view.
Future<bool> openInBrowser(Uri uri) async {
  try {
    return await launchUrl(uri, mode: LaunchMode.externalApplication);
  } on Object {
    return false;
  }
}

/// Opens [value] when it is an https link; says so when it cannot.
Future<void> openDeveloperLink(
    BuildContext context, LinkOpener open, String? value) async {
  final uri = safeLink(value);
  final messenger = ScaffoldMessenger.maybeOf(context);
  final l = AnumLocalizations.maybeOf(context);
  final opened = uri != null && await open(uri);
  if (!opened) {
    messenger?.showSnackBar(SnackBar(
        content: Text(l.f('dev.linkFailed', {
      'reason': uri == null
          ? l.t('dev.linkNotHttps')
          : l.f('dev.linkOpenIn', {'uri': uri}),
    }))));
  }
}

AnumTone devTone(DevStatus status) => switch (statusTone(status)) {
      DevTone.ok => AnumTone.ok,
      DevTone.stop => AnumTone.stop,
      DevTone.warn => AnumTone.warn,
      DevTone.neutral => AnumTone.neutral,
    };

/// The Developer destination (docs/developer-mode.md): owners ask for a
/// change, follow it through the coding run, the pull request and CI, and
/// approve the merge with its payload hash. Everyone else sees what it is
/// and who can use it.
class DeveloperScreen extends StatefulWidget {
  const DeveloperScreen({
    required this.controller,
    this.active = true,
    this.onOpenSettings,
    this.openLink = openInBrowser,
    super.key,
  });

  final DeveloperController controller;

  /// The destination is the one on screen: loads and refreshes only then.
  final bool active;

  /// Opens Settings, where owners raise the monthly model budget.
  final VoidCallback? onOpenSettings;
  final LinkOpener openLink;

  @override
  State<DeveloperScreen> createState() => _DeveloperScreenState();
}

class _DeveloperScreenState extends State<DeveloperScreen> {
  final _draft = TextEditingController();
  final _draftFocus = FocusNode();
  final _search = TextEditingController();
  bool _allowProtected = false;

  DeveloperController get controller => widget.controller;

  @override
  void initState() {
    super.initState();
    _search.text = controller.query;
    _sync();
  }

  @override
  void didUpdateWidget(DeveloperScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.setActive(false);
    }
    _sync();
  }

  /// Starting to load notifies listeners, which must not happen in build.
  void _sync() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) controller.setActive(widget.active);
      });

  @override
  void dispose() {
    controller.setActive(false);
    _draft.dispose();
    _draftFocus.dispose();
    _search.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final sent =
        await controller.create(_draft.text, allowProtected: _allowProtected);
    if (sent && mounted) {
      _draft.clear();
      setState(() => _allowProtected = false);
    }
  }

  Future<void> _refresh() async {
    if (controller.ownerState == OwnerState.owner) {
      await controller.load();
    } else {
      await controller.reloadRole();
      if (controller.ownerState == OwnerState.owner) await controller.load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    controller.l = l;
    return TickerMode(
      // Kept mounted in the workspace's IndexedStack: nothing animates
      // while another destination is on screen.
      enabled: widget.active,
      child: ListenableBuilder(
        listenable: controller,
        builder: (context, _) => AnumPage(
          onRefresh: _refresh,
          children: [
            AnumHeader(
              eyebrow: l.t('dev.eyebrow'),
              title: l.t('dev.title'),
              subtitle: l.t('dev.subtitle'),
              large: true,
              trailing: controller.ownerState == OwnerState.owner
                  ? IconButton(
                      tooltip: l.t('dev.refreshRequests'),
                      onPressed: controller.refreshing ? null : _refresh,
                      icon: const Icon(Icons.refresh))
                  : null,
            ),
            ..._body(context),
          ],
        ),
      ),
    );
  }

  List<Widget> _body(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    switch (controller.ownerState) {
      case OwnerState.unknown:
      case OwnerState.loading:
        return [_Progress(l.t('dev.checkingRole'))];
      case OwnerState.error:
        return [
          AnumSurface(
            child: AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('dev.roleError'),
              message: controller.roleError ?? '',
              action: l.t('da.tryAgain'),
              onAction: () => unawaited(_refresh()),
            ),
          ),
        ];
      case OwnerState.notOwner:
        return [_OwnerOnly(role: controller.role)];
      case OwnerState.owner:
        return _ownerBody(context);
    }
  }

  List<Widget> _ownerBody(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    switch (controller.phase) {
      case DevPhase.initial:
      case DevPhase.loading:
        return [_Progress(l.t('dev.loadingRequests'))];
      case DevPhase.error:
        return [
          AnumSurface(
            child: AnumEmpty(
              icon: Icons.cloud_off_outlined,
              title: l.t('dev.loadError'),
              message: controller.loadError ?? '',
              action: l.t('da.tryAgain'),
              onAction: () => unawaited(controller.load()),
            ),
          ),
        ];
      case DevPhase.forbidden:
        return [_OwnerOnly(message: controller.loadError)];
      case DevPhase.ready:
        break;
    }
    final setup = controller.setup;
    final items = controller.items;
    return [
      if (controller.refreshing)
        LinearProgressIndicator(semanticsLabel: l.t('dev.refreshing')),
      if (setup != null && !setup.ready) _SetupCard(setup: setup),
      if (setup != null && setup.ready)
        _ProjectsRow(
          projects: controller.projects,
          onOpen: () => openProjects(context),
        ),
      if (setup != null && setup.ready && controller.sendable.isEmpty)
        AnumSurface(
          key: const Key('developer-no-projects'),
          child: AnumEmpty(
            icon: Icons.folder_outlined,
            title: controller.projects.isEmpty
                ? l.t('dev.addProjectFirst')
                : l.t('dev.noProjectReady'),
            message: controller.projects.isEmpty
                ? l.t('dev.addProjectFirstText')
                : l.t('dev.noProjectReadyText'),
            action: controller.projects.isEmpty
                ? l.t('dev.addProject')
                : l.t('dev.openProjects'),
            onAction: () => openProjects(context),
          ),
        ),
      if (setup != null && setup.ready && controller.sendable.isNotEmpty)
        _RequestForm(
          controller: controller,
          setup: setup,
          draft: _draft,
          focus: _draftFocus,
          allowProtected: _allowProtected,
          onAllowProtected: (value) => setState(() => _allowProtected = value),
          onSubmit: () => unawaited(_submit()),
        ),
      if (controller.budget != null)
        _BudgetNotice(
          message: controller.budget!,
          onOpenSettings: widget.onOpenSettings,
          onDismiss: controller.dismissBudget,
        ),
      if (controller.notice != null)
        DeveloperNotice(
          message: controller.notice!,
          failed: controller.noticeFailed,
          onDismiss: controller.dismissNotice,
        ),
      if (items.isNotEmpty || controller.query.isNotEmpty)
        _SearchBar(
          controller: _search,
          active: controller.query,
          busy: controller.refreshing,
          onSearch: (value) => unawaited(controller.search(value)),
          onClear: () {
            _search.clear();
            unawaited(controller.clearSearch());
          },
        ),
      if (items.isEmpty)
        _EmptyRequests(
          searching: controller.query.isNotEmpty,
          canAsk: (setup?.ready ?? false) && controller.sendable.isNotEmpty,
          onExample: (example) {
            _draft.text = example;
            _draft.selection = TextSelection.collapsed(offset: example.length);
            _draftFocus.requestFocus();
          },
          onClearSearch: () {
            _search.clear();
            unawaited(controller.clearSearch());
          },
        )
      else
        Semantics(
          container: true,
          label: l.t('dev.requests'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final item in items)
                Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                  child: DeveloperRequestTile(
                    request: item,
                    reviewWaiting: controller.reviewFor(item) != null,
                    onTap: () => openRequest(context, item),
                  ),
                ),
            ],
          ),
        ),
      if (controller.hasMore)
        Center(
          child: OutlinedButton.icon(
            key: const Key('developer-more'),
            onPressed: controller.busy == null
                ? () => unawaited(controller.loadMore())
                : null,
            icon: const Icon(Icons.expand_more),
            label: Text(controller.busy == 'more'
                ? l.t('da.loading')
                : l.t('dev.loadOlder')),
          ),
        ),
    ];
  }

  void openProjects(BuildContext context) => unawaited(Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => DeveloperProjectsScreen(
              controller: controller, openLink: widget.openLink))));

  void openRequest(BuildContext context, DevRequest item) =>
      unawaited(Navigator.push(
          context,
          MaterialPageRoute<void>(
              builder: (_) => DeveloperRequestScreen(
                    controller: controller,
                    requestId: item.id,
                    openLink: widget.openLink,
                  ))));
}

/// "3 projects, 2 ready" and the way into Projects.
class _ProjectsRow extends StatelessWidget {
  const _ProjectsRow({required this.projects, required this.onOpen});
  final List<DevProject> projects;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final ready = projects.where((project) => project.ready).length;
    return AnumSurface(
      key: const Key('developer-projects-row'),
      onTap: onOpen,
      child: Row(children: [
        Icon(Icons.folder_outlined, color: p.sky),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l.t('dev.projects'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            Text(
                projects.isEmpty
                    ? l.t('dev.noProjects')
                    : l.p(
                        'dev.projectsReady', projects.length, {'ready': ready}),
                style: TextStyle(color: p.muted, fontSize: 13)),
          ]),
        ),
        Icon(Icons.chevron_right, color: p.faint),
      ]),
    );
  }
}

class _Progress extends StatelessWidget {
  const _Progress(this.label);
  final String label;

  @override
  Widget build(BuildContext context) => Semantics(
        liveRegion: true,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xl),
          child: Column(children: [
            const CircularProgressIndicator(),
            const SizedBox(height: AnumSpacing.sm),
            Text(label,
                textAlign: TextAlign.center,
                style: TextStyle(color: context.palette.muted)),
          ]),
        ),
      );
}

/// Shown to everyone but owners, and when the API answers 403.
class _OwnerOnly extends StatelessWidget {
  const _OwnerOnly({this.role, this.message});
  final String? role;
  final String? message;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final yourRole = role == null || role == 'owner'
        ? ''
        : ' ${l.f('dev.yourRole', {'role': roleLabel(role!).toLowerCase()})}';
    return AnumSurface(
      key: const Key('developer-owner-only'),
      child: AnumEmpty(
        icon: Icons.lock_outline,
        title: l.t('dev.ownerOnlyTitle'),
        message: [
          if (message != null && message != developerOwnerOnly) message!,
          '$ownerOnlyExplanation$yourRole',
        ].join('\n\n'),
      ),
    );
  }
}

/// What the person who deploys the API must configure: names only.
class _SetupCard extends StatelessWidget {
  const _SetupCard({required this.setup});
  final DevSetup setup;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final steps = setupSteps(setup);
    return AnumSurface(
      key: const Key('developer-setup'),
      accent: p.warn,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.construction, color: p.warn),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
            child: Text(l.t('dev.setupTitle'),
                style: TextStyle(
                    color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
          ),
        ]),
        const SizedBox(height: AnumSpacing.xs),
        Text(setup.message, style: TextStyle(color: p.muted)),
        if (steps.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('dev.setupIntro'), style: TextStyle(color: p.text)),
          const SizedBox(height: AnumSpacing.xs),
          for (final step in steps)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SelectableText(step.name,
                        style: TextStyle(
                            color: p.sky,
                            fontFamily: 'monospace',
                            fontWeight: FontWeight.w600)),
                    Text(step.purpose,
                        style: TextStyle(color: p.muted, fontSize: 13)),
                  ]),
            ),
          const SizedBox(height: AnumSpacing.xs),
          Text(
              l.f('dev.setupThen',
                  {'secrets': githubSecretNames.join(l.t('dev.and'))}),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ],
      ]),
    );
  }
}

class _RequestForm extends StatelessWidget {
  const _RequestForm({
    required this.controller,
    required this.setup,
    required this.draft,
    required this.focus,
    required this.allowProtected,
    required this.onAllowProtected,
    required this.onSubmit,
  });

  final DeveloperController controller;
  final DevSetup setup;
  final TextEditingController draft;
  final FocusNode focus;
  final bool allowProtected;
  final ValueChanged<bool> onAllowProtected;
  final VoidCallback onSubmit;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final sending = controller.busy == 'create';
    final project = controller.selectedProject;
    final blocked =
        setup.limitReached || controller.activeExists || project == null;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(l.t('dev.ask'),
            style: TextStyle(
                color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
        const SizedBox(height: AnumSpacing.sm),
        DropdownButtonFormField<String>(
          key: const Key('developer-project'),
          initialValue: project?.id,
          isExpanded: true,
          decoration: InputDecoration(labelText: l.t('dev.project')),
          hint: Text(l.t('dev.chooseProject')),
          items: [
            for (final option in controller.sendable)
              DropdownMenuItem(
                value: option.id,
                child: Text('${option.name} (${option.repository})',
                    overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: sending ? null : controller.selectProject,
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('developer-draft'),
          controller: draft,
          focusNode: focus,
          minLines: 2,
          maxLines: 8,
          maxLength: maxRequestChars,
          keyboardType: TextInputType.multiline,
          textCapitalization: TextCapitalization.sentences,
          decoration: InputDecoration(
            labelText: l.t('dev.whatChange'),
            hintText: l.t('dev.whatChangeHint'),
            alignLabelWithHint: true,
          ),
        ),
        // The surface is painted, not a Material: give the tile its own.
        Material(
          type: MaterialType.transparency,
          child: CheckboxListTile(
            key: const Key('developer-allow-protected'),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: allowProtected,
            onChanged: (value) => onAllowProtected(value ?? false),
            title: Text(l.t('dev.allowProtected')),
          ),
        ),
        Text(
            '${l.f('dev.usedToday', {
                  'used': setup.requestsToday,
                  'max': setup.maxRequestsPerDay
                })}'
            '${project == null ? '' : l.f('dev.buildsFrom', {
                    'repository': project.repository,
                    'branch': project.defaultBranch
                  })}',
            style: TextStyle(color: p.faint, fontSize: 12)),
        if (controller.activeExists && project != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(l.f('dev.oneAtATime', {'project': project.name}),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ],
        if (setup.limitReached) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('dev.limitReached'),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: ValueListenableBuilder<TextEditingValue>(
            valueListenable: draft,
            builder: (context, value, _) => FilledButton.icon(
              key: const Key('developer-send'),
              onPressed: sending || blocked || value.text.trim().isEmpty
                  ? null
                  : onSubmit,
              icon: const Icon(Icons.send),
              label: Text(sending ? l.t('dev.sending') : l.t('dev.send')),
            ),
          ),
        ),
      ]),
    );
  }
}

class _BudgetNotice extends StatelessWidget {
  const _BudgetNotice(
      {required this.message, required this.onDismiss, this.onOpenSettings});
  final String message;
  final VoidCallback onDismiss;
  final VoidCallback? onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    return AnumSurface(
      key: const Key('developer-budget'),
      accent: p.stop,
      child: Semantics(
        liveRegion: true,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('dev.budgetTitle'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
          const SizedBox(height: AnumSpacing.xxs),
          Text(message, style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.xxs),
          Text(l.t('dev.budgetText'),
              style: TextStyle(color: p.faint, fontSize: 12)),
          Wrap(spacing: AnumSpacing.xs, children: [
            if (onOpenSettings != null)
              TextButton(
                  onPressed: onOpenSettings,
                  child: Text(l.t('dev.openSettings'))),
            TextButton(onPressed: onDismiss, child: Text(l.t('da.dismiss'))),
          ]),
        ]),
      ),
    );
  }
}

/// A one-line result of the last action: success or what failed.
class DeveloperNotice extends StatelessWidget {
  const DeveloperNotice(
      {required this.message,
      required this.failed,
      required this.onDismiss,
      super.key});
  final String message;
  final bool failed;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = failed ? p.stop : p.ok;
    return DecoratedBox(
      key: const Key('developer-notice'),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Padding(
        padding: const EdgeInsetsDirectional.fromSTEB(
            AnumSpacing.sm, AnumSpacing.xxs, 0, AnumSpacing.xxs),
        child: Row(children: [
          Icon(failed ? Icons.error_outline : Icons.check_circle_outline,
              color: color, size: 20),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
            child: Semantics(
              liveRegion: true,
              child: Text(message, style: TextStyle(color: p.text)),
            ),
          ),
          IconButton(
              tooltip: AnumLocalizations.maybeOf(context).t('da.dismiss'),
              onPressed: onDismiss,
              icon: const Icon(Icons.close, size: 20)),
        ]),
      ),
    );
  }
}

class _SearchBar extends StatelessWidget {
  const _SearchBar({
    required this.controller,
    required this.active,
    required this.busy,
    required this.onSearch,
    required this.onClear,
  });
  final TextEditingController controller;
  final String active;
  final bool busy;
  final ValueChanged<String> onSearch;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Semantics(
      container: true,
      label: l.t('dev.searchRequests'),
      child: Wrap(
        spacing: AnumSpacing.xs,
        runSpacing: AnumSpacing.xs,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: TextField(
              key: const Key('developer-search'),
              controller: controller,
              textInputAction: TextInputAction.search,
              onSubmitted: onSearch,
              decoration: InputDecoration(
                labelText: l.t('dev.searchRequests'),
                prefixIcon: const Icon(Icons.search),
              ),
            ),
          ),
          OutlinedButton(
            onPressed: busy ? null : () => onSearch(controller.text),
            child: Text(l.t('da.search')),
          ),
          if (active.isNotEmpty)
            TextButton(onPressed: onClear, child: Text(l.t('da.clear'))),
        ],
      ),
    );
  }
}

class _EmptyRequests extends StatelessWidget {
  const _EmptyRequests({
    required this.searching,
    required this.canAsk,
    required this.onExample,
    required this.onClearSearch,
  });
  final bool searching, canAsk;
  final ValueChanged<String> onExample;
  final VoidCallback onClearSearch;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    if (searching) {
      return AnumSurface(
        child: AnumEmpty(
          icon: Icons.search_off,
          title: l.t('dev.noMatch'),
          message: l.t('dev.noMatchText'),
          action: l.t('dev.clearSearch'),
          onAction: onClearSearch,
        ),
      );
    }
    return AnumSurface(
      key: const Key('developer-empty'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumEmpty(
          icon: Icons.construction,
          title: l.t('dev.emptyTitle'),
          message: canAsk ? l.t('dev.emptyCanAsk') : l.t('dev.emptyNotSetUp'),
        ),
        if (canAsk)
          Wrap(
            alignment: WrapAlignment.center,
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              for (final example in exampleRequests)
                ActionChip(
                  label: Text(example),
                  onPressed: () => onExample(example),
                ),
            ],
          ),
      ]),
    );
  }
}

/// One request in the list: title, status, when, and the one-line summary.
class DeveloperRequestTile extends StatelessWidget {
  const DeveloperRequestTile({
    required this.request,
    required this.onTap,
    this.reviewWaiting = false,
    super.key,
  });
  final DevRequest request;
  final VoidCallback onTap;
  final bool reviewWaiting;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final tone = devTone(request.status);
    return AnumSurface(
      key: Key('developer-request-${request.id}'),
      onTap: onTap,
      accent: request.status == DevStatus.waitingApproval
          ? anumToneColor(context, AnumTone.warn)
          : null,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xxs,
              children: [
                AnumPill(label: request.statusLabelIn(l), tone: tone),
                if (reviewWaiting)
                  AnumPill(
                      label: l.t('dev.yourApproval'),
                      tone: AnumTone.warn,
                      icon: Icons.verified_user_outlined),
              ],
            ),
            const SizedBox(height: AnumSpacing.xs),
            Text(request.title,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    color: p.text, fontSize: 16, fontWeight: FontWeight.w700)),
            const SizedBox(height: AnumSpacing.xxs),
            Text(request.summary,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: p.muted, fontSize: 13.5)),
            const SizedBox(height: AnumSpacing.xxs),
            Text(
                '${request.projectName == null ? '' : '${request.projectName} · '}'
                '${relativeTime(request.createdAt)} · '
                '${request.spoken ? l.t('dev.spoken') : l.t('dev.typed')}'
                '${request.prNumber == null ? '' : ' · #${request.prNumber}'}',
                style: TextStyle(color: p.faint, fontSize: 12)),
          ]),
        ),
        const SizedBox(width: AnumSpacing.xs),
        Icon(Icons.chevron_right, color: p.faint),
      ]),
    );
  }
}

/// One request: the timeline, links, CI, and the actions (Approve & merge,
/// Check now, Cancel). Reads the request from [controller] so it follows
/// refreshes.
class DeveloperRequestScreen extends StatelessWidget {
  const DeveloperRequestScreen({
    required this.controller,
    required this.requestId,
    this.openLink = openInBrowser,
    super.key,
  });

  final DeveloperController controller;
  final String requestId;
  final LinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    controller.l = l;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('dev.request'))),
      body: ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final item = controller.find(requestId);
          if (item == null) {
            return AnumPage(children: [
              AnumSurface(
                child: AnumEmpty(
                  icon: Icons.search_off,
                  title: l.t('dev.notListed'),
                  message: l.t('dev.notListedText'),
                  action: l.t('dev.backToList'),
                  onAction: () => Navigator.maybePop(context),
                ),
              ),
            ]);
          }
          return AnumPage(
            onRefresh: () => controller.refresh(item),
            children: _children(context, item),
          );
        },
      ),
    );
  }

  List<Widget> _children(BuildContext context, DevRequest item) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final review = controller.reviewFor(item);
    final busy = controller.busy;
    return [
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          AnumPill(label: item.statusLabelIn(l), tone: devTone(item.status)),
          const SizedBox(height: AnumSpacing.xs),
          SelectableText(item.title,
              style: TextStyle(
                  color: p.text, fontSize: 19, fontWeight: FontWeight.w700)),
          const SizedBox(height: AnumSpacing.xxs),
          Text(
              '${exactTime(item.createdAt)} · '
              '${item.spoken ? l.t('dev.spoken') : l.t('dev.typed')}'
              '${item.projectName == null ? '' : ' · ${item.projectName}'}',
              style: TextStyle(color: p.faint, fontSize: 12)),
          if (item.repository != null)
            Directionality(
              textDirection: TextDirection.ltr,
              child: SelectableText(item.repository!,
                  key: const Key('developer-repository'),
                  style: TextStyle(
                      color: p.muted, fontSize: 12, fontFamily: 'monospace')),
            ),
          if (item.branch.isNotEmpty)
            // Branch names are Latin: keep them left-to-right in Arabic.
            Directionality(
              textDirection: TextDirection.ltr,
              child: SelectableText(item.branch,
                  style: TextStyle(
                      color: p.muted, fontSize: 12, fontFamily: 'monospace')),
            ),
          if (item.prTitle != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('dev.requestLabel'),
                style: TextStyle(color: p.faint, fontSize: 12)),
            SelectableText(item.text, style: TextStyle(color: p.text)),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Semantics(
            liveRegion: true,
            child: Text(item.summary,
                key: const Key('developer-summary'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w600)),
          ),
          if (item.prNumber != null) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(checksLabel(item.checks, l),
                key: const Key('developer-checks'),
                style: TextStyle(color: p.muted, fontSize: 13)),
          ],
          if (item.status == DevStatus.failed && item.failureReason != null)
            _Problem(item.failureReason!),
          if (item.mergeError != null && item.status != DevStatus.merged)
            _Problem(item.mergeError!),
        ]),
      ),
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('dev.progress'),
              style: TextStyle(
                  color: p.text, fontSize: 15, fontWeight: FontWeight.w700)),
          const SizedBox(height: AnumSpacing.xs),
          DeveloperTimeline(stages: item.timeline),
        ]),
      ),
      if (item.prUrl != null || item.runUrl != null)
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          if (item.prUrl != null)
            OutlinedButton.icon(
              key: const Key('developer-pr-link'),
              onPressed: () =>
                  unawaited(openDeveloperLink(context, openLink, item.prUrl)),
              icon: const Icon(Icons.open_in_new),
              label:
                  Text(l.f('dev.pullRequest', {'number': item.prNumber ?? ''})),
            ),
          if (item.runUrl != null)
            OutlinedButton.icon(
              key: const Key('developer-run-link'),
              onPressed: () =>
                  unawaited(openDeveloperLink(context, openLink, item.runUrl)),
              icon: const Icon(Icons.open_in_new),
              label: Text(l.t('dev.codingRun')),
            ),
        ]),
      if (review != null)
        MergeReviewCard(
          approval: review,
          request: item,
          busy: busy == 'merge:${item.id}',
          onConfirm: () => unawaited(controller.merge(item, review)),
          onDismiss: () => controller.dismissReview(item),
        ),
      if (controller.notice != null)
        DeveloperNotice(
          message: controller.notice!,
          failed: controller.noticeFailed,
          onDismiss: controller.dismissNotice,
        ),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        if (canRequestMerge(item) && review == null)
          FilledButton.icon(
            key: const Key('developer-approve'),
            onPressed:
                busy == null ? () => unawaited(controller.review(item)) : null,
            icon: const Icon(Icons.verified_user_outlined),
            label: Text(busy == 'review:${item.id}'
                ? l.t('dev.preparing')
                : l.t('dev.approveMerge')),
          ),
        if (isActive(item))
          OutlinedButton.icon(
            key: const Key('developer-check'),
            onPressed:
                busy == null ? () => unawaited(controller.refresh(item)) : null,
            icon: const Icon(Icons.refresh),
            label: Text(busy == 'refresh:${item.id}'
                ? l.t('dev.checking')
                : l.t('dev.checkNow')),
          ),
        if (canCancel(item))
          OutlinedButton.icon(
            key: const Key('developer-cancel'),
            style: OutlinedButton.styleFrom(foregroundColor: p.stop),
            onPressed:
                busy == null ? () => _confirmCancel(context, item) : null,
            icon: const Icon(Icons.cancel_outlined),
            label: Text(busy == 'cancel:${item.id}'
                ? l.t('dev.cancelling')
                : l.t('dev.cancelRequest')),
          ),
      ]),
    ];
  }

  Future<void> _confirmCancel(BuildContext context, DevRequest item) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) {
        final l = AnumLocalizations.maybeOf(context);
        return AlertDialog(
          title: Text(l.t('dev.cancelTitle')),
          content: Text(
              l.f('dev.cancelText', {'text': shortText(item.text, limit: 60)})),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: Text(l.t('da.keepIt'))),
            FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: context.palette.stop,
                    foregroundColor: context.palette.background),
                onPressed: () => Navigator.pop(context, true),
                child: Text(l.t('dev.cancelRequest'))),
          ],
        );
      },
    );
    if (ok ?? false) await controller.cancel(item);
  }
}

class _Problem extends StatelessWidget {
  const _Problem(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xs),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(Icons.error_outline, size: 18, color: p.stop),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(child: Text(text, style: TextStyle(color: p.stop))),
      ]),
    );
  }
}

/// Queued, Coding, Pull request opened, CI checks, Waiting for approval,
/// Merged: each with its state, read out as "label: state".
class DeveloperTimeline extends StatelessWidget {
  const DeveloperTimeline({required this.stages, super.key});
  final List<DevStage> stages;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    if (stages.isEmpty) {
      return Text(l.t('dev.noProgress'), style: TextStyle(color: p.muted));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final stage in stages)
          Semantics(
            container: true,
            label: '${stage.label}: ${l.t('dev.stage.${stage.state.name}')}',
            excludeSemantics: true,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
              child: Row(children: [
                Icon(_icon(stage.state), size: 20, color: _color(p, stage)),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                  child: Text(stage.label,
                      style: TextStyle(
                          color: stage.state == StageState.pending
                              ? p.faint
                              : p.text,
                          fontWeight: stage.state == StageState.active
                              ? FontWeight.w700
                              : FontWeight.normal)),
                ),
              ]),
            ),
          ),
      ],
    );
  }

  static IconData _icon(StageState state) => switch (state) {
        StageState.done => Icons.check_circle,
        StageState.active => Icons.radio_button_checked,
        StageState.failed => Icons.cancel,
        StageState.pending => Icons.radio_button_unchecked,
      };

  static Color _color(AnumPalette p, DevStage stage) => switch (stage.state) {
        StageState.done => p.ok,
        StageState.active => p.sky,
        StageState.failed => p.stop,
        StageState.pending => p.faint,
      };
}

/// The explicit approval step: exactly what will run, bound to the payload
/// hash shown here. Confirm merge sends that hash to the approvals flow.
class MergeReviewCard extends StatelessWidget {
  const MergeReviewCard({
    required this.approval,
    required this.request,
    required this.busy,
    required this.onConfirm,
    required this.onDismiss,
    this.compact = false,
    super.key,
  });

  final WorkspaceApproval approval;
  final DevRequest request;
  final bool busy;
  final VoidCallback onConfirm, onDismiss;

  /// The chat and voice version: no argument table.
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final expiry = expiryLabel(approval);
    final canConfirm = !busy && approval.canApprove();
    return AnumSurface(
      key: const Key('developer-merge-review'),
      accent: p.warn,
      child: Semantics(
        container: true,
        label: l.t('dev.mergeApproval'),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.verified_user_outlined, color: p.warn),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
              child: Text(l.t('dev.approveTheMerge'),
                  style: TextStyle(
                      color: p.text,
                      fontSize: 16,
                      fontWeight: FontWeight.w700)),
            ),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          Text(
              l.f('dev.squash', {
                'number': request.prNumber ?? '?',
                'title': request.prTitle ?? request.branch
              }),
              style: TextStyle(color: p.text)),
          if (!compact) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(approval.reason, style: TextStyle(color: p.muted)),
            if (approval.target != null)
              Text(l.f('dev.sendsTo', {'target': approval.target!}),
                  style: TextStyle(color: p.muted, fontSize: 12)),
            const SizedBox(height: AnumSpacing.xs),
            Text(l.f('dev.tool', {'tool': approval.action}),
                style: TextStyle(color: p.faint, fontSize: 12)),
            const SizedBox(height: AnumSpacing.xxs),
            ApprovalArgumentsTable(approval: approval),
          ],
          const SizedBox(height: AnumSpacing.xs),
          Text('${l.t('dev.payloadHash')}${expiry.isEmpty ? '' : ' · $expiry'}',
              style: TextStyle(color: p.faint, fontSize: 12)),
          Directionality(
            textDirection: TextDirection.ltr,
            child: SelectableText(approval.payloadHash ?? l.t('dev.notBound'),
                key: const Key('developer-payload-hash'),
                style: TextStyle(
                    color: p.text, fontSize: 13, fontFamily: 'monospace')),
          ),
          if (approval.payloadHash == null)
            _Problem(l.t('dev.notBoundProblem')),
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('dev.mergeNeedsCi'),
              style: TextStyle(color: p.muted, fontSize: 13)),
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
              key: const Key('developer-confirm-merge'),
              onPressed: canConfirm ? onConfirm : null,
              icon: const Icon(Icons.check_circle_outline),
              label: Text(busy ? l.t('dev.merging') : l.t('dev.confirmMerge')),
            ),
            OutlinedButton(
              onPressed: busy ? null : onDismiss,
              child: Text(l.t('da.notNow')),
            ),
          ]),
        ]),
      ),
    );
  }
}
