import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'files_screen.dart';
import '../meetings/meetings_screen.dart';

/// Files, memory, skills and integrations: the workspace's resources.
class ResourcesScreen extends StatefulWidget {
  const ResourcesScreen(
      {required this.controller,
      this.initialTab = ResourcesTab.files,
      this.focusMemoryId,
      super.key});
  final WorkspaceController controller;
  final int initialTab;

  /// A memory to scroll to and mark, e.g. one a run's "Sources used" listed.
  final String? focusMemoryId;

  @override
  State<ResourcesScreen> createState() => _ResourcesScreenState();
}

class _ResourcesScreenState extends State<ResourcesScreen>
    with SingleTickerProviderStateMixin {
  late final _tabs =
      TabController(length: 5, vsync: this, initialIndex: widget.initialTab);

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          return AnumBackdrop(
            child: SafeArea(
              bottom: false,
              child: Column(children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: AnumHeader(
                    eyebrow: 'Workspace',
                    title: 'Resources',
                    subtitle: [
                      _plural(
                          c.filesController?.usage?.fileCount ?? c.files.length,
                          'file'),
                      _plural(c.memories.length, 'memory', 'memories'),
                      _plural(c.skills.length, 'skill'),
                      _plural(c.integrations.length, 'service'),
                    ].join(', '),
                    large: true,
                  ),
                ),
                TabBar(
                  controller: _tabs,
                  isScrollable: true,
                  tabAlignment: TabAlignment.start,
                  labelColor: p.text,
                  unselectedLabelColor: p.muted,
                  indicatorColor: p.violet,
                  dividerColor: p.line,
                  tabs: [
                    Tab(text: 'Files'),
                    Tab(text: 'Memory'),
                    Tab(text: 'Skills'),
                    Tab(text: 'Services'),
                    Tab(text: AnumLocalizations.maybeOf(context).t('meetings')),
                  ],
                ),
                Expanded(
                  child: TabBarView(controller: _tabs, children: [
                    _FilesTab(controller: c),
                    _MemoryTab(
                        controller: c, focusMemoryId: widget.focusMemoryId),
                    _SkillsTab(controller: c),
                    _ServicesTab(controller: c),
                    _MeetingsTab(controller: c),
                  ]),
                ),
              ]),
            ),
          );
        },
      );
}

Widget _tabList(BuildContext context, Future<void> Function() refresh,
        List<Widget> children) =>
    RefreshIndicator(
      onRefresh: refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        children: [
          for (var i = 0; i < children.length; i++) ...[
            children[i],
            if (i < children.length - 1) const SizedBox(height: 12),
          ]
        ],
      ),
    );

class _FilesTab extends StatelessWidget {
  const _FilesTab({required this.controller});
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final files = controller.filesController;
    if (files == null) {
      return _tabList(context, controller.load, const [
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.folder_off_outlined,
            title: 'Files are not available',
            message:
                'This build of the app has no connection to the files API.',
          ),
        ),
      ]);
    }
    return FilesTab(controller: files, currentUserId: controller.currentUserId);
  }
}

/// Tab positions in [ResourcesScreen].
abstract final class ResourcesTab {
  static const files = 0;
  static const memory = 1;
  static const skills = 2;
  static const services = 3;

  /// Calls & Meetings (docs/meetings.md#mobile).
  static const meetings = 4;
}

class _MeetingsTab extends StatelessWidget {
  const _MeetingsTab({required this.controller});
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final meetings = controller.meetingsController;
    if (meetings == null) {
      return _tabList(context, controller.load, [
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.phone_disabled_outlined,
            title: AnumLocalizations.maybeOf(context).t('meetings'),
            message:
                'This build of the app has no connection to the meetings API.',
          ),
        ),
      ]);
    }
    return MeetingsTab(
        controller: meetings, currentUserId: controller.currentUserId);
  }
}

/// Opens the Memory tab on its own page with [memoryId] scrolled into view
/// and marked (the "Sources used" panel's memory rows).
Future<void> openMemoryInResources(BuildContext context,
        WorkspaceController controller, String memoryId) =>
    Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => Scaffold(
                appBar: AppBar(title: const Text('Memory')),
                body: ResourcesScreen(
                    controller: controller,
                    initialTab: ResourcesTab.memory,
                    focusMemoryId: memoryId))));

class _MemoryTab extends StatefulWidget {
  const _MemoryTab({required this.controller, this.focusMemoryId});
  final WorkspaceController controller;
  final String? focusMemoryId;
  @override
  State<_MemoryTab> createState() => _MemoryTabState();
}

class _MemoryTabState extends State<_MemoryTab> {
  final _content = TextEditingController();
  final _focused = GlobalKey();
  String? _taskId;

