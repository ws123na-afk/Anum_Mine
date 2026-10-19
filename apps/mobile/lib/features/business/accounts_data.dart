import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'business_controller.dart';
import 'business_models.dart';

/// Accounts and contacts on the phone (docs/business.md#what-it-does). Owners
/// and members add and edit; deleting and clearing a do-not-contact mark are
/// owner actions. The server enforces both and its sentence is shown.

const consentBases = [
  'unknown',
  'consent_given',
  'inbound_inquiry',
  'existing_relationship',
  'referral',
  'public_business_channel',
];

class AccountContact {
  const AccountContact({
    required this.id,
    required this.name,
    required this.roleTitle,
    required this.email,
    required this.phone,
    required this.profileUrl,
    required this.consentBasis,
    required this.consentNote,
    required this.doNotContact,
    required this.dncReason,
    required this.contactable,
    this.blockReason,
  });
  final String id, name, roleTitle, consentBasis, consentNote, dncReason;
  final String? email, phone, profileUrl, blockReason;
  final bool doNotContact, contactable;

  factory AccountContact.fromJson(JsonReader r) => AccountContact(
        id: r.string('id'),
        name: r.string('name'),
        roleTitle: r.optString('role_title') ?? '',
        email: r.optString('email'),
        phone: r.optString('phone'),
        profileUrl: r.optString('profile_url'),
        consentBasis: r.string('consent_basis'),
        consentNote: r.optString('consent_note') ?? '',
        doNotContact: r.optBool('do_not_contact') ?? false,
        dncReason: r.optString('dnc_reason') ?? '',
        contactable: r.optBool('contactable') ?? false,
        blockReason: r.optString('block_reason'),
      );
}

class AccountLeadRef {
  const AccountLeadRef(this.id, this.title, this.state);
  final String id, title, state;
  factory AccountLeadRef.fromJson(JsonReader r) =>
      AccountLeadRef(r.string('id'), r.string('title'), r.string('state'));
}

class BusinessAccount {
  const BusinessAccount({
    required this.id,
    required this.name,
    required this.industry,
    required this.country,
    required this.city,
    required this.notes,
    required this.doNotContact,
    required this.dncReason,
    required this.contactCount,
    required this.openLeads,
    required this.contacts,
    required this.leads,
    this.website,
  });
  final String id, name, industry, country, city, notes, dncReason;
  final String? website;
  final bool doNotContact;
  final int contactCount, openLeads;

  /// Only the detail carries the people and leads; a list row has counts.
  final List<AccountContact> contacts;
  final List<AccountLeadRef> leads;

  String get place => [city, country].where((v) => v.isNotEmpty).join(', ');

  factory BusinessAccount.fromJson(JsonReader r) {
    // The list sends `contacts` as a count, the detail as a list.
    final raw = r.json['contacts'];
    return BusinessAccount(
      id: r.string('id'),
      name: r.string('name'),
      website: r.optString('website'),
      industry: r.optString('industry') ?? '',
      country: r.optString('country') ?? '',
      city: r.optString('city') ?? '',
      notes: r.optString('notes') ?? '',
      doNotContact: r.optBool('do_not_contact') ?? false,
      dncReason: r.optString('dnc_reason') ?? '',
      contactCount: raw is num ? raw.toInt() : (raw is List ? raw.length : 0),
      openLeads: r.optInt('open_leads') ?? 0,
      contacts: raw is List
          ? r.optObjects('contacts', AccountContact.fromJson)
          : const [],
      leads: r.optObjects('leads', AccountLeadRef.fromJson),
    );
  }
}

abstract interface class AccountsRepository {
  Future<BusinessPage<BusinessAccount>> accounts({
    String? query,
    required int offset,
    int limit,
  });
  Future<BusinessAccount> account(String id);
  Future<BusinessAccount> createAccount(Map<String, Object?> fields);
  Future<BusinessAccount> updateAccount(String id, Map<String, Object?> fields);
  Future<void> deleteAccount(String id);
  Future<BusinessAccount> accountDnc(String id, bool on, String reason);
  Future<BusinessAccount> addContact(
    String accountId,
    Map<String, Object?> fields,
  );
  Future<AccountContact> updateContact(String id, Map<String, Object?> fields);
  Future<AccountContact> contactDnc(String id, bool on, String reason);
  Future<void> deleteContact(String id);
}

