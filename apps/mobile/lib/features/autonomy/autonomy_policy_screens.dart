import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_controller.dart' show AdminPhase;
import 'autonomy_controller.dart';
import 'autonomy_models.dart';
import 'autonomy_policy_widgets.dart';

/// The workspace's rules (docs/autonomy.md#rules-and-ask-once-remember): the
/// level for one exact tool, optionally with a template. Lists, adds and
/// removes against `/autonomy/rules`; the API's refusals are shown as sent.
class AutonomyRulesScreen extends StatefulWidget {
  const AutonomyRulesScreen({required this.controller, super.key});
  final AutonomyRulesController controller;
  @override
  State<AutonomyRulesScreen> createState() => _AutonomyRulesScreenState();
}

class _AutonomyRulesScreenState extends State<AutonomyRulesScreen> {
  final _tool = TextEditingController();
  final _template = TextEditingController();
  final _toolFocus = FocusNode();
  String _level = 'ask_always';

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  void dispose() {
    _tool.dispose();
    _template.dispose();
    _toolFocus.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final ok = await widget.controller.add(
      tool: _tool.text,
      template: _template.text,
      level: _level,
    );
    if (ok) {
      _tool.clear();
      _template.clear();
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final l = AnumLocalizations.maybeOf(context);
          c.l = l;
          return Scaffold(
            appBar: AppBar(
              title: Text(l.t('ap.rulesTitle')),
              actions: [
                IconButton(
                  tooltip: l.t('da.refresh'),
                  onPressed: c.adding ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                AnumHeader(
                  eyebrow: l.t('auto.eyebrow'),
                  title: l.t('ap.rulesHeading'),
                  subtitle: l.t('ap.rulesSubtitle'),
                  large: true,
                ),
                ...c.phase == AdminPhase.ready
                    ? _ready(context, c, l)
                    : policyPhaseState(
                        context, c.phase, c.loadMessage, 'rules', c.load),
              ],
            ),
          );
        },
      );

  List<Widget> _ready(
      BuildContext context, AutonomyRulesController c, AnumLocalizations l) {
    final p = context.palette;
    final theme = Theme.of(context).textTheme;
    final canAdd = c.canManage || c.canTighten;
    final levels = c.canManage ? autonomyLevels : const ['ask_always'];
    final level = levels.contains(_level) ? _level : levels.first;
    return [
      if (!c.canManage)
        AnumSurface(
          key: const Key('rules-owner-required'),
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('auto.ownerRequired'),
            message:
                canAdd ? l.t('ap.rules.tightenOnly') : l.t('ap.rules.noAdd'),
          ),
        ),
      if (c.notice != null)
        PolicyNotice(
          text: c.notice!,
          failed: c.noticeFailed,
          noticeKey: const Key('rules-notice'),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              header: true,
              child: AnumHeader(title: l.t('ap.rules.list')),
            ),
            if (c.searchable) ...[
              const SizedBox(height: AnumSpacing.sm),
              TextField(
                key: const Key('rules-search'),
                decoration: InputDecoration(
                  labelText: l.t('ap.filterList'),
                  prefixIcon: const Icon(Icons.search),
                ),
                onChanged: c.search,
              ),
            ],
            if (c.rules.isEmpty)
              AnumEmpty(
                key: const Key('rules-empty'),
                icon: Icons.rule,
                title: l.t('ap.rules.empty'),
                message: l.t('ap.rules.emptyMessage'),
                action: canAdd ? l.t('ap.rules.addFirst') : null,
                onAction: canAdd ? _toolFocus.requestFocus : null,
              )
            else if (c.matching.isEmpty)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: Text(l.f('ap.noMatch', {'q': c.query.trim()}),
                    style: theme.bodySmall),
              )
            else
              for (final rule in c.shown) _ruleRow(context, c, l, rule),
            if (c.matching.length > c.shown.length)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(
                  key: const Key('rules-more'),
                  onPressed: c.showMore,
                  child: Text(l.f('ap.showMore', {
                    'shown': c.shown.length,
                    'total': c.matching.length,
                  })),
                ),
              ),
          ],
        ),
      ),
      if (canAdd)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Semantics(
                header: true,
                child: AnumHeader(
                  title: l.t('ap.rules.add'),
                  subtitle: l.t('ap.rules.addSubtitle'),
                ),
              ),
              const SizedBox(height: AnumSpacing.sm),
              TextField(
                key: const Key('rule-tool'),
                controller: _tool,
                focusNode: _toolFocus,
                autocorrect: false,
                decoration: InputDecoration(
                  labelText: l.t('ap.rules.tool'),
                  helperText: l.t('ap.rules.toolHelp'),
                  helperMaxLines: 3,
                ),
              ),
              const SizedBox(height: AnumSpacing.sm),
              TextField(
                key: const Key('rule-template'),
                controller: _template,
                autocorrect: false,
                decoration: InputDecoration(
                  labelText: l.t('ap.rules.template'),
                  helperText: l.t('ap.rules.templateHelp'),
                  helperMaxLines: 3,
                ),
              ),
              const SizedBox(height: AnumSpacing.sm),
              DropdownButtonFormField<String>(
                key: const Key('rule-level'),
                initialValue: level,
                isExpanded: true,
                decoration: InputDecoration(labelText: l.t('ap.rules.level')),
                items: [
                  for (final v in levels)
                    DropdownMenuItem(value: v, child: Text(levelLabel(v, l))),
                ],
                onChanged: (v) => setState(() => _level = v ?? 'ask_always'),
              ),
              if (c.addError != null) ...[
                const SizedBox(height: AnumSpacing.sm),
                PolicyNotice(
                  text: c.addError!,
                  failed: true,
                  noticeKey: const Key('rule-error'),
                ),
              ],
              const SizedBox(height: AnumSpacing.md),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: const Key('rule-add'),
                  onPressed: c.adding ? null : _add,
                  icon: const Icon(Icons.add),
                  label: Text(
                      c.adding ? l.t('da.saving') : l.t('ap.rules.addButton')),
                ),
              ),
            ],
          ),
        ),
    ];
  }

  Widget _ruleRow(BuildContext context, AutonomyRulesController c,
      AnumLocalizations l, AutonomyRule rule) {
    final p = context.palette;
    final theme = Theme.of(context).textTheme;
    final pattern = patternLabel(rule.tool, rule.template, l);
    final detail = l.f('ap.rules.detail', {
      'category': categoryLabel(rule.category, l),
      'level': levelLabel(rule.level, l),
      'source': l.t('ap.rules.source.${rule.source}'),
    });
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: Semantics(
        container: true,
        label: '$pattern. $detail',
        child: Column(
          key: Key('rule-${rule.id}'),
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(pattern, style: theme.titleSmall),
                      Text(detail, style: theme.bodySmall),
                    ],
                  ),
                ),
                if (c.canManage)
                  IconButton(
                    key: Key('rule-delete-${rule.id}'),
                    tooltip: l.f('ap.rules.delete', {'pattern': pattern}),
                    onPressed: c.busy == rule.id ? null : () => c.remove(rule),
                    icon: const Icon(Icons.delete_outline),
                  ),
              ],
            ),
            if (c.rowErrors[rule.id] != null)
              PolicyNotice(text: c.rowErrors[rule.id]!, failed: true),
            Divider(color: p.line),
          ],
        ),
      ),
    );
  }
}

