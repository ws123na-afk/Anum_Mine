/// Voice call models (docs/voice.md#call-mode).
library;

import '../../data/api_models.dart';

/// What a call can use in this workspace (`GET /api/v1/voice/capabilities`).
class CallCapabilities {
  const CallCapabilities({
    required this.visionAvailable,
    this.visionModel,
    this.visionReason,
    this.maxFrameBytes = 1500000,
    this.firstAudioTargetMs = 1500,
  });

  factory CallCapabilities.fromJson(JsonMap value) {
    final r = JsonReader(value);
    final vision = r.optObject('vision');
    final latency = r.optObject('latency_target_ms');
    return CallCapabilities(
      visionAvailable: vision?.optBool('available') ?? false,
      visionModel: vision?.optString('model'),
      visionReason: vision?.optString('reason'),
      maxFrameBytes: vision?.optInt('max_frame_bytes') ?? 1500000,
      firstAudioTargetMs: latency?.optInt('first_audio') ?? 1500,
    );
  }

  /// Used before the API answers and when it cannot: no camera answers.
  static const deviceOnly = CallCapabilities(visionAvailable: false);

  final bool visionAvailable;
  final String? visionModel;
  final String? visionReason;
  final int maxFrameBytes;
  final int firstAudioTargetMs;
}

class CallSummary {
  const CallSummary({
    required this.summary,
    required this.fromModel,
    this.conversationId,
  });

  factory CallSummary.fromJson(JsonMap value) {
    final r = JsonReader(value);
    return CallSummary(
      summary: r.optString('summary') ?? '',
      fromModel: r.optString('source') == 'model',
      conversationId: r.optString('conversation_id'),
    );
  }

  final String summary;
  final bool fromModel;
  final String? conversationId;
}

/// Where the call is. Drives the status line and the orb.
enum CallPhase {
  idle,
  connecting,
  listening,
  thinking,
  speaking,
  ending,
  ended
}

/// Problems the call screen explains, each with a way forward.
enum CallProblem {
  micDenied,
  speechUnavailable,
  offline,
  provider,
  budget,
  limit,
  session,
  forbidden,
  visionOff,
  photoTooLarge,
  heardNothing,
  summary,
}

/// `m:ss` or `h:mm:ss`.
String formatCallTime(Duration elapsed) {
  final seconds = elapsed.inSeconds < 0 ? 0 : elapsed.inSeconds;
  final hours = seconds ~/ 3600;
  final minutes = (seconds % 3600) ~/ 60;
  final rest = (seconds % 60).toString().padLeft(2, '0');
  return hours > 0
      ? '$hours:${minutes.toString().padLeft(2, '0')}:$rest'
      : '$minutes:$rest';
}

/// Recognized words over a reply that mostly repeat it are its echo, not the
/// caller talking over it.
bool soundsLikeEcho(String heard, String spoken) {
  List<String> words(String value) => value
      .toLowerCase()
      .replaceAll(RegExp(r'[^\p{L}\p{N}\s]', unicode: true), ' ')
      .split(RegExp(r'\s+'))
      .where((word) => word.isNotEmpty)
      .toList();
  final said = words(spoken).toSet();
  final got = words(heard);
  if (got.isEmpty) return true;
  final fresh = got.where((word) => !said.contains(word)).length;
  return fresh / got.length < 0.5;
}
