import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'business_models.dart';

/// Business over the live API (docs/business.md). The phone reads the desk,
/// moves leads, records what happened and updates milestones; the catalog,
/// proposal editing and acceptance decisions are on the web and in Approvals.
abstract interface class BusinessRepository {
  Future<BusinessSetup> setup();
  Future<BusinessKpi> kpi(int days);
  Future<BusinessPage<BusinessLead>> leads({
    String? query,
    String? state,
    required int offset,
    int limit,
  });
  Future<LeadDetail> lead(String id);
  Future<LeadDetail> transition(String id, String to, String reason);
  Future<void> logNote(String id, String kind, String summary, String key);
  Future<BusinessPage<BusinessOpportunity>> opportunities({
    required int offset,
    int limit,
  });
  Future<BusinessPage<BusinessProposal>> proposals({
    required int offset,
    int limit,
  });
  Future<BusinessPage<BusinessProject>> projects({
    required int offset,
    int limit,
  });
  Future<BusinessProject> updateMilestone(String id, String status);
}

class ApiBusinessRepository implements BusinessRepository {
  ApiBusinessRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!,
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$_base$path'));

  @override
  Future<BusinessSetup> setup() async =>
      BusinessSetup.fromJson(await _get('/setup'));

  @override
  Future<BusinessKpi> kpi(int days) async =>
      BusinessKpi.fromJson(await _get('/kpi${_q({'days': '$days'})}'));

  @override
  Future<BusinessPage<BusinessLead>> leads({
    String? query,
    String? state,
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        await _get(
          '/leads${_q({
                'q': query,
                'state': state,
                'offset': '$offset',
                'limit': '$limit'
              })}',
        ),
        BusinessLead.fromJson,
      );

  @override
  Future<LeadDetail> lead(String id) async =>
      LeadDetail.fromJson(await _get('/leads/${_id(id)}'));

  @override
  Future<LeadDetail> transition(String id, String to, String reason) async =>
      LeadDetail.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$_base/leads/${_id(id)}/transition',
            body: {'to': to, 'reason': reason},
          ),
        ),
      );

  @override
  Future<void> logNote(String id, String kind, String summary, String key) =>
      api.request(
        'POST',
        '$_base/leads/${_id(id)}/activities',
        body: {'kind': kind, 'summary': summary},
        headers: {'idempotency-key': key},
      );

  @override
  Future<BusinessPage<BusinessOpportunity>> opportunities({
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        await _get(
            '/opportunities${_q({'offset': '$offset', 'limit': '$limit'})}'),
        BusinessOpportunity.fromJson,
      );

  @override
  Future<BusinessPage<BusinessProposal>> proposals({
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        await _get('/proposals${_q({'offset': '$offset', 'limit': '$limit'})}'),
        BusinessProposal.fromJson,
      );

  @override
  Future<BusinessPage<BusinessProject>> projects({
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        await _get('/projects${_q({'offset': '$offset', 'limit': '$limit'})}'),
        BusinessProject.fromJson,
      );

  @override
  Future<BusinessProject> updateMilestone(String id, String status) async =>
      BusinessProject.fromJson(
        JsonReader.of(
          await api.request(
            'PATCH',
            '$_base/milestones/${_id(id)}',
            body: {'status': status},
          ),
        ),
      );
}
