import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'governance_controller.dart';
import 'governance_models.dart';

/// Policy packs on the Organization screen (docs/governance-and-scale.md#policy-packs):
/// one card per pack name with its active version's rules, search and "Show
/// more" ten groups at a time; organization administrators publish packs and
/// new versions from the editor, activate, archive or delete versions, and
/// everyone compares two versions rule by rule.
class PolicyPacksSection extends StatefulWidget {
  const PolicyPacksSection(
      {required this.controller, required this.canManage, super.key});
  final GovernanceController controller;
  final bool canManage;
  @override
  State<PolicyPacksSection> createState() => _PolicyPacksSectionState();
}

class _PolicyPacksSectionState extends State<PolicyPacksSection> {
  final _search = TextEditingController();
  int _shown = 10;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _openEditor(BuildContext context, {PackGroup? group}) async {
    final l = AnumLocalizations.maybeOf(context);
    final messenger = ScaffoldMessenger.of(context);
    final published = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) =>
            PolicyPackEditor(controller: widget.controller, group: group)));
    final pack = widget.controller.lastPublished;
    if (published == true && pack != null) {
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
            content: Text(l.f('ad.gov.editor.published',
                {'name': pack.name, 'version': pack.version}))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final c = widget.controller;
    final all = c.snapshot?.policies ?? const <PolicyPack>[];
    final groups = packGroups(filterPacks(all, _search.text));
    final shown = groups.take(_shown).toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(l.t('ad.gov.packs.title'),
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.lg,
                    fontWeight: FontWeight.w700)),
            if (widget.canManage)
              FilledButton.icon(
                  key: const Key('pack-new'),
                  onPressed: c.mutating ? null : () => _openEditor(context),
                  icon: const Icon(Icons.add),
                  label: Text(l.t('ad.gov.packs.new'))),
          ]),
      const SizedBox(height: AnumSpacing.sm),
      if (all.isEmpty)
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.policy_outlined,
            title: l.t(widget.canManage
                ? 'ad.gov.packs.emptyAdmin'
                : 'ad.gov.packs.emptyOther'),
            message: '',
          ),
        )
      else ...[
        TextField(
          key: const Key('packs-search'),
          controller: _search,
          onChanged: (_) => setState(() => _shown = 10),
          decoration: InputDecoration(
              labelText: l.t('ad.gov.packs.search'),
              prefixIcon: const Icon(Icons.search)),
        ),
        const SizedBox(height: AnumSpacing.xs),
        Text(
            l.f('ad.gov.packs.shown',
                {'shown': shown.length, 'count': groups.length}),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        if (groups.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: Text(
                l.f('ad.gov.packs.noMatch', {'query': _search.text.trim()}),
                style: TextStyle(color: p.muted)),
          ),
        for (final group in shown)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: _PackGroupCard(
                group: group,
                controller: c,
                canManage: widget.canManage,
                onNewVersion: () => _openEditor(context, group: group)),
          ),
        if (groups.length > _shown)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                  key: const Key('packs-more'),
                  onPressed: () => setState(() => _shown += 10),
                  child: Text(l.t('ad.gov.packs.showMore'))),
            ),
          ),
      ],
    ]);
  }
}

class _PackGroupCard extends StatelessWidget {
  const _PackGroupCard(
      {required this.group,
      required this.controller,
      required this.canManage,
      required this.onNewVersion});
  final PackGroup group;
  final GovernanceController controller;
  final bool canManage;
  final VoidCallback onNewVersion;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final active = group.active;
    final shownRules = (active ?? group.versions.first).ruleItems;
    return AnumSurface(
      key: Key('pack-group-${group.name}'),
      accent: active != null ? p.ok : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(group.name,
                  style: TextStyle(
                      color: p.text,
                      fontSize: AnumType.lg,
                      fontWeight: FontWeight.w700)),
              AnumPill(
                  tone: active != null ? AnumTone.ok : AnumTone.neutral,
                  label: active != null
                      ? l.f('ad.gov.packs.activeVersion',
                          {'version': active.version})
                      : l.t('ad.gov.packs.noActive')),
              AnumPill(
                  label: l.f('ad.gov.packs.versions',
                      {'count': group.versions.length})),
            ]),
        if ((active ?? group.versions.first).description.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text((active ?? group.versions.first).description,
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ],
        const SizedBox(height: AnumSpacing.xs),
        for (final rule in shownRules)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.rule, size: 16, color: p.sky),
              const SizedBox(width: AnumSpacing.xs),
              Expanded(
                  child: Text(
                      '${rule.action}: ${l.t('ad.gov.effect.${rule.effect}')}'
                      '${rule.conditions.isEmpty ? '' : ' ${rule.conditionsText}'}',
                      textDirection: TextDirection.ltr,
                      textAlign: TextAlign.start,
                      style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
            ]),
          ),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          OutlinedButton(
              key: Key('pack-versions-${group.name}'),
              onPressed: () => showModalBottomSheet<void>(
                  context: context,
                  isScrollControlled: true,
                  showDragHandle: true,
                  builder: (_) => _VersionsSheet(
                      group: group,
                      controller: controller,
                      canManage: canManage)),
              child: Text(l.f(
                  'ad.gov.packs.versions', {'count': group.versions.length}))),
          if (group.versions.length > 1)
            OutlinedButton(
                key: Key('pack-compare-${group.name}'),
                onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                        builder: (_) => PackCompareScreen(group: group))),
                child: Text(l.t('ad.gov.packs.compare'))),
          if (canManage)
            OutlinedButton(
                key: Key('pack-new-version-${group.name}'),
                onPressed: controller.mutating ? null : onNewVersion,
                child: Text(l.t('ad.gov.packs.newVersion'))),
        ]),
      ]),
    );
  }
}