  @override
  void initState() {
    super.initState();
    if (widget.focusMemoryId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        final target = _focused.currentContext;
        if (mounted && target != null) {
          Scrollable.ensureVisible(target,
              alignment: 0.2, duration: const Duration(milliseconds: 250));
        }
      });
    }
  }

  @override
  void dispose() {
    _content.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final taskId = _taskId;
    final text = _content.text.trim();
    if (taskId == null || text.isEmpty) return;
    await widget.controller.addMemory(taskId, text);
    _content.clear();
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final p = context.palette;
    final memories = [...c.memories]
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    String taskTitle(String id) =>
        c.tasks.where((t) => t.id == id).firstOrNull?.title ?? id;
    return _tabList(context, c.load, [
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const AnumHeader(
            title: 'Teach your agents',
            subtitle:
                'Memories are linked to a task so you can see where each fact came from.',
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: _taskId,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Related task'),
            items: [
              for (final t in c.tasks)
                DropdownMenuItem(
                    value: t.id,
                    child: Text(t.title, overflow: TextOverflow.ellipsis)),
            ],
            onChanged: (v) => setState(() => _taskId = v),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _content,
            minLines: 2,
            maxLines: 5,
            onChanged: (_) => setState(() {}),
            decoration:
                const InputDecoration(labelText: 'What should they remember?'),
          ),
          const SizedBox(height: 10),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: FilledButton.icon(
              onPressed:
                  c.mutating || _taskId == null || _content.text.trim().isEmpty
                      ? null
                      : _add,
              icon: const Icon(Icons.add),
              label: const Text('Save memory'),
            ),
          ),
          if (c.tasks.isEmpty)
            Text('Create a task first; memories are always tied to one.',
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ]),
      ),
      if (memories.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.psychology_outlined,
            title: 'No memories yet',
            message: 'Saved context shows here with its source and task.',
          ),
        )
      else
        AnumSurface(
          child: Column(children: [
            for (final m in memories)
              AnumRow(
                key: m.id == widget.focusMemoryId ? _focused : null,
                icon: Icons.psychology_outlined,
                tone: m.id == widget.focusMemoryId ? AnumTone.accent : null,
                title: m.content,
                subtitle: 'From: ${taskTitle(m.taskId)}',
                detail: [
                  '${humanSource(m.sourceType)} · ${exactTime(m.createdAt)}',
                  if (m.id == widget.focusMemoryId)
                    AnumLocalizations.maybeOf(context).t('memoryUsedByRun'),
                ].join('\n'),
                trailing: IconButton(
                  tooltip: 'Delete memory',
                  icon: const Icon(Icons.delete_outline),
                  onPressed:
                      c.mutating ? null : () => _confirmDelete(context, m),
                ),
              ),
          ]),
        ),
    ]);
  }

  Future<void> _confirmDelete(
      BuildContext context, WorkspaceMemory memory) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this memory?'),
        content: Text('“${memory.content}”\n\nAgents will no longer use it.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (ok == true) await widget.controller.deleteMemory(memory.id);
  }
}

String humanSource(String value) => switch (value) {
      'mobile' => 'Added from the app',
      'web' || 'user' || 'manual' => 'Added by a person',
      'agent' || 'run' || 'runtime' => 'Learned by an agent',
      'voice' => 'From voice',
      'unknown' => 'Source not recorded',
      _ => value.replaceAll('_', ' '),
    };

