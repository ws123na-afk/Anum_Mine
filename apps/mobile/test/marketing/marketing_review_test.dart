// Review fixes (docs/marketing.md): a media-only post still sends one
// variant per account, and clearing the time of a scheduled post stops it.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/marketing/marketing_controller.dart';
import 'package:anum_mobile/features/marketing/marketing_models.dart';
import 'package:anum_mobile/features/marketing/marketing_repository.dart';
import 'package:flutter_test/flutter_test.dart';

import 'marketing_test.dart' show FakeMarketing, postJson;

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    return const ApiResponse(statusCode: 200, body: {'data': []});
  }
}

class _Scheduled extends FakeMarketing {
  final cancelled = <String>[];
  final rescheduled = <String>[];
  @override
  Future<MkPost> updatePost(String postId,
      {required String text,
      required List<String> hashtags,
      required String title,
      required String? link,
      required List<MkMedia> media,
      required String? campaignId}) async {
    await super.updatePost(postId,
        text: text,
        hashtags: hashtags,
        title: title,
        link: link,
        media: media,
        campaignId: campaignId);
    return MkPost.fromJson(
        JsonReader(postJson(postId, status: 'scheduled', text: text)));
  }

  @override
  Future<MkPost> cancel(String postId) async {
    cancelled.add(postId);
    return super.cancel(postId);
  }

  @override
  Future<MkPost> reschedule(String postId, DateTime at) async {
    rescheduled.add(postId);
    return super.reschedule(postId, at);
  }
}

void main() {
  test('a media-only post sends one variant per account, with empty text',
      () async {
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession(
        accessToken: 'token',
        tokenType: 'bearer',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
        context: const TenantContext(
            tenantId: 't', workspaceId: 'w', userId: 'u', roles: ['owner'])));
    final transport = _Transport();
    final repo = ApiMarketingRepository(AnumApiClient(
        baseUri: Uri.parse('http://localhost:8000'),
        transport: transport,
        sessions: sessions));
    await repo.createPosts(
        accountIds: ['a1', 'a2'], texts: const {}, hashtags: const {});
    final variants = transport.requests.single.body!['variants'] as List;
    expect(variants, hasLength(2));
    expect((variants.first as Map)['text'], '');
    expect((variants.first as Map)['account_id'], 'a1');
  });

  test('clearing the time of a scheduled post cancels it', () async {
    final fake = _Scheduled();
    final c = MarketingController(fake);
    await c.load();
    final post = fake.stored.first;
    expect(post.scheduledAt, isNotNull);
    await c.editPost(post,
        text: 'Same',
        hashtags: const [],
        title: '',
        link: null,
        media: const [],
        campaignId: null,
        at: null,
        done: 'ok');
    expect(fake.cancelled, [post.id]);
    expect(c.posts.firstWhere((p) => p.id == post.id).status, 'cancelled');
  });

  test('an unchanged time on a scheduled post is not cancelled', () async {
    final fake = _Scheduled();
    final c = MarketingController(fake);
    await c.load();
    final post = fake.stored.first;
    await c.editPost(post,
        text: 'Same',
        hashtags: const [],
        title: '',
        link: null,
        media: const [],
        campaignId: null,
        at: post.scheduledAt,
        done: 'ok');
    expect(fake.cancelled, isEmpty);
    expect(fake.rescheduled, isEmpty);
  });
}
