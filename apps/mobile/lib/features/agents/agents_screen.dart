import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'agents_controller.dart';
import 'agents_models.dart';
import 'agents_strings.dart';

/// Builds the chat with an agent (the Assistant screen on that agent's threads).
typedef AgentCallOpener = Future<void> Function(
    BuildContext context, AgentProfile agent);

typedef AgentChatBuilder = Widget Function(
  BuildContext context,
  AgentProfile agent,
);

const _icons = <String, IconData>{
  'bot': Icons.smart_toy_outlined,
  'truck': Icons.local_shipping_outlined,
  'search': Icons.search,
  'mail': Icons.mail_outline,
  'calendar': Icons.calendar_today_outlined,
  'file-text': Icons.description_outlined,
  'phone': Icons.phone_outlined,
  'briefcase': Icons.work_outline,
  'user': Icons.person_outline,
  'sparkles': Icons.auto_awesome_outlined,
  'wrench': Icons.build_outlined,
  'chart': Icons.bar_chart,
  'shopping-cart': Icons.shopping_cart_outlined,
  'heart': Icons.favorite_border,
};

Color agentColor(String hex) {
  final value = int.tryParse(hex.replaceFirst('#', ''), radix: 16);
  return value == null ? const Color(0xFF4F46E5) : Color(0xFF000000 | value);
}

class AgentAvatar extends StatelessWidget {
  const AgentAvatar({
    required this.icon,
    required this.color,
    this.size = 44,
    super.key,
  });
  final String icon, color;
  final double size;
  @override
  Widget build(BuildContext context) {
    final tint = agentColor(color);
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: tint.withValues(alpha: 0.32),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: tint.withValues(alpha: 0.6)),
        ),
        child: Icon(
          _icons[icon] ?? Icons.smart_toy_outlined,
          color: Colors.white,
          size: size * 0.48,
        ),
      ),
    );
  }
}

/// The Agents list (docs/agents.md): categories All, Work, Personal and Custom,
/// search, paging, and Create agent.
class AgentsScreen extends StatefulWidget {
  const AgentsScreen(
      {required this.controller, this.chatBuilder, this.callOpener, super.key});
  final AgentsController controller;
  final AgentChatBuilder? chatBuilder;

  /// Call opens the voice call screen (docs/voice.md#call-mode); without it,
  /// Call opens the call summary.
  final AgentCallOpener? callOpener;
  @override
  State<AgentsScreen> createState() => _AgentsScreenState();
}

