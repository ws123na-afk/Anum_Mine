import 'package:flutter/material.dart';

import '../../data/api_models.dart';
import '../../src/theme/anum_theme.dart';
import 'model_choice.dart';
import 'settings_controller.dart';
import '../../src/widgets/depth.dart';
import '../auth/account_screens.dart';
import '../admin/admin.dart';
import '../autonomy/autonomy_controller.dart';
import '../autonomy/autonomy_repository.dart';
import '../autonomy/autonomy_screens.dart';
import '../learning/learning_controller.dart';
import '../learning/learning_screen.dart';
import '../privacy/privacy_controller.dart';
import '../privacy/privacy_screen.dart';
import '../push/push_settings_card.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({required this.controller, super.key});
  final SettingsController controller;
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  // The chat memory card, for the Privacy screen's way back to its switch.
  final _chatMemoryKey = GlobalKey();

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == SettingsPhase.initial) {
      widget.controller.load();
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (_, __) => Scaffold(
          appBar: AppBar(title: const Text('Settings')),
          body: switch (widget.controller.phase) {
            SettingsPhase.initial || SettingsPhase.loading => const _State(
                icon: Icons.settings_outlined,
                title: 'Loading settings',
                loading: true),
            SettingsPhase.offline => _State(
                icon: Icons.cloud_off_outlined,
                title: 'You are offline',
                message: 'Reconnect to load workspace settings.',
                action: widget.controller.load),
            SettingsPhase.permissionDenied => _State(
                icon: Icons.lock_outline,
                title: 'Permission required',
                message:
                    'An organization owner must grant access to these settings.',
                action: widget.controller.load),
            SettingsPhase.error => _State(
                icon: Icons.error_outline,
                title: 'Settings unavailable',
                message: widget.controller.message,
                action: widget.controller.load),
            SettingsPhase.ready || SettingsPhase.saving => AnumBackdrop(
                child: _Content(
                    controller: widget.controller,
                    chatMemoryKey: _chatMemoryKey)),
          }));
}

class _Content extends StatelessWidget {
  const _Content({required this.controller, required this.chatMemoryKey});
  final SettingsController controller;
  final GlobalKey chatMemoryKey;

