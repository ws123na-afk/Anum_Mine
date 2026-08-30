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
    String? password,
  }) async {
    final json = await api.request(
      'POST',
      '/api/v1/auth/local/session',
      authenticated: false,
      body: {
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'user_id': userId,
        if (password != null && password.isNotEmpty) 'password': password,
      },
    );
    final session = LocalSession.fromJson(json);
    await sessions.write(session);
    return session;
  }

  Future<String> requestOtp({required String tenantId,required String workspaceId,required String userId}) async {
    final value=await api.request('POST','/api/v1/auth/local/otp/request',authenticated:false,body:{'tenant_id':tenantId,'workspace_id':workspaceId,'user_id':userId});
    return value['challenge_id']! as String;
  }

  Future<LocalSession> verifyOtp({required String challengeId,required String code}) => _acceptSession(api.request('POST','/api/v1/auth/local/otp/verify',authenticated:false,body:{'challenge_id':challengeId,'code':code}));

  Future<String> requestPasswordReset({required String tenantId,required String workspaceId,required String userId}) async {
    final value=await api.request('POST','/api/v1/auth/local/password/forgot',authenticated:false,body:{'tenant_id':tenantId,'workspace_id':workspaceId,'user_id':userId});
    return value['challenge_id']! as String;
  }

  Future<LocalSession> resetPassword({required String challengeId,required String token,required String newPassword}) => _acceptSession(api.request('POST','/api/v1/auth/local/password/reset',authenticated:false,body:{'challenge_id':challengeId,'token':token,'new_password':newPassword}));

  Future<LocalSession> switchWorkspace(String workspaceId) => _acceptSession(api.request('POST','/api/v1/auth/local/workspace/switch',body:{'workspace_id':workspaceId}));

  Future<LocalSession> _acceptSession(Future<JsonMap> response) async { final session=LocalSession.fromJson(await response);await sessions.write(session);return session; }

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

  Future<void> testModelConnection() async {
    await api.request('POST', '/api/v1/model-config/test');
  }

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
