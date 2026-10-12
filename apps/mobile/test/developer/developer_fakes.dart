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

JsonMap projectJson(
  String id,
  String name, {
  String repository = 'example/anum',
  String description = '',
  bool enabled = true,
  bool token = true,
  String tokenSource = 'project',
  List<String> protectedPaths = const [],
  JsonMap? lastCheck,
  int? installPr,
}) {
  final problems = [
    if (!enabled)
      'The project is turned off. Turn it on to send requests to it.',
    if (!token)
      'No GitHub token: add a fine-grained token for this repository.',
    if (lastCheck != null && lastCheck['ok'] != true) lastCheck['message'],
  ];
  return {
    'id': id,
    'name': name,
    'github_owner': repository.split('/').first,
    'github_repo': repository.split('/').last,
    'repository': repository,
    'default_branch': 'main',
    'description': description,
    'enabled': enabled,
    'protected_paths': protectedPaths,
    'workflow_file': 'anum-developer.yml',
    'token_source': tokenSource,
    'token_configured': token,
    'token_updated_at': null,
    'last_check': lastCheck,
    'install_pr_number': installPr,
    'install_pr_url': installPr == null
        ? null
        : 'https://github.com/$repository/pull/$installPr',
    'ready': problems.isEmpty,
    'problems': problems,
    'created_by': 'user_test',
    'created_at': '2026-10-11T09:00:00Z',
    'updated_at': '2026-10-11T09:00:00Z',
  };
}

JsonMap anumProject() =>
    projectJson('devproj_anum', 'ANUM', description: 'The ANUM agent platform');
