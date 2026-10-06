import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'workspace_sheets.dart';

/// Files, memory, skills and integrations: the workspace's resources.
class ResourcesScreen extends StatefulWidget {
  const ResourcesScreen({required this.controller, super.key});
  final WorkspaceController controller;

  @override
  State<ResourcesScreen> createState() => _ResourcesScreenState();
}

class _ResourcesScreenState extends State<ResourcesScreen>
    with SingleTickerProviderStateMixin {
  late final _tabs = TabController(length: 4, vsync: this);

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
                      _plural(c.files.length, 'file'),
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
                  tabs: const [
                    Tab(text: 'Files'),
                    Tab(text: 'Memory'),
                    Tab(text: 'Skills'),
                    Tab(text: 'Services'),
                  ],
                ),
                Expanded(
                  child: TabBarView(controller: _tabs, children: [
                    _FilesTab(controller: c),
                    _MemoryTab(controller: c),
                    _SkillsTab(controller: c),
                    _ServicesTab(controller: c),
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

  Future<void> _upload(BuildContext context) async {
    final picked = await FilePicker.pickFiles(withData: false);
    final path = picked?.files.single.path;
    if (path == null) return;
    await controller.uploadFile(path);
  }

  IconData _icon(String type) {
    if (type.startsWith('image/')) return Icons.image_outlined;
    if (type.contains('pdf')) return Icons.picture_as_pdf_outlined;
    if (type.startsWith('text/') || type.contains('json')) {
      return Icons.description_outlined;
    }
    if (type.startsWith('audio/')) return Icons.audio_file_outlined;
    return Icons.insert_drive_file_outlined;
  }

  @override
  Widget build(BuildContext context) {
    final files = [...controller.files]
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    final total = files.fold<int>(0, (sum, f) => sum + f.sizeBytes);
    return _tabList(context, controller.load, [
      AnumSurface(
        child: Row(children: [
          Expanded(
            child: AnumHeader(
              title: '${files.length} files',
              subtitle:
                  '${formatBytes(total)} stored · checksum verified on upload',
            ),
          ),
          FilledButton.icon(
            onPressed: controller.mutating ? null : () => _upload(context),
            icon: const Icon(Icons.upload_rounded),
            label: const Text('Upload'),
          ),
        ]),
      ),
      if (files.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.folder_open,
            title: 'No files yet',
            message: 'Upload documents your agents can read in this workspace.',
          ),
        )
      else
        AnumSurface(
          child: Column(children: [
            for (final f in files)
              AnumRow(
                icon: _icon(f.contentType),
                title: f.name,
                subtitle: '${f.contentType} · ${formatBytes(f.sizeBytes)}',
                detail: 'Added ${exactTime(f.createdAt)}',
                trailing: const Icon(Icons.chevron_right),
                onTap: () => showModalBottomSheet<void>(
                  context: context,
                  builder: (_) => FileDetailSheet(
                    file: f,
                    onDownload: () => controller.repository.downloadFile(f),
                    onDelete: () => controller.deleteFile(f.id),
                  ),
                ),
              ),
          ]),
        ),
    ]);
  }
}

class _MemoryTab extends StatefulWidget {
  const _MemoryTab({required this.controller});
  final WorkspaceController controller;
  @override
  State<_MemoryTab> createState() => _MemoryTabState();
}

class _MemoryTabState extends State<_MemoryTab> {
  final _content = TextEditingController();
  String? _taskId;

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
                style: TextStyle(color: p.faint, fontSize: 12)),
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
                icon: Icons.psychology_outlined,
                title: m.content,
                subtitle: 'From: ${taskTitle(m.taskId)}',
                detail:
                    '${humanSource(m.sourceType)} · ${exactTime(m.createdAt)}',
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
      if (skills.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.extension_outlined,
            title: 'No skills published',
            message:
                'Skills are reviewed capability packs. Published versions will appear here to install.',
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
                                fontSize: 16,
                                fontWeight: FontWeight.w700)),
                        Text('v${s.version} · ${s.skillId}',
                            style: TextStyle(color: p.faint, fontSize: 12)),
                      ]),
                ),
                AnumPill(label: '${s.risk} risk', tone: riskTone(s.risk)),
              ]),
              const SizedBox(height: 10),
              Text(s.description,
                  style: TextStyle(color: p.muted, fontSize: 13.5)),
              if (s.tools.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text('Can use', style: TextStyle(color: p.faint, fontSize: 12)),
                const SizedBox(height: 6),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final tool in s.tools)
                    AnumPill(label: tool, icon: Icons.build_outlined),
                ]),
              ],
              const SizedBox(height: 12),
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: s.installed
                    ? const AnumPill(
                        label: 'Installed',
                        tone: AnumTone.ok,
                        icon: Icons.check)
                    : FilledButton(
                        onPressed: controller.mutating
                            ? null
                            : () => controller.installSkill(s),
                        child: const Text('Install'),
                      ),
              ),
            ]),
          ),
    ]);
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
              AnumRow(
                icon: _icon(x.kind),
                tone: integrationTone(x.status),
                title: x.name,
                subtitle: x.detail,
                detail:
                    '${x.endpoint}${x.latencyMs == null ? '' : ' · ${x.latencyMs} ms'}',
                trailing: AnumPill(
                    label: integrationLabel(x.status),
                    tone: integrationTone(x.status)),
              ),
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
