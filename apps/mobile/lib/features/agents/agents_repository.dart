import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'agents_models.dart';

/// The agents API (docs/agents.md). Reading needs `task:read`; creating,
/// changing and deleting need `task:create` and, for someone else's shared
/// agent, the workspace owner role; running a task as an agent needs
/// `task:run`. The server enforces all of it; the app only reflects it.
abstract interface class AgentsRepository {
  Future<AgentCatalog> catalog();
  Future<AgentPage> list({
    String? category,
    String query = '',
    String? cursor,
    int limit = 20,
  });
  Future<AgentProfile> get(String id);
  Future<AgentProfile> create(Map<String, Object?> body);
  Future<AgentProfile> update(String id, Map<String, Object?> body);
  Future<void> delete(String id);
  Future<AgentMemoryPage> memories(String id, {String query = ''});
  Future<AgentMemory> addMemory(String id, String content);
  Future<void> deleteMemory(String id, String memoryId);
  Future<List<AgentTask>> recentTasks(String id);
  Future<AgentRunOutcome> runTask(String id, String prompt, {String? title});
}

class ApiAgentsRepository implements AgentsRepository {
  const ApiAgentsRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/agents';
  String _one(String id) => '$_base/${Uri.encodeComponent(id)}';

  @override
  Future<AgentCatalog> catalog() async => AgentCatalog.fromReader(
        JsonReader(await api.request('GET', '$_base/catalog')),
      );

  @override
  Future<AgentPage> list({
    String? category,
    String query = '',
    String? cursor,
    int limit = 20,
  }) async {
    final params = <String, String>{
      'limit': '$limit',
      if (category != null) 'category': category,
      if (query.trim().isNotEmpty) 'q': query.trim(),
      if (cursor != null) 'cursor': cursor,
    };
    final path = Uri(path: _base, queryParameters: params).toString();
    return AgentPage.fromReader(JsonReader(await api.request('GET', path)));
  }

  @override
  Future<AgentProfile> get(String id) async =>
      AgentProfile.fromReader(JsonReader(await api.request('GET', _one(id))));

  @override
  Future<AgentProfile> create(Map<String, Object?> body) async =>
      AgentProfile.fromReader(
        JsonReader(await api.request('POST', _base, body: body)),
      );

  @override
  Future<AgentProfile> update(String id, Map<String, Object?> body) async =>
      AgentProfile.fromReader(
        JsonReader(await api.request('PATCH', _one(id), body: body)),
      );

  @override
  Future<void> delete(String id) async {
    await api.request('DELETE', _one(id));
  }

  @override
  Future<AgentMemoryPage> memories(String id, {String query = ''}) async {
    final path = Uri(
      path: '${_one(id)}/memories',
      queryParameters: {
        'limit': '100',
        if (query.trim().isNotEmpty) 'q': query.trim(),
      },
    ).toString();
    return AgentMemoryPage.fromReader(
      JsonReader(await api.request('GET', path)),
    );
  }

  @override
  Future<AgentMemory> addMemory(String id, String content) async =>
      AgentMemory.fromReader(
        JsonReader(
          await api.request(
            'POST',
            '${_one(id)}/memories',
            body: {'content': content},
          ),
        ),
      );

  @override
  Future<void> deleteMemory(String id, String memoryId) async {
    await api.request(
      'DELETE',
      '${_one(id)}/memories/${Uri.encodeComponent(memoryId)}',
    );
  }

  @override
  Future<List<AgentTask>> recentTasks(String id) async {
    final path = Uri(
      path: '/api/v1/tasks',
      queryParameters: {'agent_id': id, 'limit': '10'},
    ).toString();
    return JsonReader(await api.request('GET', path))
        .optObjects('data', AgentTask.fromReader);
  }

  @override
  Future<AgentRunOutcome> runTask(
    String id,
    String prompt, {
    String? title,
  }) async {
    final words = prompt.trim().split(RegExp(r'\s+')).join(' ');
    final created = JsonReader(
      await api.request(
        'POST',
        '/api/v1/tasks',
        body: {
          'title': title ??
              (words.length > 80 ? '${words.substring(0, 79)}…' : words),
          'prompt': prompt,
          'agent_id': id,
        },
      ),
    );
    final result = JsonReader(
      await api.request(
        'POST',
        '/api/v1/tasks/${Uri.encodeComponent(created.string('id'))}/run',
      ),
    );
    final task = result.object('task');
    final run = result.optObject('run');
    return AgentRunOutcome(
      taskTitle: task.optString('title') ?? '',
      status: task.optString('status') ?? 'created',
      waitingApproval: result.optObject('approval') != null,
      result: run?.optString('result'),
    );
  }
}
