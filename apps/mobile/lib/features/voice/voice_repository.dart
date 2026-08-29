import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'voice_models.dart';

class VoiceRepository {
  const VoiceRepository(this.api);
  final AnumApiClient api;

  Future<VoiceSession> createSession({
    required String locale,
    required VoiceRetention retention,
  }) async => _session(await api.request('POST', '/api/v1/voice/sessions', body: {
        'locale': locale,
        'retention': retention.apiValue,
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
    return VoiceSegment(id: value['id']! as String, text: value['text']! as String);
  }

  Future<VoiceCommand> submitAndRun(
    String sessionId,
    String segmentId,
    String title,
  ) async {
    final command = await api.request(
      'POST',
      '/api/v1/voice/sessions/$sessionId/commands',
      body: {
        'transcript_segment_id': segmentId,
        'title': title,
      },
    );
    final task = command['task']! as JsonMap;
    final run = await api.request('POST', '/api/v1/tasks/${task['id']}/run');
    final updated = run['task']! as JsonMap;
    return VoiceCommand(
      taskId: updated['id']! as String,
      title: updated['title']! as String,
      status: updated['status']! as String,
    );
  }

  Future<void> complete(String sessionId) async {
    await api.request('POST', '/api/v1/voice/sessions/$sessionId/complete');
  }

  Future<void> cancel(String sessionId) async {
    await api.request('DELETE', '/api/v1/voice/sessions/$sessionId');
  }

  VoiceSession _session(JsonMap value) => VoiceSession(
        id: value['id']! as String,
        locale: value['locale']! as String,
        status: value['status']! as String,
      );
}
