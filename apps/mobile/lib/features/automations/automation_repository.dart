import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'automation_models.dart';

/// `/api/v1/automation` (docs/automation.md). Lists ask for one item more
/// than a page, so the screen knows whether "Show more" has anything to show.
abstract interface class AutomationRepository {
  Future<({String? userId, String? role})> viewer();
  Future<AutomationPage<AutomationWorkflow>> workflows(
      {String query = '', String status = '', int limit = 20, int offset = 0});
  Future<AutomationWorkflow> workflow(String id);
  Future<AutomationWorkflow> createWorkflow(Map<String, Object?> body);
  Future<AutomationWorkflow> updateWorkflow(
      String id, Map<String, Object?> body);
  Future<AutomationWorkflow> setWorkflowEnabled(String id, bool enabled);
  Future<void> deleteWorkflow(String id);
  Future<List<AutomationScheduleItem>> schedules(String workflowId);
  Future<SchedulePreview> preview(String cron, String timezone);
  Future<AutomationScheduleItem> createSchedule(
      {required String workflowId,
      required String name,
      required String cron,
      required String timezone});
  Future<AutomationScheduleItem> updateSchedule(String id,
      {String? name, String? cron, String? timezone});
  Future<AutomationScheduleItem> setScheduleEnabled(String id, bool enabled);
  Future<void> deleteSchedule(String id);
  Future<AutomationPage<AutomationRunItem>> runs(String workflowId,
      {String status = '', int limit = 10, int offset = 0});
  Future<AutomationRunItem> start(String workflowId, String idempotencyKey);
  Future<AutomationRunItem> transition(String runId, String action);
}

class ApiAutomationRepository implements AutomationRepository {
  const ApiAutomationRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/automation';

  static String _id(String value) => Uri.encodeComponent(value);
  static String _query(Map<String, Object?> params) {
    final kept = {
      for (final e in params.entries)
        if (e.value != null && '${e.value}'.isNotEmpty) e.key: '${e.value}'
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<List<JsonReader>> _list(String path) async =>
      JsonReader(await api.request('GET', path)).optObjects('data', (x) => x);

  AutomationPage<T> _page<T>(List<T> items, int limit) =>
      AutomationPage(items.length > limit ? items.sublist(0, limit) : items,
          hasMore: items.length > limit);

  @override
  Future<({String? userId, String? role})> viewer() async {
    final session = await api.sessions.read();
    try {
      final value = JsonReader(
          await api.request('GET', '/api/v1/workspace-memberships/current'));
      return (
        userId: value.optString('user_id') ?? session?.context.userId,
        role: value['active'] == false ? 'viewer' : value.optString('role'),
      );
    } on ApiException {
      return (userId: session?.context.userId, role: null);
    }
  }

  @override
  Future<AutomationPage<AutomationWorkflow>> workflows(
      {String query = '',
      String status = '',
      int limit = 20,
      int offset = 0}) async {
    final items = await _list('$_base/workflows${_query({
          'query': query.trim(),
          'status': status,
          'limit': limit + 1,
          'offset': offset
        })}');
    return _page(items.map(AutomationWorkflow.fromJson).toList(), limit);
  }

  @override
  Future<AutomationWorkflow> workflow(String id) async =>
      AutomationWorkflow.fromJson(
          JsonReader(await api.request('GET', '$_base/workflows/${_id(id)}')));

  @override
  Future<AutomationWorkflow> createWorkflow(Map<String, Object?> body) async =>
      AutomationWorkflow.fromJson(JsonReader(
          await api.request('POST', '$_base/workflows', body: body)));

  @override
  Future<AutomationWorkflow> updateWorkflow(
          String id, Map<String, Object?> body) async =>
      AutomationWorkflow.fromJson(JsonReader(
          await api.request('PUT', '$_base/workflows/${_id(id)}', body: body)));

  @override
  Future<AutomationWorkflow> setWorkflowEnabled(
          String id, bool enabled) async =>
      AutomationWorkflow.fromJson(JsonReader(await api.request('POST',
          '$_base/workflows/${_id(id)}/${enabled ? 'enable' : 'disable'}')));

  @override
  Future<void> deleteWorkflow(String id) async {
    await api.request('DELETE', '$_base/workflows/${_id(id)}');
  }

