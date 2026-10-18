import 'dart:convert';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'files_models.dart';
import 'task_management.dart';
import 'workspace_models.dart';
import 'workspace_repository.dart';

abstract interface class WorkspaceFileTransfer {
  Future<JsonMap> upload(String path);
  Future<void> download(WorkspaceFile file);
}

class ApiWorkspaceRepository
    implements WorkspaceRepository, TaskManagementRepository {
  const ApiWorkspaceRepository(this.api, {required this.fileTransfer});
  final AnumApiClient api;
  final WorkspaceFileTransfer fileTransfer;

  Future<List<JsonReader>> _list(String path) async =>
      JsonReader(await api.request('GET', path)).optObjects('data', (x) => x);

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async {
    try {
      final values = await Future.wait([
        _list('/api/v1/tasks'),
        _list('/api/v1/approvals'),
        _list('/api/v1/automation/runs'),
        _list('/api/v1/files'),
        _list('/api/v1/memories'),
        _list('/api/v1/automation/workflows'),
        _list('/api/v1/automation/schedules'),
        _list('/api/v1/skills/versions'),
        _list('/api/v1/skills/installations'),
        _list('/api/v1/integrations'),
      ]);
      return WorkspaceSnapshot(
        tasks: values[0].map(_task).toList(),
        approvals: values[1].map(_approval).toList(),
        automations:
            values[2].map((run) => _automation(run, values[5])).toList(),
        files: values[3].map(_file).toList(),
        memories: values[4].map(_memory).toList(),
        workflowDefinitions: values[5].map(_workflow).toList(),
        schedules: values[6].map(_schedule).toList(),
        skills: values[7].map((item) => _skill(item, values[8])).toList(),
        integrations: values[9].map(_integration).toList(),
      );
    } on ApiException catch (error) {
      if (error.statusCode == 0 || error.statusCode >= 500) {
        throw WorkspaceOfflineException(error.message);
      }
      rethrow;
    }
  }

  @override
  Future<WorkspaceTask> createAndRunTask(String prompt) async {
    final created = await api.request('POST', '/api/v1/tasks', body: {
      'title': prompt.length > 80 ? '${prompt.substring(0, 77)}...' : prompt,
      'prompt': prompt,
    });
    final result = await api.request(
        'POST', '/api/v1/tasks/${JsonReader(created).string('id')}/run');
    final value = JsonReader(result);
    return _task(value.optObject('task') ?? JsonReader(created),
        run: value.optObject('run'), approval: value.optObject('approval'));
  }

  @override
  Future<WorkspaceTask> loadTask(String taskId) async {
    final task = await api.request('GET', '/api/v1/tasks/$taskId');
    JsonReader? run;
    try {
      run = JsonReader(
          await api.request('GET', '/api/v1/tasks/$taskId/latest-run'));
    } on ApiException catch (error) {
      if (error.statusCode != 404) rethrow;
    }
    return _task(JsonReader(task), run: run);
  }

  String _taskPath(String taskId, [String suffix = '']) =>
      '/api/v1/tasks/${Uri.encodeComponent(taskId)}$suffix';

  @override
  Future<List<WorkspaceTask>> listTasks(TaskListQuery query,
          {String? cursor}) async =>
      (await _list('/api/v1/tasks?'
              '${query.queryString(cursor: cursor, limit: taskPageSize)}'))
          .map(_task)
          .toList();

  @override
  Future<TaskSummaryCounts> taskSummary(TaskListQuery query) async {
    final qs = query.queryString(withStatus: false);
    final j = JsonReader(await api.request(
        'GET', '/api/v1/tasks/summary${qs.isEmpty ? '' : '?$qs'}'));
    final byStatus = j.optMap('by_status') ?? const {};
    return TaskSummaryCounts(
      total: j.optInt('total') ?? 0,
      archived: j.optInt('archived') ?? 0,
      byStatus: {
        for (final entry in byStatus.entries)
          if (entry.value is int) _status(entry.key): entry.value as int,
      },
    );
  }

  @override
  Future<WorkspaceTask> createPlannedTask(TaskDraft draft) async {
    final created = JsonReader(
        await api.request('POST', '/api/v1/tasks', body: draft.body()));
    if (!draft.runNow) return _task(created);
    final value = JsonReader(
        await api.request('POST', _taskPath(created.string('id'), '/run')));
    return _task(value.optObject('task') ?? created,
        run: value.optObject('run'), approval: value.optObject('approval'));
  }

  @override
  Future<WorkspaceTask> runTask(String taskId) async {
    final value =
        JsonReader(await api.request('POST', _taskPath(taskId, '/run')));
    return _task(value.object('task'),
        run: value.optObject('run'), approval: value.optObject('approval'));
  }

  @override
  Future<WorkspaceTask> planTask(String taskId,
          {TaskPriority? priority,
          DateTime? dueAt,
          bool clearDue = false}) async =>
      _task(JsonReader(await api.request('PUT', _taskPath(taskId),
          body: taskPlanBody(
              priority: priority, dueAt: dueAt, clearDue: clearDue))));

  @override
  Future<WorkspaceTask> renameTask(String taskId, String title) async =>
      _task(JsonReader(
          await api.request('PUT', _taskPath(taskId), body: {'title': title})));

  @override
  Future<WorkspaceTask> archiveTask(String taskId) async => _task(
      JsonReader(await api.request('POST', _taskPath(taskId, '/archive'))));

  @override
  Future<WorkspaceTask> unarchiveTask(String taskId) async => _task(
      JsonReader(await api.request('POST', _taskPath(taskId, '/unarchive'))));

  @override
  Future<WorkspaceTask> retryTask(String taskId) async {
    final value =
        JsonReader(await api.request('POST', _taskPath(taskId, '/retry')));
    return _task(value.object('task'),
        run: value.optObject('run'), approval: value.optObject('approval'));
  }

  @override
  Future<List<WorkspaceRun>> listRuns(String taskId, {String? cursor}) async {
    final after =
        cursor == null ? '' : '&cursor=${Uri.encodeQueryComponent(cursor)}';
    return (await _list(_taskPath(taskId, '/runs?limit=$runPageSize$after')))
        .map(_run)
        .toList();
  }

  @override
  Future<String> exportRunLog(String taskId) async =>
      const JsonEncoder.withIndent('  ').convert(
          await api.request('GET', _taskPath(taskId, '/export?format=json')));

  @override
  Future<WorkspaceTask> cancelTask(String taskId) async => _task(
      JsonReader(await api.request('POST', '/api/v1/tasks/$taskId/cancel')));

  @override
  Future<WorkspaceTask> resumeTask(String taskId) async {
    final run = await api.request('GET', '/api/v1/tasks/$taskId/latest-run');
    final value = JsonReader(await api.request(
        'POST', '/api/v1/agent-runs/${JsonReader(run).string('id')}/resume'));
    return _task(value.object('task'),
        run: value.optObject('run'), approval: value.optObject('approval'));
  }

  @override
  Future<WorkspaceApproval> decideApproval(WorkspaceApproval approval,
      {required bool approve, String? reason}) async {
    // Send back the payload hash that was displayed: the API refuses a stale
    // one and the runtime executes only a call that still matches it. The
    // optional reason is kept on the approval and in the audit trail.
    final body = decisionBody(approval, reason);
    final value = await api.request(
        'POST',
        '/api/v1/approvals/${Uri.encodeComponent(approval.id)}/'
            '${approve ? 'approve' : 'reject'}',
        body: body.isEmpty ? null : body);
    return _approval(JsonReader(value).object('approval'));
  }

  @override
  Future<WorkspaceFile> uploadFile(String path) async =>
      _file(JsonReader(await fileTransfer.upload(path)));

  @override
  Future<void> downloadFile(WorkspaceFile file) => fileTransfer.download(file);

  @override
  Future<void> deleteFile(String fileId) async {
    await api.request('DELETE', '/api/v1/files/$fileId');
  }

  @override
  Future<WorkspaceMemory> createMemory(
          {required String taskId, required String content}) async =>
      _memory(JsonReader(await api.request('POST', '/api/v1/memories', body: {
        'task_id': taskId,
        'content': content,
        'source_type': 'mobile',
      })));

  @override
  Future<void> deleteMemory(String memoryId) async {
    await api.request('DELETE', '/api/v1/memories/$memoryId');
  }

  @override
  Future<void> installSkill(WorkspaceSkill skill) async {
    await api.request('POST', '/api/v1/skills/installations', body: {
      'skill_id': skill.skillId,
      'version': skill.version,
      'approved_tools': skill.tools
    });
  }

  @override
  Future<void> setSkillEnabled(String skillId, bool enabled) async {
    await api.request(
        'PATCH', '/api/v1/skills/installations/${Uri.encodeComponent(skillId)}',
        body: {'enabled': enabled});
  }

  @override
  Future<void> uninstallSkill(String skillId) async {
    await api.request('DELETE',
        '/api/v1/skills/installations/${Uri.encodeComponent(skillId)}');
  }

  WorkspaceTask _task(JsonReader j, {JsonReader? run, JsonReader? approval}) =>
      WorkspaceTask(
        id: j.string('id'),
        title: j.string('title'),
        prompt: j.string('prompt'),
        status: _status(j.optString('status') ?? 'created'),
        createdAt: _date(j, 'created_at'),
        updatedAt: _date(j, 'updated_at'),
        run: run == null ? null : _run(run),
        approval: approval == null ? null : _approval(approval),
        createdBy: j.optString('created_by'),
        archivedAt: j['archived_at'] == null ? null : _date(j, 'archived_at'),
        priority: taskPriorityFromWire(j.optString('priority')),
        dueAt: j.optDate('due_at')?.toLocal(),
      );
  WorkspaceRun _run(JsonReader j) => WorkspaceRun(
        id: j.string('id'),
        status:
            _status(j.optString('status') ?? j.optString('phase') ?? 'running'),
        steps: j.optObjects(
            'steps',
            (x) => RunStep(
                id: x.string('id'),
                type: x.string('type'),
                summary: x.string('summary'),
                createdAt: _date(x, 'created_at'),
                metadata: x.optMap('metadata') ?? const {})),
        result: j.optString('result'),
        createdAt: j['created_at'] == null ? null : _date(j, 'created_at'),
      );
  WorkspaceApproval _approval(JsonReader j) => workspaceApprovalFromJson(j);
  WorkspaceAutomation _automation(JsonReader j,
      [List<JsonReader> workflows = const []]) {
    final workflowId = j.string('workflow_id');
    final matches = workflows.where((x) => x['id'] == workflowId).toList();
    return WorkspaceAutomation(
        id: j.string('id'),
        workflowId: workflowId,
        name: matches.isEmpty ? workflowId : matches.first.string('name'),
        status: j.string('status'),
        updatedAt: _updated(j),
        currentStep: j.optInt('current_step') ?? 0,
        stepCount: j.optList('steps')?.length ?? 0);
  }

  WorkspaceFile _file(JsonReader j) => parseWorkspaceFile(j);
  WorkspaceMemory _memory(JsonReader j) => WorkspaceMemory(
      id: j.string('id'),
      taskId: j.string('task_id'),
      content: j.string('content'),
      sourceType:
          j.optObject('provenance')?.optString('source_type') ?? 'unknown',
      createdAt: _date(j, 'created_at'));
  AutomationDefinition _workflow(JsonReader j) => AutomationDefinition(
      id: j.string('id'),
      name: j.string('name'),
      description: j.optString('description') ?? '',
      status: j.optString('status') ?? 'active',
      steps: j.optObjects(
          'steps', (x) => x.optString('name') ?? x.string('action')),
      updatedAt: _updated(j));
  AutomationSchedule _schedule(JsonReader j) => AutomationSchedule(
      id: j.string('id'),
      workflowId: j.string('workflow_id'),
      name: j.string('name'),
      cron: j.string('cron'),
      timezone: j.string('timezone'),
      enabled: j.boolean('enabled'));
  WorkspaceSkill _skill(JsonReader j, List<JsonReader> installs) {
    final install =
        installs.where((x) => x['skill_id'] == j['skill_id']).firstOrNull;
    return WorkspaceSkill(
        id: j.string('id'),
        skillId: j.string('skill_id'),
        name: j.string('name'),
        version: j.string('version'),
        description: j.string('description'),
        risk: j.string('risk_level'),
        tools: j.optStrings('required_tools') ?? const [],
        installed: install != null && install['version'] == j['version'],
        installedVersion: install?.optString('version'),
        enabled: install?.optBool('enabled') ?? false,
        signature: _signature(j.optObject('verification')));
  }

  SkillSignature? _signature(JsonReader? j) => j == null
      ? null
      : SkillSignature(
          status: j.string('status'),
          installable: j.boolean('installable'),
          detail: j.string('detail'),
          signer: j.optString('signer'),
          keyId: j.optString('key_id'),
          keySource: j.optString('key_source'),
          fingerprint: j.optString('fingerprint'));

  WorkspaceIntegration _integration(JsonReader j) => WorkspaceIntegration(
      id: j.string('id'),
      name: j.string('name'),
      kind: j.string('kind'),
      status: j.string('status'),
      endpoint: j.string('endpoint'),
      detail: j.string('detail'),
      latencyMs: j.optInt('latency_ms'),
      remediation: j.optString('remediation'),
      lastError: j.optString('last_error'),
      lastErrorAt: j['last_error_at'] == null
          ? null
          : j.date('last_error_at').toLocal());
  DateTime _date(JsonReader j, String key) => j.date(key).toLocal();

  /// `updated_at`, or `created_at` for records that were never updated.
  DateTime _updated(JsonReader j) =>
      j['updated_at'] == null ? _date(j, 'created_at') : _date(j, 'updated_at');
  WorkStatus _status(String value) => switch (value) {
        'queued' => WorkStatus.queued,
        'running' || 'planning' || 'executing' => WorkStatus.running,
        'waiting_approval' => WorkStatus.waitingApproval,
        'completed' => WorkStatus.completed,
        'failed' => WorkStatus.failed,
        'cancelled' => WorkStatus.cancelled,
        _ => WorkStatus.created
      };
}

/// An API approval (`/api/v1/approvals`, a run response's `approval`, the
/// developer merge approval) in the app's shape.
WorkspaceApproval workspaceApprovalFromJson(JsonReader j) => WorkspaceApproval(
    id: j.string('id'),
    taskId: j.string('task_id'),
    action: j.string('action'),
    reason: j.string('reason'),
    risk: j.string('risk_level'),
    status: j.string('status'),
    createdAt: j.date('created_at').toLocal(),
    arguments: j.optMap('arguments') ?? const {},
    payloadHash: j.optString('payload_hash'),
    expiresAt: j.optDate('expires_at')?.toLocal(),
    decidedAt: j.optDate('decided_at')?.toLocal(),
    decidedBy: j.optString('decided_by'),
    decisionReason: j.optString('decision_reason'),
    requestedBy: j.optString('requested_by'),
    target: j.optString('target'),
    requiredApprovals: j.optInt('required_approvals') ?? 1,
    approvers: j.optObjects(
        'approvers',
        (a) => ApprovalApprover(
            userId: a.string('user_id'),
            approvedAt: a.date('approved_at').toLocal(),
            reason: a.optString('reason'))));
