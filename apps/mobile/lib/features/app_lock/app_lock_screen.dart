import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/brand.dart';
import 'app_lock_controller.dart';
import 'device_auth.dart';

/// Makes the app lock reachable from any screen below it. The app puts one
/// at its root; without one (most widget tests) gates let everything through.
class AppLockAccess extends InheritedNotifier<AppLockController> {
  const AppLockAccess({
    required AppLockController controller,
    required super.child,
    super.key,
  }) : super(notifier: controller);

  static AppLockController? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppLockAccess>()?.notifier;
}

/// Stands in front of a Finance or Books screen: while the lock blocks
/// [area] the child is neither painted nor reachable (it stays mounted, so a
/// form keeps what was typed), and the app's [AppLockCover] shows the lock
/// screen above everything, dialogs included.
class AppLockGate extends StatefulWidget {
  const AppLockGate({required this.area, required this.child, super.key});
  final AppLockArea area;
  final Widget child;

  @override
  State<AppLockGate> createState() => _AppLockGateState();
}

class _AppLockGateState extends State<AppLockGate> {
  AppLockController? _controller;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final next = AppLockAccess.maybeOf(context);
    if (identical(next, _controller)) return;
    _controller?.sensitiveScreenGone();
    _controller = next;
    if (widget.area != AppLockArea.app) next?.sensitiveScreenShown();
  }

  @override
  void dispose() {
    if (widget.area != AppLockArea.app) _controller?.sensitiveScreenGone();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final blocked = _controller?.blocks(widget.area) ?? false;
    return _Hidden(hidden: blocked, child: widget.child);
  }
}

/// Keeps [child] mounted but unpainted, unfocusable and silent to screen
/// readers and touch while [hidden].
class _Hidden extends StatelessWidget {
  const _Hidden({required this.hidden, required this.child});
  final bool hidden;
  final Widget child;

  @override
  Widget build(BuildContext context) => Offstage(
        offstage: hidden,
        // Animations keep running: "Go back" pops the hidden routes, and a
        // route only leaves the tree when its transition has finished.
        child: ExcludeFocus(excluding: hidden, child: child),
      );
}

/// The app-wide cover (in `MaterialApp.builder`, above the navigator): while
/// the lock covers the window, everything below is hidden and the lock
/// screen is the only thing on screen. [active] is false while nobody is
/// signed in, so the sign-in screens are never covered.
class AppLockCover extends StatelessWidget {
  const AppLockCover({
    required this.controller,
    required this.active,
    required this.onSignOut,
    required this.child,
    this.onLeave,
    super.key,
  });

  final AppLockController controller;
  final bool active;

  /// Signs the whole app out (the way out for someone who cannot unlock).
  final Future<void> Function() onSignOut;

  /// Leaves the locked area for the rest of the app (only offered when the
  /// lock covers Finance and Books, not the whole app).
  final VoidCallback? onLeave;
  final Widget child;

  @override
  Widget build(BuildContext context) => AppLockAccess(
        controller: controller,
        child: ListenableBuilder(
          listenable: controller,
          builder: (context, _) {
            final covered = active && controller.coversWindow;
            if (covered) {
              // Close the keyboard of whatever is now hidden.
              FocusManager.instance.primaryFocus?.unfocus();
            }
            return Stack(
              fit: StackFit.expand,
              children: [
                _Hidden(hidden: covered, child: child),
                if (covered)
                  controller.restored
                      ? AppLockScreen(
                          controller: controller,
                          onSignOut: onSignOut,
                          onLeave: controller.settings.scope ==
                                  AppLockScope.sensitive
                              ? onLeave
                              : null,
                        )
                      // Start-up, before the settings are read: a plain
                      // cover, so nothing flashes.
                      : const ColoredBox(
                          key: Key('app-lock-starting'),
                          color: Color(0xFF0B0F14)),
              ],
            );
          },
        ),
      );
}

/// The lock screen: the app's name, why it is locked, **Unlock**, and
/// **Sign out** for someone who cannot unlock. The check starts by itself
/// once when the screen appears; a failed or cancelled check stays here and
/// says what happened.
class AppLockScreen extends StatefulWidget {
  const AppLockScreen({
    required this.controller,
    required this.onSignOut,
    this.onLeave,
    this.autoPrompt = true,
    super.key,
  });

  final AppLockController controller;
  final Future<void> Function() onSignOut;
  final VoidCallback? onLeave;
  final bool autoPrompt;

