import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart' as permissions;

import '../../src/theme/anum_theme.dart';
import 'push_controller.dart';
import 'push_models.dart';

/// Settings › Notifications › Phone push (docs/notifications.md): what this
/// phone can do, one button for the next step, and the caller's registered
/// phones in this workspace, each removable.
class PhonePushCard extends StatefulWidget {
  const PhonePushCard(
      {required this.controller, this.openPhoneSettings, super.key});
  final PushController controller;

  /// Opens this app's page in the phone's settings (tests replace it).
  final Future<void> Function()? openPhoneSettings;

  @override
  State<PhonePushCard> createState() => _PhonePushCardState();
}

class _PhonePushCardState extends State<PhonePushCard> {
  @override
  void initState() {
    super.initState();
    widget.controller.refresh();
  }

  Future<void> _openSettings() async {
    final open = widget.openPhoneSettings;
    if (open != null) {
      await open();
    } else {
      await permissions.openAppSettings();
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final theme = Theme.of(context);
        final (icon, title, text) = _describe(c);
        final action = _action(c);
        return Card(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
              ListTile(
                  leading: Icon(icon),
                  title: Text(title),
                  subtitle: Text(text)),
              if (c.busy) const LinearProgressIndicator(),
              if (c.error != null)
                Padding(
                    padding: const EdgeInsetsDirectional.fromSTEB(16, 0, 16, 8),
                    child: Semantics(
                        liveRegion: true,
                        child: Text(c.error!,
                            style: TextStyle(color: theme.colorScheme.error)))),
              if (c.notice != null)
                Padding(
                    padding: const EdgeInsetsDirectional.fromSTEB(16, 0, 16, 8),
                    child: Semantics(
                        liveRegion: true,
                        child:
                            Text(c.notice!, style: theme.textTheme.bodySmall))),
              if (action != null)
                Padding(
                    padding: const EdgeInsetsDirectional.fromSTEB(
                        16, 0, 16, AnumSpacing.sm),
                    child: Align(
                        alignment: AlignmentDirectional.centerEnd,
                        child: action)),
              if (c.devices.isNotEmpty) ...[
                const Divider(height: 1),
                Padding(
                    padding:
                        const EdgeInsetsDirectional.fromSTEB(16, 12, 16, 0),
                    child: Text('My phones in this workspace',
                        style: theme.textTheme.labelLarge)),
                for (final device in c.devices) _DeviceTile(c, device),
              ],
            ]));
      });

  (IconData, String, String) _describe(PushController c) => switch (c.phase) {
        PushPhase.checking => (
            Icons.notifications_none,
            'Phone push',
            'Checking whether this phone can get notifications...'
          ),
        PushPhase.unavailableInBuild => (
            Icons.notifications_off_outlined,
            'Phone push is not set up in this app',
            'This build has no Firebase configuration, so it cannot get push '
                'notifications. Approvals and results still appear in the app. '
                'The owner adds google-services.json and '
                'GoogleService-Info.plist and rebuilds (docs/mobile.md).'
          ),
        PushPhase.offOnServer => (
            Icons.cloud_off_outlined,
            'Phone push is off on the server',
            '${c.server?.message ?? ''} Ask the person who runs this ANUM '
                    'server to set it up.'
                .trim()
          ),
        PushPhase.notAsked => (
            Icons.notifications_none,
            'Get notified on this phone',
            'Hear about approvals waiting for you and tasks that finish or '
                'fail, even when the app is closed. A notification says only '
                'what happened, never the content.'
          ),
        PushPhase.blocked => (
            Icons.notifications_off_outlined,
            'Notifications are blocked',
            'Notifications for ANUM are turned off in this phone\'s settings. '
                'Turn them on there, then come back.'
          ),
        PushPhase.on => (
            Icons.notifications_active_outlined,
            'This phone gets notifications',
            'For this workspace. Turn off "Phone push" above to pause them, or '
                'remove this phone below.'
          ),
        PushPhase.signedOut => (
            Icons.notifications_none,
            'Phone push',
            'Sign in to get notifications on this phone.'
          ),
        PushPhase.error => (
            Icons.error_outline,
            'Phone push needs attention',
            'See below for what failed.'
          ),
      };

  Widget? _action(PushController c) => switch (c.phase) {
        PushPhase.notAsked => FilledButton.icon(
            onPressed: c.busy ? null : c.enable,
            icon: const Icon(Icons.notifications_active_outlined),
            label: const Text('Turn on notifications')),
        PushPhase.blocked => OutlinedButton.icon(
            onPressed: _openSettings,
            icon: const Icon(Icons.settings_outlined),
            label: const Text('Open phone settings')),
        PushPhase.error || PushPhase.offOnServer => OutlinedButton.icon(
            onPressed: c.busy ? null : c.refresh,
            icon: const Icon(Icons.refresh),
            label: const Text('Check again')),
        _ => null,
      };
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile(this.controller, this.device);
  final PushController controller;
  final PushDevice device;

  @override
  Widget build(BuildContext context) {
    final mine = device.id == controller.thisDeviceId;
    final sent = device.lastSentAt;
    return ListTile(
        leading: Icon(device.platform == 'ios'
            ? Icons.phone_iphone
            : Icons.phone_android),
        title: Text(mine ? '${device.title} (this phone)' : device.title),
        subtitle: Text(sent == null
            ? 'Not notified yet'
            : 'Last notified ${MaterialLocalizations.of(context).formatMediumDate(sent.toLocal())}'),
        trailing: IconButton(
            tooltip: 'Remove ${device.title}',
            onPressed:
                controller.busy ? null : () => controller.removeDevice(device),
            icon: const Icon(Icons.delete_outline)));
  }
}
