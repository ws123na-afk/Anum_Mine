import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/meetings/meetings_controller.dart';
import 'package:anum_mobile/features/meetings/meetings_models.dart';
import 'package:anum_mobile/features/meetings/meetings_repository.dart';
import 'package:anum_mobile/features/meetings/meetings_screen.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// Calls & Meetings on mobile (docs/meetings.md#mobile): the API client, the
// list with recent activity and states, the detail with AI notes, Arabic RTL
// and the layout at every width with 200% text.
final _now = DateTime.utc(2026, 10, 11, 9).toIso8601String();

Map<String, Object?> _meeting(
        {String id = 'meeting_pmv',
        String title = 'PMV meeting',
        String status = 'ready',
        String language = 'en',
        String? createdBy,
        String? summary =
            'The team reviewed the PMV plan and agreed to restock Noon UAE first.'}) =>
    {
      'id': id,
      'title': title,
      'source': 'transcript',
      'status': status,
      'language': language,
      'created_by': createdBy ?? 'user_test',
      'created_at': _now,
      'updated_at': _now,
      'occurred_at': _now,
      'duration_seconds': 1800,
      'agent_skill_id': 'pmv.planner',
      'segment_count': 2,
      'error_code': status == 'needs_model' ? 'no_model' : null,
      'error': status == 'needs_model' ? 'Connect a model in Settings.' : null,
      'summary': summary,
      'decisions': summary == null
          ? <String>[]
          : (language == 'ar'
              ? ['إعادة التعبئة لنون الإمارات أولًا']
              : ['Restock Noon UAE first']),
      'action_items': summary == null
          ? <Object>[]
          : [
              {
                'id': 'act_1',
                'text': language == 'ar'
                    ? 'إرسال تقرير المخزون'
                    : 'Send the tyre stock report',
                'owner': 'Omar Ali',
                'due_date': '2026-10-16',
                'done': false,
                'task_id': null,
              }
            ],
      'follow_up': {
        'to': <String>[],
        'subject': 'PMV call recap',
        'body': 'Hello all,\n\nThanks for joining.',
        'edited': false,
        'send_task_id': null,
      },
      'provenance': summary == null
          ? null
          : {
              'label': 'ai_generated',
              'provider': 'anthropic',
              'model': 'claude-sonnet-5-5',
              'generated_at': _now,
              'truncated': false,
            },
    };

Map<String, Object?> _setup({bool canCreate = true, bool modelReady = true}) =>
    {
      'transcription': {
        'configured': false,
        'provider': 'none',
        'problem': 'Set ANUM_TRANSCRIPTION_PROVIDER.',
        'max_recording_bytes': 26214400,
        'accepted_extensions': ['.mp3', '.wav'],
      },
      'transcript_extensions': ['.vtt', '.srt', '.txt', '.docx'],
      'max_transcript_bytes': 5242880,
      'connectors': [
        {
          'provider': 'zoom',
          'available': true,
          'connected': true,
          'account_label': 'owner@example.com'
        },
        {
          'provider': 'teams',
          'available': false,
          'connected': false,
          'problem': 'Not set up.'
        },
      ],
      'model_ready': modelReady,
      'email_accounts': <Object>[],
      'can_create': canCreate,
      'can_manage_all': false,
    };

