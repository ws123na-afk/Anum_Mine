import 'package:flutter/foundation.dart';

import '../../data/api_models.dart';
import 'auth_repository.dart';

enum AuthPhase { restoring, signedOut, onboarding, modelSetup, ready, busy, error }
enum AuthIssue { none, offline, permission, invalidSession, validation, unknown }

class AuthController extends ChangeNotifier {
  AuthController(this.repository);

  final AuthRepository repository;
  AuthPhase phase = AuthPhase.restoring;
  String? message;
  AuthIssue issue = AuthIssue.none;
  AuthPhase _retryPhase = AuthPhase.signedOut;

  Future<void> restore() async {
    try {
      final session = await repository.restoreSession();
      if (session == null) {
        phase = AuthPhase.signedOut;
      } else {
        final status = await repository.onboardingStatus();
        phase = status.complete
            ? (status.modelConfigured ? AuthPhase.ready : AuthPhase.modelSetup)
            : AuthPhase.onboarding;
      }
    } on Object catch (error) {
      phase = AuthPhase.error;
      _setError(error);
    }
    notifyListeners();
  }

  Future<void> signIn(String tenant, String workspace, String user, {String? password}) async {
    if ([tenant, workspace, user].any((value) => value.trim().length < 3)) {
      phase = AuthPhase.error;
      issue = AuthIssue.validation;
      message = 'Enter valid organization, workspace, and user identifiers.';
      _retryPhase = AuthPhase.signedOut;
      notifyListeners();
      return;
    }
    await _run(AuthPhase.signedOut, () async {
      await repository.startLocalSession(
        tenantId: tenant.trim(),
        workspaceId: workspace.trim(),
        userId: user.trim(),
        password: password?.trim(),
      );
      final status = await repository.onboardingStatus();
      phase = status.complete
          ? (status.modelConfigured ? AuthPhase.ready : AuthPhase.modelSetup)
          : AuthPhase.onboarding;
    });
  }

  Future<void> completeExternalSignIn(Future<Object?> Function() action) async {
    await _run(AuthPhase.signedOut, () async {
      await action();
      final status = await repository.onboardingStatus();
      phase = status.complete
          ? (status.modelConfigured ? AuthPhase.ready : AuthPhase.modelSetup)
          : AuthPhase.onboarding;
    });
  }

  Future<void> createWorkspace(String organization, String workspace) async {
    if (organization.trim().isEmpty || workspace.trim().isEmpty) {
      phase = AuthPhase.error;
      issue = AuthIssue.validation;
      message = 'Organization and workspace names are required.';
      _retryPhase = AuthPhase.onboarding;
      notifyListeners();
      return;
    }
    await _run(AuthPhase.onboarding, () async {
      await repository.completeOnboarding(
        organizationName: organization.trim(),
        workspaceName: workspace.trim(),
      );
      phase = AuthPhase.modelSetup;
    });
  }

  Future<void> connectModel({
    required String provider,
    required String model,
    required String baseUrl,
    required String apiKey,
  }) async {
    await _run(AuthPhase.modelSetup, () async {
      await repository.configureModel(
        provider: provider,
        model: model,
        baseUrl: baseUrl,
        apiKey: apiKey,
      );
      await repository.testModelConnection();
      phase = AuthPhase.ready;
    });
  }

  Future<void> signOut() async {
    await repository.signOut();
    phase = AuthPhase.signedOut;
    notifyListeners();
  }

  void retry() {
    message = null;
    issue = AuthIssue.none;
    phase = _retryPhase;
    notifyListeners();
  }

  Future<void> _run(AuthPhase retryPhase, Future<void> Function() action) async {
    _retryPhase = retryPhase;
    phase = AuthPhase.busy;
    message = null;
    issue = AuthIssue.none;
    notifyListeners();
    try {
      await action();
    } on Object catch (error) {
      phase = AuthPhase.error;
      _setError(error);
    }
    notifyListeners();
  }

  void _setError(Object error) {
    final text = error.toString();
    message = text.replaceFirst(RegExp(r'^ApiException\(\d+,\s*'), '').replaceFirst(RegExp(r'\)$'), '');
    issue = text.contains('(401,')
        ? AuthIssue.invalidSession
        : text.contains('(403,')
            ? AuthIssue.permission
            : text.toLowerCase().contains('socket') || text.toLowerCase().contains('network')
                ? AuthIssue.offline
                : AuthIssue.unknown;
  }
}
