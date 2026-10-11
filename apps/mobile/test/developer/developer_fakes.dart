// An in-memory stand-in for `/api/v1/developer` (services/api/anum_api/
// developer.py) and the approvals route it hands the merge to.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/developer/developer_controller.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';

/// A plainly fake, low-entropy payload hash (gitleaks scans test fixtures).
const exampleHash = 'examplehashexamplehashexamplehashexamplehash';

const _stages = [
  ('queued', 'Queued'),
  ('coding', 'Coding'),
  ('pr_opened', 'Pull request opened'),
  ('ci', 'CI checks'),
  ('approval', 'Waiting for approval'),
  ('merged', 'Merged'),
];

List<JsonMap> timelineFor(String status) {
  final current = switch (status) {
    'queued' => 0,
    'coding' => 1,
    'ci_running' || 'ci_failed' => 3,
    'waiting_approval' => 4,
    'merged' => 5,
    _ => 1,
  };
  final failed = {'ci_failed', 'failed', 'cancelled'}.contains(status);
  return [
    for (final (i, (id, label)) in _stages.indexed)
      {
        'id': id,
        'label': label,
        'state': status == 'merged' || i < current
            ? 'done'
            : i == current
                ? (failed ? 'failed' : 'active')
                : 'pending',
      }
  ];
}

JsonMap approvalJson(String id, {String? hash = exampleHash, int pr = 42}) => {
      'id': id,
      'task_id': 'task_merge_$id',
      'action': 'developer.merge_pull_request',
      'reason': 'High-risk tool requires approval.',
      'risk_level': 'high',
      'status': 'pending',
      'created_at': '2026-10-11T10:05:00Z',
      'arguments': {
        'repository': 'example/anum',
        'pull_number': pr,
        'head_sha': 'abc123',
        'merge_method': 'squash',
      },
      'payload_hash': hash,
      'target': 'api.github.com',
      'expires_at': '2099-01-01T00:00:00Z',
    };

JsonMap requestJson(
  String id, {
  String text = 'Add a dark/light toggle to Settings',
  String status = 'coding',
  String source = 'text',
  int? pr,
  String? prTitle,
  JsonMap? approval,
  JsonMap? checks,
  String? failureReason,
  String? mergeError,
  String createdAt = '2026-10-11T10:00:00Z',
}) {
  final title = prTitle ?? text;
  return {
    'id': id,
    'created_by': 'user_test',
    'text': text,
    'source': source,
    'status': status,
    'allow_protected_paths': false,
    'branch': 'anum-dev/$id',
    'run_id': 7,
    'run_url': 'https://github.com/example/anum/actions/runs/7',
    'pr_number': pr,
    'pr_url': pr == null ? null : 'https://github.com/example/anum/pull/$pr',
    'pr_title': prTitle,
    'pr_draft': pr == null ? null : true,
    'head_sha': pr == null ? null : 'abc123',
    'checks': checks ??
        (pr == null
            ? null
            : {
                'total': 3,
                'passed': status == 'ci_failed' ? 2 : 3,
                'failed': status == 'ci_failed' ? 1 : 0,
                'pending': 0,
                'failing':
                    status == 'ci_failed' ? ['Web and contracts'] : <String>[],
              }),
    'failure_reason': failureReason,
    'merge_error': mergeError,
    'approval_id': approval?['id'],
    'merge_commit_sha': null,
    'created_at': createdAt,
    'updated_at': createdAt,
    'merged_at': null,
    'timeline': timelineFor(status),
    'summary': switch (status) {
      'queued' => 'Queued: waiting for the developer workflow to start.',
      'coding' => 'The developer is making the change and running the checks.',
      'ci_running' => 'Pull request opened: $title. CI is running.',
      'ci_failed' => 'CI failed on $title (Web and contracts).',
      'waiting_approval' =>
        'The change is ready for review: $title. Approve to merge.',
      'merged' => 'Merged: $title.',
      'cancelled' => 'Cancelled.',
      _ => failureReason ?? 'The developer run failed.',
    },
    'approval': approval,
  };
}

JsonMap setupJson(
        {bool configured = true,
        bool enabled = true,
        List<String> missing = const [],
        int today = 1,
        int max = 10}) =>
    {
      'configured': configured,
      'enabled_here': enabled,
      'repository': configured ? 'example/anum' : null,
      'workflow': 'anum-developer.yml',
      'base_branch': 'main',
      'missing': missing,
      'message': missing.isNotEmpty
          ? 'Developer mode needs these API settings: ${missing.join(', ')}.'
          : configured && enabled
              ? 'Ready: requests go to example/anum through anum-developer.yml.'
              : 'Developer mode is configured for another organization or workspace.',
      'max_requests_per_day': max,
      'requests_today': today,
    };

class FakeDeveloperApi implements ApiTransport {
  FakeDeveloperApi({
    List<JsonMap>? requests,
    JsonMap? setup,
    this.role = 'owner',
  })  : requests = requests ?? [],
        setup = setup ?? setupJson();

  final List<JsonMap> requests;
  JsonMap setup;
  String? role;
  final sent = <ApiRequest>[];

  /// Answer a route with this status instead: keys like `GET setup`,
  /// `GET list`, `POST create`, `POST cancel`, `POST merge`, `POST approve`,
  /// `GET role`, `GET get`.
  final failures = <String, (int, String)>{};

  /// While set, the request list waits for it (to see the loading state).
  Completer<void>? holdList;