class _AgentsScreenState extends State<AgentsScreen> {
  final _search = TextEditingController();
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    widget.controller.load();
    widget.controller.loadCatalog();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  Future<void> _openCreate() async {
    final c = widget.controller;
    if (c.catalog == null) await c.loadCatalog();
    if (!mounted) return;
    final catalog = c.catalog;
    if (catalog == null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(c.catalogError ?? '')));
      return;
    }
    final created = await Navigator.push<AgentProfile>(
      context,
      MaterialPageRoute(
        builder: (_) => AgentEditorScreen(
          catalog: catalog,
          onSave: (draft) => c.create(draft),
        ),
      ),
    );
    if (created != null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            AgentsText.of(context).f('created', {'name': created.name}),
          ),
        ),
      );
      _open(created);
    }
  }

  Future<void> _open(AgentProfile agent) async {
    final c = widget.controller;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => AgentDetailScreen(
          controller: AgentDetailController(c.repository, agent.id),
          catalog: c.catalog,
          chatBuilder: widget.chatBuilder,
          callOpener: widget.callOpener,
        ),
      ),
    );
    if (mounted) await c.load();
  }

  @override
  Widget build(BuildContext context) {
    final s = AgentsText.of(context);
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('title'))),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openCreate,
        icon: const Icon(Icons.add),
        label: Text(s.t('create')),
      ),
      body: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          int count(String key) => c.counts[key] ?? 0;
          return AnumPage(
            onRefresh: c.load,
            children: [
              Text(
                s.t('subtitle'),
                style: TextStyle(color: p.muted, fontSize: 13.5),
              ),
              TextField(
                controller: _search,
                decoration: InputDecoration(
                  prefixIcon: const Icon(Icons.search),
                  labelText: s.t('search'),
                ),
                onChanged: (value) {
                  _debounce?.cancel();
                  _debounce = Timer(
                    const Duration(milliseconds: 300),
                    () => c.setQuery(value),
                  );
                },
              ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  ChoiceChip(
                    label: Text('${s.t('all')} ${count('all')}'),
                    selected: c.category == null,
                    onSelected: (_) => c.setCategory(null),
                  ),
                  for (final category in agentCategories)
                    ChoiceChip(
                      label: Text('${s.t('cat.$category')} ${count(category)}'),
                      selected: c.category == category,
                      onSelected: (_) => c.setCategory(category),
                    ),
                ],
              ),
              if (c.phase == AgentsPhase.loading)
                Semantics(
                  liveRegion: true,
                  child: Text(s.t('loading'), style: TextStyle(color: p.muted)),
                ),
              if (c.phase == AgentsPhase.error)
                AnumSurface(
                  child: AnumEmpty(
                    icon: Icons.error_outline,
                    title: s.t('errorTitle'),
                    message: c.error ?? '',
                    action: s.t('retry'),
                    onAction: c.load,
                  ),
                ),
              if (c.phase == AgentsPhase.ready && c.items.isEmpty)
                AnumSurface(
                  child: c.filtered
                      ? AnumEmpty(
                          icon: Icons.search_off,
                          title: s.t('noMatchTitle'),
                          message: s.t('noMatchText'),
                          action: s.t('clearFilters'),
                          onAction: () {
                            _search.clear();
                            c.clearFilters();
                          },
                        )
                      : AnumEmpty(
                          icon: Icons.smart_toy_outlined,
                          title: s.t('emptyTitle'),
                          message: s.t('emptyText'),
                          action: s.t('create'),
                          onAction: _openCreate,
                        ),
                ),
              for (final agent in c.items)
                _AgentCard(agent: agent, onTap: () => _open(agent)),
              if (c.nextCursor != null)
                OutlinedButton(
                  onPressed: c.loadingMore ? null : c.loadMore,
                  child: Text(s.t('loadMore')),
                ),
              const SizedBox(height: 64),
            ],
          );
        },
      ),
    );
  }
}

