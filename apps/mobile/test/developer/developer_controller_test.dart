// The Developer screen's controller against the in-memory API: role,
// setup, paging, search, create, cancel and the payload-hash merge.
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/developer/developer_controller.dart';
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

void main() {
  test('an owner loads the setup and the first page', () async {
    final api = FakeDeveloperApi(requests: manyRequests(25));
    final c = await buildDeveloper(api);
    expect(await c.ensureOwner(), isTrue);
    expect(await c.load(), isTrue);
    expect(c.phase, DevPhase.ready);
    expect(c.setup!.ready, isTrue);
    expect(c.items, hasLength(20));
    expect(c.hasMore, isTrue);
    expect(
        api.calls('GET', '/developer/requests').single.uri.query, 'limit=20');

    await c.loadMore();
    expect(c.items, hasLength(25));
    expect(c.hasMore, isFalse);
    expect(
        api
            .calls('GET', '/developer/requests')
            .last
            .uri
            .queryParameters['before'],
        'dev_6');
  });

  test('a member is not an owner and never calls the developer API', () async {
    final api = FakeDeveloperApi(role: 'member');
    final c = await buildDeveloper(api);
    expect(await c.ensureOwner(), isFalse);
    expect(c.ownerState, OwnerState.notOwner);
    expect(c.role, 'member');
    c.setActive(true);
    await pumpEventQueue();
    expect(api.calls('GET', '/developer/'), isEmpty);
    c.setActive(false);
  });

  test('a role check that fails says so and can be retried', () async {
    final api = FakeDeveloperApi()..failures['GET role'] = (503, 'Down');
    final c = await buildDeveloper(api);
    expect(await c.ensureOwner(), isFalse);
    expect(c.ownerState, OwnerState.error);
    expect(c.roleError, contains('Down'));
    api.failures.clear();
    expect(await c.reloadRole(), isTrue);
  });

  test('a 403 from the API is the owner-only state', () async {
    final api = FakeDeveloperApi()..failures['GET setup'] = (403, 'Forbidden');
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    expect(c.phase, DevPhase.forbidden);
    expect(c.loadError, developerOwnerOnly);
  });

  test('a failed first load is an error; a failed refresh keeps the list',
      () async {
    final api = FakeDeveloperApi(requests: [requestJson('dev_1')])
      ..failures['GET list'] = (500, 'Database down');
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    expect(await c.load(), isFalse);
    expect(c.phase, DevPhase.error);
    expect(c.loadError, contains('Database down'));

    api.failures.clear();
    await c.load();
    api.failures['GET list'] = (500, 'Database down');
    await c.load();
    expect(c.phase, DevPhase.ready);
    expect(c.items, hasLength(1));
    expect(c.noticeFailed, isTrue);
    expect(c.notice, contains('could not be refreshed'));
  });

  test('search sends q and a failed search keeps the previous one', () async {
    final api = FakeDeveloperApi(requests: [
      requestJson('dev_2', text: 'Add search to Files'),
      requestJson('dev_1',
          text: 'Dark mode toggle in Settings', status: 'merged'),
    ]);
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    await c.search('dark');
    expect(c.query, 'dark');
    expect(c.items.single.id, 'dev_1');
    expect(
        api.calls('GET', '/developer/requests').last.uri.queryParameters['q'],
        'dark');

    api.failures['GET list'] = (502, 'GitHub unavailable');
    await c.search('files');
    expect(c.query, 'dark');
    api.failures.clear();
    await c.clearSearch();
    expect(c.items, hasLength(2));
  });

  test('create validates locally, then sends text and the allow flag',
      () async {
    final api = FakeDeveloperApi(setup: setupJson(today: 2));
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    expect(await c.create('too short'), isFalse);
    expect(c.notice, contains('at least 10 characters'));
    expect(await c.create('Rotate the API keys used by the CI'), isFalse);
    expect(c.notice, contains('Allow changes'));
    expect(api.calls('POST', '/developer/requests'), isEmpty);

    expect(await c.create('  Add a dark/light toggle to Settings '), isTrue);
    final body = api.calls('POST', '/developer/requests').single.body!;
    expect(body, {
      'text': 'Add a dark/light toggle to Settings',
      'source': 'text',
      'allow_protected_paths': false,
    });
    expect(c.items.first.status, DevStatus.queued);
    expect(c.setup!.requestsToday, 3);
    expect(c.activeExists, isTrue);
    expect(c.noticeFailed, isFalse);
  });

  test('a used-up budget and a busy developer say what to do', () async {
    final api = FakeDeveloperApi()
      ..failures['POST create'] = (402, 'Budget used up until 1 November.');
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    expect(await c.create('Add a dark/light toggle to Settings'), isFalse);
    expect(c.budget, 'Budget used up until 1 November.');

    api.failures['POST create'] = (
      409,
      'A developer request is already queued or coding. Wait for it to open '
          'its pull request, or cancel it.'
    );
    expect(await c.create('Add a dark/light toggle to Settings'), isFalse);
    expect(c.notice, startsWith('Not sent: A developer request is already'));
  });

  test('cancel and check now replace the request', () async {
    final api = FakeDeveloperApi(requests: [requestJson('dev_1')]);
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    await c.refresh(c.items.single);
    expect(api.calls('GET', '/developer/requests/dev_1').single.uri.query,
        'refresh=true');
    await c.cancel(c.items.single);
    expect(c.items.single.status, DevStatus.cancelled);
    expect(api.calls('POST', '/dev_1/cancel'), hasLength(1));

    api.failures['POST cancel'] = (502, 'GitHub refused the cancel.');
    await c.cancel(c.items.single);
    expect(c.notice, 'GitHub refused the cancel.');
    expect(c.noticeFailed, isTrue);
  });

  test('approve & merge opens the approval; confirm sends its payload hash',
      () async {
    final api = FakeDeveloperApi(requests: [
      requestJson('dev_1',
          status: 'waiting_approval', pr: 42, prTitle: 'Dark mode toggle')
    ]);
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    final item = c.items.single;
    expect(c.reviewFor(item), isNull);

    await c.review(item);
    expect(api.calls('POST', '/approve'), isEmpty,
        reason: 'opening the review never approves');
    final approval = c.reviewFor(c.items.single)!;
    expect(approval.payloadHash, exampleHash);

    c.dismissReview(c.items.single);
    expect(c.reviewFor(c.items.single), isNull);
    await c.review(c.items.single);

    await c.merge(c.items.single, c.reviewFor(c.items.single)!);
    final approve = api.calls('POST', '/approve').single;
    expect(approve.uri.path, '/api/v1/approvals/appr_dev_1/approve');
    expect(approve.body, {'payload_hash': exampleHash});
    expect(c.items.single.status, DevStatus.merged);
    expect(c.notice, 'Merged pull request #42.');
    expect(c.noticeFailed, isFalse);
  });

  test('a refused merge says why and merges nothing', () async {
    final api = FakeDeveloperApi(requests: [
      requestJson('dev_1',
          status: 'waiting_approval', pr: 42, approval: approvalJson('appr_1'))
    ])
      ..failures['POST approve'] =
          (403, 'The two-person rule needs another owner to approve this.');
    final c = await buildDeveloper(api);
    await c.ensureOwner();
    await c.load();
    final item = c.items.single;
    expect(c.reviewFor(item)!.id, 'appr_1',
        reason: 'the pending approval from the list is shown');
    await c.merge(item, c.reviewFor(item)!);
    expect(c.notice, startsWith('Merge refused: The two-person rule'));
    expect(c.items.single.status, DevStatus.waitingApproval);

    api.failures.clear();
    api.approvedStatus = 'waiting_approval';
    await c.merge(c.items.single, c.reviewFor(c.items.single)!);
    expect(c.notice, 'A check failed after you approved.');
    expect(c.noticeFailed, isTrue);
  });

  test('a workspace switch forgets the role and the list', () async {
    final api = FakeDeveloperApi(requests: [requestJson('dev_1')]);
    final sessions = MemorySessionStore();
    final client = await developerApi(api);
    await sessions.write((await client.sessions.read())!);
    final c = DeveloperController(
        ApiDeveloperRepository(await developerApi(api, sessions: sessions)));
    await c.ensureOwner();
    await c.load();
    expect(c.items, hasLength(1));

    final current = (await sessions.read())!;
    await sessions.write(LocalSession(
        accessToken: current.accessToken,
        tokenType: current.tokenType,
        expiresAt: current.expiresAt,
        context: const TenantContext(
            tenantId: 'tenant_test',
            workspaceId: 'workspace_other',
            userId: 'user_test',
            roles: ['member'])));
    api.role = 'member';
    expect(await c.ensureOwner(), isFalse);
    expect(c.items, isEmpty);
    expect(c.phase, DevPhase.initial);
  });
}
