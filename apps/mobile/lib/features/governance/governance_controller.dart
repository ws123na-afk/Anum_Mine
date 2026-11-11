import '../../data/readable_error.dart';
import 'package:flutter/foundation.dart';
import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'governance_models.dart';
import 'governance_repository.dart';

enum GovernancePhase { initial, loading, ready, empty, error, offline }

class GovernanceController extends ChangeNotifier {
  GovernanceController(this.repository);
  final GovernanceRepository repository;
  GovernancePhase phase = GovernancePhase.initial;
  GovernanceSnapshot? snapshot;
  String? message;
  bool mutating = false;

  /// Forgets the audit, policy and member data (sign-out).
  void reset() {
    phase = GovernancePhase.initial;
    snapshot = null;
    message = null;
    mutating = false;
    notifyListeners();
  }

  Future<void> load() async {
    phase = GovernancePhase.loading;
    message = null;
    notifyListeners();
    try {
      snapshot = await repository.load();
      phase = GovernancePhase.ready;
    } on GovernanceOfflineException catch (e) {
      phase = GovernancePhase.offline;
      message = e.message;
    } catch (e) {
      phase = GovernancePhase.error;
      message = readableError(e);
    }
    notifyListeners();
  }

  Future<void> baseline(String name) =>
      _act(() => repository.createBaselinePolicy(name));
  Future<void> setPolicyActive(String id, {required bool active}) =>
      _act(() => repository.setPolicyActive(id, active: active));

  /// Publishes a pack from the editor; null when done, else the sentence.
  Future<String?> publish(JsonMap body) => _quiet(() async {
        final pack = await repository.publishPolicyPack(body);
        lastPublished = pack;
        return pack;
      });

  /// The pack the editor published last, for its confirmation.
  PolicyPack? lastPublished;

  /// Deletes an archived version; null when done, else the sentence.
  Future<String?> deletePack(PolicyPack pack) =>
      _quiet(() => repository.deletePolicyPack(pack.id));

  /// Activates or archives one version; null when done, else the sentence.
  Future<String?> setPackActive(PolicyPack pack, {required bool active}) =>
      _quiet(() => repository.setPolicyActive(pack.id, active: active));

  /// Runs a change that reports its refusal to the caller (a dialog or a
  /// sheet) instead of replacing the screen with an error.
  Future<String?> _quiet(Future<Object?> Function() action) async {
    mutating = true;
    notifyListeners();
    try {
      await action();
      await load();
      return null;
    } on ApiException catch (e) {
      return e.readable;
    } on Object catch (e) {
      return readableError(e);
    } finally {
      mutating = false;
      notifyListeners();
    }
  }

  Future<void> install(String id) => _act(() => repository.installPackage(id));
  Future<void> uninstall(String id) =>
      _act(() => repository.uninstallPackage(id));
  Future<void> updateTarget(RoutingTarget value) =>
      _act(() => repository.updateTarget(value));
  Future<void> probeTarget(String id) => _act(() => repository.probeTarget(id));
  Future<void> exportAudit(String format) =>
      _act(() => repository.exportAudit(format), reload: false);
  Future<void> _act(Future<Object?> Function() action,
      {bool reload = true}) async {
    mutating = true;
    notifyListeners();
    try {
      await action();
      if (reload) await load();
    } on ApiException catch (e) {
      // A refused change (403) keeps the data on screen with the API's sentence.
      message = e.statusCode == 403 ? 'Not allowed: ${e.message}' : e.message;
      phase = GovernancePhase.error;
    } catch (e) {
      message = readableError(e);
      phase = GovernancePhase.error;
    } finally {
      mutating = false;
      notifyListeners();
    }
  }
}
