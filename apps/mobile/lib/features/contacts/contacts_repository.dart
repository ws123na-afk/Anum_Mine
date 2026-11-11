import '../../data/api_client.dart';
import '../../data/json_read.dart';
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
  Future<void> clearDoNotContact(
      String id, String channel, String reason) async {
    await api.request(
        'DELETE', '$_base/${Uri.encodeComponent(id)}/do-not-contact/$channel',
        body: {'reason': reason});
  }
}