class _Transport implements ApiTransport {
  _Transport(
      {List<Map<String, Object?>>? meetings,
      this.setup,
      this.listStatus = 200,
      this.arabicLines = false})
      : meetings = meetings ?? [_meeting()];
  final List<Map<String, Object?>> meetings;
  final Map<String, Object?>? setup;
  int listStatus;
  final bool arabicLines;
  final List<ApiRequest> requests = [];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path == '/api/v1/meetings/setup') {
      return ApiResponse(statusCode: 200, body: setup ?? _setup());
    }
    if (path == '/api/v1/meetings' && request.method == 'GET') {
      if (listStatus != 200) {
        final status = listStatus;
        listStatus = 200;
        return ApiResponse(statusCode: status, body: {
          'error': {'code': 'internal_error', 'message': 'Database unavailable'}
        });
      }
      return ApiResponse(
          statusCode: 200, body: {'items': meetings, 'total': meetings.length});
    }
    if (path.endsWith('/transcript')) {
      return ApiResponse(statusCode: 200, body: {
        'items': arabicLines
            ? [
                {
                  'id': 's1',
                  'seq': 0,
                  'speaker': 'سارة',
                  'start_ms': 0,
                  'text': 'صباح الخير، نراجع خطة الإطارات.'
                },
              ]
            : [
                {
                  'id': 's1',
                  'seq': 0,
                  'speaker': 'Jane Doe',
                  'start_ms': 1000,
                  'text': 'Welcome to the PMV planning call.'
                },
                {
                  'id': 's2',
                  'seq': 1,
                  'speaker': 'Omar Ali',
                  'start_ms': 5000,
                  'text': 'I will send the tyre stock report by Friday.'
                },
              ],
        'total': arabicLines ? 1 : 2,
        'speakers': ['Jane Doe', 'Omar Ali'],
      });
    }
    if (path.endsWith('/follow-up/send') && request.method == 'POST') {
      final m = Map<String, Object?>.of(meetings.first);
      m['follow_up'] = {
        ...(m['follow_up']! as Map<String, Object?>),
        'approval_id': 'approval_1',
        'sent_from': 'owner@example.com',
      };
      return ApiResponse(statusCode: 200, body: m);
    }
    if (path.endsWith('/task') && request.method == 'POST') {
      final m = Map<String, Object?>.of(meetings.first);
      m['action_items'] = [
        {
          ...(m['action_items']! as List).first as Map<String, Object?>,
          'task_id': 'task_1'
        }
      ];
      meetings[0] = m;
      return ApiResponse(statusCode: 200, body: m);
    }
    if (path.startsWith('/api/v1/meetings/connectors/zoom/meetings')) {
      return const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'external_id': 'zoom-1',
            'title': 'Noon UAE weekly',
            'started_at': '2026-10-10T08:00:00Z',
            'duration_minutes': 30,
            'imported_meeting_id': null
          }
        ]
      });
    }
    final match = RegExp(r'^/api/v1/meetings/([^/]+)$').firstMatch(path);
    if (match != null && request.method == 'GET') {
      final found = meetings.where((m) => m['id'] == match.group(1));
      return found.isEmpty
          ? const ApiResponse(statusCode: 404, body: {
              'error': {'code': 'not_found', 'message': 'Meeting not found'}
            })
          : ApiResponse(statusCode: 200, body: found.first);
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

class _Upload implements MeetingUploadTransfer {
  final calls = <(String, Map<String, String>, String)>[];
  @override
  Future<JsonMap> uploadTo(
      String endpoint, Map<String, String> query, String path,
      {required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation}) async {
    calls.add((endpoint, query, path));
    onProgress(10, 10);
    return _meeting(
        id: 'meeting_new',
        title: query['title'] ?? 'weekly',
        status: 'analyzing',
        summary: null);
  }
}

Future<MemorySessionStore> _sessions() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_example',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'roles': ['member'],
    },
  }));
  return sessions;
}

Future<ApiMeetingsRepository> _repository(_Transport transport,
        {MeetingUploadTransfer? upload}) async =>
    ApiMeetingsRepository(
        AnumApiClient(
            baseUri: Uri.parse('http://127.0.0.1:8000'),
            transport: transport,
            sessions: await _sessions()),
        transfer: upload);

Widget _app(Widget child, {Locale locale = const Locale('en')}) => MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates,
      ],
      home: Scaffold(body: child),
    );

