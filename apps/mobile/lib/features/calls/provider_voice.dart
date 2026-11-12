// Phone calls through the workspace's voice provider from the phone
// (docs/calls.md#calls-through-anum-from-the-phone), with the web's rules: only
// with the provider connected and calls through ANUM on (`GET /setup`
// capabilities), a full international number, and a recorded call only after
// the person confirms the other side will hear the recording notice and that
// recording is lawful. The API prepares the call (`POST /calls/provider/calls`:
// a call log entry and a 10 minute access token) and dials only that number
// for this person; the provider's SDK on the phone carries the audio.
//
// [ProviderVoiceClient] is the boundary to that SDK. The build ships
// [UnsupportedProviderVoice]: Twilio's Flutter plugin (twilio_voice) was checked
// and not linked (it requires Android minSdk 26, registers its own Firebase
// messaging service next to the app's, and asks for phone-state and call
// permissions; docs/pending-work.md, "Phone calls"). Until a client that says
// [ProviderVoiceClient.supported] is linked, the phone shows no "Call through
// ANUM" choice and calls go through the phone's dialer as before.
import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import 'calls_models.dart';

/// What `POST /calls/provider/calls` answers.
class ProviderCallTicket {
  const ProviderCallTicket(
      {required this.callId,
      required this.token,
      required this.identity,
      required this.expiresIn});
  final String callId;

  /// The provider SDK's access token (10 minutes). Never logged or stored.
  final String token;
  final String identity;
  final int expiresIn;

  static ProviderCallTicket fromJson(JsonReader r) => ProviderCallTicket(
        callId: r.object('call').string('id'),
        token: r.string('token'),
        identity: r.string('identity'),
        expiresIn: r.optInt('expires_in') ?? 600,
      );

  @override
  String toString() => 'ProviderCallTicket($callId)';
}

enum ProviderCallState { connecting, ringing, connected, ended, failed }

/// The provider's voice SDK on this phone.
abstract interface class ProviderVoiceClient {
  /// Whether this build can place calls through the provider at all.
  bool get supported;

  /// Places the prepared call; states arrive on [states].
  Future<void> connect(ProviderCallTicket ticket);
  Stream<ProviderCallState> get states;
  Future<void> setMuted(bool muted);
  Future<void> hangUp();
}

/// The default: no provider SDK is linked into this build.
class UnsupportedProviderVoice implements ProviderVoiceClient {
  const UnsupportedProviderVoice();
  @override
  bool get supported => false;
  @override
  Future<void> connect(ProviderCallTicket ticket) =>
      Future.error(UnsupportedError('No voice provider SDK in this build'));
  @override
  Stream<ProviderCallState> get states => const Stream.empty();
  @override
  Future<void> setMuted(bool muted) async {}
  @override
  Future<void> hangUp() async {}
}

typedef PrepareProviderCall = Future<ProviderCallTicket> Function(
    Map<String, Object?> body);

/// `POST /api/v1/calls/provider/calls` over the live API.
PrepareProviderCall prepareProviderCallOver(AnumApiClient api) =>
    (body) async => ProviderCallTicket.fromJson(JsonReader(
        await api.request('POST', '/api/v1/calls/provider/calls', body: body)));

/// One call through the provider: prepare, connect, mute, hang up.
class ProviderCallController extends ChangeNotifier {
  ProviderCallController({required this.client, required this.prepare});

  final ProviderVoiceClient client;
  final PrepareProviderCall prepare;

  ProviderCallState? state;
  bool preparing = false;
  bool muted = false;

  /// A [AnumLocalizations] key and values for what went wrong, or null.
  String? problem;
  Map<String, Object> problemValues = const {};
  String? callId;
  StreamSubscription<ProviderCallState>? _states;

  bool get active =>
      preparing ||
      state == ProviderCallState.connecting ||
      state == ProviderCallState.ringing ||
      state == ProviderCallState.connected;