/// A pack's versions with Activate or Archive and, for an archived one,
/// Delete behind a confirmation.
class _VersionsSheet extends StatelessWidget {
  const _VersionsSheet(
      {required this.group, required this.controller, required this.canManage});
  final PackGroup group;
  final GovernanceController controller;
  final bool canManage;

  Future<void> _run(
      BuildContext context, Future<String?> work, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    final l = AnumLocalizations.maybeOf(context);
    final problem = await work;
    navigator.pop();
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
          content: Text(problem == null
              ? done
              : l.f('ad.gov.packs.failed', {'message': problem}))));
  }

  Future<void> _delete(BuildContext context, PolicyPack pack) async {
    final l = AnumLocalizations.maybeOf(context);
    final values = {'name': pack.name, 'version': pack.version};
    final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text(l.f('ad.gov.packs.deleteTitle', values)),
              content: Text(l.t('ad.gov.packs.deleteBody')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.of(context).pop(false),
                    child: Text(l.t('ad.gov.packs.cancel'))),
                FilledButton(
                    key: const Key('pack-delete-confirm'),
                    onPressed: () => Navigator.of(context).pop(true),
                    child: Text(l.t('ad.gov.packs.delete'))),
              ],
            ));
    if (ok != true || !context.mounted) return;
    await _run(context, controller.deletePack(pack),
        l.f('ad.gov.packs.deleted', values));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return SafeArea(
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.85),
        child: ListView(
          shrinkWrap: true,
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
          children: [
            Text(l.f('ad.gov.packs.versionsTitle', {'name': group.name}),
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.lgPlus,
                    fontWeight: FontWeight.w700)),
            for (final pack in group.versions)
              Padding(
                key: Key('pack-version-${pack.id}'),
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Divider(height: 1, color: p.line),
                      const SizedBox(height: AnumSpacing.xs),
                      Wrap(
                          spacing: AnumSpacing.xs,
                          runSpacing: AnumSpacing.xxs,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            Text(
                                l.f('ad.gov.packs.version',
                                    {'version': pack.version}),
                                style: TextStyle(
                                    color: p.text,
                                    fontWeight: FontWeight.w700)),
                            AnumPill(
                                tone: pack.active
                                    ? AnumTone.ok
                                    : AnumTone.neutral,
                                label: l.t(pack.active
                                    ? 'ad.gov.packs.active'
                                    : 'ad.gov.packs.archived')),
                            Text(
                                l.f('ad.gov.packs.rules',
                                    {'count': pack.ruleItems.length}),
                                style: TextStyle(
                                    color: p.faint, fontSize: AnumType.xs)),
                            if (pack.createdAt != null)
                              Text(
                                  l.f('ad.gov.packs.published', {
                                    'when': MaterialLocalizations.of(context)
                                        .formatMediumDate(
                                            pack.createdAt!.toLocal())
                                  }),
                                  style: TextStyle(
                                      color: p.faint, fontSize: AnumType.xs)),
                          ]),
                      if (canManage)
                        Wrap(
                            spacing: AnumSpacing.xs,
                            runSpacing: AnumSpacing.xs,
                            children: [
                              TextButton(
                                  key: Key('pack-toggle-${pack.id}'),
                                  onPressed: controller.mutating
                                      ? null
                                      : () => _run(
                                          context,
                                          controller.setPackActive(pack,
                                              active: !pack.active),
                                          l.f(
                                              pack.active
                                                  ? 'ad.gov.packs.archivedDone'
                                                  : 'ad.gov.packs.activated',
                                              {
                                                'name': pack.name,
                                                'version': pack.version
                                              })),
                                  child: Text(l.t(pack.active
                                      ? 'ad.gov.packs.archive'
                                      : 'ad.gov.packs.activate'))),
                              if (!pack.active)
                                TextButton(
                                    key: Key('pack-delete-${pack.id}'),
                                    onPressed: controller.mutating
                                        ? null
                                        : () => _delete(context, pack),
                                    child: Semantics(
                                        label: l.f('ad.gov.packs.deleteLabel', {
                                          'name': pack.name,
                                          'version': pack.version
                                        }),
                                        excludeSemantics: true,
                                        child: Text(l.t('ad.gov.packs.delete'),
                                            style: TextStyle(color: p.stop))))
                              else
                                Text(l.t('ad.gov.packs.deleteOnlyArchived'),
                                    style: TextStyle(
                                        color: p.faint, fontSize: AnumType.xs)),
                            ]),
                    ]),
              ),
          ],
        ),
      ),
    );
  }
}