  @override
  Future<List<AutomationScheduleItem>> schedules(String workflowId) async =>
      (await _list('$_base/schedules${_query({
            'workflow_id': workflowId,
            'limit': 500
          })}'))
          .map(AutomationScheduleItem.fromJson)
          .toList();

  @override
  Future<SchedulePreview> preview(String cron, String timezone) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/schedules/preview',
        body: {'cron': cron, 'timezone': timezone, 'count': 3}));
    return SchedulePreview(
        description: value.string('description'),
        timezone: value.optString('timezone') ?? timezone,
        nextRuns: [
          for (final item in value.optList('next_runs') ?? const [])
            if (item is String && DateTime.tryParse(item) != null)
              DateTime.parse(item)
        ]);
  }

  @override
  Future<AutomationScheduleItem> createSchedule(
          {required String workflowId,
          required String name,
          required String cron,
          required String timezone}) async =>
      AutomationScheduleItem.fromJson(
          JsonReader(await api.request('POST', '$_base/schedules', body: {
        'workflow_id': workflowId,
        'name': name,
        'cron': cron,
        'timezone': timezone,
        'enabled': true,
      })));

  @override
  Future<AutomationScheduleItem> updateSchedule(String id,
          {String? name, String? cron, String? timezone}) async =>
      AutomationScheduleItem.fromJson(JsonReader(
          await api.request('PUT', '$_base/schedules/${_id(id)}', body: {
        if (name != null) 'name': name,
        if (cron != null) 'cron': cron,
        if (timezone != null) 'timezone': timezone,
      })));

  @override
  Future<AutomationScheduleItem> setScheduleEnabled(
          String id, bool enabled) async =>
      AutomationScheduleItem.fromJson(JsonReader(await api.request('POST',
          '$_base/schedules/${_id(id)}/${enabled ? 'enable' : 'disable'}')));

  @override
  Future<void> deleteSchedule(String id) async {
    await api.request('DELETE', '$_base/schedules/${_id(id)}');
  }

  @override
  Future<AutomationPage<AutomationRunItem>> runs(String workflowId,
      {String status = '', int limit = 10, int offset = 0}) async {
    final items = await _list('$_base/runs${_query({
          'workflow_id': workflowId,
          'status': status,
          'limit': limit + 1,
          'offset': offset
        })}');
    return _page(items.map(AutomationRunItem.fromJson).toList(), limit);
  }

  @override
  Future<AutomationRunItem> start(
          String workflowId, String idempotencyKey) async =>
      AutomationRunItem.fromJson(JsonReader(await api.request(
          'POST', '$_base/workflows/${_id(workflowId)}/runs',
          headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<AutomationRunItem> transition(String runId, String action) async =>
      AutomationRunItem.fromJson(JsonReader(
          await api.request('POST', '$_base/runs/${_id(runId)}/$action')));
}

/// Triggers and taking over (docs/automation.md#triggers). A separate
/// interface so repositories that predate triggers keep compiling; the
/// screens show triggers only when the repository offers them.
abstract interface class AutomationTriggerRepository {
  Future<List<TriggerEventInfo>> triggerEvents();
  Future<List<AutomationTriggerItem>> triggers(String workflowId);
  Future<({AutomationTriggerItem trigger, String? secret})> createTrigger(
      String workflowId, Map<String, Object?> body);
  Future<AutomationTriggerItem> updateTrigger(
      String id, Map<String, Object?> body);
  Future<AutomationTriggerItem> setTriggerEnabled(String id, bool enabled);
  Future<({AutomationTriggerItem trigger, String? secret})> rotateTriggerSecret(
      String id);
  Future<AutomationTriggerItem> takeOverTrigger(String id);
  Future<void> deleteTrigger(String id);
  Future<AutomationScheduleItem> takeOverSchedule(String id);
}

class ApiAutomationTriggerRepository extends ApiAutomationRepository
    implements AutomationTriggerRepository {
  const ApiAutomationTriggerRepository(super.api);
  static const _base = '/api/v1/automation';
  static String _id(String value) => Uri.encodeComponent(value);

  ({AutomationTriggerItem trigger, String? secret}) _withSecret(
      Map<String, Object?> raw) {
    final value = JsonReader(raw);
    return (
      trigger: AutomationTriggerItem.fromJson(
          JsonReader(value.optMap('trigger') ?? const {})),
      secret: value.optString('secret'),
    );
  }

  @override
  Future<List<TriggerEventInfo>> triggerEvents() async =>
      (await _list('$_base/trigger-events'))
          .map(TriggerEventInfo.fromJson)
          .toList();

  @override
  Future<List<AutomationTriggerItem>> triggers(String workflowId) async =>
      (await _list('$_base/triggers${ApiAutomationRepository._query({
            'workflow_id': workflowId,
            'limit': 500
          })}'))
          .map(AutomationTriggerItem.fromJson)
          .toList();

  @override
  Future<({AutomationTriggerItem trigger, String? secret})> createTrigger(
          String workflowId, Map<String, Object?> body) async =>
      _withSecret(await api.request('POST', '$_base/triggers',
          body: {...body, 'workflow_id': workflowId}));

  @override
  Future<AutomationTriggerItem> updateTrigger(
          String id, Map<String, Object?> body) async =>
      AutomationTriggerItem.fromJson(JsonReader(
          await api.request('PUT', '$_base/triggers/${_id(id)}', body: {
        for (final e in body.entries)
          if (e.key != 'kind' && e.value != null) e.key: e.value
      })));

  @override
  Future<AutomationTriggerItem> setTriggerEnabled(
          String id, bool enabled) async =>
      AutomationTriggerItem.fromJson(JsonReader(await api.request('POST',
          '$_base/triggers/${_id(id)}/${enabled ? 'enable' : 'disable'}')));

  @override
  Future<({AutomationTriggerItem trigger, String? secret})> rotateTriggerSecret(
          String id) async =>
      _withSecret(await api.request(
          'POST', '$_base/triggers/${_id(id)}/rotate-secret'));

  @override
  Future<AutomationTriggerItem> takeOverTrigger(String id) async =>
      AutomationTriggerItem.fromJson(JsonReader(
          await api.request('POST', '$_base/triggers/${_id(id)}/take-over')));

  @override
  Future<void> deleteTrigger(String id) async {
    await api.request('DELETE', '$_base/triggers/${_id(id)}');
  }

  @override
  Future<AutomationScheduleItem> takeOverSchedule(String id) async =>
      AutomationScheduleItem.fromJson(JsonReader(
          await api.request('POST', '$_base/schedules/${_id(id)}/take-over')));
}