  /// From Privacy back to "Remember things from my chats": close Privacy and
  /// bring the chat memory card into view.
  void _openChatMemory(BuildContext context) {
    Navigator.of(context).pop();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final target = chatMemoryKey.currentContext;
      if (target != null && target.mounted) {
        Scrollable.ensureVisible(target,
            alignment: 0.2, duration: const Duration(milliseconds: 250));
      }
    });
  }

  Future<void> _changeModel(BuildContext context) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) =>
          _ModelSheet(controller: controller, current: controller.model));
  @override
  Widget build(BuildContext context) {
    final session = controller.session!;
    return Stack(children: [
      ListView(
          padding: const EdgeInsetsDirectional.fromSTEB(
              AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xl),
          children: [
            _Heading('Profile and session'),
            Card(
                child: Column(children: [
              ListTile(
                  leading: const Icon(Icons.person_outline),
                  title: Text(session.context.userId),
                  subtitle: Text(session.context.roles.join(' · '))),
              ListTile(
                  leading: const Icon(Icons.apartment_outlined),
                  title: Text(session.context.tenantId),
                  subtitle: Text(session.context.workspaceId)),
              ListTile(
                  leading: const Icon(Icons.schedule_outlined),
                  title: const Text('Session expiry'),
                  subtitle: Text(MaterialLocalizations.of(context)
                      .formatFullDate(session.expiresAt.toLocal())))
            ])),
            Card(
                child: ListTile(
                    leading: const Icon(Icons.swap_horiz),
                    title: const Text('Switch workspace'),
                    subtitle: const Text(
                        'Roles and agent access follow the selected workspace.'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                            builder: (_) => WorkspaceSwitcherScreen(
                                repository: controller.repository,
                                currentWorkspaceId: session.context.workspaceId,
                                role: session.context.roles.join(', '),
                                onSwitched: controller.load))))),
            if (controller.autonomy != null) ...[
              _Heading('Autonomy'),
              _AutonomyCard(repository: controller.autonomy!),
            ],
            if (controller.admin != null) ...[
              _Heading('Workspace administration'),
              _AdminCard(
                  repository: controller.admin!,
                  workspaceId: session.context.workspaceId),
            ],
            _Heading('Model connection'),
            Card(
                child: controller.model == null
                    ? ListTile(
                        leading: const Icon(Icons.link_off),
                        title: const Text('No model configured'),
                        subtitle: const Text(
                            'Connect a model to run agent tasks. Ollama is free.'),
                        trailing: TextButton(
                            onPressed: () => _changeModel(context),
                            child: const Text('Connect')))
                    : Column(children: [
                        ListTile(
                            leading: const Icon(Icons.smart_toy_outlined),
                            title: Text(
                                controller.model!.provider == 'anthropic'
                                    ? providerLabel('anthropic')
                                    : controller.model!.model),
                            subtitle: Text(
                                controller.model!.provider == 'anthropic'
                                    ? 'Chat and voice: ${controller.model!.fastModel}\n'
                                        'Planning: ${controller.model!.capableModel}'
                                    : '${controller.model!.provider} · ${controller.model!.baseUrl}',
                                textDirection: TextDirection.ltr),
                            isThreeLine:
                                controller.model!.provider == 'anthropic',
                            trailing: Icon(controller
                                        .model!.credentialConfigured ||
                                    controller.model!.provider == 'ollama' ||
                                    controller.model!.provider == 'mock'
                                ? Icons.verified_user_outlined
                                : Icons.warning_amber)),
                        Padding(
                            padding: const EdgeInsetsDirectional.fromSTEB(
                                16, 0, 16, 12),
                            child: Wrap(
                                alignment: WrapAlignment.end,
                                spacing: 8,
                                runSpacing: 8,
                                children: [
                                  TextButton.icon(
                                      onPressed: () => _changeModel(context),
                                      icon: const Icon(Icons.swap_horiz),
                                      label: const Text('Change model')),
                                  OutlinedButton.icon(
                                      onPressed: controller.testModel,
                                      icon: const Icon(Icons.network_check),
                                      label: const Text('Test connection')),
                                ])),
                        if (controller.modelError != null)
                          Padding(
                              padding: const EdgeInsetsDirectional.fromSTEB(
                                  16, 0, 16, 12),
                              child: Semantics(
                                  liveRegion: true,
                                  child: Text(controller.modelError!,
                                      style: TextStyle(
                                          color: Theme.of(context)
                                              .colorScheme
                                              .error)))),
                      ])),
            _Heading('Notifications'),
            _NotificationCard(controller: controller),
            if (controller.push != null) ...[
              const SizedBox(height: AnumSpacing.xs),
              PhonePushCard(controller: controller.push!),
            ],
            _Heading('Chat memory'),
            _ChatMemoryCard(key: chatMemoryKey, controller: controller),
            if (controller.learning != null)
              Card(
                  child: ListTile(
                      leading: const Icon(Icons.school_outlined),
                      title: const Text('What ANUM learned'),
                      subtitle: const Text(
                          'Your style, preferences, routines and weekly suggestions. Edit, delete or turn learning off.'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute<void>(
                              builder: (_) => LearningScreen(
                                  controller:
                                      LearningController(controller.learning!),
                                  onOpenChatMemory: () =>
                                      _openChatMemory(context)))))),
            if (controller.privacy != null) ...[
              _Heading('Privacy'),
              Card(
                  child: ListTile(
                      leading: const Icon(Icons.privacy_tip_outlined),
                      title: const Text('Your data and privacy'),
                      subtitle: const Text(
                          'See what is stored, download it or delete it.'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute<void>(
                              builder: (_) => PrivacyScreen(
                                  controller:
                                      PrivacyController(controller.privacy!),
                                  onOpenChatMemory: () =>
                                      _openChatMemory(context)))))),
            ],
            _Heading('Security'),
            Card(
                child: Column(children: [
              const ListTile(
                  leading: Icon(Icons.key_outlined),
                  title: Text('Protected credentials'),
                  subtitle: Text(
                      'Provider keys are write-only and stored outside API responses.')),
              const Divider(height: 1),
              ListTile(
                  leading: Icon(Icons.logout,
                      color: Theme.of(context).colorScheme.error),
                  title: Text('Sign out',
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
                  subtitle: const Text(
                      'Revoke this local session on the server and device.'),
                  onTap: () => _confirmSignOut(context, controller))
            ])),
            if (controller.message != null)
              Padding(
                  padding: const EdgeInsets.only(top: AnumSpacing.md),
                  child: Text(controller.message!,
                      style: Theme.of(context).textTheme.bodySmall)),
          ]),
      if (controller.phase == SettingsPhase.saving)
        const LinearProgressIndicator()
    ]);
  }
}