/// The organization's guardrails (`/autonomy/guardrails`). Editable only when
/// the API says the caller administers the organization; read-only with the
/// reason otherwise.
class AutonomyGuardrailsScreen extends StatefulWidget {
  const AutonomyGuardrailsScreen({required this.controller, super.key});
  final GuardrailsController controller;
  @override
  State<AutonomyGuardrailsScreen> createState() =>
      _AutonomyGuardrailsScreenState();
}

class _AutonomyGuardrailsScreenState extends State<AutonomyGuardrailsScreen> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final l = AnumLocalizations.maybeOf(context);
          c.l = l;
          return Scaffold(
            appBar: AppBar(
              title: Text(l.t('ap.guardrailsTitle')),
              actions: [
                IconButton(
                  tooltip: l.t('da.refresh'),
                  onPressed: c.saving ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                AnumHeader(
                  eyebrow: l.t('auto.eyebrow'),
                  title: l.t('ap.g.heading'),
                  subtitle: l.t('ap.g.subtitle'),
                  large: true,
                ),
                ...c.phase == AdminPhase.ready
                    ? _ready(context, c, l)
                    : policyPhaseState(
                        context, c.phase, c.loadMessage, 'guardrails', c.load),
              ],
            ),
          );
        },
      );

  Widget _number(GuardrailsController c, AnumLocalizations l, String key,
      String label, String value, void Function(String) set,
      {String? help, bool decimal = true}) {
    return TextFormField(
      key: Key(key),
      initialValue: value,
      enabled: !c.readOnly,
      keyboardType: TextInputType.numberWithOptions(decimal: decimal),
      decoration: InputDecoration(
        labelText: label,
        helperText: help,
        helperMaxLines: 3,
        hintText: c.readOnly ? '-' : l.t('ap.g.noLimit'),
      ),
      onChanged: set,
    );
  }

  List<Widget> _ready(
      BuildContext context, GuardrailsController c, AnumLocalizations l) {
    final p = context.palette;
    return [
      if (c.readOnly)
        AnumSurface(
          key: const Key('guardrails-admin-required'),
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('ap.g.adminRequired'),
            message: [
              l.t('ap.g.adminOnly'),
              if (c.denied != null)
                l.f('da.apiAnswered', {'message': c.denied!}),
            ].join('\n'),
          ),
        ),
      PolicySection(
        title: l.t('ap.g.limits'),
        subtitle: l.t('ap.g.limitsSubtitle'),
        children: [
          _number(c, l, 'g-spending', l.t('ap.g.spending'), c.spending,
              (v) => c.spending = v,
              help: l.t('ap.g.spendingHelp')),
          _number(c, l, 'g-max-amount', l.t('ap.g.maxAmount'), c.maxAmount,
              (v) => c.maxAmount = v),
          _number(c, l, 'g-max-recipients', l.t('ap.g.maxRecipients'),
              c.maxRecipients, (v) => c.maxRecipients = v,
              decimal: false),
          _number(c, l, 'g-max-price', l.t('ap.g.maxPrice'), c.maxPrice,
              (v) => c.maxPrice = v),
        ],
      ),
      PolicySection(
        title: l.t('ap.g.ceilings'),
        subtitle: l.t('ap.g.ceilingsSubtitle'),
        children: [
          for (final category in autonomyCategories)
            DropdownButtonFormField<String>(
              key: Key('ceiling-$category'),
              initialValue: c.ceilings[category] ?? '',
              isExpanded: true,
              decoration:
                  InputDecoration(labelText: categoryLabel(category, l)),
              items: [
                DropdownMenuItem(
                  value: '',
                  child: Text(c.readOnly ? '-' : l.t('ap.g.noCeiling')),
                ),
                for (final level in autonomyLevels)
                  DropdownMenuItem(
                      value: level, child: Text(levelLabel(level, l))),
              ],
              onChanged: c.readOnly || c.saving
                  ? null
                  : (v) => c.setCeiling(category, v),
            ),
        ],
      ),
      PolicySection(
        title: l.t('ap.quiet'),
        subtitle: l.t('ap.g.quietSubtitle'),
        children: [
          QuietHoursEditor(
            draft: c.policy,
            readOnly: c.readOnly,
            onChanged: c.changed,
          ),
        ],
      ),
      PolicySection(
        title: l.t('ap.caps'),
        subtitle: l.t('ap.g.capsSubtitle'),
        children: [
          CapsEditor(
            draft: c.policy,
            readOnly: c.readOnly,
            onChanged: c.changed,
          ),
        ],
      ),
      PolicySection(
        title: l.t('ap.blockedRecipients'),
        subtitle: l.t('ap.g.blockedRecipientsSubtitle'),
        children: [
          StringListEditor(
            id: 'g-recipient',
            items: c.policy.recipients,
            readOnly: c.readOnly,
            fieldLabel: l.t('ap.recipientField'),
            emptyText: l.t('ap.recipientsEmpty'),
            onAdd: (v) {
              final problem = c.policy.addRecipient(v, l);
              if (problem == null) c.changed();
              return problem;
            },
            onRemove: (v) {
              c.policy.removeRecipient(v);
              c.changed();
            },
          ),
        ],
      ),
      PolicySection(
        title: l.t('ap.blockedDomains'),
        subtitle: l.t('ap.g.blockedDomainsSubtitle'),
        children: [
          StringListEditor(
            id: 'g-domain',
            items: c.policy.domains,
            readOnly: c.readOnly,
            fieldLabel: l.t('ap.domainField'),
            emptyText: l.t('ap.domainsEmpty'),
            onAdd: (v) {
              final problem = c.policy.addDomain(v, l);
              if (problem == null) c.changed();
              return problem;
            },
            onRemove: (v) {
              c.policy.removeDomain(v);
              c.changed();
            },
          ),
        ],
      ),
      if (c.notice != null || !c.readOnly)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (c.notice != null)
                PolicyNotice(
                  text: c.notice!,
                  failed: c.noticeFailed,
                  noticeKey: const Key('guardrails-notice'),
                ),
              if (c.notice != null && !c.readOnly)
                const SizedBox(height: AnumSpacing.sm),
              if (!c.readOnly)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: FilledButton.icon(
                    key: const Key('guardrails-save'),
                    onPressed: c.saving ? null : c.save,
                    icon: const Icon(Icons.save_outlined),
                    label: Text(c.saving ? l.t('da.saving') : l.t('ap.g.save')),
                  ),
                ),
            ],
          ),
        ),
    ];
  }
}