  /// Prepares and places a call to [phone]. A recorded call needs [consent]
  /// (the server refuses it otherwise; this checks first so nobody is rung).
  Future<void> start(
      {required String phone,
      String? contactId,
      required bool record,
      required bool consent,
      required String language}) async {
    if (active) return;
    problem = null;
    final number = dialable(phone);
    if (!RegExp(r'^\+[1-9]\d{6,14}$').hasMatch(number)) {
      problem = 'calls.provider.call.number';
      notifyListeners();
      return;
    }
    if (record && !consent) {
      problem = 'calls.provider.call.consent';
      notifyListeners();
      return;
    }
    preparing = true;
    notifyListeners();
    try {
      final ticket = await prepare({
        if (contactId != null) 'contact_id': contactId else 'phone': number,
        'record': record,
        'recording_consent': record && consent,
        'language': language,
      });
      callId = ticket.callId;
      await _states?.cancel();
      _states = client.states.listen((next) {
        state = next;
        notifyListeners();
      });
      state = ProviderCallState.connecting;
      await client.connect(ticket);
    } on Object catch (error) {
      state = ProviderCallState.failed;
      problem = 'calls.provider.call.failed';
      problemValues = {
        'message': error is ApiException ? error.readable : readableError(error)
      };
    } finally {
      preparing = false;
      notifyListeners();
    }
  }

  Future<void> toggleMute() async {
    muted = !muted;
    notifyListeners();
    await client.setMuted(muted).catchError((Object _) {});
  }

  Future<void> hangUp() async {
    await client.hangUp().catchError((Object _) {});
    state = ProviderCallState.ended;
    notifyListeners();
  }

  @override
  void dispose() {
    unawaited(_states?.cancel());
    super.dispose();
  }
}

/// The sheet of a call through ANUM: the recording choice (only when the
/// workspace records calls), the confirmation it needs, then the call's state
/// with mute and hang up.
class ProviderCallSheet extends StatefulWidget {
  const ProviderCallSheet(
      {required this.controller,
      required this.phone,
      required this.recordingOn,
      this.contactId,
      this.contactName,
      super.key});
  final ProviderCallController controller;
  final String phone;
  final String? contactId;
  final String? contactName;

  /// Whether an owner turned recording on for this workspace.
  final bool recordingOn;

  @override
  State<ProviderCallSheet> createState() => _ProviderCallSheetState();
}

class _ProviderCallSheetState extends State<ProviderCallSheet> {
  bool _record = false;
  bool _consent = false;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final c = widget.controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final state = c.state;
        final started = state != null || c.preparing;
        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Semantics(
                header: true,
                child: Text(l.t('calls.provider.call.title'),
                    style: Theme.of(context).textTheme.titleLarge),
              ),
              const SizedBox(height: 4),
              Text(widget.contactName ?? widget.phone,
                  textDirection: TextDirection.ltr, textAlign: TextAlign.start),
              const SizedBox(height: 4),
              Text(l.t('calls.provider.call.cost')),
              if (!started && widget.recordingOn) ...[
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(l.t('calls.provider.call.record')),
                  value: _record,
                  onChanged: (value) => setState(() {
                    _record = value;
                    if (!value) _consent = false;
                  }),
                ),
                if (_record)
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    controlAffinity: ListTileControlAffinity.leading,
                    title: Text(l.t('calls.provider.call.confirm')),
                    value: _consent,
                    onChanged: (value) =>
                        setState(() => _consent = value ?? false),
                  ),
              ],
              if (c.problem != null) ...[
                const SizedBox(height: 8),
                Semantics(
                  liveRegion: true,
                  child: Text(l.f(c.problem!, c.problemValues),
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
                ),
              ],
              if (state != null) ...[
                const SizedBox(height: 8),
                Semantics(
                  liveRegion: true,
                  child: Text(l.t('calls.provider.call.state.${state.name}'),
                      style: Theme.of(context).textTheme.titleMedium),
                ),
              ],
              const SizedBox(height: 16),
              if (!c.active)
                FilledButton.icon(
                  onPressed: c.preparing
                      ? null
                      : () => c.start(
                          phone: widget.phone,
                          contactId: widget.contactId,
                          record: _record,
                          consent: _consent,
                          language: l.isArabic ? 'ar' : 'en'),
                  icon: const Icon(Icons.call),
                  label: Text(l.t('calls.provider.call.start')),
                )
              else
                Wrap(spacing: 12, runSpacing: 8, children: [
                  OutlinedButton.icon(
                    onPressed: c.preparing ? null : c.toggleMute,
                    icon: Icon(c.muted ? Icons.mic_off : Icons.mic),
                    label: Text(l.t(c.muted
                        ? 'calls.provider.call.unmute'
                        : 'calls.provider.call.mute')),
                  ),
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFFBE123C)),
                    onPressed: c.preparing ? null : c.hangUp,
                    icon: const Icon(Icons.call_end),
                    label: Text(l.t('calls.provider.call.hangUp')),
                  ),
                ]),
            ],
          ),
        );
      },
    );
  }
}