class _SkillsTab extends StatelessWidget {
  const _SkillsTab({required this.controller});
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final skills = controller.skills;
    final p = context.palette;
    return _tabList(context, controller.load, [
      if (controller.skillNotice != null)
        AnumSurface(
          child: Semantics(
            liveRegion: true,
            child: Text(controller.skillNotice!,
                style: TextStyle(color: p.text, fontSize: AnumType.smPlus)),
          ),
        ),
      if (skills.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.extension_outlined,
            title: 'No skills published',
            message:
                'An organization administrator publishes skills for every workspace. Ask one to publish the skill you need; it then appears here to install.',
          ),
        )
      else
        for (final s in skills)
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const AnumIconChip(icon: Icons.extension_outlined),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(s.name,
                            style: TextStyle(
                                color: p.text,
                                fontSize: AnumType.lg,
                                fontWeight: FontWeight.w700)),
                        Text('v${s.version} · ${s.skillId}',
                            style: TextStyle(
                                color: p.faint, fontSize: AnumType.xs)),
                        const SizedBox(height: 6),
                        // Under the name so it never squeezes it on narrow screens.
                        Wrap(spacing: 6, runSpacing: 6, children: [
                          AnumPill(
                              label: '${s.risk} risk', tone: riskTone(s.risk)),
                          if (s.signature != null)
                            AnumPill(
                                label: s.signature!.label,
                                tone: _signatureTone(s.signature!),
                                icon: s.signature!.verified
                                    ? Icons.verified_user_outlined
                                    : Icons.gpp_maybe_outlined),
                        ]),
                      ]),
                ),
              ]),
              const SizedBox(height: 10),
              Text(s.description,
                  style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
              if (s.installed && !s.enabled && s.disabledReason != null) ...[
                const SizedBox(height: 8),
                Text(s.disabledReason!,
                    style:
                        TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
              ],
              if (s.signature != null && !s.signature!.verified) ...[
                const SizedBox(height: 8),
                // Installed but no longer verifies (a revoked key): flagged.
                Text(
                    s.installed
                        ? 'Flagged: ${s.signature!.detail}'
                        : s.signature!.detail,
                    style: TextStyle(
                        color: anumToneColor(
                            context, _signatureTone(s.signature!)),
                        fontSize: AnumType.xsPlus)),
              ] else if (s.signature?.fingerprint != null) ...[
                const SizedBox(height: 6),
                Text(
                    'Key ${s.signature!.keyId}, fingerprint ${s.signature!.fingerprint}',
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ],
              if (s.tools.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text('Can use',
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                const SizedBox(height: 6),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final tool in s.tools)
                    AnumPill(label: tool, icon: Icons.build_outlined),
                ]),
              ],
              const SizedBox(height: 12),
              Wrap(
                alignment: WrapAlignment.end,
                crossAxisAlignment: WrapCrossAlignment.center,
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (s.installed) ...[
                    AnumPill(
                        label: s.enabled ? 'Installed' : 'Disabled',
                        tone: s.enabled ? AnumTone.ok : AnumTone.warn,
                        icon: s.enabled ? Icons.check : Icons.pause),
                    OutlinedButton(
                      onPressed: controller.mutating
                          ? null
                          : () => controller.setSkillEnabled(s, !s.enabled),
                      child: Text(s.enabled ? 'Disable' : 'Enable'),
                    ),
                    TextButton(
                      onPressed: controller.mutating
                          ? null
                          : () => _confirmUninstall(context, s),
                      child: const Text('Uninstall'),
                    ),
                  ] else ...[
                    if (s.hasInstall)
                      AnumPill(label: 'v${s.installedVersion} installed'),
                    FilledButton(
                      // Only a verified version installs; the API refuses the rest.
                      onPressed: controller.mutating || !s.installable
                          ? null
                          : () => controller.installSkill(s),
                      child: Text(
                          s.hasInstall ? 'Install v${s.version}' : 'Install'),
                    ),
                  ],
                ],
              ),
            ]),
          ),
    ]);
  }

  static AnumTone _signatureTone(SkillSignature s) => switch (s.status) {
        'verified' => AnumTone.ok,
        'unsigned' => AnumTone.warn,
        _ => AnumTone.stop,
      };

  Future<void> _confirmUninstall(BuildContext context, WorkspaceSkill s) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Uninstall ${s.name}?'),
        content: const Text(
            'Agents in this workspace stop using it and its tool grants are removed. The change is audited.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Uninstall')),
        ],
      ),
    );
    if (yes == true) await controller.uninstallSkill(s);
  }
}

class _ServicesTab extends StatelessWidget {
  const _ServicesTab({required this.controller});
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final items = controller.integrations;
    return _tabList(context, controller.load, [
      if (items.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.hub_outlined,
            title: 'No services reported',
            message: 'The API did not return any integration health records.',
          ),
        )
      else
        AnumSurface(
          child: Column(children: [
            for (final x in items)
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                AnumRow(
                  icon: _icon(x.kind),
                  tone: integrationTone(x.status),
                  title: x.name,
                  subtitle: x.detail,
                  detail:
                      '${x.endpoint}${x.latencyMs == null ? '' : ' · ${x.latencyMs} ms'}',
                ),
                // The status sits under the text so it never squeezes it
                // (narrow phones, large text).
                Padding(
                  padding:
                      const EdgeInsetsDirectional.only(start: 52, bottom: 10),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        AnumPill(
                            label: integrationLabel(x.status),
                            tone: integrationTone(x.status)),
                        if (x.remediation != null) ...[
                          const SizedBox(height: 6),
                          Text('How to fix: ${x.remediation}',
                              style: TextStyle(
                                  color: context.palette.text,
                                  fontSize: AnumType.sm)),
                        ],
                        if (x.lastError != null && x.lastError != x.detail) ...[
                          const SizedBox(height: 4),
                          Text('Last error: ${x.lastError}',
                              style: TextStyle(
                                  color: context.palette.warn,
                                  fontSize: AnumType.xs)),
                        ],
                      ]),
                ),
              ]),
          ]),
        ),
    ]);
  }

  IconData _icon(String kind) => switch (kind) {
        'database' => Icons.storage_outlined,
        'cache' => Icons.memory_outlined,
        'event_bus' => Icons.podcasts_outlined,
        'workflow' => Icons.account_tree_outlined,
        'identity' => Icons.badge_outlined,
        'object_storage' => Icons.cloud_outlined,
        _ => Icons.hub_outlined,
      };
}

String _plural(int n, String one, [String? many]) =>
    '$n ${n == 1 ? one : (many ?? '${one}s')}';
