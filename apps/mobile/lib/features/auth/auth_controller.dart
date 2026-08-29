import 'package:flutter/foundation.dart';

import '../../data/api_models.dart';
import 'auth_repository.dart';

enum AuthPhase { restoring, signedOut, onboarding, modelSetup, ready, busy, error }

class AuthController extends ChangeNotifier {
  AuthController(this.repository);

  final AuthRepository repository;
  AuthPhase phase = AuthPhase.restoring;
  String? message;

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
      message = error.toString();
    }
    notifyListeners();
  }

  Future<void> signIn(String tenant, String workspace, String user) async {
    await _run(() async {
      await repository.startLocalSession(
        tenantId: tenant.trim(),
        workspaceId: workspace.trim(),
        userId: user.trim(),
      );
      final status = await repository.onboardingStatus();
      phase = status.complete
          ? (status.modelConfigured ? AuthPhase.ready : AuthPhase.modelSetup)
          : AuthPhase.onboarding;
    });
  }

  Future<void> createWorkspace(String organization, String workspace) async {
    await _run(() async {
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
    await _run(() async {
      await repository.configureModel(
        provider: provider,
        model: model,
        baseUrl: baseUrl,
        apiKey: apiKey,
      );
      phase = AuthPhase.ready;
    });
  }

  Future<void> signOut() async {
    await repository.signOut();
    phase = AuthPhase.signedOut;
    notifyListeners();
  }

  Future<void> _run(Future<void> Function() action) async {
    phase = AuthPhase.busy;
    message = null;
    notifyListeners();
    try {
      await action();
    } on Object catch (error) {
      phase = AuthPhase.error;
      message = error.toString();
    }
    notifyListeners();
  }
}
