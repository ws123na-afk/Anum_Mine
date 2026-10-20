// Triggers of one automation (docs/automation.md#triggers): event triggers
// with conditions and signed webhooks. Lists them with every state, adds and
// edits them in a sheet, turns them on or off, deletes, rotates a webhook's
// secret (shown once) and lets an owner take over another member's trigger.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'automation_models.dart';
import 'automation_repository.dart';
import 'automations_controller.dart';

class AutomationTriggersCard extends StatefulWidget {
  const AutomationTriggersCard(
      {required this.controller,
      required this.repository,
      required this.workflowId,
      this.onChanged,
      super.key});
  final AutomationsController controller;
  final AutomationTriggerRepository repository;
  final String workflowId;
  final VoidCallback? onChanged;

  @override
  State<AutomationTriggersCard> createState() => _AutomationTriggersCardState();
}

class _AutomationTriggersCardState extends State<AutomationTriggersCard> {
  List<AutomationTriggerItem>? triggers;
  List<TriggerEventInfo> events = const [];
  String? error;
  bool busy = false;

  AutomationsController get c => widget.controller;
  AnumLocalizations get l => AnumLocalizations.maybeOf(context);

  @override
  void initState() {
    super.initState();
    _load();
    widget.repository.triggerEvents().then((value) {
      if (mounted) setState(() => events = value);
    }, onError: (_) {});
  }

  @override
  void didUpdateWidget(AutomationTriggersCard old) {
    super.didUpdateWidget(old);
    if (old.workflowId != widget.workflowId ||
        old.repository != widget.repository) {
      setState(() => triggers = null);
      _load();
    }
  }