  /// What the approve call does to the request (merged unless set).
  String approvedStatus = 'merged';
  int _ids = 0;

  Iterable<ApiRequest> calls(String method, String pathPart) =>
      sent.where((r) => r.method == method && r.uri.path.contains(pathPart));

  ApiResponse? _fail(String key) {
    final failure = failures[key];
    if (failure == null) return null;
    return ApiResponse(statusCode: failure.$1, body: {'detail': failure.$2});
  }

  JsonMap _find(String id) => requests.firstWhere((r) => r['id'] == id);

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    sent.add(request);
    final path = request.uri.path;
    final method = request.method;
    if (path == '/api/v1/workspace-memberships/current') {
      return _fail('GET role') ??
          ApiResponse(statusCode: 200, body: {
            'user_id': 'user_test',
            'workspace_id': 'workspace_test',
            'role': role,
            'active': role != null,
          });
    }
    if (path.startsWith('/api/v1/approvals/') && path.endsWith('/approve')) {
      final failed = _fail('POST approve');
      if (failed != null) return failed;
      final id = path.split('/')[4];
      final target =
          requests.firstWhere((r) => r['approval_id'] == id, orElse: () => {});
      if (target.isNotEmpty) {
        final next = requestJson(target['id']! as String,
            text: target['text']! as String,
            status: approvedStatus,
            pr: target['pr_number'] as int?,
            prTitle: target['pr_title'] as String?,
            mergeError: approvedStatus == 'merged'
                ? null
                : 'A check failed after you approved.');
        requests[requests.indexOf(target)] = next;
      }
      return ApiResponse(statusCode: 200, body: {
        'approval': {...approvalJson(id), 'status': 'approved'},
        'run': {'result': 'Merged.'},
      });
    }
    const base = '/api/v1/developer';
    if (path == '$base/setup') {
      if (role != 'owner') {
        return const ApiResponse(statusCode: 403, body: {
          'error': {'code': 'forbidden', 'message': 'Forbidden'}
        });
      }
      return _fail('GET setup') ?? ApiResponse(statusCode: 200, body: setup);
    }
    if (path == '$base/requests' && method == 'GET') {
      if (role != 'owner') {
        return const ApiResponse(
            statusCode: 403, body: {'detail': 'Forbidden'});
      }
      await holdList?.future;
      final failed = _fail('GET list');
      if (failed != null) return failed;
      final params = request.uri.queryParameters;
      final limit = int.parse(params['limit'] ?? '20');
      final q = params['q']?.toLowerCase();
      var items = [
        for (final r in requests)
          if (q == null || (r['text']! as String).toLowerCase().contains(q)) r
      ];
      final before = params['before'];
      if (before != null) {
        final index = items.indexWhere((r) => r['id'] == before);
        items = items.sublist(index + 1);
      }
      return ApiResponse(
          statusCode: 200, body: {'data': items.take(limit).toList()});
    }
    if (path == '$base/requests' && method == 'POST') {
      final failed = _fail('POST create');
      if (failed != null) return failed;
      final body = request.body!;
      final created = requestJson('dev_new_${++_ids}',
          text: body['text']! as String,
          status: 'queued',
          source: body['source']! as String);
      requests.insert(0, created);
      return ApiResponse(statusCode: 201, body: created);
    }
    final rest = path.substring('$base/requests/'.length).split('/');
    final id = Uri.decodeComponent(rest.first);
    if (rest.length == 1) {
      return _fail('GET get') ?? ApiResponse(statusCode: 200, body: _find(id));
    }
    if (rest[1] == 'cancel') {
      final failed = _fail('POST cancel');
      if (failed != null) return failed;
      final current = _find(id);
      final next = requestJson(id,
          text: current['text']! as String,
          status: 'cancelled',
          pr: current['pr_number'] as int?);
      requests[requests.indexOf(current)] = next;
      return ApiResponse(statusCode: 200, body: next);
    }
    if (rest[1] == 'merge-approval') {
      final failed = _fail('POST merge');
      if (failed != null) return failed;
      final current = _find(id);
      final approval =
          approvalJson('appr_$id', pr: (current['pr_number'] as int?) ?? 0);
      final next = {
        ...current,
        'approval_id': approval['id'],
        'approval': approval
      };
      requests[requests.indexOf(current)] = next;
      return ApiResponse(
          statusCode: 200, body: {'request': next, 'approval': approval});
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<AnumApiClient> developerApi(ApiTransport transport,
    {MemorySessionStore? sessions}) async {
  final store = sessions ?? MemorySessionStore();
  if (sessions == null) {
    await store.write(LocalSession(
      accessToken: 'example',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_test',
          roles: ['owner']),
    ));
  }
  return AnumApiClient(
      baseUri: Uri.parse('https://anum.test'),
      transport: transport,
      sessions: store);
}

Future<DeveloperController> buildDeveloper(FakeDeveloperApi api) async =>
    DeveloperController(ApiDeveloperRepository(await developerApi(api)));

/// Requests newest first, as the API lists them.
List<JsonMap> manyRequests(int count, {String prefix = 'dev_'}) => [
      for (var i = count; i > 0; i--)
        requestJson('$prefix$i',
            text: 'Request number $i: change something in the app',
            status: 'merged',
            pr: 100 + i,
            createdAt:
                '2026-10-${(i % 28 + 1).toString().padLeft(2, '0')}T10:00:00Z'),
    ];
