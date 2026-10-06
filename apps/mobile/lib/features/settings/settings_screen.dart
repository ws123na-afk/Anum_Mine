import 'package:flutter/material.dart';

import '../../data/api_models.dart';
import '../../src/theme/anum_theme.dart';
import 'settings_controller.dart';
import '../../src/widgets/depth.dart';
import '../auth/account_screens.dart';

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
            _Heading('Model connection'),
            Card(
                child: controller.model == null
                    ? const ListTile(
                        leading: Icon(Icons.link_off),
                        title: Text('No model configured'),
                        subtitle:
                            Text('Complete model setup to run agent tasks.'))
                    : Column(children: [
                        ListTile(
                            leading: const Icon(Icons.smart_toy_outlined),
                            title: Text(controller.model!.model),
                            subtitle: Text(
                                '${controller.model!.provider} · ${controller.model!.baseUrl}',
                                textDirection: TextDirection.ltr),
                            trailing: Icon(
                                controller.model!.credentialConfigured
                                    ? Icons.verified_user_outlined
                                    : Icons.warning_amber)),
                        Padding(
                            padding: const EdgeInsetsDirectional.fromSTEB(
                                16, 0, 16, 12),
                            child: Align(
                                alignment: AlignmentDirectional.centerEnd,
                                child: OutlinedButton.icon(
                                    onPressed: controller.testModel,
                                    icon: const Icon(Icons.network_check),
                                    label: const Text('Test connection'))))
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