  Future<void> _load() async {
    try {
      final items = await widget.repository.triggers(widget.workflowId);
      if (mounted) {
        setState(() {
          triggers = items;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = automationErrorText(e));
    }
  }

  void _say(String message) => ScaffoldMessenger.maybeOf(context)
      ?.showSnackBar(SnackBar(content: Text(message)));

  Future<void> _act(Future<Object?> Function() action, String done) async {
    setState(() => busy = true);
    final failure = await c.run(() async {
      await action();
    });
    if (!mounted) return;
    setState(() => busy = false);
    _say(failure ?? done);
    await _load();
    widget.onChanged?.call();
  }

  Future<bool> _confirm(String text) async =>
      await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                content: Text(text),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: Text(l.t('autoTrigNo'))),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: Text(l.t('autoTrigYes'))),
                ],
              )) ??
      false;

  Future<void> _showSecret(AutomationTriggerItem trigger, String secret) =>
      showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (context) =>
              _SecretDialog(trigger: trigger, secret: secret));

  Future<void> _edit({AutomationTriggerItem? existing, String? kind}) async {
    final saved = await showModalBottomSheet<
            ({AutomationTriggerItem trigger, String? secret})>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => TriggerSheet(
            repository: widget.repository,
            workflowId: widget.workflowId,
            events: events,
            existing: existing,
            kind: kind ?? existing?.kind ?? 'event'));
    if (saved == null || !mounted) return;
    _say(l.f(existing == null ? 'autoTrigAdded' : 'autoTrigSaved',
        {'name': saved.trigger.name}));
    if (saved.secret != null) await _showSecret(saved.trigger, saved.secret!);
    await _load();
    widget.onChanged?.call();
  }

  String _eventName(String? type) {
    final key = 'autoEvent_${(type ?? '').replaceAll('.', '_')}';
    final text = l.t(key);
    if (text != key) return text;
    return events
            .where((e) => e.type == type)
            .map((e) => e.label)
            .firstOrNull ??
        (type ?? '');
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final items = triggers;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(title: l.t('autoTrigTitle')),
        Text(l.t('autoTrigHelp'),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        if (c.canManage)
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            TextButton.icon(
                onPressed: busy ? null : () => _edit(kind: 'event'),
                icon: const Icon(Icons.bolt),
                label: Text(l.t('autoTrigAddEvent'))),
            TextButton.icon(
                onPressed: busy ? null : () => _edit(kind: 'webhook'),
                icon: const Icon(Icons.key),
                label: Text(l.t('autoTrigAddWebhook'))),
          ]),
        if (error != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('autoTrigLoadFailed'),
              message: error!,
              action: l.t('autoTrigTryAgain'),
              onAction: _load)
        else if (items == null)
          Padding(
              padding: const EdgeInsets.all(AnumSpacing.md),
              child: Center(
                  child: CircularProgressIndicator(
                      semanticsLabel: l.t('autoTrigLoading'))))
        else if (items.isEmpty)
          AnumEmpty(
              icon: Icons.bolt,
              title: l.t('autoTrigEmptyTitle'),
              message: c.canManage
                  ? l.t('autoTrigEmptyManage')
                  : l.t('autoTrigEmptyRead'))
        else
          for (final t in items) _row(context, t),
      ]),
    );
  }

  Widget _row(BuildContext context, AutomationTriggerItem t) {
    final editable = c.canManage && c.canEdit(t.createdBy);
    final takeOver = canTakeOver(c.role, c.userId, t.createdBy);
    final outcome = triggerOutcomeLabel(t.lastOutcome);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      AnumRow(
        icon: t.webhook ? Icons.key : Icons.bolt,
        tone: t.enabled ? AnumTone.ok : AnumTone.neutral,
        title: t.name,
        subtitle: [
          t.webhook ? l.t('autoTrigWebhook') : _eventName(t.eventType),
          if (t.conditions.isNotEmpty)
            t.conditions.map(conditionText).join(' & '),
        ].join(' · '),
        detail: [
          if (t.webhook)
            l.f('autoTrigSecretHint', {'hint': t.secretHint ?? '????'}),
          t.lastFiredAt == null
              ? l.t('autoTrigNeverFired')
              : l.f('autoTrigLastFired',
                  {'when': relativeWhen(t.lastFiredAt!), 'count': t.fireCount}),
          if (outcome != null && t.lastOutcome != 'fired') outcome,
          l.f('autoTrigRunsAs', {'user': t.createdBy ?? '?'}),
        ].join(' · '),
        trailing: AnumPill(
            label: t.enabled ? l.t('autoTrigOn') : l.t('autoTrigOff'),
            tone: t.enabled ? AnumTone.ok : AnumTone.neutral),
      ),
      if (editable || takeOver)
        Wrap(spacing: AnumSpacing.xs, children: [
          if (editable) ...[
            TextButton(
                onPressed: busy
                    ? null
                    : () => _act(
                        () => widget.repository
                            .setTriggerEnabled(t.id, !t.enabled),
                        l.f(
                            t.enabled
                                ? 'autoTrigTurnedOff'
                                : 'autoTrigTurnedOn',
                            {'name': t.name})),
                child: Text(
                    t.enabled ? l.t('autoTrigTurnOff') : l.t('autoTrigTurnOn'),
                    semanticsLabel:
                        '${t.enabled ? l.t('autoTrigTurnOff') : l.t('autoTrigTurnOn')} ${t.name}')),
            TextButton(
                onPressed: busy ? null : () => _edit(existing: t),
                child: Text(l.t('autoTrigEdit'),
                    semanticsLabel: '${l.t('autoTrigEdit')} ${t.name}')),
            if (t.webhook)
              TextButton(
                  onPressed: busy
                      ? null
                      : () async {
                          if (!await _confirm(
                              l.f('autoTrigConfirmRotate', {'name': t.name}))) {
                            return;
                          }
                          setState(() => busy = true);
                          try {
                            final result = await widget.repository
                                .rotateTriggerSecret(t.id);
                            if (mounted && result.secret != null) {
                              await _showSecret(result.trigger, result.secret!);
                            }
                          } catch (e) {
                            _say(automationErrorText(e));
                          } finally {
                            if (mounted) setState(() => busy = false);
                          }
                          await _load();
                        },
                  child: Text(l.t('autoTrigRotate'),
                      semanticsLabel: '${l.t('autoTrigRotate')} ${t.name}')),
            TextButton(
                onPressed: busy
                    ? null
                    : () async {
                        if (await _confirm(
                            l.f('autoTrigConfirmDelete', {'name': t.name}))) {
                          await _act(
                              () => widget.repository.deleteTrigger(t.id),
                              l.f('autoTrigDeleted', {'name': t.name}));
                        }
                      },
                child: Text(l.t('autoTrigDelete'),
                    semanticsLabel: '${l.t('autoTrigDelete')} ${t.name}')),
          ],
          if (takeOver)
            TextButton(
                onPressed: busy
                    ? null
                    : () async {
                        if (await _confirm(l.f('autoTrigConfirmTakeOver',
                            {'name': t.name, 'user': t.createdBy ?? ''}))) {
                          await _act(
                              () => widget.repository.takeOverTrigger(t.id),
                              l.f('autoTrigTakenOver', {'name': t.name}));
                        }
                      },
                child: Text(l.t('autoTakeOver'),
                    semanticsLabel: '${l.t('autoTakeOver')} ${t.name}')),
        ]),
    ]);
  }
}