class _NotificationCard extends StatefulWidget {
  const _NotificationCard({required this.controller});
  final SettingsController controller;
  @override
  State<_NotificationCard> createState() => _NotificationState();
}

class _NotificationState extends State<_NotificationCard> {
  late NotificationPreferences value = widget.controller.notifications;
  void update(NotificationPreferences next) {
    setState(() => value = next);
    widget.controller.saveNotifications(next);
  }

  NotificationPreferences copy(
          {bool? task,
          bool? approval,
          bool? failure,
          bool? automation,
          bool? email,
          bool? desktop,
          bool? push,
          bool? commerce}) =>
      NotificationPreferences(
          taskCompleted: task ?? value.taskCompleted,
          approvalRequired: approval ?? value.approvalRequired,
          runFailed: failure ?? value.runFailed,
          automationFailed: automation ?? value.automationFailed,
          emailEnabled: email ?? value.emailEnabled,
          desktopEnabled: desktop ?? value.desktopEnabled,
          pushEnabled: push ?? value.pushEnabled,
          commerceAlerts: commerce ?? value.commerceAlerts);
  @override
  Widget build(BuildContext context) => Card(
          child: Column(children: [
        SwitchListTile(
            secondary: const Icon(Icons.task_alt),
            title: const Text('Task completed'),
            value: value.taskCompleted,
            onChanged: (v) => update(copy(task: v))),
        SwitchListTile(
            secondary: const Icon(Icons.approval_outlined),
            title: const Text('Approval required'),
            value: value.approvalRequired,
            onChanged: (v) => update(copy(approval: v))),
        SwitchListTile(
            secondary: const Icon(Icons.error_outline),
            title: const Text('Run failed'),
            value: value.runFailed,
            onChanged: (v) => update(copy(failure: v))),
        SwitchListTile(
            secondary: const Icon(Icons.account_tree_outlined),
            title: const Text('Automation failed'),
            value: value.automationFailed,
            onChanged: (v) => update(copy(automation: v))),
        SwitchListTile(
            secondary: const Icon(Icons.storefront_outlined),
            title: const Text('Store alerts'),
            subtitle: const Text(
                'Low stock, late orders, return spikes and price changes.'),
            value: value.commerceAlerts,
            onChanged: (v) => update(copy(commerce: v))),
        const Divider(height: 1),
        SwitchListTile(
            secondary: const Icon(Icons.email_outlined),
            title: const Text('Email delivery'),
            value: value.emailEnabled,
            onChanged: (v) => update(copy(email: v))),
        SwitchListTile(
            secondary: const Icon(Icons.phone_iphone),
            title: const Text('Phone push'),
            subtitle: const Text(
                'Send these to your phones, even when the app is closed.'),
            value: value.pushEnabled,
            onChanged: (v) => update(copy(push: v))),
        SwitchListTile(
            secondary: const Icon(Icons.notifications_outlined),
            title: const Text('Desktop notifications'),
            value: value.desktopEnabled,
            onChanged: (v) => update(copy(desktop: v)))
      ]));
}

/// "Remember things from my chats" (docs/memory.md#from-conversations):
/// saved at once, with what it means now and a way to delete the memories
/// already remembered.
class _ChatMemoryCard extends StatelessWidget {
  const _ChatMemoryCard({required this.controller, super.key});
  final SettingsController controller;

