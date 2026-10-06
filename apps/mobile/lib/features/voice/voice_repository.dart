import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'voice_models.dart';

class VoiceRepository {
  const VoiceRepository(this.api);
  final AnumApiClient api;

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

  Future<void> complete(String sessionId) async {
    await api.request('POST', '/api/v1/voice/sessions/$sessionId/complete');
  }

  Future<void> cancel(String sessionId) async {
    await api.request('DELETE', '/api/v1/voice/sessions/$sessionId');
  }
}