void main() {
  group('models and paths', () {
    test('a meeting parses its notes and provenance', () {
      final m = Meeting.fromJson(JsonReader(_meeting()));
      expect(m.summary, contains('PMV plan'));
      expect(m.model, 'claude-sonnet-5-5');
      expect(m.actionItems.single.owner, 'Omar Ali');
      expect(m.followUp!.subject, 'PMV call recap');
      expect(m.isWorking, isFalse);
      expect(Meeting.fromJson(JsonReader(_meeting(language: 'ar'))).isArabic,
          isTrue);
    });

    test('list path, upload checks and clock', () {
      expect(meetingsListPath(query: ' PMV ', status: 'failed', offset: 20),
          '/api/v1/meetings?limit=20&offset=20&q=PMV&status=failed');
      final setup = MeetingSetup.fromJson(JsonReader(_setup()));
      expect(
          meetingUploadProblem(
              name: 'a.vtt', size: 10, recording: false, setup: setup),
          isNull);
      expect(
          meetingUploadProblem(
              name: 'a.pdf', size: 10, recording: false, setup: setup),
          'type');
      expect(
          meetingUploadProblem(
              name: 'a.vtt', size: 6000000, recording: false, setup: setup),
          'size');
      expect(
          meetingUploadProblem(
              name: 'a.vtt', size: 0, recording: false, setup: setup),
          'empty');
      expect(transcriptClock(62000), '01:02');
      expect(transcriptClock(3723000), '1:02:03');
    });
  });

  test('the repository calls the documented endpoints', () async {
    final transport = _Transport();
    final upload = _Upload();
    final repo = await _repository(transport, upload: upload);
    final page = await repo.list(query: 'tyre');
    expect(page.total, 1);
    expect(transport.requests.last.uri.queryParameters['q'], 'tyre');
    final transcript = await repo.transcript('meeting_pmv', query: 'stock');
    expect(transcript.lines, hasLength(2));
    expect(transport.requests.last.uri.path,
        '/api/v1/meetings/meeting_pmv/transcript');
    final created = await repo.importFile(
        recording: false,
        path: '/tmp/weekly.vtt',
        name: 'اجتماع.vtt',
        title: 'Weekly',
        language: 'ar',
        onProgress: (_, __) {},
        cancellation: UploadCancellation());
    expect(created.status, 'analyzing');
    expect(upload.calls.single.$1, 'api/v1/meetings/transcripts');
    expect(upload.calls.single.$2,
        {'name': 'اجتماع.vtt', 'title': 'Weekly', 'language': 'ar'});
    final sent = await repo.sendFollowUp('meeting_pmv', 'emb_1');
    expect(sent.followUp!.approvalId, 'approval_1');
    expect(sent.followUp!.sentFrom, 'owner@example.com');
    expect(transport.requests.last.body, {'account_id': 'emb_1'});
    final withTask = await repo.createTask('meeting_pmv', 'act_1');
    expect(withTask.actionItems.single.taskId, 'task_1');
    expect(transport.requests.last.headers['authorization'],
        'Bearer anum_local_example');
  });

  testWidgets('the list shows recent call summaries and opens the notes',
      (tester) async {
    final controller =
        MeetingsController(await _repository(_Transport()), pollEvery: null);
    await tester.pumpWidget(
        _app(MeetingsTab(controller: controller, currentUserId: 'user_test')));
    await tester.pumpAndSettle();
    expect(find.text('Recent activity'), findsOneWidget);
    expect(find.text('Call summary: PMV meeting'), findsOneWidget);
    await tester.tap(find.text('Call summary: PMV meeting'));
    await tester.pumpAndSettle();
    expect(find.text('AI-generated · claude-sonnet-5-5'), findsOneWidget);
    expect(find.text('• Restock Noon UAE first'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Create task'), 200,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('Create task'));
    await tester.pumpAndSettle();
    expect(find.text('Task created'), findsOneWidget);
    await tester.scrollUntilVisible(
        find.text('Welcome to the PMV planning call.'), 300,
        scrollable: find.byType(Scrollable).last);
    expect(find.text('00:01 · Jane Doe'), findsOneWidget);
    // No email tool: sending is off and says why.
    expect(
        find.textContaining('Connect a mailbox under Inbox'), findsOneWidget);
  });

  testWidgets('viewers read but cannot import; failures offer a retry',
      (tester) async {
    final transport =
        _Transport(setup: _setup(canCreate: false), listStatus: 500);
    final controller =
        MeetingsController(await _repository(transport), pollEvery: null);
    await tester.pumpWidget(_app(MeetingsTab(controller: controller)));
    await tester.pumpAndSettle();
    expect(find.textContaining('You can read meetings'), findsOneWidget);
    expect(find.text('Import a meeting'), findsNothing);
    expect(find.text('Meetings could not be loaded'), findsOneWidget);
    expect(find.text('Database unavailable'), findsOneWidget);
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('PMV meeting'), findsOneWidget);
  });

  testWidgets('an empty workspace offers the first import and its sheet',
      (tester) async {
    final upload = _Upload();
    final controller = MeetingsController(
        await _repository(_Transport(meetings: []), upload: upload),
        pollEvery: null);
    await tester.pumpWidget(_app(MeetingsTab(
        controller: controller,
        pickFile: (_) async => ('/tmp/weekly.vtt', 'weekly.vtt', 120))));
    await tester.pumpAndSettle();
    expect(find.text('No meetings yet'), findsOneWidget);
    await tester
        .tap(find.widgetWithText(FilledButton, 'Import a meeting').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Recording'));
    await tester.pumpAndSettle();
    expect(find.text('Recording transcription is not set up'), findsOneWidget);
    await tester.tap(find.text('Zoom'));
    await tester.pumpAndSettle();
    expect(find.text('Noon UAE weekly'), findsOneWidget);
    await tester.tap(find.text('Transcript file').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Choose a transcript file'));
    await tester.pumpAndSettle();
    expect(upload.calls.single.$1, 'api/v1/meetings/transcripts');
  });

  testWidgets('an Arabic meeting reads right to left', (tester) async {
    final transport = _Transport(
        meetings: [_meeting(language: 'ar', title: 'اجتماع خطة الإطارات')],
        arabicLines: true);
    final repo = await _repository(transport);
    await tester.pumpWidget(_app(
        MeetingDetailScreen(
            repository: repo,
            meetingId: 'meeting_pmv',
            setup: MeetingSetup.fromJson(JsonReader(_setup())),
            currentUserId: 'user_test',
            pollEvery: null),
        locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('الملخص'), findsOneWidget);
    final decision = find.text('• إعادة التعبئة لنون الإمارات أولًا');
    expect(tester.widget<Text>(decision).textDirection, TextDirection.rtl);
    await tester.scrollUntilVisible(
        find.text('صباح الخير، نراجع خطة الإطارات.'), 300,
        scrollable: find.byType(Scrollable).last);
    expect(
        Directionality.of(
            tester.element(find.text('صباح الخير، نراجع خطة الإطارات.'))),
        TextDirection.rtl);
  });

  testWidgets('the list and the notes fit every width with 200% text',
      (tester) async {
    for (final width in const [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in const [1.0, 2.0]) {
        await tester.binding.setSurfaceSize(Size(width, 900));
        final repo = await _repository(_Transport());
        final controller = MeetingsController(repo, pollEvery: null);
        await tester.pumpWidget(MaterialApp(
            key: ValueKey('$width $scale'),
            theme: AnumTheme.dark(),
            localizationsDelegates: const [
              AnumLocalizations.delegate,
              ...GlobalMaterialLocalizations.delegates,
            ],
            home: MediaQuery.withClampedTextScaling(
                minScaleFactor: scale,
                maxScaleFactor: scale,
                child: Scaffold(body: MeetingsTab(controller: controller)))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'list $width x$scale');
        await tester.pumpWidget(MaterialApp(
            key: ValueKey('detail $width $scale'),
            theme: AnumTheme.dark(),
            localizationsDelegates: const [
              AnumLocalizations.delegate,
              ...GlobalMaterialLocalizations.delegates,
            ],
            home: MediaQuery.withClampedTextScaling(
                minScaleFactor: scale,
                maxScaleFactor: scale,
                child: MeetingDetailScreen(
                    repository: repo,
                    meetingId: 'meeting_pmv',
                    setup: MeetingSetup.fromJson(JsonReader(_setup())),
                    currentUserId: 'user_test',
                    pollEvery: null))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'detail $width x$scale');
      }
    }
    await tester.binding.setSurfaceSize(null);
  });
}