class _AgentCard extends StatelessWidget {
  const _AgentCard({required this.agent, required this.onTap});
  final AgentProfile agent;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final meta = [
      s.t('cat.${agent.category}'),
      if (agent.department != null) agent.department!,
      agent.shared ? s.t('shared') : s.t('private'),
    ].join(' · ');
    return AnumSurface(
      onTap: onTap,
      semanticLabel: agent.name,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AgentAvatar(icon: agent.icon, color: agent.color),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        agent.name,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: p.text,
                          fontSize: 15,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    if (agent.paused)
                      AnumPill(label: s.t('paused'), tone: AnumTone.warn),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  agent.description.isEmpty
                      ? s.t('noDescription')
                      : agent.description,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: p.muted, fontSize: 13),
                ),
                const SizedBox(height: 4),
                Text(meta, style: TextStyle(color: p.faint, fontSize: 12)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One agent: Chat, Call, Edit and More, and the Overview, Skills, Memory and
/// Settings tabs.
class AgentDetailScreen extends StatefulWidget {
  const AgentDetailScreen({
    required this.controller,
    this.catalog,
    this.chatBuilder,
    this.callOpener,
    super.key,
  });
  final AgentDetailController controller;
  final AgentCatalog? catalog;
  final AgentChatBuilder? chatBuilder;
  final AgentCallOpener? callOpener;
  @override
  State<AgentDetailScreen> createState() => _AgentDetailScreenState();
}

class _AgentDetailScreenState extends State<AgentDetailScreen>
    with SingleTickerProviderStateMixin {
  late final _tabs = TabController(length: 4, vsync: this)..addListener(_onTab);
  final _prompt = TextEditingController();
  final _memory = TextEditingController();

  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  void _onTab() {
    if (_tabs.index == 2 && !widget.controller.memoriesLoaded) {
      widget.controller.loadMemories();
    }
    setState(() {});
  }

  @override
  void dispose() {
    _tabs.dispose();
    _prompt.dispose();
    _memory.dispose();
    super.dispose();
  }

  void _snack(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  Future<void> _edit(AgentProfile agent) async {
    final c = widget.controller;
    var catalog = widget.catalog;
    if (catalog == null) {
      try {
        catalog = await c.repository.catalog();
      } catch (e) {
        _snack(agentError(e));
        return;
      }
    }
    if (!mounted) return;
    final saved = await Navigator.push<AgentProfile>(
      context,
      MaterialPageRoute(
        builder: (_) => AgentEditorScreen(
          catalog: catalog!,
          agent: agent,
          onSave: (draft) async {
            if (!await c.save(draft)) throw StateError(c.actionError ?? '');
            return c.agent!;
          },
        ),
      ),
    );
    if (saved != null && mounted) _snack(AgentsText.of(context).t('saved'));
  }

  Future<void> _delete(AgentProfile agent) async {
    final s = AgentsText.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.f('deleteTitle', {'name': agent.name})),
        content: Text(s.t('deleteText')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(s.t('cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(s.t('delete')),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    if (await widget.controller.delete()) {
      if (!mounted) return;
      _snack(s.f('deleted', {'name': agent.name}));
      Navigator.pop(context);
    }
  }

  void _call(AgentProfile agent) {
    final s = AgentsText.of(context);
    final notes = TextEditingController();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          16,
          16,
          16,
          16 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: ListenableBuilder(
          listenable: widget.controller,
          builder: (context, _) {
            final c = widget.controller;
            return Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  s.t('callTitle'),
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                Text(s.t('callNote')),
                const SizedBox(height: 12),
                TextField(
                  controller: notes,
                  minLines: 4,
                  maxLines: 8,
                  maxLength: 7000,
                  decoration: InputDecoration(labelText: s.t('callHint')),
                ),
                FilledButton.icon(
                  onPressed: c.running || !agent.canRun
                      ? null
                      : () => c.run(
                            '${s.t('callPrompt')}\n\n${notes.text.trim()}',
                            title: s.f('callTaskTitle', {'name': agent.name}),
                          ),
                  icon: const Icon(Icons.summarize_outlined),
                  label: Text(c.running ? s.t('running') : s.t('callSubmit')),
                ),
                _RunFeedback(controller: c),
              ],
            );
          },
        ),
      ),
    ).whenComplete(notes.dispose);
  }

  @override
  Widget build(BuildContext context) {
    final s = AgentsText.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final agent = c.agent;
        return Scaffold(
          appBar: AppBar(
            title: Text(agent?.name ?? s.t('title')),
            actions: [
              if (agent != null)
                PopupMenuButton<String>(
                  tooltip: s.t('more'),
                  icon: const Icon(Icons.more_vert),
                  onSelected: (value) {
                    if (value == 'summary') _call(agent);
                    if (value == 'pause') c.setPaused(!agent.paused);
                    if (value == 'delete') _delete(agent);
                  },
                  itemBuilder: (_) => [
                    if (!agent.paused)
                      PopupMenuItem(
                        value: 'summary',
                        child: Text(s.t('callTitle')),
                      ),
                    if (agent.canEdit) ...[
                      PopupMenuItem(
                        value: 'pause',
                        child:
                            Text(agent.paused ? s.t('resume') : s.t('pause')),
                      ),
                      PopupMenuItem(
                          value: 'delete', child: Text(s.t('delete'))),
                    ],
                  ],
                ),
            ],
          ),
          body: switch (c.phase) {
            AgentsPhase.loading => const Center(
                child: CircularProgressIndicator(),
              ),
            AgentsPhase.error => AnumPage(
                onRefresh: c.load,
                children: [
                  AnumSurface(
                    child: AnumEmpty(
                      icon: c.missing ? Icons.search_off : Icons.error_outline,
                      title:
                          c.missing ? s.t('missingTitle') : s.t('errorTitle'),
                      message: c.missing ? s.t('missingText') : (c.error ?? ''),
                      action: c.missing ? null : s.t('retry'),
                      onAction: c.missing ? null : c.load,
                    ),
                  ),
                ],
              ),
            AgentsPhase.ready => _body(context, agent!),
          },
        );
      },
    );
  }

  Widget _body(BuildContext context, AgentProfile agent) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final c = widget.controller;
    return AnumPage(
      onRefresh: c.load,
      children: [
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  AgentAvatar(icon: agent.icon, color: agent.color, size: 52),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          agent.name,
                          style: TextStyle(
                            color: p.text,
                            fontSize: 19,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          agent.description.isEmpty
                              ? s.t('noDescription')
                              : agent.description,
                          style: TextStyle(color: p.muted, fontSize: 13),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  AnumPill(
                    label: agent.paused ? s.t('paused') : s.t('active'),
                    tone: agent.paused ? AnumTone.warn : AnumTone.ok,
                  ),
                  AnumPill(label: s.t('cat.${agent.category}')),
                  AnumPill(
                    label: agent.shared ? s.t('shared') : s.t('private'),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FilledButton.icon(
                    onPressed: agent.paused || widget.chatBuilder == null
                        ? null
                        : () => Navigator.push(
                              context,
                              MaterialPageRoute<void>(
                                builder: (ctx) => Scaffold(
                                  appBar: AppBar(title: Text(agent.name)),
                                  body: widget.chatBuilder!(ctx, agent),
                                ),
                              ),
                            ),
                    icon: const Icon(Icons.chat_bubble_outline),
                    label: Text(s.t('chat')),
                  ),
                  OutlinedButton.icon(
                    onPressed: agent.paused
                        ? null
                        : () => widget.callOpener != null
                            ? widget.callOpener!(context, agent)
                            : _call(agent),
                    icon: const Icon(Icons.phone_outlined),
                    label: Text(s.t('call')),
                  ),
                  if (agent.canEdit)
                    OutlinedButton.icon(
                      onPressed: () => _edit(agent),
                      icon: const Icon(Icons.edit_outlined),
                      label: Text(s.t('edit')),
                    ),
                ],
              ),
            ],
          ),
        ),
        if (agent.paused)
          Text(s.t('pausedNote'), style: TextStyle(color: p.warn)),
        if (!agent.canEdit)
          Text(s.t('readOnly'), style: TextStyle(color: p.muted)),
        if (c.actionError != null)
          Text(c.actionError!, style: TextStyle(color: p.stop)),
        TabBar(
          controller: _tabs,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          labelColor: p.text,
          unselectedLabelColor: p.muted,
          indicatorColor: p.violet,
          dividerColor: p.line,
          tabs: [
            for (final tab in const [
              'overview',
              'skills',
              'memory',
              'settings',
            ])
              Tab(text: s.t('tab.$tab')),
          ],
        ),
        ...switch (_tabs.index) {
          0 => _overview(context, agent),
          1 => _skills(context, agent),
          2 => _memoryTab(context, agent),
          _ => _settings(context, agent),
        },
      ],
    );
  }

  List<Widget> _overview(BuildContext context, AgentProfile agent) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final c = widget.controller;
    return [
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              s.t('runTitle'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            if (agent.canRun) ...[
              TextField(
                controller: _prompt,
                minLines: 2,
                maxLines: 5,
                maxLength: 8000,
                decoration: InputDecoration(
                  labelText: s.f('runHint', {'name': agent.name}),
                ),
              ),
              FilledButton.icon(
                onPressed: c.running
                    ? null
                    : () async {
                        if (await c.run(_prompt.text)) _prompt.clear();
                      },
                icon: const Icon(Icons.play_arrow),
                label: Text(c.running ? s.t('running') : s.t('runSubmit')),
              ),
            ] else
              Text(
                agent.paused ? s.t('runPaused') : s.t('runNoPermission'),
                style: TextStyle(color: p.muted),
              ),
            _RunFeedback(controller: c),
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('recentTasks'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            if (c.tasksError != null) ...[
              Text(c.tasksError!, style: TextStyle(color: p.stop)),
              TextButton(onPressed: c.loadTasks, child: Text(s.t('retry'))),
            ] else if (c.tasks == null)
              const LinearProgressIndicator()
            else if (c.tasks!.isEmpty)
              Text(s.t('noTasks'), style: TextStyle(color: p.muted))
            else
              for (final task in c.tasks!)
                AnumRow(
                  title: task.title,
                  detail: task.status,
                  icon: Icons.task_alt,
                ),
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('instructions'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            Text(
              agent.instructions.isEmpty
                  ? s.t('noInstructions')
                  : agent.instructions,
              style: TextStyle(color: p.muted),
            ),
            const SizedBox(height: 10),
            Text(
              '${s.t('model')}: ${s.t('model.${agent.modelRoute}')}',
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
            Text(
              '${s.t('knowledge')}: ${s.t('knowledge.${agent.knowledgeMode}')}',
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
          ],
        ),
      ),
    ];
  }

  List<Widget> _skills(BuildContext context, AgentProfile agent) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final skills = {
      for (final skill in widget.catalog?.skills ?? const <AgentSkill>[])
        skill.id: skill,
    };
    return [
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('skills'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            if (agent.allowedSkills.isEmpty)
              Text(s.t('noSkills'), style: TextStyle(color: p.muted)),
            for (final id in agent.allowedSkills)
              AnumRow(
                title: skills[id]?.name ?? id,
                subtitle: skills[id]?.description,
                icon: Icons.extension_outlined,
              ),
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('tools'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            for (final tool in [alwaysOnTool, ...agent.allowedTools])
              AnumRow(
                title: tool,
                icon: Icons.handyman_outlined,
                tone: agent.unavailableTools.contains(tool)
                    ? AnumTone.stop
                    : null,
              ),
          ],
        ),
      ),
    ];
  }

  List<Widget> _memoryTab(BuildContext context, AgentProfile agent) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final c = widget.controller;
    return [
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              s.t('memoryTitle'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            Text(
              s.t('memoryIntro'),
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
            const SizedBox(height: 8),
            if (agent.canEdit) ...[
              TextField(
                controller: _memory,
                maxLength: 2000,
                minLines: 1,
                maxLines: 4,
                decoration: InputDecoration(labelText: s.t('memoryAdd')),
              ),
              OutlinedButton.icon(
                onPressed: c.busy
                    ? null
                    : () async {
                        if (await c.addMemory(_memory.text)) _memory.clear();
                      },
                icon: const Icon(Icons.add),
                label: Text(s.t('memorySave')),
              ),
            ] else
              Text(s.t('memoryReadOnly'), style: TextStyle(color: p.muted)),
            if (c.memoryError != null)
              Text(c.memoryError!, style: TextStyle(color: p.stop)),
            if (!c.memoriesLoaded && c.memoryError == null)
              const LinearProgressIndicator(),
            if (c.memoriesLoaded && c.memories.isEmpty)
              Text(s.t('memoryEmpty'), style: TextStyle(color: p.muted)),
            if (c.memoriesLoaded && c.memories.isNotEmpty)
              Text(
                s.f('memoryCount', {'count': c.memoryTotal}),
                style: TextStyle(color: p.faint, fontSize: 12),
              ),
            for (final memory in c.memories)
              AnumRow(
                title: memory.content,
                icon: Icons.psychology_outlined,
                trailing: agent.canEdit
                    ? IconButton(
                        tooltip: s.t('memoryDelete'),
                        onPressed: c.busy ? null : () => c.deleteMemory(memory),
                        icon: const Icon(Icons.delete_outline),
                      )
                    : null,
              ),
          ],
        ),
      ),
    ];
  }

  List<Widget> _settings(BuildContext context, AgentProfile agent) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final c = widget.controller;
    final floor = widget.catalog?.floor ?? const AgentAutonomy();
    return [
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('sharing'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            Text(
              agent.shared ? s.t('sharedText') : s.t('privateText'),
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
            if (agent.canEdit)
              Material(
                  type: MaterialType.transparency,
                  child: SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(s.t('shared')),
                    subtitle: Text(s.t('sharingOwnerOnly')),
                    value: agent.shared,
                    onChanged: c.busy
                        ? null
                        : (on) =>
                            c.update({'sharing': on ? 'workspace' : 'private'}),
                  )),
            if (agent.canEdit)
              Material(
                  type: MaterialType.transparency,
                  child: SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(s.t('statusLabel')),
                    subtitle: Text(s.t('statusText')),
                    value: !agent.paused,
                    onChanged: c.busy ? null : (on) => c.setPaused(!on),
                  )),
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.t('autonomy'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
            ),
            for (final category in toolCategories)
              AnumRow(
                title:
                    '${s.t('toolCat.$category')}: ${s.t('level.${agent.autonomy.level(category)}')}',
                subtitle: agent.autonomy.level(category) == 'auto' &&
                        floor.level(category) != 'auto'
                    ? s.t('floorNote')
                    : null,
              ),
          ],
        ),
      ),
    ];
  }
}

