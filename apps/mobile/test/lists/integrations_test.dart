// Integrations (mockup 12): what the integrations API reports, the
// All / Connected / Available tabs, and what Connect does for each kind:
// an owner turns a service on, tests it and stores its key; a mailbox is
// connected in the inbox; an unconfigured service names the operator setting.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/integrations/integrations_controller.dart';
import 'package:anum_mobile/features/integrations/integrations_models.dart';
import 'package:anum_mobile/features/integrations/integrations_repository.dart';
import 'package:anum_mobile/features/integrations/integrations_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'lists_harness.dart';

JsonMap _integration(String id, String name, String kind,
        {String status = 'connected',
        bool enabled = true,
        bool available = true,
        bool acceptsKey = false,
        String? env}) =>
    {
      'id': id,
      'name': name,
      'kind': kind,
      'status': status,
      'endpoint': 'https://$id.example',
      'detail': '$name answered',
      'enabled': enabled,
      'available': available,
      'credentials': {
        'configured': true,
        'source': 'environment',
        'scopes': <String>[]
      },
      'accepts_workspace_credential': acceptsKey,
      'environment_variable': env,
      if (acceptsKey) 'workspace_credential': {'configured': false},
    };

class _Api {
  _Api({this.role = 'owner'});
  String role;
  final items = <JsonMap>[
    _integration('postgresql', 'PostgreSQL', 'database'),
    _integration('email-gmail', 'Gmail', 'email',
        status: 'configured', enabled: true),
    _integration('external-webhook', 'External REST webhook', 'tool',
        status: 'disabled', enabled: false, acceptsKey: true),
    _integration('searxng', 'SearXNG web search', 'search',
        status: 'disabled', available: false, env: 'ANUM_SEARXNG_URL'),
  ];
  int listStatus = 200;