class _SecretDialog extends StatelessWidget {
  const _SecretDialog({required this.trigger, required this.secret});
  final AutomationTriggerItem trigger;
  final String secret;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    Widget copyRow(String label, String value) =>
        Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(label, style: const TextStyle(fontWeight: FontWeight.w700)),
          SelectableText(value, textDirection: TextDirection.ltr),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: TextButton.icon(
                onPressed: () => Clipboard.setData(ClipboardData(text: value)),
                icon: const Icon(Icons.copy, size: 16),
                label: Text(l.t('autoTrigCopy'),
                    semanticsLabel: '${l.t('autoTrigCopy')} $label')),
          ),
        ]);
    return AlertDialog(
      title: Text(l.f('autoTrigSecretTitle', {'name': trigger.name})),
      content: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('autoTrigSecretOnce')),
              const SizedBox(height: AnumSpacing.sm),
              copyRow(l.t('autoTrigUrl'), trigger.webhookPath ?? ''),
              copyRow(l.t('autoTrigSecret'), secret),
              Text(l.t('autoTrigSignHelp'),
                  style: TextStyle(
                      color: context.palette.muted, fontSize: AnumType.xsPlus)),
            ]),
      ),
      actions: [
        FilledButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('autoTrigSavedSecret'))),
      ],
    );
  }
}

/// Adds or edits one trigger; pops with the saved trigger (and, for a new
/// webhook, its secret).
class TriggerSheet extends StatefulWidget {
  const TriggerSheet(
      {required this.repository,
      required this.workflowId,
      required this.events,
      required this.kind,
      this.existing,
      super.key});
  final AutomationTriggerRepository repository;
  final String workflowId;
  final List<TriggerEventInfo> events;
  final String kind;
  final AutomationTriggerItem? existing;

  @override
  State<TriggerSheet> createState() => _TriggerSheetState();
}

class _TriggerSheetState extends State<TriggerSheet> {
  late final TextEditingController name =
      TextEditingController(text: widget.existing?.name ?? '');
  late String eventType = widget.existing?.eventType ??
      (widget.events.isNotEmpty ? widget.events.first.type : 'email.received');
  late final List<TriggerCondition> conditions = [
    for (final c
        in widget.existing?.conditions ?? const <Map<String, Object?>>[])
      TriggerCondition(
          field: '${c['field'] ?? ''}',
          operator: '${c['operator'] ?? 'equals'}',
          value: c['value'] is String ? c['value'] as String : '')
  ];
  late int rate = widget.existing?.rateLimit ?? 30;
  late int replay = widget.existing?.replayWindow ?? 300;
  Map<String, String> errors = const {};
  String? failure;
  bool busy = false;

  bool get webhook => widget.kind == 'webhook';