/// Two versions side by side: each rule Added, Removed, Changed (with the
/// previous rule) or Unchanged.
class PackCompareScreen extends StatefulWidget {
  const PackCompareScreen({required this.group, super.key});
  final PackGroup group;
  @override
  State<PackCompareScreen> createState() => _PackCompareScreenState();
}

class _PackCompareScreenState extends State<PackCompareScreen> {
  late PolicyPack _from = widget.group.versions.length > 1
      ? widget.group.versions[1]
      : widget.group.versions.first;
  late PolicyPack _to = widget.group.versions.first;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final changes = diffRules(_from.ruleItems, _to.ruleItems);
    final unchanged = changes.every((c) => c.kind == RuleChangeKind.same);
    DropdownButtonFormField<PolicyPack> picker(Key key, String label,
            PolicyPack value, ValueChanged<PolicyPack> set) =>
        DropdownButtonFormField<PolicyPack>(
          key: key,
          initialValue: value,
          isExpanded: true,
          decoration: InputDecoration(labelText: label),
          items: [
            for (final pack in widget.group.versions)
              DropdownMenuItem(
                  value: pack,
                  child: Text(
                      l.f('ad.gov.packs.version', {'version': pack.version}))),
          ],
          onChanged: (v) {
            if (v != null) setState(() => set(v));
          },
        );
    return Scaffold(
      appBar: AppBar(
          title:
              Text(l.f('ad.gov.compare.title', {'name': widget.group.name}))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            LayoutBuilder(builder: (context, box) {
              final wide = box.maxWidth >= 560;
              final from = picker(const Key('compare-from'),
                  l.t('ad.gov.compare.from'), _from, (v) => _from = v);
              final to = picker(const Key('compare-to'),
                  l.t('ad.gov.compare.to'), _to, (v) => _to = v);
              return wide
                  ? Row(children: [
                      Expanded(child: from),
                      const SizedBox(width: AnumSpacing.sm),
                      Expanded(child: to),
                    ])
                  : Column(children: [
                      from,
                      const SizedBox(height: AnumSpacing.sm),
                      to,
                    ]);
            }),
            const SizedBox(height: AnumSpacing.md),
            if (unchanged)
              Text(l.t('ad.gov.compare.same'),
                  key: const Key('compare-same'),
                  style: TextStyle(color: p.muted)),
            for (final change in changes)
              AnumSurface(
                key: Key('change-${change.action}'),
                accent: switch (change.kind) {
                  RuleChangeKind.added => p.ok,
                  RuleChangeKind.removed => p.stop,
                  RuleChangeKind.changed => p.warn,
                  RuleChangeKind.same => null,
                },
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      AnumPill(
                          tone: switch (change.kind) {
                            RuleChangeKind.added => AnumTone.ok,
                            RuleChangeKind.removed => AnumTone.stop,
                            RuleChangeKind.changed => AnumTone.warn,
                            RuleChangeKind.same => AnumTone.neutral,
                          },
                          label: l.t('ad.gov.change.${change.kind.name}')),
                      const SizedBox(height: AnumSpacing.xxs),
                      Text('${change.after ?? change.before}',
                          textDirection: TextDirection.ltr,
                          style: TextStyle(
                              color: p.text,
                              decoration: change.kind == RuleChangeKind.removed
                                  ? TextDecoration.lineThrough
                                  : null)),
                      if (change.kind == RuleChangeKind.changed)
                        Text(
                            l.f('ad.gov.change.before',
                                {'rule': '${change.before}'}),
                            style: TextStyle(
                                color: p.faint,
                                decoration: TextDecoration.lineThrough)),
                    ]),
              ),
          ],
        ),
      ),
    );
  }
}

