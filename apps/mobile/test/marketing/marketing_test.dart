// Marketing (docs/marketing.md): parsing, the controller against a fake API,
// and the screen in English and Arabic from a 320 px phone at 200 percent
// text to 1440 px.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/marketing/marketing_controller.dart';
import 'package:anum_mobile/features/marketing/marketing_models.dart';
import 'package:anum_mobile/features/marketing/marketing_repository.dart';
import 'package:anum_mobile/features/marketing/marketing_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const _hash =
    '9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a9a';

Map<String, Object?> postJson(String id,
        {String status = 'draft',
        String language = 'en',
        String text = 'Summer tyre check',
        Map<String, Object?>? approval,
        String? approvedHash}) =>
    {
      'id': id,
      'platform': 'facebook',
      'language': language,
      'status': status,
      'text': text,
      'hashtags': ['Tyres'],
      'account_name': 'Tyre Shop',
      'length': 25,
      'limit': 63206,
      'issues': <Object>[],
      'created_at': '2026-10-11T09:00:00Z',
      'scheduled_at': '2026-10-14T17:00:00Z',
      'approval': approval,
      'approved_hash': approvedHash,
      'metrics': {'reach': 1200, 'engagement': 90},
    };

final _approvalJson = <String, Object?>{
  'id': 'approval_mk',
  'task_id': 'task_mk',
  'action': 'marketing.schedule_post',
  'risk_level': 'high',
  'status': 'pending',
  'reason': 'External or high-impact actions require explicit approval.',
  'created_at': '2026-10-11T09:00:00Z',
  'payload_hash': _hash,
  'arguments': {'account': 'Tyre Shop', 'text': 'Summer tyre check'},
};

class FakeMarketing implements MarketingRepository {
  List<MkPost> stored = [MkPost.fromJson(JsonReader(postJson('mkpost_1')))];
  final decisions = <String?>[];
  final created = <Map<String, String>>[];
  bool deny = false;
  bool canManage = true;
  int pages = 0;

  @override
  Future<MkSetup> setup() async {
    if (deny) throw const ApiException(403, 'Workspace permission denied');
    return MkSetup.fromJson(JsonReader({
      'providers': [
        {
          'provider': 'meta',
          'label': 'Meta (Facebook and Instagram)',
          'platforms': ['facebook', 'instagram'],
          'configured': true,
          'missing': <String>[]
        },
        {
          'provider': 'tiktok',
          'label': 'TikTok',
          'platforms': ['tiktok'],
          'configured': false,
          'missing': ['ANUM_MARKETING_TIKTOK_CLIENT_KEY']
        }
      ],
      'platforms': [
        {'platform': 'facebook', 'text_limit': 63206},
        {'platform': 'x', 'text_limit': 280}
      ],
      'can_manage': canManage,
      'can_write': true,
    }));
  }

  @override
  Future<List<MkAccount>> accounts() async => [
        MkAccount.fromJson(const JsonReader({
          'id': 'acct_fb',
          'platform': 'facebook',
          'provider': 'meta',
          'display_name': 'Tyre Shop',
          'status': 'connected'
        })),
        MkAccount.fromJson(const JsonReader({
          'id': 'acct_x',
          'platform': 'x',
          'provider': 'x',
          'display_name': 'Tyre Shop X',
          'status': 'needs_reauth',
          'last_error': {
            'code': 'auth_expired',
            'message': 'The X connection expired.',
            'fix': 'Reconnect the account.'
          }
        })),
      ];

  @override
  Future<List<MkPost>> posts(
      {String? status,
      String? query,
      String? before,
      DateTime? from,
      DateTime? to,
      int limit = 20}) async {
    if (before != null) {
      pages++;
      return [MkPost.fromJson(JsonReader(postJson('mkpost_old')))];
    }
    return stored
        .where(
            (post) => status == null || status.isEmpty || post.status == status)
        .toList();
  }

  @override
  Future<List<MkPost>> createPosts(
      {required List<String> accountIds,
      required Map<String, String> texts,
      required Map<String, List<String>> hashtags,
      String? link,
      DateTime? scheduledAt}) async {
    created.add(texts);
    return [
      for (final entry in texts.entries)
        MkPost.fromJson(JsonReader(postJson('mkpost_${entry.key}',
            language: entry.key, text: entry.value)))
    ];
  }

