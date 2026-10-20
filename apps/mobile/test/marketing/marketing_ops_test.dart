// Marketing ops (docs/marketing.md): post editing, campaign edit, delete and
// auto-post, AI assist, media, brand voice, live limit checks, link tracking
// and best times, against the fake API of marketing_test.dart.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/marketing/marketing_controller.dart';
import 'package:anum_mobile/features/marketing/marketing_models.dart';
import 'package:anum_mobile/features/marketing/marketing_ops_controller.dart';
import 'package:anum_mobile/features/marketing/marketing_ops_models.dart';
import 'package:anum_mobile/features/marketing/marketing_composer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'marketing_test.dart' show FakeMarketing, postJson, pump;

/// Scrolls [finder] into view in its list or sheet, then taps it.
Future<void> tapText(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> openTab(WidgetTester tester, String name) =>
    tapText(tester, find.text(name));

/// Types into the field labelled [label] after scrolling it into view.
Future<void> typeInto(WidgetTester tester, String label, String value) async {
  final field = find.widgetWithText(TextField, label);
  await tester.ensureVisible(field);
  await tester.pumpAndSettle();
  await tester.enterText(field, value);
  await tester.pump();
}

Future<void> openComposer(WidgetTester tester) async {
  await tester.tap(find.text('New post').last);
  await tester.pumpAndSettle();
}

void main() {
  group('models', () {
    test('a post keeps what the editor needs', () {
      final post = MkPost.fromJson(JsonReader({
        ...postJson('p'),
        'title': 'Clip',
        'link_url': 'https://shop.example/tyres',
        'campaign_id': 'mkcamp_1',
        'ai_generated': true,
        'media': [
          {
            'file_id': 'file_1',
            'kind': 'video',
            'name': 'ad.mp4',
            'content_type': 'video/mp4',
            'size_bytes': 10,
            'width': 1080,
            'height': 1920,
            'duration_seconds': 12.5
          }
        ]
      }));
      expect(post.linkUrl, 'https://shop.example/tyres');
      expect(post.campaignId, 'mkcamp_1');
      expect(post.aiGenerated, isTrue);
      expect(post.media.single.toInput(), {
        'file_id': 'file_1',
        'width': 1080,
        'height': 1920,
        'duration_seconds': 12.5
      });
    });

    test('a campaign keeps its dates, tracking name and auto-post accounts',
        () {
      final campaign = MkCampaign.fromJson(JsonReader(
          FakeMarketing.campaignJson('c', 'Summer', autoPost: true)));
      expect(campaign.startDate, '2026-11-01');
      expect(campaign.endDate, isNull);
      expect(campaign.utmCampaign, 'summer_tyres');
      expect(campaign.autoPostAccounts, ['acct_fb']);
      expect(mkIsoDate(DateTime(2026, 3, 7)), '2026-03-07');
    });

    test('best times read as a weekday and an hour', () {
      const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      const slot = MkTimeSlot(weekday: 6, hour: 21, posts: 0, basis: 'default');
      expect(mkSlotLabel(slot, days), 'Sun 21:00');
      expect(slot.fromHistory, isFalse);
    });
  });

  group('controller', () {
    test('editing a post saves the content and moves the time', () async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await c.load();
      final post = c.posts.first;
      final at = DateTime.now().add(const Duration(days: 2));
      expect(
          await c.editPost(post,
              text: 'New copy',
              hashtags: const ['Tyres'],
              title: '',
              link: null,
              media: const [],
              campaignId: 'mkcamp_1',
              at: at,
              done: 'saved'),
          isTrue);
      expect(fake.updates.single['text'], 'New copy');
      expect(fake.updates.single['campaign'], 'mkcamp_1');
      expect(c.notice, 'saved');
    });

    test('a refused edit shows the server words', () async {
      final fake = _RefusingEdit();
      final c = MarketingController(fake);
      await c.load();
      expect(
          await c.editPost(c.posts.first,
              text: 'x',
              hashtags: const [],
              title: '',
              link: null,
              media: const [],
              campaignId: null,
              done: 'saved'),
          isFalse);
      expect(c.notice, 'This post is published and cannot be edited.');
    });

    test('a campaign is created, edited and deleted', () async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await c.load();
      await c.createCampaign(const MkCampaignInput(name: 'Winter'), 'made');
      expect(c.campaigns.first.name, 'Winter');
      await c.updateCampaign(
          c.campaigns.last,
          const MkCampaignInput(
              name: 'Summer 2', product: 'ANUM', utmCampaign: 'summer_2'),
          'updated');
      expect(c.campaigns.last.name, 'Summer 2');
      expect(c.campaigns.last.utmCampaign, 'summer_2');
      await c.deleteCampaign(c.campaigns.last, 'gone');
      expect(c.campaigns.map((x) => x.id), ['mkcamp_2']);
      expect(fake.campaignCalls, [
        'create:Winter',
        'update:mkcamp_1',
        'delete:mkcamp_1',
      ]);
    });

    test('a campaign with queued posts is not deleted and says why', () async {
      final fake = FakeMarketing()..deleteRefuses = true;
      final c = MarketingController(fake);
      await c.load();
      expect(await c.deleteCampaign(c.campaigns.first, 'gone'), isFalse);
      expect(c.campaigns, hasLength(1));
      expect(c.notice, startsWith('2 posts of this campaign are scheduled'));
    });

    test('auto-post asks for approval, and the server rules are shown',
        () async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await c.load();
      await c.setAutoPost(c.campaigns.first,
          enabled: true, accountIds: ['acct_fb'], done: 'asked');
      expect(c.campaigns.first.approval, isNotNull);
      expect(c.campaigns.first.autoPost, isFalse);
      expect(fake.autoPostCalls.single['accounts'], ['acct_fb']);

      fake.campaignStatus = 'paused';
      await c.setAutoPost(c.campaigns.first,
          enabled: true, accountIds: ['acct_fb'], done: 'asked');
      expect(c.notice,
          'Activate the campaign first: auto-post works for active campaigns only.');
      fake.canManage = false;
      await c.setAutoPost(c.campaigns.first, enabled: false, done: 'off');
      expect(c.notice, 'Not allowed: Only workspace owners can do this');
    });

    test('campaign search and status go to the API', () async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await c.load();
      await c.filterCampaigns(search: 'nothing');
      expect(c.campaigns, isEmpty);
      expect(fake.lastCampaignQuery, 'nothing');
      await c.filterCampaigns(search: 'summer');
      expect(c.campaigns, hasLength(1));
    });

    test('campaigns page', () async {
      final fake = FakeMarketing()..bigCampaignList = true;
      final c = MarketingController(fake);
      await c.load();
      expect(c.moreCampaigns, isTrue);
      await c.loadMoreCampaigns();
      expect(c.campaigns.last.id, 'mkcamp_last');
      expect(c.moreCampaigns, isFalse);
    });

    test('AI ideas say where they came from; refusals say what to do',
        () async {
      final fake = FakeMarketing();
      final ops = MarketingController(fake).ops;
      expect(
          await ops.suggestIdeas(
              brief: 'tyres', platforms: ['facebook'], languages: ['en']),
          isTrue);
      expect(ops.ideas!.fromModel, isTrue);
      expect(ops.ideas!.bestTimes.single.hour, 21);

      fake.generator = 'template';
      fake.untrustedIdeas = true;
      await ops.suggestIdeas(
          brief: 'tyres',
          platforms: ['facebook'],
          languages: ['en'],
          source: 'listing text');
      expect(ops.ideas!.fromModel, isFalse);
      expect(ops.ideas!.untrustedSource, isTrue);
      expect(fake.ideaRequests.last['source'], 'listing text');

      for (final (error, kind) in [
        (const ApiException(402, 'Budget used.'), MkAssistProblem.budget),
        (const ApiException(403, 'No right.'), MkAssistProblem.denied),
        (const ApiException(503, 'No model.'), MkAssistProblem.unavailable),
        (const ApiException(500, 'Boom.'), MkAssistProblem.other),
      ]) {
        fake.ideasError = error;
        expect(
            await ops.suggestIdeas(
                brief: 'x', platforms: ['facebook'], languages: ['en']),
            isFalse);
        expect(ops.assistProblem, kind);
        expect(ops.assistMessage, error.message);
      }
    });

    test('hashtags come back with their source or a refusal', () async {
      final fake = FakeMarketing();
      final ops = MarketingController(fake).ops;
      final tags = await ops.suggestHashtags(
          text: 'x', platform: 'facebook', language: 'en');
      expect(tags!.hashtags, ['Tyres', 'KSA']);
      fake.hashtagsError = const ApiException(402, 'Budget used.');
      expect(
          await ops.suggestHashtags(
              text: 'x', platform: 'facebook', language: 'en'),
          isNull);
      expect(ops.assistProblem, MkAssistProblem.budget);
    });

    test('brand voice loads, saves, and a 403 or an unknown zone is said',
        () async {
      final fake = FakeMarketing();
      final ops = MarketingController(fake).ops;
      await ops.loadPreferences();
      expect(ops.prefsPhase, MkLoad.ready);
      expect(ops.prefs!.brandVoice, 'Friendly and practical');
      expect(
          await ops.savePreferences(const MkPreferences(
              brandVoice: 'Warm',
              timezone: 'Asia/Dubai',
              hashtagsEn: ['A'],
              hashtagsAr: [])),
          isNull);
      expect(fake.savedPrefs!.timezone, 'Asia/Dubai');
      fake.savePrefsError =
          const ApiException(422, 'Unknown time zone "Mars/Base".');
      final failure = await ops.savePreferences(fake.prefs);
      expect(MarketingController.describe(failure!),
          'Unknown time zone "Mars/Base".');
      fake.prefsError = const ApiException(403, 'Marketing is not for you.');
      await ops.loadPreferences();
      expect(ops.prefsPhase, MkLoad.denied);
      fake.prefsError = const ApiException(500, 'Down.');
      await ops.loadPreferences();
      expect(ops.prefsPhase, MkLoad.error);
    });

    test('media searches and grows its page', () async {
      final fake = FakeMarketing();
      final ops = MarketingController(fake).ops;
      await ops.loadMedia();
      expect(ops.mediaFiles.map((x) => x.name), ['tyre.jpg', 'ad.mp4']);
      expect(ops.moreMedia, isFalse);
      await ops.loadMedia(query: 'ad');
      expect(ops.mediaFiles.map((x) => x.name), ['ad.mp4']);
      fake.mediaError = const ApiException(403, 'Files are off.');
      await ops.loadMedia();
      expect(ops.mediaPhase, MkLoad.denied);
    });

    test('best times load, are empty, or fail', () async {
      final fake = FakeMarketing();
      final ops = MarketingController(fake).ops;
      await ops.loadBestTimes();
      expect(ops.slots, hasLength(2));
      fake.noTimes = true;
      await ops.loadBestTimes();
      expect(ops.slots, isEmpty);
      expect(ops.timesPhase, MkLoad.ready);
      fake.timesError = const ApiException(500, 'Down.');
      await ops.loadBestTimes();
      expect(ops.timesPhase, MkLoad.error);
    });
  });

  group('composer', () {
    testWidgets('editing a post fills the form and saves the change',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await tapText(tester, find.text('Edit'));
      expect(find.text('Edit post'), findsOneWidget);
      expect(find.text('Summer tyre check'), findsWidgets);
      await typeInto(tester, 'Text in English', 'Changed copy');
      await tapText(tester, find.text('Save changes'));
      expect(fake.updates.single['text'], 'Changed copy');
      expect(fake.updates.single['hashtags'], ['Tyres']);
      expect(find.text('Edit post'), findsNothing);
    });

    testWidgets('AI ideas are labelled and tapping Use inserts them',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await tapText(tester, find.text('Suggest with AI'));
      await typeInto(tester, 'What to promote', 'Summer tyre check');
      await tapText(tester, find.text('Suggest ideas'));
      expect(find.text('Written by your connected model'), findsOneWidget);
      expect(find.text('Uses your brand voice'), findsOneWidget);
      expect(find.textContaining('Good times to post: Sunday 21:00'),
          findsOneWidget);
      await tapText(tester, find.text('Use this idea'));
      expect(find.text('Check your tyres before the trip'), findsWidgets);
      expect(find.textContaining('Inserted the idea'), findsOneWidget);
      await tapText(tester, find.text('Save drafts'));
      expect(fake.created.single['en'], 'Check your tyres before the trip');
      expect(fake.createdExtras.single['ai'], isTrue);
      expect(fake.createdExtras.single['untrusted'], isFalse);
    });

    testWidgets('template ideas from pasted text are flagged untrusted',
        (tester) async {
      final fake = FakeMarketing()
        ..generator = 'template'
        ..untrustedIdeas = true;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await tapText(tester, find.text('Suggest with AI'));
      await typeInto(tester, 'What to promote', 'x');
      await typeInto(tester, 'Source text (optional)', 'A listing');
      await tapText(tester, find.text('Suggest ideas'));
      expect(find.text('Built from a template, no model used'), findsOneWidget);
      expect(find.text('Built from pasted text'), findsOneWidget);
      expect(find.textContaining('Check every claim'), findsOneWidget);
      expect(find.textContaining('come from templates'), findsOneWidget);
      await tapText(tester, find.text('Use this idea'));
      await tapText(tester, find.text('Save drafts'));
      expect(fake.createdExtras.single['ai'], isFalse);
      expect(fake.createdExtras.single['untrusted'], isTrue);
    });

    testWidgets('budget, permission and model refusals are shown',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await tapText(tester, find.text('Suggest with AI'));
      await tapText(tester, find.text('Suggest ideas'));
      expect(find.textContaining('Describe what to promote'), findsOneWidget);
      await typeInto(tester, 'What to promote', 'x');
      for (final (error, shown) in [
        (
          const ApiException(402, 'Monthly budget reached.'),
          'The AI budget is used up'
        ),
        (const ApiException(403, 'Members cannot.'), 'Not allowed: Members'),
        (
          const ApiException(503, 'No model is connected.'),
          'AI assist is not available right now. No model is connected.'
        ),
      ]) {
        fake.ideasError = error;
        await tapText(tester, find.text('Suggest ideas'));
        expect(find.textContaining(shown), findsOneWidget, reason: shown);
      }
    });

    testWidgets('hashtag suggestions are added and say their source',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await tapText(tester, find.text('Suggest English hashtags'));
      expect(find.text('Write the post first, then ask for hashtags.'),
          findsOneWidget);
      await typeInto(tester, 'Text in English', 'Hello');
      await tapText(tester, find.text('Suggest English hashtags'));
      expect(find.text('Tyres KSA'), findsOneWidget);
      expect(find.text('Hashtags suggested by your connected model.'),
          findsOneWidget);
      fake.hashtagsError = const ApiException(402, 'Budget used.');
      await tapText(tester, find.text('Suggest English hashtags'));
      expect(find.textContaining('The AI budget is used up'), findsOneWidget);
    });

    testWidgets('limits are checked live and a failing check blocks saving',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await typeInto(tester, 'Text in English', 'Hello');
      await tester.pump(const Duration(milliseconds: 700));
      await tester.pumpAndSettle();
      expect(fake.validateCalls, greaterThan(0));
      expect(
          find.text('Facebook, English: 5 of 280 characters'), findsOneWidget);
      fake.validateFails = true;
      await typeInto(tester, 'Text in English', 'Hello again');
      await tester.pump(const Duration(milliseconds: 700));
      await tester.pumpAndSettle();
      expect(find.text('X takes at most 280 characters.'), findsOneWidget);
      expect(find.text('Fix the problems shown above before saving.'),
          findsOneWidget);
      final save = tester.widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Save drafts'));
      expect(save.onPressed, isNull);
    });

    testWidgets('an unavailable live check does not stop saving',
        (tester) async {
      final fake = FakeMarketing()
        ..validateError = const ApiException(500, 'Down.');
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await typeInto(tester, 'Text in English', 'Hello');
      await tester.pump(const Duration(milliseconds: 700));
      await tester.pumpAndSettle();
      expect(find.textContaining('live limit check is not available'),
          findsOneWidget);
      await tapText(tester, find.text('Save drafts'));
      expect(fake.created, hasLength(1));
    });

    testWidgets('links are tracked per platform unless switched off',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await typeInto(tester, 'Text in English', 'Hello');
      await typeInto(tester, 'Link (optional)', 'https://shop.example/tyres');
      await tapText(tester, find.text('Save drafts'));
      expect(fake.createdExtras.single['links'], {
        'acct_fb':
            'https://shop.example/tyres?utm_source=facebook&utm_campaign='
      });

      await openComposer(tester);
      await typeInto(tester, 'Text in English', 'Hello');
      await typeInto(tester, 'Link (optional)', 'not a link');
      await tapText(tester, find.text('Save drafts'));
      expect(find.text('The link must start with https:// and name a website.'),
          findsOneWidget);
      await typeInto(tester, 'Link (optional)', 'https://shop.example/tyres');
      await tapText(
          tester,
          find.text(
              'Add campaign tracking (UTM) to the link for each platform'));
      await tapText(tester, find.text('Save drafts'));
      expect(fake.createdExtras.last['links'], isNull);
      expect(fake.createdExtras.last['link'], 'https://shop.example/tyres');
    });

    testWidgets('media is picked from Files and sent with the post',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await typeInto(tester, 'Text in English', 'Hello');
      expect(find.textContaining('No media attached'), findsOneWidget);
      await tapText(tester, find.text('Attach from Files'));
      expect(find.text('tyre.jpg'), findsOneWidget);
      expect(find.textContaining('5.0 MB'), findsOneWidget);
      await tester.tap(find.text('tyre.jpg'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Done (1 chosen)'));
      await tester.pumpAndSettle();
      expect(find.text('tyre.jpg'), findsOneWidget);
      await tapText(tester, find.text('Save drafts'));
      expect(fake.createdExtras.single['media'], ['file_1']);
    });

    testWidgets('the media library shows empty, error and denied states',
        (tester) async {
      final fake = FakeMarketing()..mediaFiles = [];
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openComposer(tester);
      await tapText(tester, find.text('Attach from Files'));
      expect(find.textContaining('No images or videos in Files yet'),
          findsOneWidget);
      await tester.tap(find.text('Cancel').last);
      await tester.pumpAndSettle();

      fake.mediaError = const ApiException(500, 'Storage is down.');
      await tapText(tester, find.text('Attach from Files'));
      expect(find.textContaining('Storage is down.'), findsOneWidget);
      fake.mediaError = null;
      fake.mediaFiles = [
        {
          'id': 'f',
          'name': 'back.png',
          'kind': 'image',
          'content_type': 'image/png',
          'size_bytes': 1
        }
      ];
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('back.png'), findsOneWidget);
    });
  });

  group('campaigns tab', () {
    testWidgets('edit, then delete with the server refusal shown',
        (tester) async {
      final fake = FakeMarketing()..deleteRefuses = true;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      expect(find.text('utm_campaign=summer_tyres'), findsOneWidget);
      expect(find.text('2026-11-01 to -'), findsOneWidget);

      await tapText(tester, find.text('Edit campaign'));
      await typeInto(tester, 'Name', 'Summer tyres 2');
      await tapText(tester, find.text('Save changes'));
      expect(fake.lastCampaignInput!.name, 'Summer tyres 2');
      expect(fake.lastCampaignInput!.startDate, '2026-11-01');

      await tester.tap(find.byTooltip('Delete campaign Summer tyres 2'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Its 3 posts stay'), findsOneWidget);
      await tester.tap(find.text('Delete').last);
      await tester.pumpAndSettle();
      expect(find.textContaining('2 posts of this campaign are scheduled'),
          findsOneWidget);
      expect(find.text('Summer tyres 2'), findsOneWidget);

      fake.deleteRefuses = false;
      await tester.tap(find.byTooltip('Delete campaign Summer tyres 2'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete').last);
      await tester.pumpAndSettle();
      expect(find.text('Summer tyres 2'), findsNothing);
      expect(find.text('No campaigns yet'), findsOneWidget);
    });

    testWidgets('the form checks the name, the tracking name and the dates',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      await tapText(tester, find.text('New campaign').first);
      await typeInto(tester, 'Name', '');
      await tapText(tester, find.text('Create'));
      expect(find.text('Give the campaign a name.'), findsOneWidget);
      await typeInto(tester, 'Name', 'Winter');
      await typeInto(tester, 'Tracking name (utm_campaign)', 'bad name!');
      await tapText(tester, find.text('Create'));
      expect(find.textContaining('only use letters'), findsOneWidget);
      await typeInto(tester, 'Tracking name (utm_campaign)', 'winter_1');
      await tapText(tester, find.text('Create'));
      expect(fake.campaignCalls, ['create:Winter']);
    });

    testWidgets('owners turn auto-post on through an approval', (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      expect(find.text('Every post waits for an owner\'s approval.'),
          findsOneWidget);
      await tapText(tester, find.text('Turn on auto-post'));
      expect(find.text('Auto-post for this campaign'), findsOneWidget);
      await tapText(tester, find.text('Ask for approval'));
      expect(fake.autoPostCalls.single['accounts'], ['acct_fb']);
      expect(find.text('Waiting for an owner to approve publishing'),
          findsOneWidget);
    });

    testWidgets('a paused campaign explains auto-post; a refusal is shown',
        (tester) async {
      final fake = FakeMarketing()..campaignStatus = 'paused';
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      expect(
          find.textContaining('Activate the campaign first'), findsOneWidget);
      final button = tester.widget<OutlinedButton>(
          find.widgetWithText(OutlinedButton, 'Turn on auto-post'));
      expect(button.onPressed, isNull);
    });

    testWidgets('members see auto-post as owner only', (tester) async {
      final fake = FakeMarketing()..canManage = false;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      expect(
          find.text('Only workspace owners change auto-post.'), findsOneWidget);
      expect(find.text('Turn on auto-post'), findsNothing);
    });

    testWidgets('search and the next page', (tester) async {
      final fake = FakeMarketing()..bigCampaignList = true;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Campaigns');
      await tapText(tester, find.text('Load more'));
      expect(find.text('Last campaign'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextField, 'Search campaigns'), 'zzz');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(fake.lastCampaignQuery, 'zzz');
    });
  });

  group('accounts and analytics tabs', () {
    testWidgets('owners edit the brand voice', (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Accounts');
      expect(find.text('Brand voice and defaults'), findsOneWidget);
      await typeInto(tester, 'Brand voice', 'Short and warm');
      await typeInto(tester, 'Default English hashtags', '#KSA tyres');
      await tapText(tester, find.text('Save defaults'));
      expect(fake.savedPrefs!.brandVoice, 'Short and warm');
      expect(fake.savedPrefs!.hashtagsEn, ['KSA', 'tyres']);
      expect(find.text('Defaults saved.'), findsOneWidget);
    });

    testWidgets('a refused save shows the server words', (tester) async {
      final fake = FakeMarketing()
        ..savePrefsError = const ApiException(422, 'Unknown time zone "X".');
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Accounts');
      await tapText(tester, find.text('Save defaults'));
      expect(find.text('Unknown time zone "X".'), findsOneWidget);
    });

    testWidgets('members read the brand voice but cannot save', (tester) async {
      final fake = FakeMarketing()..canManage = false;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Accounts');
      expect(find.textContaining('Only workspace owners change these'),
          findsOneWidget);
      expect(find.text('Friendly and practical'), findsOneWidget);
      expect(find.text('Save defaults'), findsNothing);
    });

    testWidgets('brand voice denied shows the API text', (tester) async {
      final fake = FakeMarketing()
        ..prefsError = const ApiException(403, 'Marketing is members only.');
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Accounts');
      expect(
          find.text('Not allowed: Marketing is members only.'), findsOneWidget);
    });

    testWidgets('brand voice error offers a retry', (tester) async {
      final broken = FakeMarketing()
        ..prefsError = const ApiException(500, 'The database is down.');
      final c = MarketingController(broken);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Accounts');
      expect(find.textContaining('The database is down.'), findsOneWidget);
      broken.prefsError = null;
      await tapText(tester, find.text('Try again').last);
      expect(find.text('Friendly and practical'), findsOneWidget);
    });

    testWidgets('best times show history and defaults', (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Analytics');
      expect(find.text('Best times to post'), findsOneWidget);
      expect(find.text('Sunday 21:00'), findsOneWidget);
      expect(
          find.textContaining('5.2% engagement over 6 posts'), findsOneWidget);
      expect(find.text('Wednesday 20:00'), findsOneWidget);
      expect(
          find.textContaining('Default for a Gulf audience'), findsOneWidget);
    });

    testWidgets('best times empty', (tester) async {
      final fake = FakeMarketing()..noTimes = true;
      final c = MarketingController(fake);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Analytics');
      expect(find.text('No best times yet'), findsOneWidget);
    });

    testWidgets('best times error offers a retry', (tester) async {
      final broken = FakeMarketing()
        ..timesError = const ApiException(500, 'Analytics are down.');
      final c = MarketingController(broken);
      await pump(tester, c, size: const Size(1024, 900));
      await openTab(tester, 'Analytics');
      expect(find.textContaining('Analytics are down.'), findsOneWidget);
      broken.timesError = null;
      await tapText(tester, find.text('Try again').last);
      expect(find.text('Sunday 21:00'), findsOneWidget);
    });
  });

  group('Arabic', () {
    testWidgets('the composer, ideas and the campaign form are right to left',
        (tester) async {
      final fake = FakeMarketing();
      final c = MarketingController(fake);
      await pump(tester, c,
          size: const Size(390, 844), locale: const Locale('ar'));
      await tester.tap(find.byType(FloatingActionButton));
      await tester.pumpAndSettle();
      expect(find.byType(ComposeSheet), findsOneWidget);
      expect(Directionality.of(tester.element(find.byType(ComposeSheet))),
          TextDirection.rtl);
      await tapText(tester, find.text('اقتراح بالذكاء الاصطناعي'));
      await typeInto(tester, 'ما الذي تروّج له', 'إطارات الصيف');
      await tapText(tester, find.text('اقترح أفكارًا'));
      expect(find.text('كتبه النموذج المتصل بحسابك'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  // Every new surface at 320, 390, 768 and 1440 px at 200 percent text, in
  // English and Arabic, with nothing overflowing.
  for (final locale in const [Locale('en'), Locale('ar')]) {
    for (final width in const [320.0, 390.0, 768.0, 1440.0]) {
      testWidgets(
          'ops surfaces lay out in ${locale.languageCode} at $width px, text x2',
          (tester) async {
        final fake = FakeMarketing();
        final c = MarketingController(fake);
        await pump(tester, c,
            size: Size(width, 900), textScale: 2, locale: locale);
        final ar = locale.languageCode == 'ar';

        await tester.tap(find.byType(FloatingActionButton));
        await tester.pumpAndSettle();
        await tapText(tester,
            find.text(ar ? 'اقتراح بالذكاء الاصطناعي' : 'Suggest with AI'));
        await typeInto(
            tester, ar ? 'ما الذي تروّج له' : 'What to promote', 'x');
        await tapText(
            tester, find.text(ar ? 'اقترح أفكارًا' : 'Suggest ideas'));
        await tapText(
            tester, find.text(ar ? 'استخدم هذه الفكرة' : 'Use this idea'));
        fake.validateFails = true;
        await tester.pump(const Duration(milliseconds: 700));
        await tester.pumpAndSettle();
        await tapText(
            tester, find.text(ar ? 'أرفق من الملفات' : 'Attach from Files'));
        expect(tester.takeException(), isNull, reason: 'media picker');
        await tester.tap(find.text(ar ? 'تم (0 مختارة)' : 'Done (0 chosen)'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'composer');
        Navigator.of(tester.element(find.byType(ComposeSheet))).pop();
        await tester.pumpAndSettle();

        await tapText(tester, find.text(ar ? 'الحملات' : 'Campaigns'));
        expect(tester.takeException(), isNull, reason: 'campaigns');
        await tapText(tester, find.text(ar ? 'تعديل الحملة' : 'Edit campaign'));
        expect(tester.takeException(), isNull, reason: 'campaign form');
        await tapText(tester, find.text(ar ? 'إلغاء' : 'Cancel').last);
        await tapText(tester,
            find.text(ar ? 'فعّل النشر التلقائي' : 'Turn on auto-post'));
        expect(tester.takeException(), isNull, reason: 'auto-post sheet');
        await tapText(tester, find.text(ar ? 'إلغاء' : 'Cancel').last);

        await tapText(tester, find.text(ar ? 'الحسابات' : 'Accounts'));
        expect(tester.takeException(), isNull, reason: 'accounts');
        await tapText(tester, find.text(ar ? 'التحليلات' : 'Analytics'));
        expect(tester.takeException(), isNull, reason: 'analytics');
      });
    }
  }
}

/// Refuses every edit the way the API does for a post that is already out.
class _RefusingEdit extends FakeMarketing {
  @override
  Future<MkPost> updatePost(String postId,
          {required String text,
          required List<String> hashtags,
          required String title,
          required String? link,
          required List<MkMedia> media,
          required String? campaignId}) =>
      throw const ApiException(
          409, 'This post is published and cannot be edited.');
}
