import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/model_choice.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:anum_mobile/features/settings/settings_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _forbidden = ApiResponse(statusCode: 403, body: {
  'error': {
    'code': 'forbidden',
    'message': 'permission_denied: organization:manage',
  }
});

const _savedClaude = <String, Object?>{
  'provider': 'anthropic',
  'model': 'claude-sonnet-5-5',
  'base_url': 'https://api.anthropic.com',
  'fast_model': 'claude-haiku-5-5',
  'capable_model': 'claude-sonnet-5-5',
  'fast_effort': 'low',
  'capable_effort': null,
  'credential_configured': true,
  'credential_hint': '...mple',
  'updated_at': '2026-10-06T10:00:00Z',
};

class _ModelTransport implements ApiTransport {
  _ModelTransport(
      {this.reachable = true,
      this.saved,
      this.saveForbidden = false,
      this.testForbidden = false});
  final bool reachable;
  final Map<String, Object?>? saved;
  final bool saveForbidden;
  final bool testForbidden;
  final List<ApiRequest> requests = [];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/model-config') && request.method == 'PUT') {
      if (saveForbidden) return _forbidden;
      final body = request.body!;
      final kept = body['api_key'] == null && saved != null;
      return ApiResponse(statusCode: 200, body: {
        'provider': body['provider'],
        'model': body['model'],
        'base_url': body['base_url'] ?? 'https://api.anthropic.com',
        'fast_model': body['fast_model'],
        'capable_model': body['capable_model'],
        'fast_effort': body['fast_effort'],
        'capable_effort': body['capable_effort'],
        'credential_configured': body['api_key'] != null || kept,
        'credential_hint': kept ? saved!['credential_hint'] : null,
        'updated_at': '2026-10-06T10:00:00Z',
      });
    }
    if (path.endsWith('/model-config/test')) {
      if (testForbidden) return _forbidden;
      return reachable
          ? const ApiResponse(
              statusCode: 200,
              body: {'provider': 'ollama', 'model': 'llama3.2'})
          : const ApiResponse(statusCode: 502, body: {
              'detail': 'Could not reach Ollama at http://localhost:11434/v1.'
            });
    }
    if (path.endsWith('/notification-preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'task_completed': true,
        'approval_required': true,
        'run_failed': true,
        'automation_failed': true,
        'email_enabled': false,
        'desktop_enabled': true,
      });
    }
    if (path.endsWith('/model-config') && request.method == 'GET') {
      return saved != null
          ? ApiResponse(statusCode: 200, body: saved)
          : const ApiResponse(
              statusCode: 404,
              body: {'detail': 'Model configuration not found'});
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

  test('switching to Claude sends the key and routing, without a base URL',
      () async {
    final transport = _ModelTransport();
    final controller = await _controller(transport);

    final ok = await controller.changeModel(
      provider: 'anthropic',
      model: 'claude-sonnet-5-5',
      baseUrl: '',
      apiKey: 'sk-ant-example',
      routing: const ModelRouting(
          fastModel: 'claude-haiku-5-5',
          capableModel: 'claude-sonnet-5-5',
          fastEffort: 'low',
          capableEffort: ''),
    );

    expect(ok, isTrue);
    final put = transport.requests.firstWhere((r) => r.method == 'PUT');
    expect(put.body, {
      'provider': 'anthropic',
      'model': 'claude-sonnet-5-5',
      'api_key': 'sk-ant-example',
      'fast_model': 'claude-haiku-5-5',
      'capable_model': 'claude-sonnet-5-5',
      'fast_effort': 'low',
      'capable_effort': null,
    });
    expect(controller.model?.fastModel, 'claude-haiku-5-5');
    expect(controller.model?.capableModel, 'claude-sonnet-5-5');
    expect(controller.message,
        'Saved. claude-haiku-5-5 answers chat and voice, claude-sonnet-5-5 plans tasks.');
  });

  for (final size in const [Size(360, 640), Size(1024, 768)]) {
    testWidgets(
        'Settings connects Claude from the model sheet at '
        '${size.width.toInt()}x${size.height.toInt()}', (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final transport = _ModelTransport();
      final controller = await _controller(transport);
      await tester.pumpWidget(MaterialApp(
          theme: AnumTheme.dark(),
          home: SettingsScreen(controller: controller)));
      await tester.pumpAndSettle();

      await tester.ensureVisible(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<String>).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Anthropic (Claude)').last);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      await tester.enterText(
          find.widgetWithText(TextFormField, 'Anthropic API key'),
          'sk-ant-example');
      final save = find.text('Save and test');
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();

      final put = transport.requests.firstWhere((r) => r.method == 'PUT');
      expect(put.body!['provider'], 'anthropic');
      expect(put.body!['fast_model'], 'claude-haiku-5-5');
      expect(put.body!['capable_model'], 'claude-sonnet-5-5');
      expect(put.body!['api_key'], 'sk-ant-example');
      expect(find.text('Anthropic (Claude)'), findsOneWidget);
      expect(find.textContaining('Chat and voice: claude-haiku-5-5'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  test('a 403 on save stays in the model sheet and says who can do it',
      () async {
    final controller =
        await _controller(_ModelTransport(saveForbidden: true));
    await controller.load();

    final ok = await controller.changeModel(
        provider: 'ollama',
        model: 'llama3.2',
        baseUrl: 'http://localhost:11434/v1');

    expect(ok, isFalse);
    expect(controller.phase, SettingsPhase.ready);
    expect(controller.message, contains('Your role cannot change the model'));
    expect(controller.message, contains('owner role'));
  });

  test('a 403 on the card\'s connection test is shown in the card', () async {
    final controller = await _controller(
        _ModelTransport(saved: _savedClaude, testForbidden: true));
    await controller.load();

    final ok = await controller.testModel();

    expect(ok, isFalse);
    expect(controller.phase, SettingsPhase.ready);
    expect(controller.modelError,
        contains('Your role cannot test the model connection'));
  });

  test('the key field keeps the saved key only for the same address', () {
    final saved = ModelConfiguration.fromJson(_savedClaude);
    expect(savedKeyState('anthropic', 'https://api.anthropic.com', saved),
        SavedKeyState.keep);
    expect(savedKeyState('anthropic', '', saved), SavedKeyState.keep);
    expect(savedKeyState('anthropic', 'https://api.anthropic.com/v1/', saved),
        SavedKeyState.keep);
    expect(savedKeyState('anthropic', 'https://claude-proxy.example', saved),
        SavedKeyState.moved);
    expect(savedKeyState('openai_compatible', 'https://api.anthropic.com', saved),
        SavedKeyState.none);
    expect(validateApiKey('', SavedKeyState.keep), isNull);
    expect(validateApiKey('', SavedKeyState.moved), contains('base URL changed'));
    expect(validateApiKey('', SavedKeyState.none), 'Enter the API key');
    expect(validateApiKey('sk-ant-example', SavedKeyState.moved), isNull);
  });

  testWidgets(
      'a routing change saves from the sheet without re-entering the key',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final transport = _ModelTransport(saved: _savedClaude);
    final controller = await _controller(transport);
    await tester.pumpWidget(MaterialApp(
        theme: AnumTheme.dark(), home: SettingsScreen(controller: controller)));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Change model'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Change model'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Leave empty to keep the saved key (...mple)'),
        findsOneWidget);

    final save = find.text('Save and test');
    await tester.ensureVisible(save);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();

    final put = transport.requests.firstWhere((r) => r.method == 'PUT');
    expect(put.body!.containsKey('api_key'), isFalse);
    expect(put.body!['fast_model'], 'claude-haiku-5-5');
    expect(find.text('Save and test'), findsNothing); // the sheet closed
    expect(tester.takeException(), isNull);
  });

  testWidgets('a 403 when saving keeps Settings and explains it in the sheet',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final controller = await _controller(
        _ModelTransport(saved: _savedClaude, saveForbidden: true));
    await tester.pumpWidget(MaterialApp(
        theme: AnumTheme.dark(), home: SettingsScreen(controller: controller)));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Change model'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Change model'));
    await tester.pumpAndSettle();
    final save = find.text('Save and test');
    await tester.ensureVisible(save);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();

    expect(find.text('Save and test'), findsOneWidget); // still in the sheet
    expect(find.textContaining('Your role cannot change the model'),
        findsWidgets);
    expect(find.text('Permission required'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  test('other providers never send routing fields', () async {
    final transport = _ModelTransport();
    final controller = await _controller(transport);

    await controller.changeModel(
      provider: 'openai_compatible',
      model: 'gpt-4.1-mini',
      baseUrl: 'https://api.openai.com/v1',
      apiKey: 'sk-example',
      routing: const ModelRouting(fastModel: 'ignored'),
    );

    final put = transport.requests.firstWhere((r) => r.method == 'PUT');
    expect(put.body!.keys,
        unorderedEquals(['provider', 'model', 'base_url', 'api_key']));
  });

  test('a saved Claude config parses its routes; unset routes use the model',
      () {
    final config = ModelConfiguration.fromJson({
      'provider': 'anthropic',
      'model': 'claude-opus-5-5',
      'base_url': 'https://api.anthropic.com',
      'fast_model': null,
      'capable_model': null,
      'fast_effort': 'low',
      'credential_configured': true,
      'credential_hint': '...mple',
      'updated_at': '2026-10-06T10:00:00Z',
    });
    expect(config.fastModel, 'claude-opus-5-5');
    expect(config.capableModel, 'claude-opus-5-5');
    expect(config.routing.fastEffort, 'low');
    expect(config.routing.capableEffort, isNull);

    final ollama = ModelConfiguration.fromJson({
      'provider': 'ollama',
      'model': 'llama3.2',
      'base_url': 'http://localhost:11434/v1',
      'credential_configured': false,
      'updated_at': '2026-10-06T10:00:00Z',
    });
    expect(ollama.fastModel, 'llama3.2');
    expect(ollama.routing.fastModel, isNull);
  });
}
