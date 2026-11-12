import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/session_store.dart';
import '../action_browser/action_browser_data.dart';
import '../action_browser/action_browser_screen.dart';
import '../action_browser/credentials_screen.dart';
import '../admin/owned_controller.dart';
import '../executive/executive_data.dart';
import '../executive/executive_screen.dart';
import '../ibkr/ibkr_data.dart';
import '../ibkr/ibkr_screen.dart';
import '../selling/selling_data.dart';
import '../selling/selling_screen.dart';

/// The owner desk screens on the phone (docs/mobile-features.md#owner-desk),
/// each a named route opened from the Menu with a fresh controller that
/// lives as long as its page:
///
/// * `/executive`: the Executive desk (docs/ANUM_MINE_EXECUTIVE_PA.md#mobile)
/// * `/ready-to-sell`: the platform owner's checklist (docs/selling-readiness.md#mobile)
/// * `/finance/ibkr`: IBKR trading (docs/finance-live-trading.md#on-the-phone)
/// * `/action-browser`: browser runs, their steps, approvals, the live
///   picture and "Needs you" input, and the saved logins and cards
///   (docs/action-browser.md#on-the-phone)
///
/// The headquarters (the 3D office) stays on the web; docs/mobile.md says why.
class OwnerDeskRoutes {
  OwnerDeskRoutes(
      {required this.api, required this.sessions, this.switchWorkspace});

  final AnumApiClient api;
  final SessionStore sessions;

  /// Switches the app to another workspace, as Settings does.
  final Future<void> Function(String workspaceId)? switchWorkspace;

  static const executive = '/executive';
  static const readyToSell = '/ready-to-sell';
  static const ibkr = '/finance/ibkr';
  static const actionBrowser = '/action-browser';
  static const all = [executive, readyToSell, ibkr, actionBrowser];

  /// The route for [settings], wrapped by [guard] (the signed-in check), or
  /// null when the name is not one of these.
  Route<void>? route(
      RouteSettings settings, Widget Function(Widget child) guard) {
    final Widget Function(BuildContext)? build = switch (settings.name) {
      executive => (context) => OwnedController<ExecutiveController>(
            create: () => ExecutiveController(
                ApiExecutiveRepository(api, sessions),
                switchWorkspace: switchWorkspace),
            builder: (_, c) => ExecutiveScreen(
              controller: c,
              onOpenTasks: () => Navigator.of(context).pushNamed('/tasks'),
              onOpenSettings: () => Navigator.of(context).pushNamed('/profile'),
            ),
          ),
      readyToSell => (_) => OwnedController<SellingController>(
            create: () => SellingController(ApiSellingRepository(api)),
            builder: (_, c) => SellingScreen(controller: c),
          ),
      ibkr => (_) => OwnedController<IbkrController>(
            create: () => IbkrController(ApiIbkrRepository(api)),
            builder: (_, c) => IbkrScreen(controller: c),
          ),
      actionBrowser => (_) => OwnedController<ActionBrowserController>(
            create: () =>
                ActionBrowserController(ApiActionBrowserRepository(api)),
            builder: (_, c) => ActionBrowserScreen(
                controller: c,
                openRun: openBrowserRun,
                openVault: openBrowserVault),
          ),
      _ => null,
    };
    if (build == null) return null;
    return MaterialPageRoute<void>(
        settings: settings, builder: (context) => guard(build(context)));
  }

  /// Opens the saved logins and cards (owners) above the list.
  Future<void> openBrowserVault(BuildContext context) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => OwnedController<CredentialsController>(
                create: () =>
                    CredentialsController(ApiActionBrowserRepository(api)),
                builder: (_, c) => CredentialsScreen(controller: c),
              )));

  /// Opens one browser run above the list.
  Future<void> openBrowserRun(BuildContext context, BrowserRun run) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => OwnedController<BrowserRunController>(
                create: () => BrowserRunController(
                    ApiActionBrowserRepository(api), run.id),
                builder: (_, c) => BrowserRunScreen(controller: c),
              )));
}
