import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:flutter_test/flutter_test.dart';

class _ModelTransport implements ApiTransport {
  _ModelTransport({this.reachable = true});
  final bool reachable;
  final List<ApiRequest> requests = [];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/model-config') && request.method == 'PUT') {
      final body = request.body!;
      return ApiResponse(statusCode: 200, body: {
        'provider': body['provider'],
        'model': body['model'],
        'base_url': body['base_url'],
        'credential_configured': false,
        'updated_at': '2026-10-06T10:00:00Z',
      });
    }
    if (path.endsWith('/model-config/test')) {
      return reachable
          ? const ApiResponse(
              statusCode: 200,
              body: {'provider': 'ollama', 'model': 'llama3.2'})
          : const ApiResponse(statusCode: 502, body: {
              'detail': 'Could not reach Ollama at http://localhost:11434/v1.'
            });
    }
    return const ApiResponse(statusCode: 204);
  }
}

Future<SettingsController> _controller(_ModelTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_test',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'roles': ['owner'],
    },
  }));
  final api = AnumApiClient(
    baseUri: Uri.parse('http://127.0.0.1:8000'),
    transport: transport,
    sessions: sessions,
  );
  return SettingsController(AuthRepository(api: api, sessions: sessions));
}

void main() {
  test('switching to Ollama saves without a key and tests the connection',
      () async {
    final transport = _ModelTransport();
    final controller = await _controller(transport);

    final ok = await controller.changeModel(
      provider: 'ollama',
      model: 'llama3.2',
      baseUrl: 'http://localhost:11434/v1',
    );

    expect(ok, isTrue);
    expect(controller.phase, SettingsPhase.ready);
    expect(controller.model?.provider, 'ollama');
    final put = transport.requests.firstWhere((r) => r.method == 'PUT');
    expect(put.body!.containsKey('api_key'), isFalse);
    expect(transport.requests.last.uri.path, endsWith('/model-config/test'));
  });

  test('an unreachable model keeps Settings usable and explains why', () async {
    final controller = await _controller(_ModelTransport(reachable: false));

    final ok = await controller.changeModel(
      provider: 'ollama',
      model: 'llama3.2',
      baseUrl: 'http://localhost:11434/v1',
    );

    expect(ok, isFalse);
    expect(controller.phase, SettingsPhase.ready);
    expect(controller.message, contains('Ollama'));
  });
}