/// The policy pack editor: a new pack (blank or the organization baseline)
/// or the next version of [group], with the rules in order.
class PolicyPackEditor extends StatefulWidget {
  const PolicyPackEditor({required this.controller, this.group, super.key});
  final GovernanceController controller;
  final PackGroup? group;
  @override
  State<PolicyPackEditor> createState() => _PolicyPackEditorState();
}

class _PolicyPackEditorState extends State<PolicyPackEditor> {
  late final _name = TextEditingController(text: widget.group?.name);
  late final _description = TextEditingController(
      text:
          (widget.group?.active ?? widget.group?.versions.first)?.description);
  late List<RuleDraft> _rules = [
    for (final r
        in (widget.group?.active ?? widget.group?.versions.first)?.ruleItems ??
            const <PackRule>[])
      RuleDraft(
          action: r.action, effect: r.effect, conditions: r.conditionsText)
  ];
  String _start = 'blank';
  String? _error;
  bool _saving = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    if (_rules.isEmpty) _rules = [RuleDraft()];
  }

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    super.dispose();
  }

  void _startFrom(String value, AnumLocalizations l) {
    setState(() {
      _start = value;
      _generation++;
      if (value == 'baseline') {
        _name.text = l.t('ad.gov.baseline.name');
        _description.text = l.t('ad.gov.baseline.description');
        _rules = baselineRules();
      } else {
        _name.clear();
        _description.clear();
        _rules = [RuleDraft()];
      }
    });
  }

  void _move(int index, int step) {
    final target = index + step;
    if (target < 0 || target >= _rules.length) return;
    setState(() {
      final r = _rules.removeAt(index);
      _rules.insert(target, r);
      _generation++;
    });
  }

  Future<void> _publish(AnumLocalizations l) async {
    final checked = packBody(_name.text, _description.text, _rules);
    if (checked.body == null) {
      setState(() => _error = l.f(checked.error!, {'row': checked.row ?? 0}));
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final problem = await widget.controller.publish(checked.body!);
    if (!mounted) return;
    if (problem == null) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _saving = false;
      _error = l.f('ad.gov.packs.failed', {'message': problem});
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final group = widget.group;
    return Scaffold(
      appBar: AppBar(
          title: Text(group == null
              ? l.t('ad.gov.editor.title')
              : l.f('ad.gov.editor.titleVersion', {'name': group.name}))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Text(l.t('ad.gov.editor.intro'), style: TextStyle(color: p.muted)),
            const SizedBox(height: AnumSpacing.sm),
            if (group == null)
              DropdownButtonFormField<String>(
                key: const Key('editor-start'),
                initialValue: _start,
                isExpanded: true,
                decoration:
                    InputDecoration(labelText: l.t('ad.gov.editor.startFrom')),
                items: [
                  DropdownMenuItem(
                      value: 'blank',
                      child: Text(l.t('ad.gov.editor.start.blank'),
                          overflow: TextOverflow.ellipsis)),
                  DropdownMenuItem(
                      value: 'baseline',
                      child: Text(l.t('ad.gov.editor.start.baseline'),
                          overflow: TextOverflow.ellipsis)),
                ],
                onChanged: (v) => _startFrom(v ?? 'blank', l),
              ),
            TextField(
              key: const Key('editor-name'),
              controller: _name,
              enabled: group == null,
              maxLength: 160,
              decoration: InputDecoration(
                  labelText: l.t('ad.gov.editor.name'),
                  helperText: l.t('ad.gov.editor.nameHint'),
                  helperMaxLines: 3),
            ),
            TextField(
              key: const Key('editor-description'),
              controller: _description,
              maxLength: 1000,
              minLines: 1,
              maxLines: 4,
              decoration:
                  InputDecoration(labelText: l.t('ad.gov.editor.description')),
            ),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('ad.gov.editor.rules'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            Text(l.t('ad.gov.editor.rulesHint'),
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            for (var i = 0; i < _rules.length; i++)
              _RuleEditor(
                key: ValueKey('rule-$_generation-$i'),
                row: i + 1,
                rule: _rules[i],
                canUp: i > 0,
                canDown: i < _rules.length - 1,
                canRemove: _rules.length > 1,
                onUp: () => _move(i, -1),
                onDown: () => _move(i, 1),
                onRemove: () => setState(() {
                  _rules.removeAt(i);
                  _generation++;
                }),
                onChanged: () => setState(() {}),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                  key: const Key('editor-add-rule'),
                  onPressed: _rules.length >= 100
                      ? null
                      : () => setState(() => _rules.add(RuleDraft())),
                  icon: const Icon(Icons.add),
                  label: Text(l.t('ad.gov.editor.addRule'))),
            ),
            if (_error != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              Semantics(
                liveRegion: true,
                child: Text(_error!,
                    key: const Key('editor-error'),
                    style: TextStyle(color: p.stop)),
              ),
            ],
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: FilledButton.icon(
                  key: const Key('editor-publish'),
                  onPressed: _saving ? null : () => _publish(l),
                  icon: const Icon(Icons.publish_outlined),
                  label: Text(l.t(_saving
                      ? 'ad.gov.editor.publishing'
                      : 'ad.gov.editor.publish'))),
            ),
          ],
        ),
      ),
    );
  }
}

