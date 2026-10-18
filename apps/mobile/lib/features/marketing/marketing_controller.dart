import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/workspace_models.dart';
import 'marketing_models.dart';
import 'marketing_repository.dart';
import '../brevo/brevo_screen.dart';

enum MkPhase { loading, ready, denied, error }

/// State of the Marketing screen (docs/marketing.md).
class MarketingController extends ChangeNotifier {
  MarketingController(this.repository, {this.brevo});
  final MarketingRepository repository;

  /// Email campaigns through Brevo (docs/brevo.md), opened from the app bar.
  final BrevoController? brevo;
  static const pageSize = 20;

  MkPhase phase = MkPhase.loading;
  String? error;

  /// The last action's outcome, shown once as a message.
  String? notice;
  MkSetup? setup;
  List<MkAccount> accounts = const [];
  List<MkPost> posts = const [];
  bool morePosts = false;
  String statusFilter = '';
  String query = '';
  List<MkCampaign> campaigns = const [];
  bool moreCampaigns = false;
  MkSummary? summary;
  int summaryDays = 30;
  MkReport? report;
  bool busy = false;

  bool get canWrite => setup?.canWrite ?? false;
  bool get canManage => setup?.canManage ?? false;
  List<MkAccount> get connected =>
      accounts.where((account) => account.connected).toList();

  static String describe(Object error) => switch (error) {
        ApiException(isPermissionDenied: true, :final message) =>
          'Not allowed: $message',
        ApiException(:final message) => message,
        _ => 'The request failed. Check your connection and try again.',
      };

  Future<void> load() async {
    phase = MkPhase.loading;
    notifyListeners();
    try {
      final results = await Future.wait([
        repository.setup(),
        repository.accounts(),
        repository.posts(status: statusFilter, query: query, limit: pageSize),
        repository.campaigns(limit: pageSize),
      ]);
      setup = results[0] as MkSetup;
      accounts = results[1] as List<MkAccount>;
      posts = results[2] as List<MkPost>;
      morePosts = posts.length == pageSize;
      campaigns = results[3] as List<MkCampaign>;
      moreCampaigns = campaigns.length == pageSize;
      phase = MkPhase.ready;
      error = null;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? MkPhase.denied : MkPhase.error;
      error = e.message;
    } catch (e) {
      phase = MkPhase.error;
      error = describe(e);
    }
    notifyListeners();
  }

  Future<void> filterPosts({String? status, String? search}) async {
    statusFilter = status ?? statusFilter;
    query = search ?? query;
    await _act(() async {
      posts = await repository.posts(
          status: statusFilter, query: query, limit: pageSize);
      morePosts = posts.length == pageSize;
    });
  }

  Future<void> loadMorePosts() => _act(() async {
        final page = await repository.posts(
            status: statusFilter,
            query: query,
            before: posts.isEmpty ? null : posts.last.id,
            limit: pageSize);
        posts = [...posts, ...page];
        morePosts = page.length == pageSize;
      });

  Future<void> loadMoreCampaigns() => _act(() async {
        final page = await repository.campaigns(
            before: campaigns.isEmpty ? null : campaigns.last.id,
            limit: pageSize);
        campaigns = [...campaigns, ...page];
        moreCampaigns = page.length == pageSize;
      });

  void _replace(MkPost post) =>
      posts = [for (final item in posts) item.id == post.id ? post : item];

  Future<bool> _act(Future<void> Function() action, [String? done]) async {
    busy = true;
    notice = null;
    notifyListeners();
    try {
      await action();
      if (done != null) notice = done;
      return true;
    } catch (e) {
      notice = describe(e);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> create(
          {required List<String> accountIds,
          required Map<String, String> texts,
          required Map<String, List<String>> hashtags,
          String? link,
          DateTime? at,
          required String done}) =>
      _act(() async {
        final created = await repository.createPosts(
            accountIds: accountIds,
            texts: texts,
            hashtags: hashtags,
            link: link,
            scheduledAt: at);
        posts = [...created, ...posts];
      }, done);

  Future<bool> schedule(MkPost post, DateTime? at, String done) =>
      _act(() async => _replace(await repository.schedule(post.id, at)), done);
  Future<bool> reschedule(MkPost post, DateTime at, String done) => _act(
      () async => _replace(await repository.reschedule(post.id, at)), done);
  Future<bool> cancel(MkPost post, String done) =>
      _act(() async => _replace(await repository.cancel(post.id)), done);
  Future<bool> publishNow(MkPost post) =>
      _act(() async => _replace(await repository.publishNow(post.id)));
  Future<bool> retry(MkPost post) =>
      _act(() async => _replace(await repository.retry(post.id)));
  Future<bool> delete(MkPost post, String done) => _act(() async {
        await repository.delete(post.id);
        posts = posts.where((item) => item.id != post.id).toList();
      }, done);

  /// Approves or rejects with the payload hash that was displayed.
  Future<bool> decide(WorkspaceApproval approval,
          {required bool approve, required String fallback}) =>
      _act(() async {
        final result = await repository.decide(approval, approve: approve);
        notice = result ?? fallback;
        posts = await repository.posts(
            status: statusFilter, query: query, limit: pageSize);
        campaigns = await repository.campaigns(limit: pageSize);
      });

  Future<bool> createCampaign(String name, String product, String done) =>
      _act(() async {
        campaigns = [
          await repository.createCampaign(name, product),
          ...campaigns
        ];
      }, done);

  Future<bool> setCampaignStatus(
          MkCampaign campaign, String status, String done) =>
      _act(() async {
        final next = await repository.setCampaignStatus(campaign.id, status);
        campaigns = [
          for (final item in campaigns) item.id == next.id ? next : item
        ];
      }, done);

  Future<String?> startConnect(String provider) async {
    String? url;
    await _act(() async => url = await repository.startConnect(provider));
    return url;
  }

  Future<bool> check(MkAccount account) => _act(() async {
        final next = await repository.check(account.id);
        accounts = [
          for (final item in accounts) item.id == next.id ? next : item
        ];
      });

  Future<bool> disconnect(MkAccount account, String done) => _act(() async {
        final next = await repository.disconnect(account.id);
        accounts = [
          for (final item in accounts) item.id == next.id ? next : item
        ];
      }, done);

  Future<void> loadAnalytics({int? days, required String language}) async {
    summaryDays = days ?? summaryDays;
    await _act(() async {
      summary = await repository.summary(summaryDays);
      report = await repository.report(language);
    });
  }

  /// Ask to email the weekly report to the connected mailbox; [requested]
  /// is the notice when it waits for approval, [notSent] gets the reason.
  Future<bool> emailReport(String language,
      {required String requested,
      required String Function(String reason) notSent}) {
    var ok = false;
    return _act(() async {
      final result = await repository.emailReport(language);
      ok = result.requested;
      notice = ok ? requested : notSent(result.detail);
    }).then((done) => done && ok);
  }

  /// Forget everything (a workspace switch); the next open loads again.
  void reset() {
    phase = MkPhase.loading;
    setup = null;
    accounts = const [];
    posts = const [];
    campaigns = const [];
    summary = null;
    report = null;
    statusFilter = query = '';
    notifyListeners();
  }

  void clearNotice() {
    notice = null;
    notifyListeners();
  }
}