  @override
  void dispose() {
    name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final check = checkTriggerDraft(
        name: name.text,
        kind: widget.kind,
        eventType: eventType,
        events: [
          for (final e in widget.events) e.type,
          if (widget.events.isEmpty) eventType
        ],
        conditions: conditions,
        rateLimit: rate,
        replayWindow: replay);
    setState(() {
      errors = check.errors;
      failure = null;
    });
    if (!check.ok) return;
    setState(() => busy = true);
    try {
      final existing = widget.existing;
      final result = existing == null
          ? await widget.repository
              .createTrigger(widget.workflowId, check.body!)
          : (
              trigger: await widget.repository
                  .updateTrigger(existing.id, check.body!),
              secret: null
            );
      if (mounted) Navigator.pop(context, result);
    } catch (e) {
      if (mounted) setState(() => failure = automationErrorText(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final info = widget.events.where((e) => e.type == eventType).firstOrNull;
    final fields = webhook ? const ['body'] : info?.fields ?? const <String>[];
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                  widget.existing != null
                      ? l.t('autoTrigEdit')
                      : webhook
                          ? l.t('autoTrigAddWebhook')
                          : l.t('autoTrigAddEvent'),
                  style: Theme.of(context).textTheme.titleLarge),
              TextField(
                  controller: name,
                  maxLength: 160,
                  decoration: InputDecoration(
                      labelText: l.t('autoTrigName'),
                      errorText: errors['name'])),
              if (!webhook)
                DropdownButtonFormField<String>(
                  initialValue: eventType,
                  isExpanded: true,
                  decoration: InputDecoration(
                      labelText: l.t('autoTrigEvent'),
                      errorText: errors['event'],
                      helperText: info?.description,
                      helperMaxLines: 3),
                  items: [
                    if (!widget.events.any((e) => e.type == eventType))
                      DropdownMenuItem(
                          value: eventType, child: Text(eventType)),
                    for (final e in widget.events)
                      DropdownMenuItem(
                          value: e.type,
                          child: Text(() {
                            final key =
                                'autoEvent_${e.type.replaceAll('.', '_')}';
                            final text = l.t(key);
                            return text == key ? e.label : text;
                          }())),
                  ],
                  onChanged: (v) => setState(() => eventType = v ?? eventType),
                ),
              TextFormField(
                  initialValue: '$rate',
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(
                      labelText: l.t('autoTrigRate'),
                      errorText: errors['rate']),
                  onChanged: (v) => rate = int.tryParse(v) ?? 0),
              if (webhook)
                TextFormField(
                    initialValue: '$replay',
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                        labelText: l.t('autoTrigReplay'),
                        errorText: errors['replay']),
                    onChanged: (v) => replay = int.tryParse(v) ?? 0),
              const SizedBox(height: AnumSpacing.sm),
              Text(l.t('autoTrigConditions'),
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              Text(
                  webhook
                      ? l.t('autoTrigConditionsWebhook')
                      : l.f('autoTrigConditionsHelp',
                          {'fields': fields.isEmpty ? '-' : fields.join(', ')}),
                  style: TextStyle(
                      color: context.palette.muted, fontSize: AnumType.sm)),
              for (var i = 0; i < conditions.length; i++)
                _condition(context, l, i),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton.icon(
                    onPressed: conditions.length >= 10
                        ? null
                        : () => setState(() => conditions.add(TriggerCondition(
                            field: webhook
                                ? 'body.'
                                : fields.isEmpty
                                    ? ''
                                    : fields.first))),
                    icon: const Icon(Icons.add),
                    label: Text(l.t('autoTrigAddCondition'))),
              ),
              if (failure != null)
                Text(failure!, style: TextStyle(color: context.palette.stop)),
              const SizedBox(height: AnumSpacing.sm),
              Wrap(
                  spacing: AnumSpacing.sm,
                  runSpacing: AnumSpacing.xs,
                  children: [
                    FilledButton(
                        onPressed: busy ? null : _save,
                        child: Text(widget.existing == null
                            ? l.t('autoTrigCreate')
                            : l.t('autoTrigSave'))),
                    OutlinedButton(
                        onPressed: busy ? null : () => Navigator.pop(context),
                        child: Text(l.t('autoTrigCancel'))),
                  ]),
            ]),
      ),
    );
  }

  Widget _condition(BuildContext context, AnumLocalizations l, int i) {
    final c = conditions[i];
    return Column(
      key: ObjectKey(c),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(children: [
          Expanded(
            child: TextFormField(
                initialValue: c.field,
                textDirection: TextDirection.ltr,
                decoration: InputDecoration(
                    labelText: l.f('autoTrigField', {'n': i + 1}),
                    errorText: errors['$i.field']),
                onChanged: (v) => c.field = v),
          ),
          IconButton(
              tooltip: l.f('autoTrigRemoveCondition', {'n': i + 1}),
              onPressed: () => setState(() => conditions.removeAt(i)),
              icon: const Icon(Icons.close)),
        ]),
        DropdownButtonFormField<String>(
          initialValue: c.operator,
          isExpanded: true,
          decoration: InputDecoration(labelText: l.t('autoTrigTest')),
          items: [
            for (final e in conditionOperators.entries)
              DropdownMenuItem(
                  value: e.key, child: Text(l.t('autoTrigOp_${e.key}'))),
          ],
          onChanged: (v) => setState(() => c.operator = v ?? c.operator),
        ),
        if (c.operator != 'exists')
          TextFormField(
              initialValue: c.value,
              decoration: InputDecoration(
                  labelText: l.t('autoTrigValue'),
                  errorText: errors['$i.value']),
              onChanged: (v) => c.value = v),
        const SizedBox(height: AnumSpacing.xs),
      ],
    );
  }
}