  @override
  State<AppLockScreen> createState() => _AppLockScreenState();
}

class _AppLockScreenState extends State<AppLockScreen> {
  final _signingOut = ValueNotifier(false);

  @override
  void initState() {
    super.initState();
    if (widget.autoPrompt) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !widget.controller.authenticating) _unlock();
      });
    }
  }

  @override
  void dispose() {
    _signingOut.dispose();
    super.dispose();
  }

  Future<void> _unlock() => widget.controller.unlock(
      reason: AnumLocalizations.maybeOf(context).t('lock.reason.unlock'));

  Future<void> _confirmSignOut(BuildContext context) async {
    final l = AnumLocalizations.maybeOf(context);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('lock.signOut.title')),
        content: Text(l.t('lock.signOut.body')),
        actions: [
          TextButton(
              key: const Key('app-lock-sign-out-cancel'),
              onPressed: () => Navigator.pop(context, false),
              child: Text(l.t('lock.signOut.cancel'))),
          FilledButton(
              key: const Key('app-lock-sign-out-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(l.t('lock.screen.signOut'))),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    _signingOut.value = true;
    try {
      await widget.onSignOut();
    } finally {
      if (mounted) _signingOut.value = false;
    }
  }

  // The cover sits above the app's navigator, so the lock screen brings its
  // own for the sign-out question.
  @override
  Widget build(BuildContext context) => ScaffoldMessenger(
          // Its own messenger: notices meant for the app behind (a finished
          // command, a push) are not shown on the lock screen.
          child: HeroControllerScope.none(
        child: Navigator(
          onGenerateRoute: (_) => MaterialPageRoute<void>(
            builder: (context) => PopScope(
              canPop: false,
              child: ListenableBuilder(
                listenable: Listenable.merge([widget.controller, _signingOut]),
                builder: (context, _) => _page(context),
              ),
            ),
          ),
        ),
      ));

  Widget _page(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final theme = Theme.of(context);
    final controller = widget.controller;
    final failure = controller.lastFailure;
    final busy = controller.authenticating || _signingOut.value;
    final wholeApp = controller.settings.scope == AppLockScope.wholeApp;
    return Scaffold(
      key: const Key('app-lock-screen'),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AnumSpacing.lg),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Center(child: AnumWordmark(markSize: 40)),
                  const SizedBox(height: AnumSpacing.xl),
                  Icon(Icons.lock_outline,
                      size: 40, color: theme.colorScheme.primary),
                  const SizedBox(height: AnumSpacing.md),
                  Semantics(
                    header: true,
                    child: Text(
                      l.t(wholeApp
                          ? 'lock.screen.title'
                          : 'lock.screen.titleArea'),
                      textAlign: TextAlign.center,
                      style: theme.textTheme.headlineSmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(l.t('lock.screen.body'), textAlign: TextAlign.center),
                  if (failure != null && !controller.authenticating) ...[
                    const SizedBox(height: AnumSpacing.md),
                    Semantics(
                      liveRegion: true,
                      child: Text(
                        l.t('lock.failed.${failure.name}'),
                        key: const Key('app-lock-failure'),
                        textAlign: TextAlign.center,
                        style: TextStyle(color: theme.colorScheme.error),
                      ),
                    ),
                  ],
                  const SizedBox(height: AnumSpacing.lg),
                  FilledButton.icon(
                    key: const Key('app-lock-unlock'),
                    onPressed: busy ? null : _unlock,
                    icon: const Icon(Icons.fingerprint),
                    label: Text(l.t(controller.authenticating
                        ? 'lock.screen.checking'
                        : 'lock.screen.unlock')),
                  ),
                  if (widget.onLeave != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    OutlinedButton(
                      key: const Key('app-lock-back'),
                      onPressed: busy ? null : widget.onLeave,
                      child: Text(l.t('lock.screen.back')),
                    ),
                  ],
                  const SizedBox(height: AnumSpacing.xs),
                  TextButton(
                    key: const Key('app-lock-sign-out'),
                    onPressed: busy ? null : () => _confirmSignOut(context),
                    child: Text(l.t('lock.screen.signOut')),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// The sentence for a check that did not pass, for snackbars outside the
/// lock screen.
String appLockFailureText(AnumLocalizations l, DeviceAuthResult result) =>
    l.t('lock.failed.${result.name}');
