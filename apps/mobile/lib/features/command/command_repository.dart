import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'command_models.dart';

/// The ANUM Command endpoints (docs/command.md).
abstract interface class CommandRepository {
  Future<List<CommandSuggestion>> suggestions(String locale);
  Future<CommandPlan> preview(String text, String locale);
  Future<CommandView> create({
    required String text,
    required List<String> steps,
    required String source,
    required String locale,
    List<String> attachments,
    String? quickCommandId,
  });

  /// `POST /api/v1/tasks/{id}/run` for one step.
  Future<void> runTask(String taskId);

  /// `POST /api/v1/tasks/bulk {"action": "cancel"}`.
  Future<void> cancelTasks(List<String> taskIds);
  Future<CommandView> load(String commandId);
  Future<List<CommandSummary>> list({String? cursor, int limit});
  Future<void> delete(String commandId);
  Future<List<NextStep>> nextSteps(String commandId);

  /// Approves one action with the payload hash shown to the person.
  Future<void> approve(CommandApproval approval);

  Future<List<QuickCommand>> quickCommands();
  Future<QuickCommand> createQuick(
      {required String title,
      required String text,
      required QuickCategory category});
  Future<QuickCommand> updateQuick(String id,
      {String? title, String? text, QuickCategory? category, int? position});
  Future<void> deleteQuick(String id);
}

class ApiCommandRepository implements CommandRepository {
  ApiCommandRepository(this.api);
  final AnumApiClient api;

  static List<Object?> _data(JsonMap body) => switch (body['data']) {
        final List<Object?> items => items,
        _ => const []
      };

  static List<T> _list<T>(JsonMap body, T Function(JsonReader item) read) => [
        for (var i = 0; i < _data(body).length; i++)
          read(JsonReader(jsonObject(_data(body)[i], 'data[$i]'), 'data[$i]')),
      ];

  @override
  Future<List<CommandSuggestion>> suggestions(String locale) async => _list(
      await api.request('GET', '/api/v1/commands/suggestions?locale=$locale'),
      CommandSuggestion.fromJson);

  @override
  Future<CommandPlan> preview(String text, String locale) async =>
      CommandPlan.fromJson(JsonReader.of(await api.request(
          'POST', '/api/v1/commands/preview',
          body: {'text': text, 'locale': locale})));

  @override
  Future<CommandView> create({
    required String text,
    required List<String> steps,
    required String source,
    required String locale,
    List<String> attachments = const [],
    String? quickCommandId,
  }) async =>
      CommandView.fromJson(
          JsonReader.of(await api.request('POST', '/api/v1/commands', body: {
        'text': text,
        'steps': [
          for (final step in steps) {'instruction': step}
        ],
        'source': source,
        'locale': locale,
        'attachments': attachments,
        if (quickCommandId != null) 'quick_command_id': quickCommandId,
      })));

  @override
  Future<void> runTask(String taskId) async {
    await api.request(
        'POST', '/api/v1/tasks/${Uri.encodeComponent(taskId)}/run');
  }

  @override
  Future<void> cancelTasks(List<String> taskIds) async {
    if (taskIds.isEmpty) return;
    await api.request('POST', '/api/v1/tasks/bulk',
        body: {'action': 'cancel', 'task_ids': taskIds});
  }

  @override
  Future<CommandView> load(String commandId) async =>
      CommandView.fromJson(JsonReader.of(await api.request(
          'GET', '/api/v1/commands/${Uri.encodeComponent(commandId)}')));

  @override
  Future<List<CommandSummary>> list({String? cursor, int limit = 20}) async {
    final query = cursor == null
        ? '?limit=$limit'
        : '?limit=$limit&cursor=${Uri.encodeQueryComponent(cursor)}';
    return _list(await api.request('GET', '/api/v1/commands$query'),
        CommandSummary.fromJson);
  }

  @override
  Future<void> delete(String commandId) async {
    await api.request(
        'DELETE', '/api/v1/commands/${Uri.encodeComponent(commandId)}');
  }

  @override
  Future<List<NextStep>> nextSteps(String commandId) async => _list(
      await api.request('GET',
          '/api/v1/commands/${Uri.encodeComponent(commandId)}/next-steps'),
      NextStep.fromJson);

  @override
  Future<void> approve(CommandApproval approval) async {
    final hash = approval.payloadHash;
    if (hash == null) {
      throw const ApiException(409,
          'This approval is not bound to a payload hash; open it under Approvals.');
    }
    await api.request(
        'POST', '/api/v1/approvals/${Uri.encodeComponent(approval.id)}/approve',
        body: {'payload_hash': hash});
  }

  @override
  Future<List<QuickCommand>> quickCommands() async => _list(
      await api.request('GET', '/api/v1/quick-commands'),
      QuickCommand.fromJson);

  @override
  Future<QuickCommand> createQuick(
          {required String title,
          required String text,
          required QuickCategory category}) async =>
      QuickCommand.fromJson(JsonReader.of(await api.request(
          'POST', '/api/v1/quick-commands',
          body: {'title': title, 'text': text, 'category': category.name})));

  @override
  Future<QuickCommand> updateQuick(String id,
          {String? title,
          String? text,
          QuickCategory? category,
          int? position}) async =>
      QuickCommand.fromJson(JsonReader.of(await api.request(
          'PUT', '/api/v1/quick-commands/${Uri.encodeComponent(id)}',
          body: {
            if (title != null) 'title': title,
            if (text != null) 'text': text,
            if (category != null) 'category': category.name,
            if (position != null) 'position': position,
          })));

  @override
  Future<void> deleteQuick(String id) async {
    await api.request(
        'DELETE', '/api/v1/quick-commands/${Uri.encodeComponent(id)}');
  }
}
