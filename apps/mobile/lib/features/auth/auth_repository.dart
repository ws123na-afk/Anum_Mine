import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';

class AuthRepository {
  const AuthRepository({required this.api, required this.sessions});

  final AnumApiClient api;
  final SessionStore sessions;

  Future<LocalSession> startLocalSession({
    required String tenantId,
    required String workspaceId,
    required String userId,
  }) async {
    final json = await api.request(
      'POST',
      '/api/v1/auth/local/session',
      authenticated: false,
      body: {
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'user_id': userId,
      },
    );
    final session = LocalSession.fromJson(json);
    await sessions.write(session);
    return session;
  }

  Future<LocalSession?> restoreSession() async {
    final session = await sessions.read();
    if (session == null) return null;
    if (session.isExpired) {
      await sessions.clear();
      return null;
    }
    return session;
  }

  Future<void> signOut() async {
    try {
      await api.request('DELETE', '/api/v1/auth/local/session');
    } finally {
      await sessions.clear();
    }
  }

  Future<OnboardingStatus> onboardingStatus() async =>
      OnboardingStatus.fromJson(await api.request('GET', '/api/v1/onboarding'));

  Future<OnboardingStatus> completeOnboarding({
    required String organizationName,
    required String workspaceName,
  }) async =>
      OnboardingStatus.fromJson(await api.request(
        'PUT',
        '/api/v1/onboarding',
        body: {
          'organization_name': organizationName,
          'workspace_name': workspaceName,
        },
      ));

  Future<ModelConfiguration> configureModel({
    required String provider,
    required String model,
    required String baseUrl,
    String? apiKey,
  }) async =>
      ModelConfiguration.fromJson(await api.request(
        'PUT',
        '/api/v1/model-config',
        body: {
          'provider': provider,
          'model': model,
          'base_url': baseUrl,
          if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
        },
      ));

  Future<ModelConfiguration> modelConfiguration() async =>
      ModelConfiguration.fromJson(await api.request('GET', '/api/v1/model-config'));

  Future<NotificationPreferences> notificationPreferences() async =>
      NotificationPreferences.fromJson(
        await api.request('GET', '/api/v1/notification-preferences'),
      );

  Future<NotificationPreferences> updateNotificationPreferences(
    NotificationPreferences value,
  ) async =>
      NotificationPreferences.fromJson(await api.request(
        'PUT',
        '/api/v1/notification-preferences',
        body: value.toJson(),
      ));
}