  Future<void> _confirmDelete(BuildContext context) async {
    final ok = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
                title: const Text('Delete memories from your chats?'),
                content: const Text(
                    'Every memory the Assistant remembered from your chats in this workspace is deleted. It no longer appears in search or in answers. This cannot be undone.'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dialogContext, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      style: FilledButton.styleFrom(
                          backgroundColor:
                              Theme.of(dialogContext).colorScheme.error),
                      onPressed: () => Navigator.pop(dialogContext, true),
                      child: const Text('Delete all')),
                ]));
    if (ok == true) await controller.deleteChatMemories();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final preference = controller.chatMemory;
    final busy = controller.chatMemoryBusy;
    return Card(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (preference == null)
        ListTile(
            leading: const Icon(Icons.psychology_outlined),
            title: const Text('Remember things from my chats'),
            subtitle: Text(controller.chatMemoryError ??
                'Loading your chat memory setting...'),
            trailing: TextButton(
                onPressed: busy ? null : controller.reloadChatMemory,
                child: const Text('Retry')))
      else ...[
        SwitchListTile(
            secondary: const Icon(Icons.psychology_outlined),
            title: const Text('Remember things from my chats'),
            subtitle: Text(preference.explanation),
            isThreeLine: true,
            value: preference.rememberConversations,
            onChanged: busy ? null : controller.setRememberConversations),
        Padding(
            padding: const EdgeInsetsDirectional.fromSTEB(16, 0, 16, 4),
            child: Text('Status: ${preference.badge}',
                style: theme.textTheme.labelLarge)),
      ],
      if (preference != null && controller.chatMemoryError != null)
        Padding(
            padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 4),
            child: Semantics(
                liveRegion: true,
                child: Text(controller.chatMemoryError!,
                    style: TextStyle(color: theme.colorScheme.error)))),
      if (controller.chatMemoryNotice != null)
        Padding(
            padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 4),
            child: Semantics(
                liveRegion: true,
                child: Text(controller.chatMemoryNotice!,
                    style: theme.textTheme.bodySmall))),
      Padding(
          padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 12),
          child: Align(
              alignment: AlignmentDirectional.centerEnd,
              child: OutlinedButton.icon(
                  onPressed: busy ? null : () => _confirmDelete(context),
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Delete memories from my chats')))),
    ]));
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsetsDirectional.fromSTEB(4, 20, 4, 8),
      child: Text(text, style: Theme.of(context).textTheme.titleMedium));
}

class _State extends StatelessWidget {
  const _State(
      {required this.icon,
      required this.title,
      this.message,
      this.action,
      this.loading = false});
  final IconData icon;
  final String title;
  final String? message;
  final VoidCallback? action;
  final bool loading;
  @override
  Widget build(BuildContext context) => Center(
      child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.lg),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                if (loading)
                  const CircularProgressIndicator()
                else
                  Icon(icon, size: 42),
                const SizedBox(height: AnumSpacing.md),
                Text(title,
                    style: Theme.of(context).textTheme.titleLarge,
                    textAlign: TextAlign.center),
                if (message != null) ...[
                  const SizedBox(height: 8),
                  Text(message!, textAlign: TextAlign.center)
                ],
                if (action != null) ...[
                  const SizedBox(height: AnumSpacing.md),
                  OutlinedButton.icon(
                      onPressed: action,
                      icon: const Icon(Icons.refresh),
                      label: const Text('Retry'))
                ]
              ]))));
}

Future<void> _confirmSignOut(
    BuildContext context, SettingsController controller) async {
  final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
              title: const Text('Sign out?'),
              content: const Text(
                  'Your local session will be revoked on this device.'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Cancel')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Sign out'))
              ]));
  if (confirmed ?? false) await controller.signOut();
}

/// Change the workspace model after setup. Ollama is free and needs no key;
/// Anthropic (Claude) routes quick replies and planning to separate models.
class _ModelSheet extends StatefulWidget {
  const _ModelSheet({required this.controller, this.current});
  final SettingsController controller;
  final ModelConfiguration? current;
  @override
  State<_ModelSheet> createState() => _ModelSheetState();
}

class _ModelSheetState extends State<_ModelSheet> {
  static const _presets = modelPresets;
  final form = GlobalKey<FormState>();
  late String provider = _normalise(widget.current?.provider) ?? 'ollama';
  late final model = TextEditingController(
      text: widget.current?.model ?? _presets[provider]!.$1);
  late final url = TextEditingController(
      text: widget.current?.baseUrl ?? _presets[provider]!.$2);
  final key = TextEditingController();
  late final fastModel = TextEditingController(
      text: _claudeSaved
          ? (widget.current!.routing.fastModel ?? '')
          : anthropicFastModel);
  late final capableModel = TextEditingController(
      text:
          _claudeSaved ? widget.current!.capableModel : anthropicCapableModel);
  late String fastEffort =
      _claudeSaved ? (widget.current!.routing.fastEffort ?? '') : 'low';
  late String capableEffort =
      _claudeSaved ? (widget.current!.routing.capableEffort ?? '') : '';
  bool saving = false;
  String? error;

