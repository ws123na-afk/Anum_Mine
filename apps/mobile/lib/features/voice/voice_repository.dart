import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/sse.dart';
import 'call_models.dart';
import 'voice_models.dart';

class VoiceRepository {
  const VoiceRepository(this.api, {this.events});
  final AnumApiClient api;

  /// Streams replies when set (`/ask/stream`), so speech starts with the
  /// first sentence; without it [ask] answers in one piece.
  final EventStreamClient? events;

  Future<VoiceSession> createSession({
    required String locale,
    required VoiceRetention retention,
    String assistantName = 'Anum',
  }) async =>
      VoiceSession.fromJson(
          await api.request('POST', '/api/v1/voice/sessions', body: {
        'locale': locale,
        'retention': retention.apiValue,
        'assistant_name': assistantName,
      }));

  Future<VoiceSegment> appendFinalTranscript(
    String sessionId,
    String text,
    int sequence,
  ) async {
    final value = await api.request(
      'POST',
      '/api/v1/voice/sessions/$sessionId/transcript',
      body: {
        'role': 'user',
        'text': text,
        'is_final': true,
        'client_sequence': sequence,
      },
    );
    final segment = JsonReader(value);
    return VoiceSegment(id: segment.string('id'), text: segment.string('text'));
  }

  /// Ask the assistant about a final user segment. Never changes state.
  Future<VoiceAskResult> ask(String sessionId, String segmentId) async =>
      VoiceAskResult.fromJson(await api.request(
        'POST',
        '/api/v1/voice/sessions/$sessionId/ask',
        body: {'transcript_segment_id': segmentId},
      ));

  /// The same answer as [ask], streamed: `meta` (intent, risk tier,
  /// proposal, workspace), `delta` (reply text), then `done` (the stored
  /// assistant segment), or `error` when the model failed before saying
  /// anything. Null when this repository has no stream client.
  Stream<SseEvent>? askStream(String sessionId, String segmentId) =>
      events?.post('/api/v1/voice/sessions/$sessionId/ask/stream',
          {'transcript_segment_id': segmentId});

  /// Creates a task from a final user segment. Call only after the user
  /// confirmed on screen; voice alone never reaches this endpoint.
  Future<VoiceCommand> createTask(
    String sessionId,
    String segmentId, {
    String? title,
  }) async {
    final command = await api.request(
      'POST',
      '/api/v1/voice/sessions/$sessionId/commands',
      body: {
        'transcript_segment_id': segmentId,
        if (title != null) 'title': title,
      },
    );
    final task = JsonReader(command).object('task');
    return VoiceCommand(
      taskId: task.string('id'),
      title: task.string('title'),
      status: task.string('status'),
    );
  }

  /// What a call can use in this workspace (docs/voice.md#call-mode):
  /// whether camera answers are available and why not, and latency targets.
  Future<CallCapabilities> capabilities() async => CallCapabilities.fromJson(
      await api.request('GET', '/api/v1/voice/capabilities'));

  /// A spoken question about one photo, streamed like [askStream]. The photo
  /// is sent once and never stored by the server.
  Stream<SseEvent>? lookStream(String sessionId, String segmentId,
          String imageBase64, String mediaType) =>
      events?.post('/api/v1/voice/sessions/$sessionId/look/stream', {
        'transcript_segment_id': segmentId,
        'image_base64': imageBase64,
        'media_type': mediaType,
      });

  /// The end-of-call summary; with [save] it is kept as a conversation
  /// together with the transcript. Call before [complete].
  Future<CallSummary> summarize(String sessionId,
          {required int durationSeconds, required bool save}) async =>
      CallSummary.fromJson(await api.request(
          'POST', '/api/v1/voice/sessions/$sessionId/summary',
          body: {'duration_seconds': durationSeconds, 'save': save}));

  Future<void> complete(String sessionId) async {
    await api.request('POST', '/api/v1/voice/sessions/$sessionId/complete');
  }

  Future<void> cancel(String sessionId) async {
    await api.request('DELETE', '/api/v1/voice/sessions/$sessionId');
  }
}
