import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'agent_skills_models.dart';
import 'agents_controller.dart' show agentError;
import 'agents_models.dart';
import 'agents_repository.dart';

/// An agent's Skills tab (docs/agents.md#skills): what the agent really loads,
/// from `GET /api/v1/agents/{id}/skills`, with each skill's state, version,
/// tools and how it loads, the one-step fixes the caller may make (allow a
/// missing tool, remove the skill) and a searchable picker to add skills.
/// Every change is a `PATCH /api/v1/agents/{id}` with the version the page
/// read, so the API checks permissions, tools and skills and refuses a stale
/// edit. Port of `apps/web/src/AgentSkillsPanel.tsx`.
class AgentSkillsPanel extends StatefulWidget {
  const AgentSkillsPanel({
    required this.agent,
    required this.repository,
    required this.onAgentChanged,
    this.catalog,
    this.onStale,
    super.key,
  });

  final AgentProfile agent;
  final AgentsRepository repository;

  /// The catalog the page already has; the panel reads it itself when null.
  final AgentCatalog? catalog;

  /// Called with the profile a change saved (it carries the new version).
  final void Function(AgentProfile agent) onAgentChanged;

  /// Called after a stale edit (409) so the page can read the agent again.
  final Future<void> Function()? onStale;

  @override
  State<AgentSkillsPanel> createState() => _AgentSkillsPanelState();
}

enum _Phase { loading, ready, error }

class _AgentSkillsPanelState extends State<AgentSkillsPanel> {
  _Phase _phase = _Phase.loading;
  AgentSkillLoadout? _loadout;
  String? _error;
  bool _busy = false;
  ({String text, bool error})? _flash;
  bool _adding = false;
  String _query = '';
  AgentCatalog? _catalog;
  String? _catalogError;
  bool _catalogLoading = false;
  int _generation = 0;
  final _heading = FocusNode(debugLabel: 'agent skills heading');
  final _search = TextEditingController();

  AnumLocalizations get _l => AnumLocalizations.maybeOf(context);

  @override
  void initState() {
    super.initState();
    _catalog = widget.catalog;
    _load();
    if (_catalog == null) _loadCatalog();
  }

  @override
  void didUpdateWidget(AgentSkillsPanel old) {
    super.didUpdateWidget(old);
    if (old.agent.id != widget.agent.id ||
        old.agent.version != widget.agent.version) {
      _load();
    }
    if (widget.catalog != null && widget.catalog != old.catalog) {
      _catalog = widget.catalog;
    }
  }