class _RunFeedback extends StatelessWidget {
  const _RunFeedback({required this.controller});
  final AgentDetailController controller;
  @override
  Widget build(BuildContext context) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final outcome = controller.outcome;
    return Semantics(
      liveRegion: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (controller.runError != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                controller.runError!,
                style: TextStyle(color: p.stop),
              ),
            ),
          if (outcome != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                outcome.waitingApproval
                    ? s.t('runWaiting')
                    : (outcome.result ?? outcome.status),
                style: TextStyle(color: p.text),
              ),
            ),
        ],
      ),
    );
  }
}

/// Create or edit an agent: template, identity, instructions, model, skills
/// and tools, autonomy, knowledge and sharing.
class AgentEditorScreen extends StatefulWidget {
  const AgentEditorScreen({
    required this.catalog,
    required this.onSave,
    this.agent,
    super.key,
  });
  final AgentCatalog catalog;
  final AgentProfile? agent;
  final Future<AgentProfile> Function(AgentDraft draft) onSave;
  @override
  State<AgentEditorScreen> createState() => _AgentEditorScreenState();
}

class _AgentEditorScreenState extends State<AgentEditorScreen> {
  late AgentDraft _draft =
      widget.agent == null ? AgentDraft() : AgentDraft.fromAgent(widget.agent!);
  late final _name = TextEditingController(text: _draft.name);
  late final _description = TextEditingController(text: _draft.description);
  late final _department = TextEditingController(text: _draft.department);
  late final _instructions = TextEditingController(text: _draft.instructions);
  String? _template;
  String? _error;
  bool _saving = false;

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    _department.dispose();
    _instructions.dispose();
    super.dispose();
  }

  void _useTemplate(AgentTemplate? template) {
    final s = AgentsText.of(context);
    setState(() {
      _template = template?.id;
      _draft = template == null
          ? AgentDraft()
          : AgentDraft.fromTemplate(template, s.t('tpl.${template.id}'));
      _name.text = _draft.name;
      _description.text = _draft.description;
      _department.text = _draft.department;
      _instructions.text = _draft.instructions;
    });
  }

  Future<void> _save() async {
    _draft
      ..name = _name.text
      ..description = _description.text
      ..department = _department.text
      ..instructions = _instructions.text;
    final problem = _draft.problem();
    if (problem != null) {
      setState(() => _error = AgentsText.of(context).t(problem));
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final saved = await widget.onSave(_draft);
      if (mounted) Navigator.pop(context, saved);
    } catch (e) {
      if (mounted) setState(() => _error = agentError(e));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = AgentsText.of(context);
    final p = context.palette;
    final catalog = widget.catalog;
    final agent = widget.agent;
    final isOwner = agent == null || agent.canEdit;
    Widget heading(String text) => Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(
            text,
            style: TextStyle(color: p.text, fontWeight: FontWeight.w600),
          ),
        );
    return Scaffold(
      appBar: AppBar(
        title: Text(
          agent == null
              ? s.t('editorCreate')
              : s.f('editorEdit', {'name': agent.name}),
        ),
      ),
      body: AnumPage(
        children: [
          if (agent == null) ...[
            heading(s.t('template')),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ChoiceChip(
                  label: Text(s.t('blank')),
                  selected: _template == null,
                  onSelected: (_) => _useTemplate(null),
                ),
                for (final template in catalog.templates)
                  ChoiceChip(
                    avatar: Icon(
                      _icons[template.icon] ?? Icons.smart_toy_outlined,
                      size: 18,
                    ),
                    label: Text(s.t('tpl.${template.id}')),
                    selected: _template == template.id,
                    onSelected: (_) => _useTemplate(template),
                  ),
              ],
            ),
            for (final template in catalog.templates.where(
              (t) => t.id == _template && t.missing.isNotEmpty,
            ))
              Text(
                s.f('notConnected', {'what': template.missing.join(', ')}),
                style: TextStyle(color: p.warn, fontSize: 13),
              ),
          ],
          TextField(
            controller: _name,
            maxLength: 80,
            decoration: InputDecoration(labelText: s.t('name')),
          ),
          TextField(
            controller: _description,
            maxLength: 500,
            minLines: 1,
            maxLines: 3,
            decoration: InputDecoration(labelText: s.t('description')),
          ),
          heading(s.t('category')),
          Wrap(
            spacing: 8,
            children: [
              for (final category in agentCategories)
                ChoiceChip(
                  label: Text(s.t('cat.$category')),
                  selected: _draft.category == category,
                  onSelected: (_) => setState(() => _draft.category = category),
                ),
            ],
          ),
          TextField(
            controller: _department,
            maxLength: 80,
            decoration: InputDecoration(labelText: s.t('department')),
          ),
          TextField(
            controller: _instructions,
            maxLength: 8000,
            minLines: 4,
            maxLines: 10,
            decoration: InputDecoration(labelText: s.t('instructions')),
          ),
          heading(s.t('model')),
          Material(
              type: MaterialType.transparency,
              child: RadioGroup<String>(
                groupValue: _draft.modelRoute,
                onChanged: (value) {
                  if (value != null) setState(() => _draft.modelRoute = value);
                },
                child: Column(
                  children: [
                    for (final route in const ['capable', 'fast'])
                      RadioListTile<String>(
                        contentPadding: EdgeInsets.zero,
                        value: route,
                        title: Text(s.t('model.$route')),
                      ),
                  ],
                ),
              )),
          heading(s.t('skills')),
          for (final skill in catalog.skills)
            Material(
                type: MaterialType.transparency,
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _draft.allowedSkills.contains(skill.id),
                  title: Text(skill.name),
                  subtitle: Text(skill.description),
                  onChanged: (on) => setState(
                    () => _draft.toggleSkill(skill, on ?? false, catalog),
                  ),
                )),
          heading(s.t('tools')),
          Text(s.t('alwaysOn'), style: TextStyle(color: p.muted, fontSize: 13)),
          for (final tool in catalog.tools.where((t) => !t.alwaysOn))
            Material(
                type: MaterialType.transparency,
                child: CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _draft.allowedTools.contains(tool.name),
                  title: Text(tool.name, textDirection: TextDirection.ltr),
                  subtitle: Text(
                    tool.availableToYou
                        ? '${s.t('toolCat.${tool.category}')} · ${tool.description}'
                        : '${s.t('unavailableTool')}: ${tool.reason ?? ''}',
                  ),
                  onChanged: !tool.availableToYou &&
                          !_draft.allowedTools.contains(tool.name)
                      ? null
                      : (on) => setState(
                            () => on == true
                                ? _draft.allowedTools.add(tool.name)
                                : _draft.allowedTools.remove(tool.name),
                          ),
                )),
          heading(s.t('autonomy')),
          for (final category in toolCategories) ...[
            Text(
              s.t('toolCat.$category'),
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
            SegmentedButton<String>(
              segments: [
                for (final level in autonomyLevelsFor(category))
                  ButtonSegment(value: level, label: Text(s.t('level.$level'))),
              ],
              selected: {_draft.autonomy.level(category)},
              onSelectionChanged: (value) => setState(
                () => _draft.autonomy = _draft.autonomy.withLevel(
                  category,
                  value.first,
                ),
              ),
            ),
          ],
          heading(s.t('knowledge')),
          if (_draft.knowledgeMode == 'selected')
            Text(s.t('knowledge.selected'), style: TextStyle(color: p.muted))
          else
            Material(
                type: MaterialType.transparency,
                child: RadioGroup<String>(
                  groupValue: _draft.knowledgeMode,
                  onChanged: (value) {
                    if (value != null) {
                      setState(() => _draft.knowledgeMode = value);
                    }
                  },
                  child: Column(
                    children: [
                      for (final mode in const ['none', 'workspace'])
                        RadioListTile<String>(
                          contentPadding: EdgeInsets.zero,
                          value: mode,
                          title: Text(s.t('knowledge.$mode')),
                        ),
                    ],
                  ),
                )),
          if (agent == null || isOwner) ...[
            heading(s.t('sharing')),
            Material(
                type: MaterialType.transparency,
                child: SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(s.t('shared')),
                  subtitle: Text(
                    _draft.sharing == 'workspace'
                        ? s.t('sharedText')
                        : s.t('privateText'),
                  ),
                  value: _draft.sharing == 'workspace',
                  onChanged: (on) => setState(
                      () => _draft.sharing = on ? 'workspace' : 'private'),
                )),
          ],
          if (_error != null)
            Semantics(
              liveRegion: true,
              child: Text(_error!, style: TextStyle(color: p.stop)),
            ),
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(agent == null ? s.t('createAgent') : s.t('save')),
          ),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
