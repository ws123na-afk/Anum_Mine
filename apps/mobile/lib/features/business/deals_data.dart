import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'business_models.dart';

/// Lead and opportunity changes on the phone (docs/business.md). Reading lives
/// in [BusinessRepository]; this holds the writes. Owners and members create
/// and edit; deleting a lead is an owner action. The server checks both.

const opportunityStages = [
  'discovery',
  'qualified',
  'demo',
  'quote',
  'negotiation',
  'lost',
];

/// Lead states from which an opportunity may be opened (the server also checks).
const opportunityReadyStates = [
  'qualified',
  'contacted',
  'replied',
  'meeting',
  'offered',
];

abstract interface class DealsRepository {
  Future<LeadDetail> createLead(Map<String, Object?> fields);
  Future<LeadDetail> updateLead(String id, Map<String, Object?> fields);
  Future<void> deleteLead(String id);
  Future<BusinessOpportunity> createOpportunity(Map<String, Object?> fields);
  Future<BusinessOpportunity> updateOpportunity(
      String id, Map<String, Object?> fields);
  Future<BusinessOpportunity> moveStage(String id, String to, String reason);
}

class ApiDealsRepository implements DealsRepository {
  ApiDealsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _call(String method, String path,
          {Map<String, Object?>? body}) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  @override
  Future<LeadDetail> createLead(Map<String, Object?> fields) async =>
      LeadDetail.fromJson(await _call('POST', '/leads', body: fields));

  @override
  Future<LeadDetail> updateLead(String id, Map<String, Object?> fields) async =>
      LeadDetail.fromJson(
          await _call('PATCH', '/leads/${_id(id)}', body: fields));

  @override
  Future<void> deleteLead(String id) =>
      api.request('DELETE', '$_base/leads/${_id(id)}');

  @override
  Future<BusinessOpportunity> createOpportunity(
          Map<String, Object?> fields) async =>
      BusinessOpportunity.fromJson(
          await _call('POST', '/opportunities', body: fields));

  @override
  Future<BusinessOpportunity> updateOpportunity(
          String id, Map<String, Object?> fields) async =>
      BusinessOpportunity.fromJson(
          await _call('PATCH', '/opportunities/${_id(id)}', body: fields));

  @override
  Future<BusinessOpportunity> moveStage(
          String id, String to, String reason) async =>
      BusinessOpportunity.fromJson(await _call(
          'POST', '/opportunities/${_id(id)}/stage',
          body: {'to': to, 'reason': reason}));
}