class ApiAccountsRepository implements AccountsRepository {
  ApiAccountsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _call(
    String method,
    String path, {
    Map<String, Object?>? body,
  }) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  @override
  Future<BusinessPage<BusinessAccount>> accounts({
    String? query,
    required int offset,
    int limit = 20,
  }) async {
    final params = <String, String>{
      if (query != null && query.isNotEmpty) 'q': query,
      'offset': '$offset',
      'limit': '$limit',
    };
    return BusinessPage.fromJson(
      await _call('GET', '/accounts?${Uri(queryParameters: params).query}'),
      BusinessAccount.fromJson,
    );
  }

  @override
  Future<BusinessAccount> account(String id) async =>
      BusinessAccount.fromJson(await _call('GET', '/accounts/${_id(id)}'));

  @override
  Future<BusinessAccount> createAccount(Map<String, Object?> fields) async =>
      BusinessAccount.fromJson(await _call('POST', '/accounts', body: fields));

  @override
  Future<BusinessAccount> updateAccount(
    String id,
    Map<String, Object?> fields,
  ) async =>
      BusinessAccount.fromJson(
        await _call('PATCH', '/accounts/${_id(id)}', body: fields),
      );

  @override
  Future<void> deleteAccount(String id) =>
      api.request('DELETE', '$_base/accounts/${_id(id)}');

  @override
  Future<BusinessAccount> accountDnc(String id, bool on, String reason) async =>
      BusinessAccount.fromJson(
        await _call(
          'POST',
          '/accounts/${_id(id)}/do-not-contact',
          body: {'on': on, 'reason': reason},
        ),
      );

  @override
  Future<BusinessAccount> addContact(
    String accountId,
    Map<String, Object?> fields,
  ) async =>
      BusinessAccount.fromJson(
        await _call('POST', '/accounts/${_id(accountId)}/contacts',
            body: fields),
      );

  @override
  Future<AccountContact> updateContact(
    String id,
    Map<String, Object?> fields,
  ) async =>
      AccountContact.fromJson(
        await _call('PATCH', '/contacts/${_id(id)}', body: fields),
      );

  @override
  Future<AccountContact> contactDnc(String id, bool on, String reason) async =>
      AccountContact.fromJson(
        await _call(
          'POST',
          '/contacts/${_id(id)}/do-not-contact',
          body: {'on': on, 'reason': reason},
        ),
      );

  @override
  Future<void> deleteContact(String id) =>
      api.request('DELETE', '$_base/contacts/${_id(id)}');
}

class AccountsController extends ChangeNotifier {
  AccountsController(this.repository);
  final AccountsRepository repository;
  static const pageSize = 20;

  BusinessLoad state = BusinessLoad.idle;
  final List<BusinessAccount> items = [];
  int total = 0;
  bool loadingMore = false;
  String? error, query;
  bool get hasMore => items.length < total;

  Future<void> load() async {
    state = BusinessLoad.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.accounts(
        query: query,
        offset: 0,
        limit: pageSize,
      );
      items
        ..clear()
        ..addAll(page.items);
      total = page.total;
      state = BusinessLoad.ready;
    } catch (e) {
      state = e is ApiException && e.isPermissionDenied
          ? BusinessLoad.denied
          : BusinessLoad.error;
      error = e is ApiException ? e.readable : '$e';
    }
    notifyListeners();
  }

  Future<void> search(String? text) {
    query = (text ?? '').trim().isEmpty ? null : text!.trim();
    return load();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.accounts(
        query: query,
        offset: items.length,
        limit: pageSize,
      );
      items.addAll(page.items);
      total = page.total;
    } catch (e) {
      error = e is ApiException ? e.readable : '$e';
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<BusinessAccount> _then(Future<BusinessAccount> change) async {
    final account = await change;
    await load();
    return account;
  }

  Future<BusinessAccount> create(Map<String, Object?> fields) =>
      _then(repository.createAccount(fields));
  Future<BusinessAccount> update(String id, Map<String, Object?> fields) =>
      _then(repository.updateAccount(id, fields));
  Future<BusinessAccount> setDnc(String id, bool on, String reason) =>
      _then(repository.accountDnc(id, on, reason));
  Future<BusinessAccount> addContact(String id, Map<String, Object?> fields) =>
      _then(repository.addContact(id, fields));
  Future<void> delete(String id) async {
    await repository.deleteAccount(id);
    await load();
  }

  void reset() {
    items.clear();
    total = 0;
    state = BusinessLoad.idle;
    notifyListeners();
  }
}
