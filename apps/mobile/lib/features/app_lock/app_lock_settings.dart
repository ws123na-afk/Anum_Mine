import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'app_lock_controller.dart';
import 'app_lock_screen.dart';

/// Settings, Security: the **App lock** switch, **Lock after** and **What to
/// lock** (docs/mobile.md#app-lock). Turning it on needs one successful
/// check; turning it off or changing it needs one too. Hidden when the app
/// has no lock controller (widget tests of other settings).
class AppLockSettingsTiles extends StatelessWidget {
  const AppLockSettingsTiles({this.controller, super.key});

  /// Null uses the app's (`AppLockAccess`).
  final AppLockController? controller;

  @override
  Widget build(BuildContext context) {
    final controller = this.controller ?? AppLockAccess.maybeOf(context);
    if (controller == null) return const SizedBox.shrink();
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => _Tiles(controller),
    );
  }
}

class _Tiles extends StatelessWidget {
  const _Tiles(this.controller);
  final AppLockController controller;

  void _say(BuildContext context, String text) {
    ScaffoldMessenger.maybeOf(context)
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _report(BuildContext context, Future<AppLockChange> change,
      {required String saved}) async {
    final l = AnumLocalizations.maybeOf(context);
    final result = await change;
    if (!context.mounted) return;
    switch (result) {
      case AppLockChange.saved:
        _say(context, saved);
      case AppLockChange.notConfirmed:
        final failure = controller.lastFailure;
        _say(
            context,
            failure == null
                ? l.t('lock.notConfirmed')
                : '${l.t('lock.notConfirmed')} '
                    '${appLockFailureText(l, failure).replaceFirst(RegExp(r'^[^:]*: '), '')}');
      case AppLockChange.failed:
        _say(context, l.t('lock.saveFailed'));
      case AppLockChange.unsupported:
        _say(context, l.t('lock.unsupported'));
      case AppLockChange.notEnrolled:
        await showDialog<void>(
            context: context, builder: (_) => const _NotEnrolledDialog());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final settings = controller.settings;
    final busy = controller.authenticating || !controller.restored;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      SwitchListTile(
        key: const Key('app-lock-switch'),
        secondary: const Icon(Icons.lock_outline),
        title: Text(l.t('lock.title')),
        subtitle: Text(
            l.t(settings.enabled ? 'lock.subtitleOn' : 'lock.subtitleOff')),
        value: settings.enabled,
        onChanged: busy
            ? null
            : (on) => _report(
                context,
                on
                    ? controller.enable(reason: l.t('lock.reason.enable'))
                    : controller.disable(reason: l.t('lock.reason.change')),
                saved: l.t(on ? 'lock.saved.on' : 'lock.saved.off')),
      ),
      if (settings.enabled) ...[
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsetsDirectional.fromSTEB(
              AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xs),
          child: DropdownButtonFormField<int>(
            key: const Key('app-lock-after'),
            isExpanded: true,
            initialValue: settings.lockAfter.inSeconds,
            decoration: InputDecoration(
                labelText: l.t('lock.after'),
                helperText: l.t('lock.afterHelp'),
                helperMaxLines: 4),
            items: [
              for (final choice in AppLockSettings.lockAfterChoices)
                DropdownMenuItem(
                    value: choice.inSeconds,
                    child: Text(l.t('lock.after.${choice.inSeconds}'))),
            ],
            onChanged: busy
                ? null
                : (seconds) {
                    if (seconds == null ||
                        seconds == settings.lockAfter.inSeconds) {
                      return;
                    }
                    _report(
                        context,
                        controller.setLockAfter(Duration(seconds: seconds),
                            reason: l.t('lock.reason.change')),
                        saved: l.t('lock.saved'));
                  },
          ),
        ),
        Padding(
          padding: const EdgeInsetsDirectional.fromSTEB(
              AnumSpacing.md, AnumSpacing.xs, AnumSpacing.md, AnumSpacing.sm),
          child: DropdownButtonFormField<AppLockScope>(
            key: const Key('app-lock-scope'),
            isExpanded: true,
            initialValue: settings.scope,
            decoration: InputDecoration(labelText: l.t('lock.scope')),
            items: [
              for (final scope in AppLockScope.values)
                DropdownMenuItem(
                    value: scope,
                    child: Text(l.t('lock.scope.${scope.name}'),
                        overflow: TextOverflow.ellipsis)),
            ],
            onChanged: busy
                ? null
                : (scope) {
                    if (scope == null || scope == settings.scope) return;
                    _report(
                        context,
                        controller.setScope(scope,
                            reason: l.t('lock.reason.change')),
                        saved: l.t('lock.saved'));
                  },
          ),
        ),
      ],
    ]);
  }
}

/// No screen lock on the phone: what that means and how to set one.
class _NotEnrolledDialog extends StatelessWidget {
  const _NotEnrolledDialog();

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return AlertDialog(
      key: const Key('app-lock-not-enrolled'),
      title: Text(l.t('lock.notEnrolled.title')),
      content: Text(l.t('lock.notEnrolled.body')),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('lock.close'))),
        FilledButton(
            key: const Key('app-lock-how-to'),
            onPressed: () {
              Navigator.pop(context);
              showDialog<void>(
                  context: context, builder: (_) => const ScreenLockHelp());
            },
            child: Text(l.t('lock.notEnrolled.how'))),
      ],
    );
  }
}

/// The steps to set a screen lock on Android and on iPhone or iPad. The app
/// cannot open the phone's security settings itself, so it says where they
/// are.
class ScreenLockHelp extends StatelessWidget {
  const ScreenLockHelp({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return AlertDialog(
      key: const Key('app-lock-help'),
      title: Text(l.t('lock.howTo.title')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l.t('lock.howTo.android')),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('lock.howTo.ios')),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('lock.howTo.then')),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('lock.close'))),
      ],
    );
  }
}
