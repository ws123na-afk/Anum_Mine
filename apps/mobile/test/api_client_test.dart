import '../lib/data/api_client.dart';
import '../lib/data/api_models.dart';
import '../lib/data/session_store.dart';

class NeverTransport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) {
    throw StateError('expired sessions must not reach the transport');
  }
}

Future<void> main() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
    accessToken: 'expired',
    tokenType: 'bearer',
    expiresAt: DateTime.now().toUtc().subtract(const Duration(minutes: 1)),
    context: const TenantContext(
      tenantId: 'tenant_test',
      workspaceId: 'workspace_test',
      userId: 'user_test',
      roles: ['owner'],
    ),
  ));
  final api = AnumApiClient(
    baseUri: Uri.parse('http://127.0.0.1:8000'),
    transport: NeverTransport(),
    sessions: sessions,
  );

  try {
    await api.request('GET', '/api/v1/onboarding');
    assert(false, 'request should reject an expired session');
  } on ApiException catch (error) {
    assert(error.statusCode == 401);
  }
  assert(await sessions.read() == null);
}