  bool get _claudeSaved => widget.current?.provider == 'anthropic';
  SavedKeyState get _keyState =>
      savedKeyState(provider, url.text, widget.current);
  bool get _claude => provider == 'anthropic';

  static String? _normalise(String? value) {
    final normalised = value?.replaceAll('-', '_');
    return modelPresets.containsKey(normalised) ? normalised : null;
  }

  @override
  void dispose() {
    model.dispose();
    url.dispose();
    key.dispose();
    fastModel.dispose();
    capableModel.dispose();
    super.dispose();
  }

  void _select(String next) {
    final previous = _presets[provider];
    final preset = _presets[next]!;
    setState(() {
      if (previous == null || model.text.trim() == previous.$1) {
        model.text = preset.$1;
      }
      if (previous == null || url.text.trim() == previous.$2) {
        url.text = preset.$2;
      }
      provider = next;
    });
  }

  Future<void> _save() async {
    if (!form.currentState!.validate()) return;
    setState(() => saving = true);
    final ok = await widget.controller.changeModel(
        provider: provider,
        // Claude's planning model is the workspace's default model.
        model: _claude ? capableModel.text.trim() : model.text.trim(),
        baseUrl: url.text.trim(),
        // Empty keeps the saved key (same provider and base URL only).
        apiKey: providerNeedsKey(provider) ? key.text.trim() : null,
        routing: _claude
            ? ModelRouting(
                fastModel: fastModel.text,
                capableModel: capableModel.text,
                fastEffort: fastEffort,
                capableEffort: capableEffort)
            : null);
    if (!mounted) return;
    setState(() {
      saving = false;
      error = ok ? null : widget.controller.message;
    });
    if (ok) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) => Padding(
      padding: EdgeInsetsDirectional.fromSTEB(AnumSpacing.md, 0, AnumSpacing.md,
          MediaQuery.viewInsetsOf(context).bottom + 24),
      child: Form(
          key: form,
          child: SingleChildScrollView(
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                Text('Change model',
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 4),
                const Text(
                    'Tasks, chat and voice answers in this workspace use this model.'),
                const SizedBox(height: AnumSpacing.md),
                DropdownButtonFormField<String>(
                    initialValue: provider,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Provider'),
                    items: providerItems(),
                    onChanged: saving
                        ? null
                        : (v) {
                            if (v != null) _select(v);
                          }),
                if (!_claude) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  TextFormField(
                      controller: model,
                      decoration: const InputDecoration(labelText: 'Model'),
                      validator: (v) => (v ?? '').trim().isEmpty
                          ? 'Enter a model name'
                          : null),
                ],
                const SizedBox(height: AnumSpacing.sm),
                TextFormField(
                    controller: url,
                    textDirection: TextDirection.ltr,
                    keyboardType: TextInputType.url,
                    // The key field's hint depends on the address.
                    onChanged: (_) => setState(() {}),
                    decoration: InputDecoration(
                        labelText: _claude ? 'Base URL (optional)' : 'Base URL',
                        helperMaxLines: 3,
                        helperText: provider == 'ollama'
                            ? 'The ANUM server calls this address, not your phone.'
                            : _claude
                                ? 'Leave empty for api.anthropic.com.'
                                : null),
                    validator: (v) => validateBaseUrl(provider, v)),
                if (providerNeedsKey(provider)) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  TextFormField(
                      controller: key,
                      obscureText: true,
                      textDirection: TextDirection.ltr,
                      decoration: InputDecoration(
                          labelText: _claude ? 'Anthropic API key' : 'API key',
                          helperMaxLines: 3,
                          helperText: switch (_keyState) {
                            SavedKeyState.keep =>
                              'Leave empty to keep the saved key '
                                  '(${widget.current?.credentialHint ?? 'hidden'}). '
                                  'Paste a new key to replace it.',
                            SavedKeyState.moved =>
                              'The base URL changed: enter the key for this '
                                  'address. The saved key is only used with '
                                  'the address it was saved for.',
                            SavedKeyState.none =>
                              'Stored encrypted; never shown again.',
                          }),
                      validator: (v) => validateApiKey(v, _keyState)),
                ],
                if (_claude) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  ClaudeRoutingFields(
                      fastModel: fastModel,
                      capableModel: capableModel,
                      fastEffort: fastEffort,
                      capableEffort: capableEffort,
                      enabled: !saving,
                      onFastEffort: (v) => setState(() => fastEffort = v),
                      onCapableEffort: (v) =>
                          setState(() => capableEffort = v)),
                ],
                if (error != null) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Semantics(
                      liveRegion: true,
                      child: Text(error!,
                          style: TextStyle(
                              color: Theme.of(context).colorScheme.error))),
                ],
                const SizedBox(height: AnumSpacing.md),
                FilledButton.icon(
                    onPressed: saving ? null : _save,
                    icon: saving
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.cable),
                    label: Text(saving ? 'Testing...' : 'Save and test')),
              ]))));
}

