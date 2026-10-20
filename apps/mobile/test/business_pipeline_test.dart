import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_pipeline_repository.dart';
import 'package:anum_mobile/features/business/business_pipeline_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/business_pipeline_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// Prospecting and outreach on the phone (docs/business.md#prospecting-pipeline):
// estimate, confirm and follow a discovery run, a lead's product fit with a
// score suggestion applied, and the outbox (queue, reply stops it). Wire shapes
// as /api/v1/business/pipeline and /outreach send them.

Map<String, Object?> runJson(String status, {bool withLead = false}) => {
      'id': 'brun_1',
      'kind': 'discover',
      'status': status,
      'plan': {
        'queries': ['concrete UAE']
      },
      'estimate': {
        'searches': 2,
        'page_fetches': 0,
        'model_calls': 0,
        'estimated_cost_usd': 0.0,
        'fits': true,
        'problems': <String>[],
      },
      'used': {'searches': status == 'estimated' ? 0 : 2, 'page_fetches': 0},
      'steps': status == 'estimated'
          ? <Object>[]
          : [
              {
                'status': 'done',
                'summary': '3 result(s): 1 new lead(s).',
                'target': 'concrete UAE'
              }
            ],
      'stop_reason': '',
      'leads': withLead
          ? [
              {
                'id': 'blead_1',
                'account_name': 'Gulf Mixers',
                'domain': 'gulfmixers.example'
              }
            ]
          : <Object>[],
    };

class FakePipeline implements BusinessPipelineRepository {
  FakePipeline({this.fail = false});
  final bool fail;
  final calls = <String>[];
  String runStatus = 'estimated';
  String itemStatus = 'pending_approval';
  bool queued = false;

  @override
  Future<PipelineSettings> settings() async {
    calls.add('settings');
    if (fail) throw const ApiException(500, 'Database unavailable');
    return PipelineSettings.fromJson(JsonReader.of({
      'sending_enabled': false,
      'paused': false,
      'paused_reason': '',
      'tools': {'web_search': true, 'page_fetch': true},
      'can_manage': true,
    }));
  }

  @override
  Future<List<({String id, String name})>> products() async => [
        (id: 'bprod_tyre', name: 'TyrePulse'),
        (id: 'bprod_voice', name: 'VoiceQuote')
      ];

  @override
  Future<BusinessPage<PipelineRunView>> runs({required int offset}) async =>
      BusinessPage(
          runStatus == 'estimated'
              ? <PipelineRunView>[]
              : [PipelineRunView.fromJson(JsonReader.of(runJson(runStatus)))],
          runStatus == 'estimated' ? 0 : 1);

  @override
  Future<PipelineRunView> run(String id) async {
    calls.add('run');
    runStatus = 'succeeded';
    return PipelineRunView.fromJson(
        JsonReader.of(runJson('succeeded', withLead: true)));
  }

  @override
  Future<PipelineRunView> estimateDiscovery(
      String productId, List<String> terms) async {
    calls.add('estimate $productId ${terms.join('|')}');
    return PipelineRunView.fromJson(JsonReader.of(runJson('estimated')));
  }

  @override
  Future<PipelineRunView> confirm(String id) async {
    calls.add('confirm $id');
    runStatus = 'running';
    return PipelineRunView.fromJson(JsonReader.of(runJson('running')));
  }

  @override
  Future<PipelineRunView> stop(String id) async {
    calls.add('stop $id');
    return PipelineRunView.fromJson(JsonReader.of(runJson('stopped')));
  }

  @override
  Future<LeadProfileView> profile(String leadId) async =>
      LeadProfileView.fromJson(JsonReader.of({
        'score_total': 0,
        'score_known': 0,
        'product_fits': [
          {
            'product_name': 'TyrePulse',
            'score': 4,
            'disqualified': false,
            'reasons': ['Industry matches: concrete.']
          },
          {
            'product_name': 'VoiceQuote',
            'score': 0,
            'disqualified': true,
            'reasons': ['Disqualified: mentions concrete.']
          },
        ],
        'best_fit': {
          'product_name': 'TyrePulse',
          'score': 4,
          'disqualified': false,
          'reasons': <String>[]
        },
        'breakdown': [
          {
            'criterion': 'reachable_channel',
            'score': null,
            'conversation_only': false,
            'suggestion': {
              'score': 4,
              'reason': 'Publishes a business address.',
              'evidence_ids': ['bevi_1']
            }
          },
          {
            'criterion': 'urgency_confirmed',
            'score': null,
            'conversation_only': true,
            'suggestion': null
          },
        ],
        'observations': [
          {
            'signal_code': 'PUBLISHED_BUSINESS_EMAIL',
            'value_text': 'info@gulfmixers.example',
            'url': 'https://gulfmixers.example/'
          }
        ],
        'signal_labels': {'PUBLISHED_BUSINESS_EMAIL': 'Business address'},
      }));

  @override
  Future<void> applySuggestion(String leadId, String criterion, int score,
          String reason, List<String> evidence) async =>
      calls.add('apply $leadId $criterion $score ${evidence.join(',')}');

