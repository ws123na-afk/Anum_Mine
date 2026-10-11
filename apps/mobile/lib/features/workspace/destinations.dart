/// Positions of the workspace's destinations, in navigation order. The
/// Assistant chat comes first, as on the web (docs/conversations.md).
abstract final class WorkspaceDestination {
  static const assistant = 0;
  static const home = 1;
  static const tasks = 2;
  static const voice = 3;
  static const approvals = 4;
  static const automations = 5;
  static const resources = 6;

  /// Owners only (docs/developer-mode.md): hidden from the navigation for
  /// everyone else; opened by link it explains who can use it. Last, so the
  /// positions above stay the same whether or not it is shown.
  static const developer = 7;
}
