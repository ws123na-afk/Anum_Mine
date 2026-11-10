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

/// Tells the app when a session that was working ends on its own: it
/// expired, or the identity provider rejected the refresh, so a read that
/// used to return a session now returns none (or an expired one). A
/// deliberate [clear] (sign-out) is not reported. Every API call, stream and
/// file transfer reads the session through the store, so wrapping the store
/// covers all of them (docs/mobile.md#session-end).
class WatchedSessionStore implements SessionStore {
  WatchedSessionStore({required this.inner, required this.onEnded});

  final SessionStore inner;

  /// Called once per ended session, from the read that found it gone.
  final void Function() onEnded;

  /// A read or write has seen a usable session since the last end or clear.
  bool _live = false;

  @override
  Future<LocalSession?> read() async {
    final session = await inner.read();
    if (session != null && !session.isExpired) {
      _live = true;
    } else if (_live) {
      _live = false;
      onEnded();
    }
    return session;
  }

  @override
  Future<void> write(LocalSession session) async {
    await inner.write(session);
    _live = true;
  }

  @override
  Future<void> clear() {
    _live = false;
    return inner.clear();
  }
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
