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
}