  @override
  Future<BusinessPage<OutboxItemView>> outbox({required int offset}) async =>
      BusinessPage(
          queued
              ? [
                  OutboxItemView.fromJson(JsonReader.of({
                    'id': 'bout_1',
                    'lead_id': 'blead_1',
                    'company': 'Gulf Mixers',
                    'channel': 'email',
                    'step': 1,
                    'status': itemStatus,
                    'recipient': 'info@gulfmixers.example',
                    'subject': 'A question for Gulf Mixers',
                    'body': 'Hello. Reply STOP to opt out.',
                    'note': itemStatus == 'cancelled'
                        ? 'The lead replied (stop-on-reply).'
                        : 'Waiting for an owner\'s approval in Approvals.',
                    'due': false,
                    'approval': itemStatus == 'pending_approval'
                        ? {'id': 'approval_1'}
                        : null,
                  }))
                ]
              : <OutboxItemView>[],
          queued ? 1 : 0);

  @override
  Future<List<ReadyDraftView>> ready() async => queued
      ? []
      : [
          const ReadyDraftView('bdraft_1', 'Gulf Mixers', 'email', 'A question')
        ];

  @override
  Future<void> queue(String draftId) async {
    calls.add('queue $draftId');
    queued = true;
  }

  @override
  Future<void> requestApproval(String itemId) async => calls.add('ask $itemId');

  @override
  Future<void> cancel(String itemId, String reason) async =>
      calls.add('cancel $itemId');

  @override
  Future<String> recordReply(String leadId, String channel, String text) async {
    calls.add('reply $leadId $text');
    itemStatus = 'cancelled';
    return 'reply';
  }
}

Widget app(Widget home,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
    MaterialApp(
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
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: child!),
      home: home,
    );

/// Snack bars cover the bottom of the test screen; clear them before the next tap.
Future<void> clearSnacks(WidgetTester tester) async {
  tester
      .state<ScaffoldMessengerState>(find.byType(ScaffoldMessenger))
      .removeCurrentSnackBar();
  await tester.pumpAndSettle();
}

void main() {
  test('English and Arabic strings have the same keys and no long dashes', () {
    expect(businessPipelineAr.keys.toSet(), businessPipelineEn.keys.toSet());
    final dashes = RegExp(
        '[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]');
    for (final value in [
      ...businessPipelineEn.values,
      ...businessPipelineAr.values
    ]) {
      expect(dashes.hasMatch(value), isFalse, reason: value);
    }
  });

  testWidgets(
      'estimate, confirm, follow the run, open the fit and apply a suggestion',
      (tester) async {
    tester.view.physicalSize = const Size(800, 3200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final fake = FakePipeline();
    await tester.pumpWidget(app(BusinessPipelineScreen(
        repository: fake, pollEvery: const Duration(milliseconds: 10))));
    await tester.pumpAndSettle();
    expect(find.text('No runs yet. Estimate a discovery run for one product.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('bizp-terms')), 'concrete, fleet');
    await tester.tap(find.byKey(const Key('bizp-estimate')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('estimate bprod_tyre concrete|fleet'));
    expect(find.textContaining('2 search(es), 0 page read(s)'), findsOneWidget);
    await clearSnacks(tester);
    await tester.ensureVisible(find.byKey(const Key('bizp-confirm')));
    await tester.tap(find.byKey(const Key('bizp-confirm')));
    await tester.pump();
    expect(fake.calls, contains('confirm brun_1'));
    await tester.pump(const Duration(milliseconds: 20));
    await tester.pumpAndSettle();
    expect(find.text('Done'), findsWidgets);
    expect(find.text('Gulf Mixers'), findsOneWidget);
    await clearSnacks(tester);
    await tester.ensureVisible(find.text('Fit'));
    await tester.tap(find.text('Fit'));
    await tester.pumpAndSettle();
    expect(find.text('Best fit: TyrePulse (4 of 5)'), findsOneWidget);
    expect(find.text('VoiceQuote: Disqualified'), findsOneWidget);
    expect(find.text('Only a conversation confirms this'), findsOneWidget);
    await clearSnacks(tester);
    await tester
        .ensureVisible(find.byKey(const Key('bizp-apply-reachable_channel')));
    await tester.tap(find.byKey(const Key('bizp-apply-reachable_channel')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('apply blead_1 reachable_channel 4 bevi_1'));
  });

  testWidgets('outbox: queue, waiting for approval, a reply cancels it',
      (tester) async {
    tester.view.physicalSize = const Size(800, 3200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final fake = FakePipeline();
    await tester.pumpWidget(app(BusinessPipelineScreen(repository: fake)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('bizp-tab-outreach')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Sending is off'), findsOneWidget);
    await tester.tap(find.text('Queue'));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('queue bdraft_1'));
    expect(find.text('Waiting for approval'), findsOneWidget);
    await clearSnacks(tester);
    await tester.tap(find.byKey(const Key('bizp-reply-bout_1')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('bizp-reply-text')), 'Call me Sunday');
    await tester.tap(find.widgetWithText(FilledButton, 'Record reply'));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('reply blead_1 Call me Sunday'));
    expect(find.text('The lead replied (stop-on-reply).'), findsOneWidget);
    expect(find.text('Cancelled'), findsOneWidget);
  });

  testWidgets('a failed load explains itself and a viewer gets no actions',
      (tester) async {
    await tester.pumpWidget(
        app(BusinessPipelineScreen(repository: FakePipeline(fail: true))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Database unavailable'), findsOneWidget);
    await tester.pumpWidget(app(
        BusinessPipelineScreen(repository: FakePipeline(), canWrite: false)));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('bizp-estimate')), findsNothing);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0]) {
    testWidgets('fits at $width wide with 200% text, in Arabic too',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(app(
          BusinessPipelineScreen(repository: FakePipeline()),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(app(
          BusinessPipelineScreen(repository: FakePipeline()),
          locale: const Locale('ar')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('البحث عن العملاء'), findsWidgets);
    });
  }
}
