import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/json_read.dart';
import 'team_models.dart';

/// Team work, notifications, assistants and organisations over the live API.
/// Every permission is the API's answer (its 403 is shown in place).
abstract interface class TeamRepository {
  Future<WorkPage> work({
    String scope,
    String status,
    String query,
    int offset,
  });
  Future<WorkItem> item(String id);
  Future<WorkItem> setStatus(WorkItem item, String status);
  Future<void> comment(String id, String body);
  Future<void> tick(String itemId, String entryId, bool done);
  Future<CommandPreview> parseCommand(String text);
  Future<WorkItem> runCommand(String text, AssigneeChoice? assignee);
  Future<NotificationPage> notifications({String? cursor});
  Future<void> markRead(String id);
  Future<void> markAllRead();
  Future<List<WorkloadRow>> workload();
  Future<List<Assistant>> assistants();
  Future<String?> route({
    required String channel,
    required String sender,
    required String body,
  });
  Future<List<OrganisationSummary>> organisations();
  Future<CreatedOrganisation> createOrganisation({
    required String tenantId,
    required String name,
    required String email,
    required String plan,
  });
}

class ApiTeamRepository implements TeamRepository {
  const ApiTeamRepository(this.api);
  final AnumApiClient api;

  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<WorkPage> work({
    String scope = 'mine',
    String status = 'open',
    String query = '',
    int offset = 0,
  }) async {
    final params = {
      if (scope != 'all') 'scope': scope,
      'status': status,
      if (query.trim().isNotEmpty) 'q': query.trim(),
      'limit': '30',
      if (offset > 0) 'offset': '$offset',
    };
    final qs = Uri(queryParameters: params).query;
    return WorkPage.fromJson(
      await api.request('GET', '/api/v1/work-items?$qs'),
    );
  }

  @override
  Future<WorkItem> item(String id) async => WorkItem.fromJson(
        JsonReader(await api.request('GET', '/api/v1/work-items/${_id(id)}')),
      );

  @override
  Future<WorkItem> setStatus(WorkItem item, String status) async =>
      WorkItem.fromJson(
        JsonReader(
          await api.request(
            'PATCH',
            '/api/v1/work-items/${_id(item.id)}',
            body: {'version': item.version, 'status': status},
          ),
        ),
      );

  @override
  Future<void> comment(String id, String body) => api.request(
        'POST',
        '/api/v1/work-items/${_id(id)}/comments',
        body: {'body': body},
      );

  @override
  Future<void> tick(String itemId, String entryId, bool done) => api.request(
        'PATCH',
        '/api/v1/work-items/${_id(itemId)}/checklist/${_id(entryId)}',
        body: {'done': done},
      );

  @override
  Future<CommandPreview> parseCommand(String text) async =>
      CommandPreview.fromJson(
        await api.request(
          'POST',
          '/api/v1/team/commands/parse',
          body: {'text': text},
        ),
      );

  @override
  Future<WorkItem> runCommand(String text, AssigneeChoice? assignee) async =>
      WorkItem.fromJson(
        JsonReader(
          await api.request(
            'POST',
            '/api/v1/team/commands',
            body: {
              'text': text,
              if (assignee != null) 'assignee': assignee.toJson(),
            },
          ),
        ),
      );

  @override
  Future<NotificationPage> notifications({
    String? cursor,
  }) async =>
      NotificationPage.fromJson(
        await api.request(
          'GET',
          '/api/v1/notifications?limit=30${cursor == null ? '' : '&cursor=${_id(cursor)}'}',
        ),
      );

  @override
  Future<void> markRead(String id) =>
      api.request('POST', '/api/v1/notifications/${_id(id)}/read');

  @override
  Future<void> markAllRead() =>
      api.request('POST', '/api/v1/notifications/read-all');

  @override
  Future<List<WorkloadRow>> workload() async =>
      JsonReader(await api.request('GET', '/api/v1/work-items/workload'))
          .objects('rows', WorkloadRow.fromJson);

  @override
  Future<List<Assistant>> assistants() async =>
      JsonReader(await api.request('GET', '/api/v1/assistants'))
          .optObjects('data', Assistant.fromJson);

  @override
  Future<String?> route({
    required String channel,
    required String sender,
    required String body,
  }) async {
    final r = JsonReader(
      await api.request(
        'POST',
        '/api/v1/assistants/route',
        body: {'channel': channel, 'sender': sender, 'body': body},
      ),
    );
    return r.optObject('assistant')?.string('name');
  }

  @override
  Future<List<OrganisationSummary>> organisations() async =>
      JsonReader(await api.request('GET', '/api/v1/platform/organizations'))
          .optObjects('data', OrganisationSummary.fromJson);

  @override
  Future<CreatedOrganisation> createOrganisation({
    required String tenantId,
    required String name,
    required String email,
    required String plan,
  }) async =>
      CreatedOrganisation.fromJson(
        await api.request(
          'POST',
          '/api/v1/platform/organizations',
          body: {
            'tenant_id': tenantId,
            'display_name': name,
            'workspace_name': 'Main',
            'first_admin_email': email,
            'plan': plan,
          },
        ),
      );
}

/// The API's message for a refusal, phrased for a person.
String describeTeamError(Object error) => switch (error) {
      ApiException(isPermissionDenied: true, :final message) => message,
      ApiException(:final message) => message,
      JsonShapeException() => 'The server sent an unexpected answer.',
      _ => 'The server could not be reached. Check your connection.',
    };
