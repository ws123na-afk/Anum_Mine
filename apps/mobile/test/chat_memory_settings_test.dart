import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/settings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// "Remember things from my chats" in Settings (docs/memory.md#from-conversations).
class _Transport implements ApiTransport {
  _Transport({this.failSave = false, this.state});
  final bool failSave;
  final String? state;
  bool remember = true;
  int chatMemories = 2;
  final List<ApiRequest> requests = [];

  JsonMap get _prefs => {
        'remember_conversations': remember,
        'updated_at': null,
        'conversation_memory': state ?? (remember ? 'on' : 'off_by_user'),
      };

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/me/preferences')) {
      if (request.method == 'PUT') {
        if (failSave) {
          return const ApiResponse(
              statusCode: 409,
              body: {'detail': 'Complete onboarding for this workspace first'});
        }
        remember = request.body!['remember_conversations'] as bool;
      }
      return ApiResponse(statusCode: 200, body: _prefs);
    }
    if (path.endsWith('/memories') && request.method == 'DELETE') {
      final deleted = chatMemories;
      chatMemories = 0;
      return ApiResponse(statusCode: 200, body: {'deleted': deleted});
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
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<SettingsController> _controller(_Transport transport) async {
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
      'roles': ['member'],
    },
  }));
  final api = AnumApiClient(
      baseUri: Uri.parse('http://127.0.0.1:8000'),
      transport: transport,
      sessions: sessions);
  return SettingsController(AuthRepository(api: api, sessions: sessions));
}

Future<void> _open(WidgetTester tester, SettingsController controller) async {
  await tester.binding.setSurfaceSize(const Size(390, 844));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(), home: SettingsScreen(controller: controller)));
  await tester.pumpAndSettle();
  await tester.scrollUntilVisible(
      find.text('Delete memories from my chats'), 200,
      scrollable: find.byType(Scrollable).first);
  await tester.pumpAndSettle();
}

Finder get _switch =>
    find.widgetWithText(SwitchListTile, 'Remember things from my chats');

void main() {
  testWidgets('the switch saves at once and explains what it means',
      (tester) async {
    final transport = _Transport();
    final controller = await _controller(transport);
    await _open(tester, controller);

    expect(tester.widget<SwitchListTile>(_switch).value, isTrue);
    expect(find.textContaining('Only you can see them.'), findsOneWidget);

    await tester.tap(_switch);
    await tester.pumpAndSettle();

    final put = transport.requests.lastWhere((r) => r.method == 'PUT');
    expect(put.uri.path, '/api/v1/me/preferences');
    expect(put.body, {'remember_conversations': false});
    expect(tester.widget<SwitchListTile>(_switch).value, isFalse);
    expect(find.text('Status: Off'), findsOneWidget);
    expect(
        find.text(
            'Saved. The Assistant will no longer remember or use things from your chats.'),
        findsOneWidget);
  });

  testWidgets('a failed save keeps the previous choice and says why',
      (tester) async {
    final controller = await _controller(_Transport(failSave: true));
    await _open(tester, controller);

    await tester.tap(_switch);
    await tester.pumpAndSettle();

    expect(tester.widget<SwitchListTile>(_switch).value, isTrue);
    expect(
        find.text(
            'Your choice was not saved. Complete onboarding for this workspace first. Try again.'),
        findsOneWidget);
  });

  testWidgets('memories from chats are deleted after confirmation',
      (tester) async {
    final transport = _Transport();
    final controller = await _controller(transport);
    await _open(tester, controller);

    await tester.tap(find.text('Delete memories from my chats'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(transport.requests.where((r) => r.method == 'DELETE'), isEmpty);

    await tester.tap(find.text('Delete memories from my chats'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete all'));
    await tester.pumpAndSettle();

    final delete = transport.requests.singleWhere((r) => r.method == 'DELETE');
    expect(delete.uri.path, '/api/v1/memories');
    expect(delete.uri.queryParameters, {'source_type': 'conversation'});
    expect(find.text('Deleted 2 memories from your chats.'), findsOneWidget);
  });

  testWidgets('an organization policy that keeps it off is explained',
      (tester) async {
    final controller =
        await _controller(_Transport(state: 'off_for_organization'));
    await _open(tester, controller);
    expect(find.text('Status: Off for your organization'), findsOneWidget);
    expect(find.textContaining('An organization administrator can allow it'),
        findsOneWidget);
  });

  test('the preference reads the API state', () {
    final pref = ChatMemoryPreference.fromJson({
      'remember_conversations': false,
      'conversation_memory': 'off_by_user'
    });
    expect(pref.rememberConversations, isFalse);
    expect(pref.badge, 'Off');
    expect(pref.explanation, contains('stay until you delete them'));
  });
}
