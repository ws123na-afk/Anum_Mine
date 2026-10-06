import 'package:flutter/material.dart';

import '../../data/api_models.dart';
import '../../src/theme/anum_theme.dart';
import 'settings_controller.dart';
import '../../src/widgets/depth.dart';
import '../auth/account_screens.dart';
import '../admin/admin.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({required this.controller, super.key});
  final SettingsController controller;
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
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
            SettingsPhase.ready ||
            SettingsPhase.saving =>
              AnumBackdrop(child: _Content(controller: widget.controller)),
          }));
}

class _Content extends StatelessWidget {
  const _Content({required this.controller});
  final SettingsController controller;

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
                        MaterialPageRoute(
                            builder: (_) => WorkspaceSwitcherScreen(
                                repository: controller.repository,
                                currentWorkspaceId: session.context.workspaceId,
                                role: session.context.roles.join(', '),
                                onSwitched: controller.load))))),
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
                            title: Text(controller.model!.model),
                            subtitle: Text(
                                '${controller.model!.provider} · ${controller.model!.baseUrl}',
                                textDirection: TextDirection.ltr),
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
                                ]))
                      ])),
            _Heading('Notifications'),
            _NotificationCard(controller: controller),
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
          bool? desktop}) =>
      NotificationPreferences(
          taskCompleted: task ?? value.taskCompleted,
          approvalRequired: approval ?? value.approvalRequired,
          runFailed: failure ?? value.runFailed,
          automationFailed: automation ?? value.automationFailed,
          emailEnabled: email ?? value.emailEnabled,
          desktopEnabled: desktop ?? value.desktopEnabled);
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
        const Divider(height: 1),
        SwitchListTile(
            secondary: const Icon(Icons.email_outlined),
            title: const Text('Email delivery'),
            value: value.emailEnabled,
            onChanged: (v) => update(copy(email: v))),
        SwitchListTile(
            secondary: const Icon(Icons.notifications_outlined),
            title: const Text('Device notifications'),
            value: value.desktopEnabled,
            onChanged: (v) => update(copy(desktop: v)))
      ]));
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

/// Change the workspace model after setup. Ollama is free and needs no key.
class _ModelSheet extends StatefulWidget {
  const _ModelSheet({required this.controller, this.current});
  final SettingsController controller;
  final ModelConfiguration? current;
  @override
  State<_ModelSheet> createState() => _ModelSheetState();
}

class _ModelSheetState extends State<_ModelSheet> {
  static const _presets = <String, (String, String)>{
    'ollama': ('llama3.2', 'http://localhost:11434/v1'),
    'openai_compatible': ('gpt-4.1-mini', 'https://api.openai.com/v1'),
    'mock': ('mock', 'http://localhost'),
  };
  final form = GlobalKey<FormState>();
  late String provider = _normalise(widget.current?.provider) ?? 'ollama';
  late final model = TextEditingController(
      text: widget.current?.model ?? _presets[provider]!.$1);
  late final url = TextEditingController(
      text: widget.current?.baseUrl ?? _presets[provider]!.$2);
  final key = TextEditingController();
  bool saving = false;
  String? error;

  static String? _normalise(String? value) => value?.replaceAll('-', '_');

  @override
  void dispose() {
    model.dispose();
    url.dispose();
    key.dispose();
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
        model: model.text.trim(),
        baseUrl: url.text.trim(),
        apiKey: provider == 'openai_compatible' ? key.text.trim() : null);
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
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Change model',
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 4),
                const Text(
                    'Tasks and voice answers in this workspace use this model.'),
                const SizedBox(height: AnumSpacing.md),
                DropdownButtonFormField<String>(
                    initialValue: provider,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Provider'),
                    items: const [
                      DropdownMenuItem(
                          value: 'ollama',
                          child: Text('Ollama (free, runs on your computer)')),
                      DropdownMenuItem(
                          value: 'openai_compatible',
                          child: Text('OpenAI compatible')),
                      DropdownMenuItem(
                          value: 'mock', child: Text('Local mock')),
                    ],
                    onChanged: saving
                        ? null
                        : (v) {
                            if (v != null) _select(v);
                          }),
                const SizedBox(height: AnumSpacing.sm),
                TextFormField(
                    controller: model,
                    decoration: const InputDecoration(labelText: 'Model'),
                    validator: (v) =>
                        (v ?? '').trim().isEmpty ? 'Enter a model name' : null),
                const SizedBox(height: AnumSpacing.sm),
                TextFormField(
                    controller: url,
                    textDirection: TextDirection.ltr,
                    keyboardType: TextInputType.url,
                    decoration: InputDecoration(
                        labelText: 'Base URL',
                        helperMaxLines: 3,
                        helperText: provider == 'ollama'
                            ? 'The ANUM server calls this address, not your phone.'
                            : null),
                    validator: (v) {
                      final u = Uri.tryParse(v ?? '');
                      return u != null && u.isAbsolute
                          ? null
                          : 'Enter an absolute URL';
                    }),
                if (provider == 'openai_compatible') ...[
                  const SizedBox(height: AnumSpacing.sm),
                  TextFormField(
                      controller: key,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: 'API key'),
                      validator: (v) => (v ?? '').trim().isEmpty
                          ? 'Enter the API key'
                          : null),
                ],
                if (error != null) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Text(error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
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
              ])));
}

/// Members, invitations and model budgets. Shown to everyone: the screens
/// show the API's 403 answer to non-owners instead of guessing roles.
class _AdminCard extends StatelessWidget {
  const _AdminCard({required this.repository, required this.workspaceId});
  final AdminRepository repository;
  final String workspaceId;

  void _push(BuildContext context, Widget screen) =>
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => screen));

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
                MembersScreen(
                    controller: MembersController(repository),
                    repository: repository,
                    currentWorkspaceId: workspaceId))),
        ListTile(
            leading: const Icon(Icons.speed_outlined),
            title: const Text('Model budgets'),
            subtitle: const Text('Monthly cost and token limits with usage.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(context,
                BudgetsScreen(controller: BudgetsController(repository)))),
        ListTile(
            leading: const Icon(Icons.how_to_reg_outlined),
            title: const Text('Accept an invitation'),
            subtitle: const Text('Paste a token or link you received.'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _push(
                context,
                AcceptInvitationScreen(
                    controller: AcceptInvitationController(repository,
                        currentWorkspaceId: workspaceId)))),
      ]));
}
