import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'workspace_models.dart';
import 'workspace_repository.dart';

abstract interface class WorkspaceFileTransfer {
  Future<JsonMap> upload(String path);
  Future<void> download(WorkspaceFile file);
}

class ApiWorkspaceRepository implements WorkspaceRepository {
  const ApiWorkspaceRepository(this.api, {required this.fileTransfer});
  final AnumApiClient api;
  final WorkspaceFileTransfer fileTransfer;

  Future<List<JsonMap>> _list(String path) async {
    final value = await api.request('GET', path);
    return ((value['data'] as List<Object?>?) ?? const [])
        .cast<JsonMap>();
  }

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async {
    try {
      final values = await Future.wait([
        _list('/api/v1/tasks'),
        _list('/api/v1/approvals'),
        _list('/api/v1/automation/runs'),
        _list('/api/v1/files'),
        _list('/api/v1/memories'),
      ]);
      return WorkspaceSnapshot(
        tasks: values[0].map(_task).toList(),
        approvals: values[1].map(_approval).toList(),
        automations: values[2].map(_automation).toList(),
        files: values[3].map(_file).toList(),
        memories: values[4].map(_memory).toList(),
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
    final result = await api.request('POST', '/api/v1/tasks/${created['id']}/run');
    return _task((result['task'] as JsonMap?) ?? created,
        run: result['run'] as JsonMap?);
  }

  @override
  Future<WorkspaceTask> loadTask(String taskId) async {
    final task = await api.request('GET', '/api/v1/tasks/$taskId');
    JsonMap? run;
    try {
      run = await api.request('GET', '/api/v1/tasks/$taskId/latest-run');
    } on ApiException catch (error) {
      if (error.statusCode != 404) rethrow;
    }
    return _task(task, run: run);
  }

  @override
  Future<WorkspaceTask> cancelTask(String taskId) async =>
      _task(await api.request('POST', '/api/v1/tasks/$taskId/cancel'));

  @override
  Future<WorkspaceTask> resumeTask(String taskId) async {
    final run = await api.request('GET', '/api/v1/tasks/$taskId/latest-run');
    final value = await api.request(
        'POST', '/api/v1/agent-runs/${run['id']}/resume');
    return _task(value['task']! as JsonMap, run: value['run'] as JsonMap?);
  }

  @override
  Future<WorkspaceApproval> decideApproval(String approvalId,
      {required bool approve}) async {
    final value = await api.request('POST',
        '/api/v1/approvals/$approvalId/${approve ? 'approve' : 'reject'}');
    return _approval(value['approval']! as JsonMap);
  }

  @override
  Future<WorkspaceAutomation> startAutomation(String automationId) async =>
      _automation(await api.request(
          'POST', '/api/v1/automation/workflows/$automationId/runs'));

  @override
  Future<WorkspaceAutomation> transitionAutomation(
          String runId, String action) async =>
      _automation(await api.request(
          'POST', '/api/v1/automation/runs/$runId/$action'));

  @override
  Future<WorkspaceFile> uploadFile(String path) async =>
      _file(await fileTransfer.upload(path));

  @override
  Future<void> downloadFile(WorkspaceFile file) => fileTransfer.download(file);

  @override
  Future<void> deleteFile(String fileId) async {
    await api.request('DELETE', '/api/v1/files/$fileId');
  }

  @override
  Future<WorkspaceMemory> createMemory(
          {required String taskId, required String content}) async =>
      _memory(await api.request('POST', '/api/v1/memories', body: {
        'task_id': taskId,
        'content': content,
        'source_type': 'mobile',
      }));

  @override
  Future<void> deleteMemory(String memoryId) async {
    await api.request('DELETE', '/api/v1/memories/$memoryId');
  }

  WorkspaceTask _task(JsonMap json, {JsonMap? run}) => WorkspaceTask(
        id: json['id']! as String,
        title: json['title']! as String,
        prompt: json['prompt']! as String,
        status: _status(json['status'] as String? ?? 'created'),
        createdAt: _date(json['created_at']),
        updatedAt: _date(json['updated_at']),
        run: run == null ? null : _run(run),
      );
  WorkspaceRun _run(JsonMap json) => WorkspaceRun(
        id: json['id']! as String,
        status: _status(json['status'] as String? ?? json['phase'] as String? ?? 'running'),
        steps: ((json['steps'] as List<Object?>?) ?? const []).cast<JsonMap>().map((x) => RunStep(id: x['id']! as String, type: x['type']! as String, summary: x['summary']! as String, createdAt: _date(x['created_at']))).toList(),
        result: json['result'] as String?,
      );
  WorkspaceApproval _approval(JsonMap j) => WorkspaceApproval(id: j['id']! as String, taskId: j['task_id']! as String, action: j['action']! as String, reason: j['reason']! as String, risk: j['risk_level']! as String, status: j['status']! as String, createdAt: _date(j['created_at']));
  WorkspaceAutomation _automation(JsonMap j) => WorkspaceAutomation(id: j['id']! as String, name: (j['workflow_name'] ?? j['workflow_id'] ?? 'Automation') as String, status: j['status']! as String, updatedAt: _date(j['updated_at'] ?? j['created_at']), currentStep: (j['current_step'] as num?)?.toInt() ?? 0, stepCount: ((j['steps'] as List<Object?>?) ?? const []).length);
  WorkspaceFile _file(JsonMap j) => WorkspaceFile(id: j['id']! as String, name: j['name']! as String, contentType: j['content_type']! as String, sizeBytes: (j['size_bytes']! as num).toInt(), createdAt: _date(j['created_at']));
  WorkspaceMemory _memory(JsonMap j) => WorkspaceMemory(id: j['id']! as String, taskId: j['task_id']! as String, content: j['content']! as String, sourceType: ((j['provenance'] as JsonMap?)?['source_type'] ?? 'unknown') as String, createdAt: _date(j['created_at']));
  DateTime _date(Object? value) => DateTime.parse(value! as String).toLocal();
  WorkStatus _status(String value) => switch (value) { 'queued' => WorkStatus.queued, 'running' || 'planning' || 'executing' => WorkStatus.running, 'waiting_approval' => WorkStatus.waitingApproval, 'completed' => WorkStatus.completed, 'failed' => WorkStatus.failed, 'cancelled' => WorkStatus.cancelled, _ => WorkStatus.created };
}