  ApiResponse handle(ApiRequest r) {
    final path = r.uri.path;
    if (path == '/api/v1/workspace-memberships/current') {
      return ApiResponse(
          statusCode: 200, body: {'user_id': 'user_me', 'role': role});
    }
    if (path == '/api/v1/integrations') {
      if (listStatus != 200) {
        return ApiResponse(
            statusCode: listStatus, body: {'detail': 'Integrations offline'});
      }
      return ApiResponse(statusCode: 200, body: {'data': items});
    }
    final id = r.uri.pathSegments[3];
    final item = items.firstWhere((i) => i['id'] == id);
    if (path.endsWith('/configuration') && r.method == 'GET') {
      return ApiResponse(
          statusCode: 200,
          body: {'enabled': item['enabled'], 'endpoint': 'note'});
    }
    if (path.endsWith('/configuration')) {
      item['enabled'] = r.body!['enabled'];
      item['status'] = r.body!['enabled'] == true ? 'connected' : 'disabled';
      return ApiResponse(statusCode: 200, body: {...r.body!, 'id': id});
    }
    if (path.endsWith('/test')) {
      return ApiResponse(statusCode: 200, body: item);
    }
    if (path.endsWith('/credential') && r.method == 'PUT') {
      item['workspace_credential'] = {'configured': true, 'hint': '…abcd'};
      return ApiResponse(
          statusCode: 200, body: {'integration_id': id, 'configured': true});
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<(IntegrationsController, ScriptedTransport)> _controller(
    _Api api) async {
  final transport = ScriptedTransport(api.handle);
  return (
    IntegrationsController(
        ApiIntegrationsRepository(await scriptedClient(transport))),
    transport
  );
}

void main() {
  test('models read the API and decide what Connect does', () {
    final list = [
      for (final j in _Api().items) Integration.fromJson(JsonReader(j))
    ];
    expect(list[0].connected, isTrue);
    expect(
        integrationAction(list[0], owner: false), IntegrationAction.connected);
    expect(integrationAction(list[1], owner: true),
        IntegrationAction.connectMailbox);
    expect(integrationAction(list[2], owner: true), IntegrationAction.connect);
    expect(
        integrationAction(list[2], owner: false), IntegrationAction.ownerOnly);
    expect(integrationAction(list[3], owner: true), IntegrationAction.operator);
    expect(integrationSecretProblem('short'), 'short');
    expect(integrationSecretProblem('has spaces in it'), 'characters');
    expect(integrationSecretProblem('plainlyfakevalue'), isNull);
  });

  testWidgets('tabs count; an owner turns a tool on and stores its key',
      (tester) async {
    final api = _Api();
    final (c, transport) = await _controller(api);
    await pumpList(tester, IntegrationsScreen(controller: c));
    expect(find.text('All 4'), findsOneWidget);
    expect(find.text('Connected 1'), findsOneWidget);
    expect(find.text('Connected'), findsOneWidget); // PostgreSQL's pill
    expect(find.text('Server setup'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('int-tab-available')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('int-tab-available')));
    await tester.pumpAndSettle();
    expect(find.text('PostgreSQL'), findsNothing);
    await expectTapTargets(tester);

    await tester.tap(find.byKey(const Key('int-connect-external-webhook')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('int-enabled')));
    await tester.pumpAndSettle();
    expect(transport.requests.where((r) => r.method == 'PUT').single.body,
        {'enabled': true, 'endpoint': 'note'});
    expect(find.text('Turned on.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('int-secret')), 'too short');
    await tester.ensureVisible(find.byKey(const Key('int-secret-save')));
    await tester.tap(find.byKey(const Key('int-secret-save')));
    await tester.pumpAndSettle();
    expect(find.text('The key needs at least 12 characters.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('int-secret')), 'plainlyfakevalue');
    await tester.tap(find.byKey(const Key('int-secret-save')));
    await tester.pumpAndSettle();
    expect(find.text('Key saved.'), findsOneWidget);
    expect(find.textContaining('ending in …abcd'), findsOneWidget);
  });

  testWidgets('a mailbox opens the inbox; the operator setting is named',
      (tester) async {
    final (c, _) = await _controller(_Api());
    var inbox = 0;
    await pumpList(
        tester, IntegrationsScreen(controller: c, onOpenInbox: () => inbox++));
    await tester.tap(find.byKey(const Key('int-connect-email-gmail')));
    await tester.pumpAndSettle();
    expect(inbox, 1);
    await tester.tap(find.byKey(const Key('int-row-searxng')));
    await tester.pumpAndSettle();
    expect(find.textContaining('ANUM_SEARXNG_URL'), findsWidgets);
  });

  testWidgets('members are told only owners can change integrations',
      (tester) async {
    final (c, _) = await _controller(_Api(role: 'member'));
    await pumpList(tester, IntegrationsScreen(controller: c));
    expect(find.text('Owner turns on'), findsOneWidget);
    await tester.tap(find.byKey(const Key('int-row-external-webhook')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('int-owner-only')), findsOneWidget);
    expect(find.byKey(const Key('int-enabled')), findsNothing);
  });

  testWidgets('a failed load says what failed and retries', (tester) async {
    final api = _Api()..listStatus = 503;
    final (c, _) = await _controller(api);
    await pumpList(tester, IntegrationsScreen(controller: c));
    expect(find.byKey(const Key('int-error')), findsOneWidget);
    expect(find.text('Integrations offline'), findsOneWidget);
    api.listStatus = 200;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('PostgreSQL'), findsOneWidget);
  });

  for (final width in listWidths) {
    testWidgets('lays out at $width wide, 200% text, English and Arabic',
        (tester) async {
      for (final locale in ['en', 'ar']) {
        final (c, _) = await _controller(_Api());
        await pumpList(tester, IntegrationsScreen(controller: c),
            width: width, text: 2, locale: locale, light: locale == 'ar');
        expect(tester.takeException(), isNull);
        await tester.tap(find.byKey(const Key('int-row-external-webhook')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tapAt(const Offset(5, 5));
        await tester.pumpAndSettle();
      }
      expect(find.text('التكاملات'), findsOneWidget);
    });
  }
}
