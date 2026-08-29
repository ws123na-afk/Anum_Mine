import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeSpeech implements SpeechService {
  bool available = true;
  void Function(String, bool)? result;
  @override Future<bool> initialize({required void Function(String message) onError}) async => available;
  @override Future<void> listen({required String locale, required void Function(String text, bool finalResult) onResult}) async => result = onResult;
  @override Future<void> stop() async {}
  @override Future<void> cancel() async {}
  @override Future<void> openSettings() async {}
  @override Future<void> speak(String text, String locale) async {}
}

class FakeVoiceTransport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    if (path.endsWith('/voice/sessions')) return ApiResponse(statusCode: 201, body: {'id':'voice_1','locale':'en-US','status':'active'});
    if (path.endsWith('/transcript')) return ApiResponse(statusCode: 201, body: {'id':'segment_1','text':request.body!['text']});
    if (path.endsWith('/commands')) return const ApiResponse(statusCode: 200, body: {'task':{'id':'task_1','title':'Prepare brief','status':'created'}});
    if (path.endsWith('/run')) return const ApiResponse(statusCode: 200, body: {'task':{'id':'task_1','title':'Prepare brief','status':'running'},'run':{'id':'run_1'}});
    if (path.endsWith('/complete')) return const ApiResponse(statusCode: 200, body: {'id':'voice_1','locale':'en-US','status':'completed'});
    return const ApiResponse(statusCode: 204);
  }
}

void main() {
  test('voice command requires transcript review before execution', () async {
    final sessions=MemorySessionStore();
    await sessions.write(LocalSession(accessToken:'test',tokenType:'bearer',expiresAt:DateTime.now().toUtc().add(const Duration(hours:1)),context:const TenantContext(tenantId:'t',workspaceId:'w',userId:'u',roles:['owner'])));
    final api=AnumApiClient(baseUri:Uri.parse('https://anum.test'),transport:FakeVoiceTransport(),sessions:sessions);
    final speech=FakeSpeech();
    final controller=VoiceController(repository:VoiceRepository(api),speech:speech);
    await controller.start();
    expect(controller.phase,VoicePhase.listening);
    speech.result!('Prepare brief',true);
    expect(controller.phase,VoicePhase.review);
    expect(controller.canSubmit,isTrue);
    await controller.submit();
    expect(controller.phase,VoicePhase.completed);
    expect(controller.command?.taskId,'task_1');
  });

  test('permission denial never creates a voice session', () async {
    final sessions=MemorySessionStore();
    final api=AnumApiClient(baseUri:Uri.parse('https://anum.test'),transport:FakeVoiceTransport(),sessions:sessions);
    final speech=FakeSpeech()..available=false;
    final controller=VoiceController(repository:VoiceRepository(api),speech:speech);
    await controller.start();
    expect(controller.phase,VoicePhase.permissionDenied);
  });
}
