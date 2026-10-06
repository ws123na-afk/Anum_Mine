import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';

class ListenCall {
  ListenCall({
    required this.locale,
    required this.onResult,
    required this.onDone,
    required this.listenFor,
    this.onLevel,
    this.onError,
  });
  final String locale;
  final SpeechResultCallback onResult;
  final void Function() onDone;
  final void Function(double level)? onLevel;
  final SpeechErrorCallback? onError;
  final Duration listenFor;
}

class FakeSpeech implements SpeechService {
  SpeechAvailability availability = SpeechAvailability.ready;
  final listens = <ListenCall>[];
  final spoken = <String>[];
  int initializeCalls = 0;
  int cancels = 0;
  int stops = 0;
  int settingsOpened = 0;

  ListenCall get last => listens.last;

  @override
  Future<SpeechAvailability> initialize() async {
    initializeCalls++;
    return availability;
  }

  @override
  Future<void> listen({
    required String locale,
    required SpeechResultCallback onResult,
    required void Function() onDone,
    void Function(double level)? onLevel,
    SpeechErrorCallback? onError,
    Duration pauseFor = const Duration(seconds: 2),
    Duration listenFor = const Duration(seconds: 30),
    List<String> phrases = const [],
  }) async {
    listens.add(ListenCall(
      locale: locale,
      onResult: onResult,
      onDone: onDone,
      onLevel: onLevel,
      onError: onError,
      listenFor: listenFor,
    ));
  }

  @override
  Future<void> stop() async => stops++;

  @override
  Future<void> cancel() async => cancels++;

  @override
  Future<void> openSettings() async => settingsOpened++;

  @override
  Future<void> speak(String text, String locale) async => spoken.add(text);

  @override
  Future<void> stopSpeaking() async {}
}

class RecordedRequest {
  RecordedRequest(this.method, this.path, this.body);
  final String method;
  final String path;
  final JsonMap? body;
}

/// A tiny in-memory stand-in for the voice API, with the same tiers as
/// `services/api/anum_api/voice_assistant.py`.
class FakeVoiceTransport implements ApiTransport {
  FakeVoiceTransport({this.pendingApprovals = 2});

  final int pendingApprovals;
  final requests = <RecordedRequest>[];
  final _segments = <String, String>{};
  int _ids = 0;

  Iterable<RecordedRequest> to(String suffix) =>
      requests.where((request) => request.path.endsWith(suffix));

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    requests.add(RecordedRequest(request.method, path, request.body));
    if (path.endsWith('/voice/sessions')) {
      return ApiResponse(statusCode: 201, body: {
        'id': 'voice_${++_ids}',
        'locale': request.body!['locale'],
        'status': 'active',
        'assistant_name': request.body!['assistant_name'],
      });
    }
    if (path.endsWith('/transcript')) {
      final id = 'segment_${++_ids}';
      _segments[id] = request.body!['text']! as String;
      return ApiResponse(
          statusCode: 201, body: {'id': id, 'text': request.body!['text']});
    }
    if (path.endsWith('/ask')) {
      final text = _segments[request.body!['transcript_segment_id']]!;
      return ApiResponse(statusCode: 200, body: _answer(text));
    }
    if (path.endsWith('/commands')) {
      final text = _segments[request.body!['transcript_segment_id']]!;
      return ApiResponse(statusCode: 200, body: {
        'task': {
          'id': 'task_1',
          'title': request.body!['title'] ?? text,
          'status': 'created'
        }
      });
    }
    return const ApiResponse(statusCode: 200, body: {'status': 'cancelled'});
  }

  JsonMap _answer(String text) {
    final lower = text.toLowerCase();
    var tier = 'read';
    var intent = 'status';
    String? proposal;
    var reply = "You've got 3 tasks, and one is running.";
    if (lower.contains('approve') || lower.contains('delete')) {
      tier = 'visual_only';
      intent = 'visual_only';
      reply =
          "That one needs your own tap on screen, so I won't do it by voice.";
    } else if (lower.startsWith('create a task')) {
      tier = 'confirm';
      intent = 'create_task';
      proposal = text.split(':').last.trim();
      reply = 'Got it: "$proposal". Tap Create task and I\'ll set it up.';
    } else if (lower.contains('name')) {
      intent = 'identity';
      reply = "I'm your assistant here in ANUM.";
    }
    return {
      'intent': intent,
      'risk_tier': tier,
      'reply': reply,
      'proposed_task': proposal,
      'workspace': {
        'tasks_total': 3,
        'running': 1,
        'waiting_approval': 1,
        'pending_approvals': pendingApprovals,
      },
      'assistant_segment': {'id': 'assistant_${++_ids}', 'text': reply},
    };
  }
}

Future<AnumApiClient> signedInApi(ApiTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'test',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 't', workspaceId: 'w', userId: 'u', roles: ['owner'])));
  return AnumApiClient(
      baseUri: Uri.parse('https://anum.test'),
      transport: transport,
      sessions: sessions);
}

Future<VoiceController> buildController({
  required FakeSpeech speech,
  required FakeVoiceTransport transport,
  MemoryVoicePreferences? preferences,
}) async =>
    VoiceController(
      repository: VoiceRepository(await signedInApi(transport)),
      speech: speech,
      preferences: preferences ?? MemoryVoicePreferences(),
      restartDelay: Duration.zero,
    );
