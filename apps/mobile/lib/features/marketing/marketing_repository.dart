import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../workspace/workspace_models.dart';
import 'marketing_models.dart';

/// Marketing over the API (docs/marketing.md). Permissions are the server's:
/// members read, members and owners draft, owners connect accounts.
abstract interface class MarketingRepository {
  Future<MkSetup> setup();
  Future<List<MkAccount>> accounts();
  Future<List<MkPost>> posts(
      {String? status,
      String? query,
      String? before,
      DateTime? from,
      DateTime? to,
      int limit = 20});
  Future<List<MkPost>> createPosts(
      {required List<String> accountIds,
      required Map<String, String> texts,
      required Map<String, List<String>> hashtags,
      String? link,
      DateTime? scheduledAt});
  Future<MkPost> schedule(String postId, DateTime? at);
  Future<MkPost> reschedule(String postId, DateTime at);
  Future<MkPost> cancel(String postId);
  Future<MkPost> publishNow(String postId);
  Future<MkPost> retry(String postId);
  Future<void> delete(String postId);
  Future<String?> decide(WorkspaceApproval approval, {required bool approve});
  Future<List<MkCampaign>> campaigns({String? before, int limit = 20});
  Future<MkCampaign> createCampaign(String name, String product);
  Future<MkCampaign> setCampaignStatus(String id, String status);
  Future<String> startConnect(String provider);
  Future<MkAccount> check(String accountId);
  Future<MkAccount> disconnect(String accountId);
  Future<MkSummary> summary(int days);
  Future<MkReport> report(String language);
  Future<MkDelivery> emailReport(String language);
}

class ApiMarketingRepository implements MarketingRepository {
  const ApiMarketingRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/marketing';
  static String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _get(String path) async =>
      JsonReader(await api.request('GET', '$_base$path'));
  Future<JsonReader> _post(String path, [Map<String, Object?>? body]) async =>
      JsonReader(await api.request('POST', '$_base$path', body: body));

  @override
  Future<MkSetup> setup() async => MkSetup.fromJson(await _get('/setup'));

  @override
  Future<List<MkAccount>> accounts() async =>
      (await _get('/accounts')).objects('data', MkAccount.fromJson);

  @override
  Future<List<MkPost>> posts(
      {String? status,
      String? query,
      String? before,
      DateTime? from,
      DateTime? to,
      int limit = 20}) async {
    final path = Uri(path: '/posts', queryParameters: {
      'limit': '$limit',
      if (status != null && status.isNotEmpty) 'status': status,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      if (before != null) 'before': before,
      if (from != null) 'from': from.toUtc().toIso8601String(),
      if (to != null) 'to': to.toUtc().toIso8601String(),
    });
    return (await _get(path.toString())).objects('data', MkPost.fromJson);
  }

  @override
  Future<List<MkPost>> createPosts(
      {required List<String> accountIds,
      required Map<String, String> texts,
      required Map<String, List<String>> hashtags,
      String? link,
      DateTime? scheduledAt}) async {
    final value = await _post('/posts', {
      'campaign_id': null,
      'scheduled_at': scheduledAt?.toUtc().toIso8601String(),
      'variants': [
        for (final account in accountIds)
          for (final entry in texts.entries)
            {
              'account_id': account,
              'language': entry.key,
              'text': entry.value,
              'title': '',
              'hashtags': hashtags[entry.key] ?? const <String>[],
              'link_url': link,
              'media': const <Object>[],
            }
      ],
    });
    return value.objects('data', MkPost.fromJson);
  }

  @override
  Future<MkPost> schedule(String postId, DateTime? at) async =>
      MkPost.fromJson(await _post('/posts/${_id(postId)}/schedule',
          {'scheduled_at': at?.toUtc().toIso8601String()}));

  @override
  Future<MkPost> reschedule(String postId, DateTime at) async =>
      MkPost.fromJson(await _post('/posts/${_id(postId)}/reschedule',
          {'scheduled_at': at.toUtc().toIso8601String()}));

  @override
  Future<MkPost> cancel(String postId) async =>
      MkPost.fromJson(await _post('/posts/${_id(postId)}/cancel'));

  @override
  Future<MkPost> publishNow(String postId) async =>
      MkPost.fromJson(await _post('/posts/${_id(postId)}/publish'));

  @override
  Future<MkPost> retry(String postId) async =>
      MkPost.fromJson(await _post('/posts/${_id(postId)}/retry'));

  @override
  Future<void> delete(String postId) async =>
      api.request('DELETE', '$_base/posts/${_id(postId)}');

  @override
  Future<String?> decide(WorkspaceApproval approval,
      {required bool approve}) async {
    final value = JsonReader(await api.request('POST',
        '/api/v1/approvals/${_id(approval.id)}/${approve ? 'approve' : 'reject'}',
        body: decisionBody(approval, null)));
    return value.optObject('run')?.optString('result');
  }

  @override
  Future<List<MkCampaign>> campaigns({String? before, int limit = 20}) async {
    final path = Uri(path: '/campaigns', queryParameters: {
      'limit': '$limit',
      if (before != null) 'before': before
    });
    return (await _get(path.toString())).objects('data', MkCampaign.fromJson);
  }

  @override
  Future<MkCampaign> createCampaign(String name, String product) async =>
      MkCampaign.fromJson(await _post('/campaigns', {
        'name': name,
        'product': product,
      }));

  @override
  Future<MkCampaign> setCampaignStatus(String id, String status) async =>
      MkCampaign.fromJson(JsonReader(await api.request(
          'PUT', '$_base/campaigns/${_id(id)}',
          body: {'status': status})));

  @override
  Future<String> startConnect(String provider) async =>
      (await _post('/oauth/start', {'provider': provider}))
          .string('authorize_url');

  @override
  Future<MkAccount> check(String accountId) async =>
      MkAccount.fromJson(await _post('/accounts/${_id(accountId)}/check'));

  @override
  Future<MkAccount> disconnect(String accountId) async =>
      MkAccount.fromJson(JsonReader(
          await api.request('DELETE', '$_base/accounts/${_id(accountId)}')));

  @override
  Future<MkSummary> summary(int days) async =>
      MkSummary.fromJson(await _get('/analytics/summary?days=$days'));

  @override
  Future<MkReport> report(String language) async =>
      MkReport.fromJson(await _get('/reports/weekly?language=$language'));

  @override
  Future<MkDelivery> emailReport(String language) async =>
      MkDelivery.fromJson((await _post(
              '/reports/weekly/deliver?language=$language', {'email': true}))
          .object('email'));
}
