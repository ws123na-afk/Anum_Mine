import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../workspace/workspace_models.dart';
import 'marketing_models.dart';
import 'marketing_ops_models.dart';

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
      Map<String, String>? linksByAccount,
      String? title,
      String? campaignId,
      List<MkMedia> media = const [],
      bool aiGenerated = false,
      bool untrustedSource = false,
      DateTime? scheduledAt});

  /// `PUT /posts/{id}`: the content of a post that is not out yet. Media is
  /// replaced as a whole, so pass the files to keep.
  Future<MkPost> updatePost(String postId,
      {required String text,
      required List<String> hashtags,
      required String title,
      required String? link,
      required List<MkMedia> media,
      required String? campaignId});
  Future<MkPost> schedule(String postId, DateTime? at);
  Future<MkPost> reschedule(String postId, DateTime at);
  Future<MkPost> cancel(String postId);
  Future<MkPost> publishNow(String postId);
  Future<MkPost> retry(String postId);
  Future<void> delete(String postId);
  Future<String?> decide(WorkspaceApproval approval, {required bool approve});
  Future<List<MkCampaign>> campaigns(
      {String? before, int limit = 20, String? query, String? status});
  Future<MkCampaign> createCampaign(MkCampaignInput input);
  Future<MkCampaign> updateCampaign(String id, MkCampaignInput input);
  Future<MkCampaign> setCampaignStatus(String id, String status);

  /// `DELETE /campaigns/{id}`; the server refuses while posts are queued.
  Future<void> deleteCampaign(String id);

  /// `POST /campaigns/{id}/auto-post`: off is immediate, on asks an owner.
  Future<MkCampaign> setAutoPost(String id,
      {required bool enabled, List<String> accountIds = const []});
  Future<MkIdeas> ideas(
      {required String brief,
      required List<String> platforms,
      required List<String> languages,
      String? campaignId,
      String source = '',
      int count = 3});
  Future<MkHashtags> suggestHashtags(
      {required String text,
      required String platform,
      required String language});
  Future<MkPreferences> preferences();
  Future<MkPreferences> savePreferences(MkPreferences value);
  Future<MkValidation> validate(
      {required String platform,
      required String text,
      required List<String> hashtags,
      String? link,
      String title = '',
      List<MkMedia> media = const []});
  Future<String> trackedLink(
      {required String url,
      required String platform,
      String campaign = '',
      String content = ''});
  Future<List<MkMediaFile>> media({String? query, int limit = 50});
  Future<List<MkTimeSlot>> bestTimes({String? platform});
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
      Map<String, String>? linksByAccount,
      String? title,
      String? campaignId,
      List<MkMedia> media = const [],
      bool aiGenerated = false,
      bool untrustedSource = false,
      DateTime? scheduledAt}) async {
    final value = await _post('/posts', {
      'campaign_id': campaignId,
      'scheduled_at': scheduledAt?.toUtc().toIso8601String(),
      'ai_generated': aiGenerated,
      'untrusted_source': untrustedSource,
      'variants': [
        for (final account in accountIds)
          for (final entry in texts.entries)
            {
              'account_id': account,
              'language': entry.key,
              'text': entry.value,
              'title': title ?? '',
              'hashtags': hashtags[entry.key] ?? const <String>[],
              'link_url': linksByAccount?[account] ?? link,
              'media': [for (final item in media) item.toInput()],
            }
      ],
    });
    return value.objects('data', MkPost.fromJson);
  }

  @override
  Future<MkPost> updatePost(String postId,
          {required String text,
          required List<String> hashtags,
          required String title,
          required String? link,
          required List<MkMedia> media,
          required String? campaignId}) async =>
      MkPost.fromJson(JsonReader(
          await api.request('PUT', '$_base/posts/${_id(postId)}', body: {
        'text': text,
        'title': title,
        'hashtags': hashtags,
        'link_url': link,
        'media': [for (final item in media) item.toInput()],
        if (campaignId != null) 'campaign_id': campaignId,
        'clear_campaign': campaignId == null,
      })));

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
  Future<List<MkCampaign>> campaigns(
      {String? before, int limit = 20, String? query, String? status}) async {
    final path = Uri(path: '/campaigns', queryParameters: {
      'limit': '$limit',
      if (before != null) 'before': before,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      if (status != null && status.isNotEmpty) 'status': status,
    });
    return (await _get(path.toString())).objects('data', MkCampaign.fromJson);
  }

  @override
  Future<MkCampaign> createCampaign(MkCampaignInput input) async =>
      MkCampaign.fromJson(await _post('/campaigns', input.toJson()));

  @override
  Future<MkCampaign> updateCampaign(String id, MkCampaignInput input) async =>
      MkCampaign.fromJson(JsonReader(await api.request(
          'PUT', '$_base/campaigns/${_id(id)}',
          body: input.toJson())));

  @override
  Future<void> deleteCampaign(String id) async =>
      api.request('DELETE', '$_base/campaigns/${_id(id)}');

  @override
  Future<MkCampaign> setAutoPost(String id,
          {required bool enabled, List<String> accountIds = const []}) async =>
      MkCampaign.fromJson(await _post('/campaigns/${_id(id)}/auto-post',
          {'enabled': enabled, 'account_ids': enabled ? accountIds : []}));

  @override
  Future<MkIdeas> ideas(
          {required String brief,
          required List<String> platforms,
          required List<String> languages,
          String? campaignId,
          String source = '',
          int count = 3}) async =>
      MkIdeas.fromJson(await _post('/assist/ideas', {
        'brief': brief,
        'campaign_id': campaignId,
        'platforms': platforms,
        'languages': languages,
        'count': count,
        'source_text': source,
        'source_label': source.isEmpty ? '' : 'pasted source',
      }));

  @override
  Future<MkHashtags> suggestHashtags(
          {required String text,
          required String platform,
          required String language}) async =>
      MkHashtags.fromJson(await _post('/assist/hashtags',
          {'text': text, 'platform': platform, 'language': language}));

  @override
  Future<MkPreferences> preferences() async =>
      MkPreferences.fromJson(await _get('/preferences'));

  @override
  Future<MkPreferences> savePreferences(MkPreferences value) async =>
      MkPreferences.fromJson(
          JsonReader(await api.request('PUT', '$_base/preferences', body: {
        'brand_voice': value.brandVoice,
        'timezone': value.timezone,
        'default_hashtags_en': value.hashtagsEn,
        'default_hashtags_ar': value.hashtagsAr,
      })));

  @override
  Future<MkValidation> validate(
          {required String platform,
          required String text,
          required List<String> hashtags,
          String? link,
          String title = '',
          List<MkMedia> media = const []}) async =>
      MkValidation.fromJson(await _post('/posts/validate', {
        'platform': platform,
        'text': text,
        'title': title,
        'hashtags': hashtags,
        'link_url': link,
        'media': [for (final item in media) item.toInput()],
      }));

  @override
  Future<String> trackedLink(
          {required String url,
          required String platform,
          String campaign = '',
          String content = ''}) async =>
      (await _post('/utm', {
        'url': url,
        'platform': platform,
        'campaign': campaign,
        'content': content,
      }))
          .string('url');

  @override
  Future<List<MkMediaFile>> media({String? query, int limit = 50}) async {
    final path = Uri(path: '/media', queryParameters: {
      'limit': '$limit',
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
    });
    return (await _get(path.toString())).objects('data', MkMediaFile.fromJson);
  }

  @override
  Future<List<MkTimeSlot>> bestTimes({String? platform}) async {
    final path = Uri(path: '/analytics/best-times', queryParameters: {
      if (platform != null && platform.isNotEmpty) 'platform': platform,
    });
    return (await _get(path.toString())).objects('data', MkTimeSlot.fromJson);
  }

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
