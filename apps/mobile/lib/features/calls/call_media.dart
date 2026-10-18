// The WebRTC side of an in-app call (docs/in-app-calls.md#media), behind an
// interface so widget tests use a fake. Media flows peer to peer; the API only
// carries the offer, the answer and the ICE candidates.
import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';

import 'call_models.dart';

/// Why the microphone or camera could not be opened.
enum CallMediaProblem { denied, noDevice, other }

class CallMediaException implements Exception {
  const CallMediaException(this.problem, [this.message = '']);
  final CallMediaProblem problem;
  final String message;
  @override
  String toString() => 'CallMediaException($problem, $message)';
}

enum CallLinkState { connecting, connected, reconnecting, failed }

/// What the media session sends through the API.
abstract interface class CallSignalSink {
  Future<void> description(String kind, String sdp);
  Future<void> candidates(List<Map<String, Object?>> batch);
}

abstract interface class CallMediaSession {
  /// Opens the microphone (and the camera for a video call); throws
  /// [CallMediaException].
  Future<void> open({required bool video});

  /// Creates the peer connection once the call is answered.
  Future<void> connect({
    required bool caller,
    required List<IceServer> iceServers,
    required CallSignalSink sink,
    required void Function(CallLinkState state) onState,
  });

  /// The caller's offer, after the callee answered.
  Future<void> offer();
  Future<void> handle(CallSignal signal);
  void setMuted(bool muted);
  Future<void> setSpeaker(bool on);
  void setCamera(bool on);

  /// The other person's video (or null for a voice call), and this phone's.
  Widget? remoteView();
  Widget? localView();
  Future<void> close();
}

typedef CallMediaFactory = CallMediaSession Function();

/// [CallMediaSession] over flutter_webrtc.
class WebRtcCallMedia implements CallMediaSession {
  MediaStream? _local;
  RTCPeerConnection? _pc;
  final RTCVideoRenderer _remoteRenderer = RTCVideoRenderer();
  final RTCVideoRenderer _localRenderer = RTCVideoRenderer();
  bool _renderers = false;
  bool _video = false;
  bool _remoteSet = false;
  bool _closed = false;
  final List<RTCIceCandidate> _pendingIn = [];
  final List<Map<String, Object?>> _pendingOut = [];
  Timer? _flush;
  Timer? _grace;
  CallSignalSink? _sink;
  void Function(CallLinkState state)? _onState;

  @override
  Future<void> open({required bool video}) async {
    _video = video;
    try {
      _local = await navigator.mediaDevices.getUserMedia({
        'audio': {'echoCancellation': true, 'noiseSuppression': true},
        'video': video ? {'facingMode': 'user'} : false,
      });
    } on Object catch (error) {
      final text = '$error';
      final lower = text.toLowerCase();
      if (lower.contains('permission') ||
          lower.contains('notallowed') ||
          lower.contains('denied')) {
        throw CallMediaException(CallMediaProblem.denied, text);
      }
      if (lower.contains('notfound') || lower.contains('no device')) {
        throw CallMediaException(CallMediaProblem.noDevice, text);
      }
      throw CallMediaException(CallMediaProblem.other, text);
    }
    if (video) {
      await _initRenderers();
      _localRenderer.srcObject = _local;
    }
  }

  Future<void> _initRenderers() async {
    if (_renderers) return;
    await _remoteRenderer.initialize();
    await _localRenderer.initialize();
    _renderers = true;
  }

