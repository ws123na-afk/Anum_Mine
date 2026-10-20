// Marketing (docs/marketing.md): parsing, the controller against a fake API,
// and the screen in English and Arabic from a 320 px phone at 200 percent
// text to 1440 px.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/marketing/marketing_controller.dart';
import 'package:anum_mobile/features/marketing/marketing_models.dart';
import 'package:anum_mobile/features/marketing/marketing_ops_models.dart';
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
  final createdExtras = <Map<String, Object?>>[];

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
      Map<String, String>? linksByAccount,
      String? title,
      String? campaignId,
      List<MkMedia> media = const [],
      bool aiGenerated = false,
      bool untrustedSource = false,
      DateTime? scheduledAt}) async {
    created.add(texts);
    createdExtras.add({
      'links': linksByAccount,
      'link': link,
      'campaign': campaignId,
      'media': [for (final item in media) item.fileId],
      'ai': aiGenerated,
      'untrusted': untrustedSource,
    });
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
  // -- campaigns -----------------------------------------------------------
  String campaignStatus = 'active';
  bool deleteRefuses = false;
  final campaignCalls = <String>[];
  final autoPostCalls = <Map<String, Object?>>[];
  bool bigCampaignList = false;
  String? lastCampaignQuery;

  static Map<String, Object?> campaignJson(String id, String name,
          {String status = 'active',
          bool autoPost = false,
          Map<String, Object?>? approval}) =>
      {
        'id': id,
        'name': name,
        'status': status,
        'auto_post': autoPost,
        'auto_post_accounts': autoPost ? ['acct_fb'] : <String>[],
        'auto_post_approval': approval,
        'product': 'TyrePulse',
        'objective': 'Awareness',
        'brief': 'Summer road trips',
        'utm_campaign': 'summer_tyres',
        'start_date': '2026-11-01',
        'end_date': null,
        'counts': {'draft': 2, 'published': 1}
      };

  @override
  Future<List<MkCampaign>> campaigns(
      {String? before, int limit = 20, String? query, String? status}) async {
    lastCampaignQuery = query;
    if (bigCampaignList) {
      if (before != null) {
        return [
          MkCampaign.fromJson(
              JsonReader(campaignJson('mkcamp_last', 'Last campaign')))
        ];
      }
      return [
        for (var i = 0; i < limit; i++)
          MkCampaign.fromJson(
              JsonReader(campaignJson('mkcamp_$i', 'Campaign $i')))
      ];
    }
    final all = [
      campaignJson('mkcamp_1', 'Summer tyres', status: campaignStatus),
    ];
    return [
      for (final item in all)
        if ((status == null || status.isEmpty || item['status'] == status) &&
            (query == null ||
                query.isEmpty ||
                (item['name'] as String)
                    .toLowerCase()
                    .contains(query.toLowerCase())))
          MkCampaign.fromJson(JsonReader(item))
    ];
  }

  @override
  Future<MkCampaign> createCampaign(MkCampaignInput input) async {
    campaignCalls.add('create:${input.name}');
    return MkCampaign.fromJson(JsonReader({
      ...campaignJson('mkcamp_2', input.name, status: 'draft'),
      'counts': {}
    }));
  }

  MkCampaignInput? lastCampaignInput;
  @override
  Future<MkCampaign> updateCampaign(String id, MkCampaignInput input) async {
    lastCampaignInput = input;
    campaignCalls.add('update:$id');
    return MkCampaign.fromJson(JsonReader({
      ...campaignJson(id, input.name, status: campaignStatus),
      'product': input.product,
      'utm_campaign': input.utmCampaign,
    }));
  }

  @override
  Future<void> deleteCampaign(String id) async {
    campaignCalls.add('delete:$id');
    if (deleteRefuses) {
      throw const ApiException(409,
          '2 posts of this campaign are scheduled or waiting for approval. Cancel them first, or archive the campaign.');
    }
  }

  @override
  Future<MkCampaign> setAutoPost(String id,
      {required bool enabled, List<String> accountIds = const []}) async {
    autoPostCalls.add({'id': id, 'enabled': enabled, 'accounts': accountIds});
    if (!canManage) {
      throw const ApiException(403, 'Only workspace owners can do this');
    }
    if (enabled && campaignStatus != 'active') {
      throw const ApiException(409,
          'Activate the campaign first: auto-post works for active campaigns only.');
    }
    return MkCampaign.fromJson(JsonReader(campaignJson(id, 'Summer tyres',
        autoPost: false, approval: enabled ? _approvalJson : null)));
  }

  @override
  Future<MkCampaign> setCampaignStatus(String id, String status) async {
    campaignStatus = status;
    return MkCampaign.fromJson(
        JsonReader({...campaignJson(id, 'Summer tyres', status: status)}));
  }

  // -- post editing --------------------------------------------------------
  final updates = <Map<String, Object?>>[];
  @override
  Future<MkPost> updatePost(String postId,
      {required String text,
      required List<String> hashtags,
      required String title,
      required String? link,
      required List<MkMedia> media,
      required String? campaignId}) async {
    updates.add({
      'id': postId,
      'text': text,
      'hashtags': hashtags,
      'link': link,
      'media': [for (final item in media) item.fileId],
      'campaign': campaignId,
    });
    final next = MkPost.fromJson(JsonReader(postJson(postId, text: text)));
    stored = [for (final post in stored) post.id == postId ? next : post];
    return next;
  }

  // -- AI assist -------------------------------------------------------------
  ApiException? ideasError;
  String generator = 'model';
  bool untrustedIdeas = false;
  final ideaRequests = <Map<String, Object?>>[];
  @override
  Future<MkIdeas> ideas(
      {required String brief,
      required List<String> platforms,
      required List<String> languages,
      String? campaignId,
      String source = '',
      int count = 3}) async {
    ideaRequests
        .add({'brief': brief, 'source': source, 'campaign': campaignId});
    if (ideasError != null) throw ideasError!;
    return MkIdeas.fromJson(JsonReader({
      'ideas': [
        {
          'title': 'Summer check',
          'angle': 'Safety first',
          'platform': 'facebook',
          'en': 'Check your tyres before the trip',
          'ar': 'افحص إطاراتك قبل الرحلة',
          'hashtags_en': ['Tyres', 'Summer'],
          'hashtags_ar': ['إطارات']
        }
      ],
      'generator': generator,
      'untrusted_source': untrustedIdeas,
      'brand_voice_used': true,
      'note': generator == 'template'
          ? 'No model is connected, so these come from templates.'
          : null,
      'best_times': [
        {'weekday': 6, 'hour': 21, 'posts': 0, 'basis': 'default'}
      ]
    }));
  }

  ApiException? hashtagsError;
  @override
  Future<MkHashtags> suggestHashtags(
      {required String text,
      required String platform,
      required String language}) async {
    if (hashtagsError != null) throw hashtagsError!;
    return MkHashtags.fromJson(JsonReader({
      'hashtags': ['Tyres', 'KSA'],
      'generator': generator
    }));
  }

  // -- brand voice -------------------------------------------------------------
  ApiException? prefsError;
  MkPreferences prefs = const MkPreferences(
      brandVoice: 'Friendly and practical',
      timezone: 'Asia/Riyadh',
      hashtagsEn: ['Tyres'],
      hashtagsAr: ['إطارات']);
  ApiException? savePrefsError;
  MkPreferences? savedPrefs;
  @override
  Future<MkPreferences> preferences() async {
    if (prefsError != null) throw prefsError!;
    return prefs;
  }

  @override
  Future<MkPreferences> savePreferences(MkPreferences value) async {
    if (savePrefsError != null) throw savePrefsError!;
    savedPrefs = value;
    prefs = value;
    return value;
  }

  // -- validation, tracking, media, best times ------------------------------------
  bool validateFails = false;
  ApiException? validateError;
  int validateCalls = 0;
  @override
  Future<MkValidation> validate(
      {required String platform,
      required String text,
      required List<String> hashtags,
      String? link,
      String title = '',
      List<MkMedia> media = const []}) async {
    validateCalls++;
    if (validateError != null) throw validateError!;
    return MkValidation.fromJson(JsonReader({
      'length': text.length,
      'limit': 280,
      'hashtags': hashtags.length,
      'ok': !validateFails,
      'issues': validateFails
          ? [
              {
                'code': 'text_too_long',
                'severity': 'error',
                'message': 'X takes at most 280 characters.'
              }
            ]
          : <Object>[]
    }));
  }

  @override
  Future<String> trackedLink(
          {required String url,
          required String platform,
          String campaign = '',
          String content = ''}) async =>
      '$url?utm_source=$platform&utm_campaign=$campaign';

  ApiException? mediaError;
  List<Map<String, Object?>> mediaFiles = [
    {
      'id': 'file_1',
      'name': 'tyre.jpg',
      'kind': 'image',
      'content_type': 'image/jpeg',
      'size_bytes': 2048
    },
    {
      'id': 'file_2',
      'name': 'ad.mp4',
      'kind': 'video',
      'content_type': 'video/mp4',
      'size_bytes': 5242880
    },
  ];
  @override
  Future<List<MkMediaFile>> media({String? query, int limit = 50}) async {
    if (mediaError != null) throw mediaError!;
    return [
      for (final item in mediaFiles)
        if (query == null ||
            query.isEmpty ||
            (item['name'] as String).contains(query))
          MkMediaFile.fromJson(JsonReader(item))
    ];
  }

  ApiException? timesError;
  bool noTimes = false;
  @override
  Future<List<MkTimeSlot>> bestTimes({String? platform}) async {
    if (timesError != null) throw timesError!;
    if (noTimes) return const [];
    return [
      MkTimeSlot.fromJson(const JsonReader({
        'weekday': 6,
        'hour': 21,
        'posts': 6,
        'basis': 'history',
        'engagement_rate': 0.052
      })),
      MkTimeSlot.fromJson(const JsonReader(
          {'weekday': 2, 'hour': 20, 'posts': 0, 'basis': 'default'})),
    ];
  }

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
  test('only absolute https links are opened from posts and connect pages', () {
    expect(mkHttpsLink('https://www.linkedin.com/feed/update/1')?.host,
        'www.linkedin.com');
    expect(mkHttpsLink('  https://x.com/a/status/1 ')?.path, '/a/status/1');
    for (final bad in [
      null,
      '',
      'http://example.com/post',
      'intent://scan/#Intent;scheme=zxing;end',
      'file:///data/data/com.anum.app/shared_prefs/x.xml',
      'tel:+971500000000',
      'javascript:alert(1)',
      'https:///no-host',
      '/relative/path',
    ]) {
      expect(mkHttpsLink(bad), isNull, reason: '$bad');
    }
  });

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
    await tester.ensureVisible(find.text('Save drafts'));
    await tester.pumpAndSettle();
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