  @override
  void dispose() {
    _heading.dispose();
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    setState(() {
      if (_phase != _Phase.ready) _phase = _Phase.loading;
      _error = null;
    });
    try {
      final loadout = await widget.repository.skills(widget.agent.id);
      if (!mounted || generation != _generation) return;
      setState(() {
        _loadout = loadout;
        _phase = _Phase.ready;
      });
    } catch (e) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _error = _l.f('agentSkills.loadFailed', {'reason': agentError(e)});
        _phase = _Phase.error;
      });
    }
  }

  Future<void> _loadCatalog() async {
    setState(() {
      _catalogLoading = true;
      _catalogError = null;
    });
    try {
      final catalog = await widget.repository.catalog();
      if (!mounted) return;
      setState(() => _catalog = catalog);
    } catch (e) {
      if (!mounted) return;
      setState(() => _catalogError = agentError(e));
    } finally {
      if (mounted) setState(() => _catalogLoading = false);
    }
  }

  void _refocus() => WidgetsBinding.instance
      .addPostFrameCallback((_) => mounted ? _heading.requestFocus() : null);

  Future<void> _change(
    Map<String, Object?> body,
    String done, {
    bool refocus = false,
  }) async {
    setState(() {
      _busy = true;
      _flash = null;
    });
    try {
      final next = await widget.repository.update(widget.agent.id, body);
      if (!mounted) return;
      widget.onAgentChanged(next);
      setState(() => _flash = (text: done, error: false));
      // The removed row and its button are gone: keep focus in the panel.
      if (refocus) _refocus();
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.statusCode == 409) {
        setState(() => _flash = (text: _l.t('agentSkills.stale'), error: true));
        await widget.onStale?.call();
        if (mounted) _load();
      } else {
        // A 403 carries the API's own sentence about who may do this.
        final text = e.isPermissionDenied
            ? e.readable
            : _l.f('agentSkills.saveFailed', {'reason': e.readable});
        setState(() => _flash = (text: text, error: true));
      }
    } catch (e) {
      if (!mounted) return;
      setState(
        () => _flash = (
          text: _l.f('agentSkills.saveFailed', {'reason': agentError(e)}),
          error: true,
        ),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  bool get _canEdit => widget.agent.canEdit && (_loadout?.canEdit ?? true);

  @override
  Widget build(BuildContext context) {
    final l = _l;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final loadout = _loadout;
    final items = sortLoadout(loadout?.items ?? const []);
    final shown = filterLoadout(items, _query);
    final canEdit = _canEdit;
    final ready = _phase == _Phase.ready;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Focus(
                  focusNode: _heading,
                  child: Semantics(
                    header: true,
                    child: Text(
                      l.t('agentSkills.title'),
                      style: text.titleSmall?.copyWith(
                        color: p.text,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ),
              ),
              IconButton(
                tooltip: l.t('agentSkills.refresh'),
                onPressed: _phase == _Phase.loading ? null : _load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          if (loadout != null && items.isNotEmpty)
            Semantics(
              liveRegion: true,
              child: Text(
                l.f('agentSkills.summary', {
                  'ready': loadout.ready,
                  'attention': loadout.attention,
                }),
                style: text.bodySmall?.copyWith(color: p.muted),
              ),
            ),
          const SizedBox(height: AnumSpacing.xs),
          Text(
            l.t('agentSkills.intro'),
            style: text.bodySmall?.copyWith(color: p.muted),
          ),
          if (!widget.agent.canEdit) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(
              l.t('agentSkills.readOnly'),
              style: text.bodySmall?.copyWith(color: p.warn),
            ),
          ],
          if (_flash != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
              liveRegion: true,
              child: Text(
                _flash!.text,
                style: text.bodyMedium?.copyWith(
                  color: _flash!.error ? p.stop : p.ok,
                ),
              ),
            ),
          ],
          if (canEdit && !_adding && ready) ...[
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.tonalIcon(
                onPressed: () => setState(() => _adding = true),
                icon: const Icon(Icons.add),
                label: Text(l.t('agentSkills.add')),
              ),
            ),
          ],
          if (_adding && canEdit) ...[
            const SizedBox(height: AnumSpacing.sm),
            _SkillPicker(
              catalog: _catalog,
              loading: _catalogLoading,
              error: _catalogError,
              loaded: widget.agent.allowedSkills,
              busy: _busy,
              onRetry: _loadCatalog,
              onClose: () {
                setState(() => _adding = false);
                _refocus();
              },
              onAdd: (skill) {
                final catalog = _catalog;
                if (catalog == null) return;
                _change(
                  attachSkillChanges(widget.agent, skill, catalog),
                  l.f('agentSkills.added', {'name': skill.name}),
                );
              },
            ),
          ],
          const SizedBox(height: AnumSpacing.sm),
          if (_phase == _Phase.loading)
            Semantics(
              liveRegion: true,
              label: l.t('agentSkills.loading'),
              child: Row(
                children: [
                  const SizedBox(
                    width: AnumSpacing.md,
                    height: AnumSpacing.md,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: AnumSpacing.xs),
                  Expanded(
                    child: Text(
                      l.t('agentSkills.loading'),
                      style: text.bodySmall?.copyWith(color: p.muted),
                    ),
                  ),
                ],
              ),
            ),
          if (_phase == _Phase.error)
            Semantics(
              liveRegion: true,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _error ?? '',
                    style: text.bodyMedium?.copyWith(color: p.stop),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  OutlinedButton.icon(
                    onPressed: _load,
                    icon: const Icon(Icons.refresh),
                    label: Text(l.t('agentSkills.retry')),
                  ),
                ],
              ),
            ),
          if (ready && items.isEmpty)
            AnumEmpty(
              icon: Icons.extension_outlined,
              title: l.t('agentSkills.emptyTitle'),
              message: canEdit
                  ? l.t('agentSkills.emptyText')
                  : l.t('agentSkills.emptyReadOnly'),
              action: canEdit && !_adding ? l.t('agentSkills.add') : null,
              onAction: () => setState(() => _adding = true),
            ),
          if (ready && items.length >= skillSearchFrom) ...[
            TextField(
              controller: _search,
              onChanged: (value) => setState(() => _query = value),
              decoration: InputDecoration(
                labelText: l.t('agentSkills.search'),
                hintText: l.t('agentSkills.searchPlaceholder'),
                prefixIcon: const Icon(Icons.search),
              ),
            ),
            const SizedBox(height: AnumSpacing.xs),
          ],
          if (ready && items.isNotEmpty && shown.isEmpty)
            Text(
              l.f('agentSkills.noMatch', {'query': _query.trim()}),
              style: text.bodyMedium?.copyWith(color: p.muted),
            ),
          if (ready)
            for (final item in shown)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                child: _SkillTile(
                  key: ValueKey(item.id),
                  item: item,
                  canEdit: canEdit,
                  busy: _busy,
                  catalog: _catalog,
                  onAllow: (tools) => _change(
                    allowToolsChanges(widget.agent, tools),
                    l.f('agentSkills.allowed', {'tools': tools.join(', ')}),
                  ),
                  onRemove: () => _change(
                    detachSkillChanges(widget.agent, item.id),
                    l.f('agentSkills.removed', {'name': item.name}),
                    refocus: true,
                  ),
                ),
              ),
        ],
      ),
    );
  }
}