  @override
  Future<void> connect({
    required bool caller,
    required List<IceServer> iceServers,
    required CallSignalSink sink,
    required void Function(CallLinkState state) onState,
  }) async {
    _sink = sink;
    _onState = onState;
    final pc = _pc = await createPeerConnection({
      'iceServers': [for (final server in iceServers) server.toRtc()],
      'sdpSemantics': 'unified-plan',
      'bundlePolicy': 'max-bundle',
    });
    final local = _local;
    if (local != null) {
      for (final track in local.getTracks()) {
        await pc.addTrack(track, local);
      }
    }
    pc.onIceCandidate = (candidate) {
      final text = candidate.candidate;
      if (text == null || text.isEmpty) return;
      _pendingOut.add({
        'candidate': text,
        'sdpMid': candidate.sdpMid,
        'sdpMLineIndex': candidate.sdpMLineIndex,
      });
      _flush ??= Timer(const Duration(milliseconds: 150), _send);
    };
    pc.onTrack = (event) async {
      if (event.streams.isEmpty) return;
      if (_video) {
        await _initRenderers();
        _remoteRenderer.srcObject = event.streams.first;
      }
    };
    pc.onConnectionState = (state) {
      switch (state) {
        case RTCPeerConnectionState.RTCPeerConnectionStateConnected:
          _grace?.cancel();
          _grace = null;
          _onState?.call(CallLinkState.connected);
        case RTCPeerConnectionState.RTCPeerConnectionStateDisconnected:
          _onState?.call(CallLinkState.reconnecting);
          _grace ??= Timer(const Duration(seconds: 10), () {
            if (_pc?.connectionState !=
                RTCPeerConnectionState.RTCPeerConnectionStateConnected) {
              _onState?.call(CallLinkState.failed);
            }
          });
        case RTCPeerConnectionState.RTCPeerConnectionStateFailed:
          _onState?.call(CallLinkState.failed);
        default:
          break;
      }
    };
    onState(CallLinkState.connecting);
  }

  void _send() {
    _flush = null;
    if (_pendingOut.isEmpty) return;
    final batch = _pendingOut.take(20).toList();
    _pendingOut.removeRange(0, batch.length);
    unawaited(_sink?.candidates(batch).catchError((_) {}));
    if (_pendingOut.isNotEmpty) {
      _flush = Timer(const Duration(milliseconds: 150), _send);
    }
  }

  @override
  Future<void> offer() async {
    final pc = _pc;
    if (pc == null) return;
    final offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await _sink?.description('offer', offer.sdp ?? '');
  }

  Future<void> _remoteReady() async {
    _remoteSet = true;
    for (final candidate in List.of(_pendingIn)) {
      await _pc?.addCandidate(candidate);
    }
    _pendingIn.clear();
  }

  @override
  Future<void> handle(CallSignal signal) async {
    final pc = _pc;
    if (pc == null || _closed) return;
    if (signal.kind == 'offer' && signal.sdp != null) {
      await pc.setRemoteDescription(RTCSessionDescription(signal.sdp, 'offer'));
      await _remoteReady();
      final answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await _sink?.description('answer', answer.sdp ?? '');
    } else if (signal.kind == 'answer' && signal.sdp != null) {
      await pc
          .setRemoteDescription(RTCSessionDescription(signal.sdp, 'answer'));
      await _remoteReady();
    } else if (signal.kind == 'ice') {
      for (final item in signal.candidates) {
        final candidate = RTCIceCandidate(
            item['candidate'] as String?,
            item['sdpMid'] as String?,
            (item['sdpMLineIndex'] as num?)?.toInt());
        if (_remoteSet) {
          await pc.addCandidate(candidate);
        } else {
          _pendingIn.add(candidate);
        }
      }
    }
  }

  @override
  void setMuted(bool muted) {
    for (final track
        in _local?.getAudioTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = !muted;
    }
  }

  @override
  Future<void> setSpeaker(bool on) => Helper.setSpeakerphoneOn(on);

  @override
  void setCamera(bool on) {
    for (final track
        in _local?.getVideoTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = on;
    }
  }

  @override
  Widget? remoteView() => _video && _renderers
      ? RTCVideoView(_remoteRenderer,
          objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitCover)
      : null;

  @override
  Widget? localView() => _video && _renderers
      ? RTCVideoView(_localRenderer,
          mirror: true,
          objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitCover)
      : null;

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    _flush?.cancel();
    _grace?.cancel();
    for (final track in _local?.getTracks() ?? const <MediaStreamTrack>[]) {
      await track.stop();
    }
    await _local?.dispose();
    await _pc?.close();
    if (_renderers) {
      _remoteRenderer.srcObject = null;
      _localRenderer.srcObject = null;
      await _remoteRenderer.dispose();
      await _localRenderer.dispose();
    }
  }
}