  @override
  Future<MkPost> schedule(String postId, DateTime? at) async {
    final next = MkPost.fromJson(JsonReader(
        postJson(postId, status: 'pending_approval', approval: _approvalJson)));
    stored = [for (final post in stored) post.id == postId ? next : post];
    return next;
  }

  @override
  Future<String?> decide(WorkspaceApproval approval,
      {required bool approve}) async {
    decisions.add(decisionBody(approval, null)['payload_hash'] as String?);
    stored = [
      MkPost.fromJson(JsonReader(
          postJson('mkpost_1', status: 'scheduled', approvedHash: 'c')))
    ];
    return 'Approved. The post goes to Tyre Shop on facebook within a minute.';
  }

  @override
  Future<MkPost> reschedule(String postId, DateTime at) async => stored.first;
  @override
  Future<MkPost> cancel(String postId) async =>
      MkPost.fromJson(JsonReader(postJson(postId, status: 'cancelled')));
  @override
  Future<MkPost> publishNow(String postId) async =>
      MkPost.fromJson(JsonReader(postJson(postId, status: 'published')));
  @override
  Future<MkPost> retry(String postId) async => stored.first;
  @override
  Future<void> delete(String postId) async =>
      stored = stored.where((post) => post.id != postId).toList();
  @override
  Future<List<MkCampaign>> campaigns({String? before, int limit = 20}) async =>
      [
        MkCampaign.fromJson(const JsonReader({
          'id': 'mkcamp_1',
          'name': 'Summer tyres',
          'status': 'active',
          'auto_post': false,
          'counts': {'draft': 2, 'published': 1}
        }))
      ];
  @override
  Future<MkCampaign> createCampaign(String name, String product) async =>
      MkCampaign.fromJson(JsonReader(
          {'id': 'mkcamp_2', 'name': name, 'status': 'draft', 'counts': {}}));
  @override
  Future<MkCampaign> setCampaignStatus(String id, String status) async =>
      MkCampaign.fromJson(JsonReader(
          {'id': id, 'name': 'Summer tyres', 'status': status, 'counts': {}}));
  @override
  Future<String> startConnect(String provider) async =>
      'https://www.facebook.com/v23.0/dialog/oauth?state=mkt.example';
  @override
  Future<MkAccount> check(String accountId) async => (await accounts()).first;
  @override
  Future<MkAccount> disconnect(String accountId) async =>
      MkAccount.fromJson(const JsonReader({
        'id': 'acct_fb',
        'platform': 'facebook',
        'provider': 'meta',
        'display_name': 'Tyre Shop',
        'status': 'revoked'
      }));
  @override
  Future<MkSummary> summary(int days) async => MkSummary.fromJson(JsonReader({
        'totals': {
          'posts': 3,
          'reach': 2400,
          'impressions': 3000,
          'engagement': 150,
          'clicks': 40,
          'engagement_rate': 0.05
        },
        'platforms': [
          {
            'platform': 'instagram',
            'posts': 2,
            'reach': 1600,
            'engagement': 120
          }
        ],
        'upcoming': 2,
        'pending_approval': 1,
        'failed': 0,
      }));
  @override
  Future<MkReport> report(String language) async =>
      MkReport.fromJson(const JsonReader({
        'headline': 'Weekly marketing report',
        'lines': ['3 posts reached 2,400 people.'],
        'language': 'en',
        'delivery': {'email': 'available', 'whatsapp': 'not_connected'}
      }));

  final emailed = <String>[];
  String emailOutcome = 'requested';
  @override
  Future<MkDelivery> emailReport(String language) async {
    emailed.add(language);
    return MkDelivery.fromJson(JsonReader({
      'outcome': emailOutcome,
      'detail': emailOutcome == 'requested' ? '' : 'Connect a mailbox.'
    }));
  }
}

