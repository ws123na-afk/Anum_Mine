import 'api_models.dart';

abstract interface class SessionStore {
  Future<LocalSession?> read();
  Future<void> write(LocalSession session);
  Future<void> clear();
}

class MemorySessionStore implements SessionStore {
  LocalSession? _session;

  @override
  Future<LocalSession?> read() async => _session;

  @override
  Future<void> write(LocalSession session) async => _session = session;

  @override
  Future<void> clear() async => _session = null;
}

/// Adapter for platform-backed encrypted storage without coupling this layer
/// to a particular Flutter plugin.
class CallbackSessionStore implements SessionStore {
  const CallbackSessionStore({
    required this.reader,
    required this.writer,
    required this.clearer,
  });

  final Future<LocalSession?> Function() reader;
  final Future<void> Function(LocalSession session) writer;
  final Future<void> Function() clearer;

  @override
  Future<LocalSession?> read() => reader();

  @override
  Future<void> write(LocalSession session) => writer(session);

  @override
  Future<void> clear() => clearer();
}