JsonMap tyreProject() => projectJson('devproj_tyre', 'TyrePulse',
    repository: 'example/tyrepulse',
    description: 'Tyre pressure monitoring for fleets');

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
  String? projectId = 'devproj_anum',
  String? projectName = 'ANUM',
  String? repository = 'example/anum',
}) {
  final title = prTitle ?? text;
  return {
    'id': id,
    'created_by': 'user_test',
    'text': text,
    'source': source,
    'status': status,
    'allow_protected_paths': false,
    'project_id': projectId,
    'project_name': projectName,
    'repository': repository,
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
    List<JsonMap>? projects,
    this.role = 'owner',
  })  : requests = requests ?? [],
        projects = projects ?? [anumProject()],
        setup = setup ?? setupJson();

  final List<JsonMap> requests;
  final List<JsonMap> projects;
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
    if (path.startsWith('$base/projects')) return _project(request);
    if (path == '$base/resolve') {
      final failed = _fail('POST resolve');
      if (failed != null) return failed;
      final text = request.body!['text']! as String;
      final sendable = [
        for (final p in projects)
          if (p['enabled'] == true && p['token_configured'] == true) p
      ];
      JsonMap ref(JsonMap p) => {
            'id': p['id'],
            'name': p['name'],
            'repository': p['repository'],
            'description': p['description'],
          };
      for (final p in sendable) {
        final suffix = ' in ${(p['name']! as String).toLowerCase()}';
        if (text.toLowerCase().endsWith(suffix)) {
          return ApiResponse(statusCode: 200, body: {
            'status': 'resolved',
            'text': text.substring(0, text.length - suffix.length),
            'project': ref(p),
            'candidates': <JsonMap>[],
            'matched_by': 'name',
          });
        }
      }
      return ApiResponse(statusCode: 200, body: {
        'status': sendable.isEmpty
            ? 'none'
            : sendable.length == 1
                ? 'resolved'
                : 'ambiguous',
        'text': text,
        'project': sendable.length == 1 ? ref(sendable.single) : null,
        'candidates': sendable.length > 1 ? sendable.map(ref).toList() : [],
        'matched_by': sendable.length == 1 ? 'only_project' : null,
      });
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
      final project = projects.firstWhere((p) => p['id'] == body['project_id'],
          orElse: () => projects.first);
      final created = requestJson('dev_new_${++_ids}',
          text: body['text']! as String,
          status: 'queued',
          source: body['source']! as String,
          projectId: project['id'] as String?,
          projectName: project['name'] as String?,
          repository: project['repository'] as String?);
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

  /// `/api/v1/developer/projects[/{id}[/test|/install-workflow]]`. Failure
  /// keys: `GET projects`, `POST project`, `PATCH project`, `DELETE project`,
  /// `POST test`, `POST install`.
  ApiResponse _project(ApiRequest request) {
    final method = request.method;
    final rest = request.uri.path
        .substring('/api/v1/developer/projects'.length)
        .split('/')
        .where((part) => part.isNotEmpty)
        .toList();
    if (rest.isEmpty && method == 'GET') {
      return _fail('GET projects') ??
          ApiResponse(statusCode: 200, body: {'data': projects});
    }
    if (rest.isEmpty && method == 'POST') {
      final failed = _fail('POST project');
      if (failed != null) return failed;
      final body = request.body!;
      final name = body['name']! as String;
      if (projects.any(
          (p) => (p['name']! as String).toLowerCase() == name.toLowerCase())) {
        return const ApiResponse(statusCode: 409, body: {
          'detail': 'Another project already has this name. Choose another '
              'name.'
        });
      }
      final created = projectJson('devproj_${body['github_repo']}', name,
          repository: '${body['github_owner']}/${body['github_repo']}',
          description: body['description'] as String? ?? '',
          protectedPaths: [
            for (final path in body['protected_paths']! as List) path as String
          ],
          token: body['token'] != null,
          tokenSource: body['token_source'] as String? ?? 'project');
      projects.add(created);
      return ApiResponse(statusCode: 201, body: created);
    }
    final index = projects.indexWhere((p) => p['id'] == rest.first);
    if (index < 0) {
      return const ApiResponse(
          statusCode: 404, body: {'detail': 'Project not found'});
    }
    final current = projects[index];
    if (rest.length == 2 && rest[1] == 'test') {
      final failed = _fail('POST test');
      if (failed != null) return failed;
      final next = projectJson(
          current['id']! as String, current['name']! as String,
          repository: current['repository']! as String,
          lastCheck: {
            'ok': false,
            'checked_at': '2026-10-11T10:00:00Z',
            'repository_reachable': true,
            'default_branch_exists': true,
            'workflow_present': false,
            'actions_readable': false,
            'message': '.github/workflows/anum-developer.yml is not on main '
                'yet. Use Install workflow, merge that pull request in '
                'GitHub, then test again.',
          });
      projects[index] = next;
      return ApiResponse(statusCode: 200, body: next);
    }
    if (rest.length == 2 && rest[1] == 'install-workflow') {
      final failed = _fail('POST install');
      if (failed != null) return failed;
      final next = {
        ...current,
        'install_pr_number': 51,
        'install_pr_url': 'https://github.com/${current['repository']}/pull/51',
      };
      projects[index] = next;
      return ApiResponse(statusCode: 200, body: {
        'project': next,
        'pull_request_number': 51,
        'pull_request_url': next['install_pr_url'],
        'created': true,
      });
    }
    if (method == 'PATCH') {
      final failed = _fail('PATCH project');
      if (failed != null) return failed;
      final body = request.body!;
      final enabled = body['enabled'] as bool? ?? current['enabled']! as bool;
      final next = {
        ...current,
        ...body,
        'enabled': enabled,
        'ready': enabled && current['token_configured'] == true,
        'problems': [
          if (!enabled)
            'The project is turned off. Turn it on to send requests to it.'
        ],
      }..remove('token');
      projects[index] = next;
      return ApiResponse(statusCode: 200, body: next);
    }
    if (method == 'DELETE') {
      final failed = _fail('DELETE project');
      if (failed != null) return failed;
      projects.removeAt(index);
      return const ApiResponse(statusCode: 204);
    }
    return ApiResponse(statusCode: 200, body: current);
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