Future<void> pump(WidgetTester tester, MarketingController controller,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: MarketingScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('posts parse their approval, actions and composed text', () {
    final post = MkPost.fromJson(JsonReader(postJson('p',
        status: 'pending_approval',
        approval: _approvalJson,
        text: 'Hello #tyres')));
    expect(post.approval?.payloadHash, _hash);
    expect(post.composed, 'Hello #tyres');
    expect(post.canCancel, isTrue);
    expect(post.canSchedule, isFalse);
    expect(parseHashtags('#Tyres tyres، إطارات'), ['Tyres', 'إطارات']);
  });

  test('the controller loads, pages, schedules and approves with the hash',
      () async {
    final fake = FakeMarketing();
    final c = MarketingController(fake);
    await c.load();
    expect(c.phase, MkPhase.ready);
    expect(c.connected.map((a) => a.id), ['acct_fb']);
    await c.loadMorePosts();
    expect(c.posts.map((p) => p.id), ['mkpost_1', 'mkpost_old']);
    await c.schedule(c.posts.first, null, 'sent');
    expect(c.posts.first.status, 'pending_approval');
    await c.decide(c.posts.first.approval!,
        approve: true, fallback: 'Approved.');
    expect(fake.decisions, [_hash]);
    expect(c.posts.first.status, 'scheduled');
    expect(c.notice, startsWith('Approved.'));
  });

  test('the weekly report is emailed only through an approval', () async {
    final fake = FakeMarketing();
    final c = MarketingController(fake);
    await c.loadAnalytics(language: 'ar');
    expect(c.report?.emailAvailable, isTrue);
    expect(
        await c.emailReport('ar',
            requested: 'waiting', notSent: (reason) => 'no: $reason'),
        isTrue);
    expect(fake.emailed, ['ar']);
    expect(c.notice, 'waiting');
    fake.emailOutcome = 'not_connected';
    expect(
        await c.emailReport('en',
            requested: 'waiting', notSent: (reason) => 'no: $reason'),
        isFalse);
    expect(c.notice, 'no: Connect a mailbox.');
  });

  test('a 403 shows who can use it', () async {
    final c = MarketingController(FakeMarketing()..deny = true);
    await c.load();
    expect(c.phase, MkPhase.denied);
  });

  testWidgets('composes a bilingual draft and approves a post', (tester) async {
    final fake = FakeMarketing();
    final c = MarketingController(fake);
    await pump(tester, c, size: const Size(1024, 900));
    expect(find.text('Summer tyre check\n\n#Tyres'), findsOneWidget);
    await tester.tap(find.text('New post').last);
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, 'Text in English'), 'Check tyres');
    await tester.enterText(
        find.widgetWithText(TextField, 'Text in Arabic'), 'افحص الإطارات');
    await tester.tap(find.text('Save drafts'));
    await tester.pumpAndSettle();
    expect(fake.created, [
      {'en': 'Check tyres', 'ar': 'افحص الإطارات'}
    ]);
    await c.schedule(c.posts.last, null, 'sent');
    await tester.pumpAndSettle();
    expect(find.textContaining('Payload hash: 9a9a9a9a9a9a…'), findsOneWidget);
    await tester.ensureVisible(find.text('Approve'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Approve'));
    await tester.pumpAndSettle();
    expect(fake.decisions, [_hash]);
  });

  testWidgets('accounts show what is missing and what to fix', (tester) async {
    final c = MarketingController(FakeMarketing()..canManage = false);
    await pump(tester, c, size: const Size(1024, 900));
    await tester.tap(find.text('Accounts'));
    await tester.pumpAndSettle();
    expect(find.textContaining('ANUM_MARKETING_TIKTOK_CLIENT_KEY'),
        findsOneWidget);
    expect(find.text('Only workspace owners connect or disconnect accounts.'),
        findsOneWidget);
    expect(find.text('Reconnect the account.'), findsOneWidget);
  });

  for (final locale in const [Locale('en'), Locale('ar')]) {
    for (final width in const [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in const [1.0, 2.0]) {
        testWidgets(
            'lays out in ${locale.languageCode} at $width px, text x$scale',
            (tester) async {
          final c = MarketingController(FakeMarketing());
          await pump(tester, c,
              size: Size(width, 900), textScale: scale, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale.languageCode == 'ar') {
            expect(find.text('التسويق'), findsWidgets);
            expect(Directionality.of(tester.element(find.byType(TabBar))),
                TextDirection.rtl);
          }
          for (final tab in locale.languageCode == 'ar'
              ? const ['التقويم', 'الحملات', 'الحسابات', 'التحليلات']
              : const ['Calendar', 'Campaigns', 'Accounts', 'Analytics']) {
            await tester.tap(find.text(tab));
            await tester.pumpAndSettle();
            expect(tester.takeException(), isNull, reason: tab);
          }
        });
      }
    }
  }
}
