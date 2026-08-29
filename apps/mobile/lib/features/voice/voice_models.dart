enum VoiceRetention { session, thirtyDays, permanent }

extension VoiceRetentionValue on VoiceRetention {
  String get apiValue => switch (this) {
        VoiceRetention.session => 'session',
        VoiceRetention.thirtyDays => '30_days',
        VoiceRetention.permanent => 'permanent',
      };
}

class VoiceSession {
  const VoiceSession({required this.id, required this.locale, required this.status});
  final String id;
  final String locale;
  final String status;
}

class VoiceSegment {
  const VoiceSegment({required this.id, required this.text});
  final String id;
  final String text;
}

class VoiceCommand {
  const VoiceCommand({required this.taskId, required this.title, required this.status});
  final String taskId;
  final String title;
  final String status;
}

enum VoicePhase {
  idle,
  requestingPermission,
  listening,
  review,
  submitting,
  running,
  completed,
  permissionDenied,
  unavailable,
  error,
}
