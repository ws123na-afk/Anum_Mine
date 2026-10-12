import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'autonomy_models.dart';

/// The /api/v1/autonomy routes (docs/autonomy.md). The API decides who may do
/// what; this client shows its answers.
abstract interface class AutonomyRepository {
  Future<ActionPage> actions({
    String? category,
    String? query,
    String? cursor,
    int limit = 25,
  });
  Future<AutonomousAction> undo(AutonomousAction action);
  Future<void> tighten(String actionId);
  Future<List<AutonomyProposal>> proposals();
  Future<void> decide(String proposalId, {required bool accept});
  Future<AutonomyOverview> overview();
  Future<AutonomyOverview> save(Map<String, Object?> body);
}

class ApiAutonomyRepository implements AutonomyRepository {
  const ApiAutonomyRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/autonomy';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<ActionPage> actions({
    String? category,
    String? query,
    String? cursor,
    int limit = 25,
  }) async {
    final params = <String, String>{
      if (category != null && category.isNotEmpty) 'category': category,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      'limit': '$limit',
      if (cursor != null) 'cursor': cursor,
    };
    final r = JsonReader(
      await api.request(
        'GET',
        '$_base/actions?${Uri(queryParameters: params).query}',
      ),
    );
    return ActionPage(
      r.objects('items', AutonomousAction.fromJson),
      r.optString('next_cursor'),
    );
  }

  @override
  Future<AutonomousAction> undo(AutonomousAction action) async =>
      AutonomousAction.fromJson(
        JsonReader(
          await api.request(
            'POST',
            '$_base/actions/${_id(action.id)}/undo',
            body: {'undo_token': action.undoToken},
          ),
        ),
      );

  @override
  Future<void> tighten(String actionId) =>
      api.request('POST', '$_base/actions/${_id(actionId)}/tighten');

  @override
  Future<List<AutonomyProposal>> proposals() async =>
      JsonReader(await api.request('GET', '$_base/proposals'))
          .optObjects('data', AutonomyProposal.fromJson);

  @override
  Future<void> decide(String proposalId, {required bool accept}) => api.request(
        'POST',
        '$_base/proposals/${_id(proposalId)}/${accept ? 'accept' : 'decline'}',
      );

  @override
  Future<AutonomyOverview> overview() async => AutonomyOverview.fromJson(
        JsonReader(await api.request('GET', '$_base/settings')),
      );

  @override
  Future<AutonomyOverview> save(Map<String, Object?> body) async =>
      AutonomyOverview.fromJson(
        JsonReader(await api.request('PUT', '$_base/settings', body: body)),
      );
}
