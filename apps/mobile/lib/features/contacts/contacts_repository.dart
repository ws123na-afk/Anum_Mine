import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'contacts_import.dart';
import 'contacts_models.dart';

/// The Contacts directory API (docs/contacts.md#api). The server checks who
/// may read (members and owners), write (business:write) and manage
/// (business:manage); the phone shows its `403` instead of guessing.
abstract interface class ContactsRepository {
  Future<PeoplePage> people(
      {String query = '', int offset = 0, int limit = contactsPageSize});
  Future<PersonDetail> person(String id);

  /// Marks a channel do not contact; the server answers 201 new or 200 renewed.
  Future<void> markDoNotContact(String id, String channel, String reason);

  /// Clears a mark (owners); the reason goes to the audit log.
  Future<void> clearDoNotContact(String id, String channel, String reason);

  /// Adds a person by hand (write); the API's 422 names a bad value.
  Future<PersonDetail> create(Map<String, Object?> body);

  /// Changes name, organisation, notes, numbers and addresses (write).
  Future<PersonDetail> update(String id, Map<String, Object?> body);

  /// Removes the person from the directory (owners, `business:manage`).
  Future<void> delete(String id);

  /// Merges [otherId] into [keepId] (write); the other record is deleted.
  Future<PersonDetail> merge(String keepId, String otherId);

  /// Open possible duplicates, 25 at a time.
  Future<ProposalPage> proposals({int offset = 0});

  /// "Different people": the pair is never proposed again (write).
  Future<void> dismissProposal(String id);

  /// The whole directory as CSV through the system save dialog (owners);
  /// false when the dialog was closed.
  Future<bool> exportCsv(String dialogTitle);

  /// What importing a CSV, Excel or vCard file would do (owners); nothing is
  /// written.
  Future<ImportPlan> previewImport(ImportFile file);

  /// Imports the previewed file with ANUM's suggestion for every row, all of
  /// it or nothing (owners); the API's 409 says what changed.
  Future<ImportOutcome> applyImport(ImportFile file, ImportPlan plan);
}

class ApiContactsRepository implements ContactsRepository {
  const ApiContactsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/contacts';

  @override
  Future<PeoplePage> people(
      {String query = '', int offset = 0, int limit = contactsPageSize}) async {
    final path = Uri(path: _base, queryParameters: {
      'limit': '$limit',
      'offset': '$offset',
      'sort': 'name',
      if (query.trim().isNotEmpty) 'q': query.trim(),
    }).toString();
    return PeoplePage.fromJson(JsonReader(await api.request('GET', path)));
  }

  @override
  Future<PersonDetail> person(String id) async =>
      PersonDetail.fromJson(JsonReader(
          await api.request('GET', '$_base/${Uri.encodeComponent(id)}')));

  @override
  Future<void> markDoNotContact(
      String id, String channel, String reason) async {
    await api.request('PUT', '$_base/${Uri.encodeComponent(id)}/do-not-contact',
        body: {'channel': channel, 'reason': reason});
  }

  @override
  Future<PersonDetail> create(Map<String, Object?> body) async =>
      PersonDetail.fromJson(
          JsonReader(await api.request('POST', _base, body: body)));

  @override
  Future<PersonDetail> update(String id, Map<String, Object?> body) async =>
      PersonDetail.fromJson(JsonReader(await api
          .request('PATCH', '$_base/${Uri.encodeComponent(id)}', body: body)));

  @override
  Future<void> delete(String id) async {
    await api.request('DELETE', '$_base/${Uri.encodeComponent(id)}');
  }

  @override
  Future<PersonDetail> merge(String keepId, String otherId) async =>
      PersonDetail.fromJson(JsonReader(await api.request(
          'POST', '$_base/${Uri.encodeComponent(keepId)}/merge',
          body: {'other_id': otherId})));

  @override
  Future<ProposalPage> proposals({int offset = 0}) async =>
      ProposalPage.fromJson(JsonReader(await api.request('GET',
          '$_base/proposals?state=open&limit=$contactsPageSize&offset=$offset')));

  @override
  Future<void> dismissProposal(String id) async {
    await api.request(
        'POST', '$_base/proposals/${Uri.encodeComponent(id)}/dismiss');
  }

  @override
  Future<bool> exportCsv(String dialogTitle) =>
      api.download('GET', '$_base/export',
          fileName: 'contacts.csv', dialogTitle: dialogTitle);

  @override
  Future<ImportPlan> previewImport(ImportFile file) async =>
      ImportPlan.fromJson(JsonReader(await api
          .request('POST', '$_base/import/preview', body: file.body())));

  @override
  Future<ImportOutcome> applyImport(ImportFile file, ImportPlan plan) async =>
      ImportOutcome.fromJson(
          JsonReader(await api.request('POST', '$_base/import/apply', body: {
        ...file.body(),
        'mapping': plan.mapping,
        'fingerprint': plan.fingerprint,
        // A row left out takes ANUM's suggestion (docs/contacts.md).
        'decisions': const <Object?>[],
      })));

  @override
  Future<void> clearDoNotContact(
      String id, String channel, String reason) async {
    await api.request(
        'DELETE', '$_base/${Uri.encodeComponent(id)}/do-not-contact/$channel',
        body: {'reason': reason});
  }
}
