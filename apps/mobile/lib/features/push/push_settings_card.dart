import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart' as permissions;

import '../../src/theme/anum_theme.dart';
import 'push_controller.dart';
import 'push_models.dart';
import 'push_strings.dart';

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
        final s = PushStrings.of(context);
        final (icon, title, text) = _describe(c, s);
        final action = _action(c, s);
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
                    child: Text(s.t('myPhones'),
                        style: theme.textTheme.labelLarge)),
                for (final device in c.devices) _DeviceTile(c, device, s),
              ],
            ]));
      });

  (IconData, String, String) _describe(PushController c, PushStrings s) =>
      switch (c.phase) {
        PushPhase.checking => (
            Icons.notifications_none,
            s.t('checkingTitle'),
            s.t('checkingText')
          ),
        PushPhase.unavailableInBuild => (
            Icons.notifications_off_outlined,
            s.t('noBuildTitle'),
            s.t('noBuildText')
          ),
        PushPhase.offOnServer => (
            Icons.cloud_off_outlined,
            s.t('serverOffTitle'),
            // The server's own sentence names the setting; it is English only.
            s.arabic || c.server?.message == null
                ? s.t('serverOffText')
                : '${c.server!.message} Ask the person who runs this ANUM '
                        'server to set it up.'
                    .trim()
          ),
        PushPhase.notAsked => (
            Icons.notifications_none,
            s.t('notAskedTitle'),
            s.t('notAskedText')
          ),
        PushPhase.blocked => (
            Icons.notifications_off_outlined,
            s.t('blockedTitle'),
            s.t('blockedText')
          ),
        PushPhase.on => (
            Icons.notifications_active_outlined,
            s.t('onTitle'),
            s.t('onText')
          ),
        PushPhase.signedOut => (
            Icons.notifications_none,
            s.t('checkingTitle'),
            s.t('signedOutText')
          ),
        PushPhase.error => (
            Icons.error_outline,
            s.t('errorTitle'),
            s.t('errorText')
          ),
      };

  Widget? _action(PushController c, PushStrings s) => switch (c.phase) {
        PushPhase.notAsked => FilledButton.icon(
            onPressed: c.busy ? null : c.enable,
            icon: const Icon(Icons.notifications_active_outlined),
            label: Text(s.t('turnOn'))),
        PushPhase.blocked => OutlinedButton.icon(
            onPressed: _openSettings,
            icon: const Icon(Icons.settings_outlined),
            label: Text(s.t('openSettings'))),
        PushPhase.error || PushPhase.offOnServer => OutlinedButton.icon(
            onPressed: c.busy ? null : c.refresh,
            icon: const Icon(Icons.refresh),
            label: Text(s.t('checkAgain'))),
        _ => null,
      };
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile(this.controller, this.device, this.strings);
  final PushController controller;
  final PushDevice device;
  final PushStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final mine = device.id == controller.thisDeviceId;
    final sent = device.lastSentAt;
    final name = device.titleIn(arabic: s.arabic);
    return ListTile(
        leading: Icon(device.platform == 'ios'
            ? Icons.phone_iphone
            : Icons.phone_android),
        title: Text(mine ? s.f('thisPhone', {'name': name}) : name),
        subtitle: Text(sent == null
            ? s.t('notNotified')
            : s.f('lastNotified', {
                'date': MaterialLocalizations.of(context)
                    .formatMediumDate(sent.toLocal())
              })),
        trailing: IconButton(
            tooltip: s.f('remove', {'name': name}),
            onPressed:
                controller.busy ? null : () => controller.removeDevice(device),
            icon: const Icon(Icons.delete_outline)));
  }
}