class _RuleEditor extends StatefulWidget {
  const _RuleEditor(
      {required this.row,
      required this.rule,
      required this.canUp,
      required this.canDown,
      required this.canRemove,
      required this.onUp,
      required this.onDown,
      required this.onRemove,
      required this.onChanged,
      super.key});
  final int row;
  final RuleDraft rule;
  final bool canUp, canDown, canRemove;
  final VoidCallback onUp, onDown, onRemove, onChanged;
  @override
  State<_RuleEditor> createState() => _RuleEditorState();
}

class _RuleEditorState extends State<_RuleEditor> {
  late final _action = TextEditingController(text: widget.rule.action);
  late final _conditions = TextEditingController(text: widget.rule.conditions);

  @override
  void dispose() {
    _action.dispose();
    _conditions.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final row = {'row': widget.row};
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: AnumSurface(
        key: Key('editor-rule-${widget.row}'),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: AnumSpacing.xxs,
              children: [
                Text(l.f('ad.gov.editor.rule', row),
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w700)),
                IconButton(
                    tooltip: l.f('ad.gov.editor.moveUp', row),
                    onPressed: widget.canUp ? widget.onUp : null,
                    icon: const Icon(Icons.arrow_upward)),
                IconButton(
                    tooltip: l.f('ad.gov.editor.moveDown', row),
                    onPressed: widget.canDown ? widget.onDown : null,
                    icon: const Icon(Icons.arrow_downward)),
                IconButton(
                    key: Key('editor-remove-${widget.row}'),
                    tooltip: l.f('ad.gov.editor.removeRule', row),
                    onPressed: widget.canRemove ? widget.onRemove : null,
                    icon: const Icon(Icons.delete_outline)),
              ]),
          TextField(
            key: Key('editor-action-${widget.row}'),
            controller: _action,
            textDirection: TextDirection.ltr,
            onChanged: (v) {
              widget.rule.action = v;
              widget.onChanged();
            },
            decoration: InputDecoration(
                labelText: l.t('ad.gov.editor.action'),
                hintText: 'integration.*.write'),
          ),
          const SizedBox(height: AnumSpacing.xs),
          DropdownButtonFormField<String>(
            key: Key('editor-effect-${widget.row}'),
            initialValue: widget.rule.effect,
            isExpanded: true,
            decoration: InputDecoration(labelText: l.t('ad.gov.editor.effect')),
            items: [
              for (final e in policyEffects)
                DropdownMenuItem(
                    value: e, child: Text(l.t('ad.gov.effect.$e'))),
            ],
            onChanged: (v) {
              widget.rule.effect = v ?? widget.rule.effect;
              widget.onChanged();
            },
          ),
          const SizedBox(height: AnumSpacing.xs),
          TextField(
            key: Key('editor-conditions-${widget.row}'),
            controller: _conditions,
            textDirection: TextDirection.ltr,
            onChanged: (v) {
              widget.rule.conditions = v;
              widget.onChanged();
            },
            decoration: InputDecoration(
                labelText: l.t('ad.gov.editor.conditions'),
                hintText: '{"risk": "high"}'),
          ),
        ]),
      ),
    );
  }
}
