import '../lib/data/api_client.dart';
import '../lib/data/api_models.dart';
import '../lib/data/session_store.dart';
import '../lib/features/auth/auth_repository.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeTransport implements ApiTransport {
  final List<ApiRequest> requests = [];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    if (request.uri.path.endsWith('/auth/local/session') && request.method == 'POST') {
      return ApiResponse(statusCode: 201, body: {
        'access_token': 'anum_local_test',
        'token_type': 'bearer',
        'expires_at': DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
        'context': {
          'tenant_id': 'tenant_test',
          'workspace_id': 'workspace_test',
          'user_id': 'user_test',
          'roles': ['owner'],
        },
      });
    }
    if (request.uri.path.endsWith('/onboarding')) {
      return const ApiResponse(statusCode: 200, body: {
        'complete': true,
        'model_configured': false,
        'tenant': {'id': 'tenant_test', 'name': 'Test Org'},
        'workspace': {
          'id': 'workspace_test',
          'tenant_id': 'tenant_test',
          'name': 'Test Workspace',
        },
        'membership': {
          'tenant_id': 'tenant_test',
          'workspace_id': 'workspace_test',
          'user_id': 'user_test',
          'role': 'owner',
          'active': true,
        },
      });
    }
    return const ApiResponse(statusCode: 204);
  }
}

void main() {
 test('local session supports onboarding and sign out', () async {
  final sessions = MemorySessionStore();
  final transport = FakeTransport();
  final api = AnumApiClient(
    baseUri: Uri.parse('http://127.0.0.1:8000'),
    transport: transport,
    sessions: sessions,
  );
  final auth = AuthRepository(api: api, sessions: sessions);

  final session = await auth.startLocalSession(
    tenantId: 'tenant_test',
    workspaceId: 'workspace_test',
    userId: 'user_test',
  );
  expect(session.context.roles.single, 'owner');
  expect((await sessions.read())?.accessToken, 'anum_local_test');

  final onboarding = await auth.completeOnboarding(
    organizationName: 'Test Org',
    workspaceName: 'Test Workspace',
  );
  expect(onboarding.complete, isTrue);
  expect(onboarding.membership?.role, 'owner');
  expect(transport.requests.last.headers['authorization'], 'Bearer anum_local_test');

  await auth.signOut();
  expect(await sessions.read(), isNull);
 });
}