String _toolNote(AnumLocalizations l, AgentSkillTool tool) {
  if (!tool.registered) return l.t('agentSkills.tool.missing');
  if (tool.granted == false) return l.t('agentSkills.tool.notGranted');
  if (!tool.allowed) return l.t('agentSkills.tool.notAllowed');
  return l.t('agentSkills.tool.ready');
}

class _SkillTile extends StatelessWidget {
  const _SkillTile({
    required this.item,
    required this.canEdit,
    required this.busy,
    required this.catalog,
    required this.onAllow,
    required this.onRemove,
    super.key,
  });
  final AgentSkillState item;
  final bool canEdit, busy;
  final AgentCatalog? catalog;
  final void Function(List<String> tools) onAllow;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final allowable =
        canEdit ? allowableMissingTools(item, catalog) : const <String>[];
    final reasonKey = skillReasonKey(item);
    final reason = item.state == 'ready'
        ? null
        : reasonKey == null
            ? item.reason
            : l.f(reasonKey.key, {'tools': reasonKey.tools});
    final stateLabel = l.t('agentSkills.state.${item.state}');
    final risk = item.riskLevel;
    final riskText = risk == null ? null : _riskText(l, risk);
    final meta = [
      l.t('agentSkills.source.${item.source}'),
      if (item.version != null)
        l.f('agentSkills.version', {'version': item.version!}),
      if (riskText != null) l.f('agentSkills.risk', {'risk': riskText}),
    ];
    final how = item.activation == 'always'
        ? l.t('agentSkills.loadsAlways')
        : item.triggers.isNotEmpty
            ? l.f('agentSkills.loadsOnRequest', {
                'words': item.triggers
                    .take(6)
                    .join(l.t('agentSkills.listSeparator')),
              })
            : l.t('agentSkills.loadsOnRequestAny');
    return Semantics(
      container: true,
      label: '${item.name}, $stateLabel',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: p.surface2,
          borderRadius: BorderRadius.circular(AnumRadius.lg),
          border: Border.all(color: p.line),
        ),
        child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.sm),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  AnumIconChip(
                    icon: item.source == 'builtin'
                        ? Icons.auto_awesome_outlined
                        : Icons.inventory_2_outlined,
                    tone: skillStateTone(item.state),
                  ),
                  const SizedBox(width: AnumSpacing.sm),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.name,
                          style: text.titleSmall?.copyWith(
                            color: p.text,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: AnumSpacing.xxs),
                        Align(
                          alignment: AlignmentDirectional.centerStart,
                          child: AnumPill(
                            label: stateLabel,
                            tone: skillStateTone(item.state),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AnumSpacing.xs),
              Text(
                meta.join(l.t('agentSkills.listSeparator')),
                style: text.bodySmall?.copyWith(color: p.muted),
              ),
              Directionality(
                textDirection: TextDirection.ltr,
                child: Text(
                  item.id,
                  style: text.bodySmall?.copyWith(
                    color: p.faint,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
              if (item.description.isNotEmpty) ...[
                const SizedBox(height: AnumSpacing.xxs),
                Text(
                  item.description,
                  style: text.bodyMedium?.copyWith(color: p.text),
                ),
              ],
              const SizedBox(height: AnumSpacing.xxs),
              Text(how, style: text.bodySmall?.copyWith(color: p.muted)),
              if (reason != null && reason.isNotEmpty) ...[
                const SizedBox(height: AnumSpacing.xxs),
                Text(
                  reason,
                  style: text.bodyMedium?.copyWith(
                    color: item.state == 'ready' ? p.muted : p.warn,
                  ),
                ),
              ],
              if (item.upgradeAvailable && item.latestVersion != null) ...[
                const SizedBox(height: AnumSpacing.xxs),
                Text(
                  l.f('agentSkills.upgrade', {'version': item.latestVersion!}),
                  style: text.bodySmall?.copyWith(color: p.muted),
                ),
              ],
              if (item.tools.isNotEmpty) ...[
                const SizedBox(height: AnumSpacing.xs),
                Semantics(
                  container: true,
                  label: l.f('agentSkills.toolsOf', {'name': item.name}),
                  child: Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xxs,
                    children: [for (final tool in item.tools) _ToolChip(tool)],
                  ),
                ),
              ],
              if (allowable.isNotEmpty || canEdit) ...[
                const SizedBox(height: AnumSpacing.xs),
                Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xxs,
                  children: [
                    if (allowable.isNotEmpty)
                      OutlinedButton.icon(
                        onPressed: busy ? null : () => onAllow(allowable),
                        icon: const Icon(Icons.build_outlined),
                        label: Text(
                          l.f('agentSkills.allow', {
                            'tools': allowable.join(', '),
                          }),
                        ),
                      ),
                    if (canEdit)
                      TextButton.icon(
                        onPressed: busy ? null : onRemove,
                        icon: const Icon(Icons.close),
                        label: Semantics(
                          label: l.f(
                            'agentSkills.removeNamed',
                            {'name': item.name},
                          ),
                          excludeSemantics: true,
                          child: Text(l.t('agentSkills.remove')),
                        ),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

String _riskText(AnumLocalizations l, String risk) {
  const known = {'low', 'medium', 'high', 'blocked'};
  final key = risk.toLowerCase();
  return known.contains(key) ? l.t('agentSkills.riskLevel.$key') : risk;
}

class _ToolChip extends StatelessWidget {
  const _ToolChip(this.tool);
  final AgentSkillTool tool;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final color = tool.ok ? p.ok : p.warn;
    return Semantics(
      label: l.f('agentSkills.toolState', {
        'tool': tool.name,
        'state': _toolNote(l, tool),
      }),
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AnumRadius.pill),
          border: Border.all(color: color.withValues(alpha: 0.4)),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.xs,
            vertical: AnumSpacing.xxs,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                tool.ok ? Icons.check : Icons.warning_amber_outlined,
                size: AnumSpacing.md,
                color: color,
              ),
              const SizedBox(width: AnumSpacing.xxs),
              Flexible(
                child: Directionality(
                  textDirection: TextDirection.ltr,
                  child: Text(
                    tool.name,
                    style: text.labelSmall?.copyWith(color: p.text),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The searchable list of skills the agent does not load yet.
class _SkillPicker extends StatefulWidget {
  const _SkillPicker({
    required this.catalog,
    required this.loading,
    required this.error,
    required this.loaded,
    required this.busy,
    required this.onRetry,
    required this.onClose,
    required this.onAdd,
  });
  final AgentCatalog? catalog;
  final bool loading, busy;
  final String? error;
  final List<String> loaded;
  final VoidCallback onRetry, onClose;
  final void Function(AgentSkill skill) onAdd;

  @override
  State<_SkillPicker> createState() => _SkillPickerState();
}

class _SkillPickerState extends State<_SkillPicker> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final catalog = widget.catalog;
    final options = addableSkills(catalog, widget.loaded, _query);
    final anyLeft = addableSkills(catalog, widget.loaded, '').isNotEmpty;
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AnumRadius.lg),
        border: Border.all(color: p.lineStrong),
      ),
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Semantics(
                    header: true,
                    child: Text(
                      l.t('agentSkills.addTitle'),
                      style: text.titleSmall?.copyWith(
                        color: p.text,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ),
                TextButton(
                  onPressed: widget.onClose,
                  child: Text(l.t('agentSkills.done')),
                ),
              ],
            ),
            if (widget.error != null) ...[
              Text(
                l.f('agentSkills.catalogFailed', {'reason': widget.error!}),
                style: text.bodyMedium?.copyWith(color: p.stop),
              ),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  onPressed: widget.onRetry,
                  icon: const Icon(Icons.refresh),
                  label: Text(l.t('agentSkills.retry')),
                ),
              ),
            ] else if (catalog == null)
              Semantics(
                liveRegion: true,
                child: Text(
                  l.t('agentSkills.catalogLoading'),
                  style: text.bodySmall?.copyWith(color: p.muted),
                ),
              ),
            if (catalog != null) ...[
              TextField(
                autofocus: true,
                onChanged: (value) => setState(() => _query = value),
                decoration: InputDecoration(
                  labelText: l.t('agentSkills.searchAdd'),
                  hintText: l.t('agentSkills.searchPlaceholder'),
                  prefixIcon: const Icon(Icons.search),
                ),
              ),
              const SizedBox(height: AnumSpacing.xs),
              if (catalog.skills.isEmpty)
                Text(
                  l.t('agentSkills.addNone'),
                  style: text.bodyMedium?.copyWith(color: p.muted),
                ),
              if (catalog.skills.isNotEmpty && !anyLeft)
                Text(
                  l.t('agentSkills.addEmpty'),
                  style: text.bodyMedium?.copyWith(color: p.muted),
                ),
              if (anyLeft && options.isEmpty)
                Text(
                  l.f('agentSkills.addNoMatch', {'query': _query.trim()}),
                  style: text.bodyMedium?.copyWith(color: p.muted),
                ),
              for (final skill in options)
                Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                  child: _PickerRow(
                    skill: skill,
                    extraTools: toolsWithSkill(skill, catalog),
                    busy: widget.busy,
                    onAdd: () => widget.onAdd(skill),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PickerRow extends StatelessWidget {
  const _PickerRow({
    required this.skill,
    required this.extraTools,
    required this.busy,
    required this.onAdd,
  });
  final AgentSkill skill;
  final List<String> extraTools;
  final bool busy;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          skill.name,
          style: text.titleSmall?.copyWith(
            color: p.text,
            fontWeight: FontWeight.w600,
          ),
        ),
        Text(
          '${l.t('agentSkills.source.${skill.source}')}  ${skill.id}',
          style: text.bodySmall?.copyWith(color: p.faint),
        ),
        if (skill.description.isNotEmpty)
          Text(
            skill.description,
            style: text.bodyMedium?.copyWith(color: p.muted),
          ),
        if (extraTools.isNotEmpty)
          Text(
            l.f('agentSkills.addTools', {'tools': extraTools.join(', ')}),
            style: text.bodySmall?.copyWith(color: p.muted),
          ),
        const SizedBox(height: AnumSpacing.xxs),
        FilledButton.tonalIcon(
          onPressed: busy ? null : onAdd,
          icon: const Icon(Icons.add),
          label: Semantics(
            label: l.f('agentSkills.addNamed', {'name': skill.name}),
            excludeSemantics: true,
            child: Text(l.t('agentSkills.addButton')),
          ),
        ),
      ],
    );
  }
}