/// What ANUM did on its own, and when it may act without asking
/// (docs/autonomy.md). Each pushed screen owns its controller.
class _AutonomyCard extends StatelessWidget {
  const _AutonomyCard({required this.repository});
  final AutonomyRepository repository;

  void _settings(BuildContext context) => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => OwnedController<AutonomySettingsController>(
              create: () => AutonomySettingsController(repository),
              builder: (_, c) => AutonomySettingsScreen(controller: c))));

  @override
  Widget build(BuildContext context) => Card(
          child: Column(children: [
        ListTile(
            key: const Key('open-activity'),
            leading: const Icon(Icons.history),
            title: const Text('Activity'),
            subtitle: const Text(
                'Everything ANUM did on its own, with Undo and suggestions.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                    builder: (_) => OwnedController<ActivityController>(
                        create: () => ActivityController(repository),
                        builder: (ctx, c) => ActivityScreen(
                            controller: c,
                            onOpenSettings: () => _settings(ctx)))))),
        ListTile(
            key: const Key('open-autonomy'),
            leading: const Icon(Icons.tune),
            title: const Text('Autonomy levels'),
            subtitle: const Text(
                'When ANUM may act without asking, by area and threshold.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _settings(context)),
      ]));
}

/// Members, invitations, model budgets and the approval policy. Shown to
/// everyone: the screens show the API's answer to non-owners instead of
/// guessing roles. Each pushed screen owns its controller and disposes it
/// when it is popped ([OwnedController]).
class _AdminCard extends StatelessWidget {
  const _AdminCard({required this.repository, required this.workspaceId});
  final AdminRepository repository;
  final String workspaceId;

  void _push<T extends ChangeNotifier>(BuildContext context,
          T Function() create, Widget Function(T controller) screen) =>
      Navigator.push(
          context,
          MaterialPageRoute<void>(
              builder: (_) => OwnedController<T>(
                  create: create, builder: (_, c) => screen(c))));

  @override
  Widget build(BuildContext context) => Card(
          child: Column(children: [
        ListTile(
            leading: const Icon(Icons.group_outlined),
            title: const Text('Members and invitations'),
            subtitle: const Text('Roles, deactivation and single-use invites.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(
                context,
                () => MembersController(repository),
                (c) => MembersScreen(
                    controller: c,
                    repository: repository,
                    currentWorkspaceId: workspaceId))),
        ListTile(
            leading: const Icon(Icons.receipt_long_outlined),
            title: const Text('Audit log'),
            subtitle: const Text(
                'Who changed what, and when. Owners and organization administrators.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(context, () => AuditLogController(repository),
                (c) => AuditLogScreen(controller: c))),
        ListTile(
            leading: const Icon(Icons.speed_outlined),
            title: const Text('Model budgets'),
            subtitle: const Text('Monthly cost and token limits with usage.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(context, () => BudgetsController(repository),
                (c) => BudgetsScreen(controller: c))),
        ListTile(
            leading: const Icon(Icons.policy_outlined),
            title: const Text('Approval policy'),
            subtitle: const Text(
                'Two-person rule and approval for medium-risk actions.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(
                context,
                () => ApprovalPolicyController(repository),
                (c) => ApprovalPolicyScreen(
                    controller: c, workspaceId: workspaceId))),
        ListTile(
            leading: const Icon(Icons.how_to_reg_outlined),
            title: const Text('Accept an invitation'),
            subtitle: const Text('Paste a token or link you received.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(
                context,
                () => AcceptInvitationController(repository,
                    currentWorkspaceId: workspaceId),
                (c) => AcceptInvitationScreen(controller: c))),
      ]));
}
