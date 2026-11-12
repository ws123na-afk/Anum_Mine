// The WebRTC side of an in-app call (docs/in-app-calls.md#media), behind an
// interface so widget tests use a fake. Media flows peer to peer; the API only
// carries the offer, the answer and the ICE candidates.
//
// Every connection negotiates an audio and a video section and a small data
// channel, so video can be turned on in a voice call by swapping the track on
// the video sender (no new offer), and each side says over the data channel
// whether its camera (or, from the web, a shared screen) is on. A group call
// holds one session per other person; [fork] makes them share one microphone
// and camera.
import '../../data/readable_error.dart';
import 'dart:async';

import 'package:flutter/foundation.dart';
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

  /// The other person's video (or null while it is off), and this phone's.
  Widget? remoteView();
  Widget? localView();
  Future<void> close();

  /// A session for another person of the same (group) call that shares this
  /// session's microphone and camera; closing it leaves them open.
  CallMediaSession fork();

  /// Opens the camera mid-call and adds it to the shared local media; throws
  /// [CallMediaException].
  Future<void> startCamera();

  /// Sends the shared camera on this connection's video section; false when
  /// the connection has none (an older app's offer).
  Future<bool> sendVideo();

  /// What the other side says about its camera, screen and microphone.
  ValueListenable<CallPeerMedia?> get peer;
}

typedef CallMediaFactory = CallMediaSession Function();

/// [CallMediaSession] over flutter_webrtc.
class WebRtcCallMedia implements CallMediaSession {
  WebRtcCallMedia() : _shared = null;
  WebRtcCallMedia._shared(this._shared);

  /// The session that owns the local media, for a forked session.
  final WebRtcCallMedia? _shared;
  MediaStream? _ownLocal;
  MediaStream? get _local => _shared?._local ?? _ownLocal;
  set _local(MediaStream? value) => _ownLocal = value;
  final ValueNotifier<CallPeerMedia?> _peer = ValueNotifier(null);
  RTCDataChannel? _channel;
  bool _muted = false;
  bool _cameraOn = false;
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
      final text = readableError(error);
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
    _cameraOn = video;
    await _initRenderers();
    if (video) _localRenderer.srcObject = _local;
  }

  @override
  ValueListenable<CallPeerMedia?> get peer => _peer;

  @override
  CallMediaSession fork() {
    final owner = _shared ?? this;
    return WebRtcCallMedia._shared(owner)
      .._video = owner._video
      .._muted = owner._muted
      .._cameraOn = owner._cameraOn;
  }

  @override
  Future<void> startCamera() async {
    final owner = _shared ?? this;
    final local = owner._local;
    if (local == null) return;
    if (local.getVideoTracks().isNotEmpty) return;
    try {
      final camera = await navigator.mediaDevices.getUserMedia({
        'video': {'facingMode': 'user'},
        'audio': false
      });
      for (final track in camera.getVideoTracks()) {
        await local.addTrack(track);
      }
    } on Object catch (error) {
      final text = readableError(error);
      final lower = text.toLowerCase();
      throw CallMediaException(
          lower.contains('permission') || lower.contains('denied')
              ? CallMediaProblem.denied
              : CallMediaProblem.other,
          text);
    }
    owner._cameraOn = true;
    await owner._initRenderers();
    owner._localRenderer.srcObject = local;
    owner._video = true;
  }

  Future<RTCRtpTransceiver?> _videoTransceiver() async {
    final pc = _pc;
    if (pc == null) return null;
    for (final transceiver in await pc.getTransceivers()) {
      if (transceiver.receiver.track?.kind == 'video' && !transceiver.stoped) {
        return transceiver;
      }
    }
    return null;
  }

  @override
  Future<bool> sendVideo() async {
    final transceiver = await _videoTransceiver();
    final tracks = _local?.getVideoTracks() ?? const <MediaStreamTrack>[];
    if (transceiver == null || tracks.isEmpty) return false;
    await transceiver.sender.replaceTrack(tracks.first);
    _video = true;
    _cameraOn = true;
    _pushState();
    return true;
  }

  void _pushState() {
    final channel = _channel;
    if (channel == null ||
        channel.state != RTCDataChannelState.RTCDataChannelOpen) {
      return;
    }
    final state = CallPeerMedia(
        camera: _cameraOn && (_local?.getVideoTracks().isNotEmpty ?? false),
        screen: false,
        muted: _muted);
    unawaited(channel
        .send(RTCDataChannelMessage(state.encode()))
        .catchError((Object _) {}));
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
    // A video section even on a voice call, so video can start mid-call.
    if (caller && (local?.getVideoTracks().isEmpty ?? true)) {
      await pc.addTransceiver(
          kind: RTCRtpMediaType.RTCRtpMediaTypeVideo,
          init: RTCRtpTransceiverInit(
              direction: TransceiverDirection.SendRecv,
              streams: [if (local != null) local]));
    }
    await _initRenderers();
    final channel = _channel = await pc.createDataChannel(
        'anum-state',
        RTCDataChannelInit()
          ..negotiated = true
          ..id = 0);
    channel.onDataChannelState = (state) {
      if (state == RTCDataChannelState.RTCDataChannelOpen) _pushState();
    };
    channel.onMessage = (message) {
      if (message.isBinary) return;
      final state = CallPeerMedia.parse(message.text);
      if (state != null) _peer.value = state;
    };
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
      await _initRenderers();
      if (event.streams.isNotEmpty) {
        _remoteRenderer.srcObject = event.streams.first;
      } else if (event.track.kind == 'video') {
        final stream = await createLocalMediaStream('remote');
        await stream.addTrack(event.track);
        _remoteRenderer.srcObject = stream;
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
      // Answer the video section as send-and-receive even on a voice call.
      final transceiver = await _videoTransceiver();
      if (transceiver != null &&
          await transceiver.getDirection() == TransceiverDirection.RecvOnly) {
        await transceiver.setDirection(TransceiverDirection.SendRecv);
      }
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
    _muted = muted;
    for (final track
        in _local?.getAudioTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = !muted;
    }
    _pushState();
  }

  @override
  Future<void> setSpeaker(bool on) => Helper.setSpeakerphoneOn(on);

  @override
  void setCamera(bool on) {
    _cameraOn = on;
    for (final track
        in _local?.getVideoTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = on;
    }
    _pushState();
  }

  /// The remote picture: when the other side says its camera or screen is on,
  /// or, for an app that does not say, in a video call.
  @override
  Widget? remoteView() {
    final peer = _peer.value;
    final on = peer == null ? _video : (peer.camera || peer.screen);
    return on && _renderers
        ? RTCVideoView(_remoteRenderer,
            objectFit: peer?.screen ?? false
                ? RTCVideoViewObjectFit.RTCVideoViewObjectFitContain
                : RTCVideoViewObjectFit.RTCVideoViewObjectFitCover)
        : null;
  }

  @override
  Widget? localView() => (_shared ?? this)._cameraOn &&
          (_local?.getVideoTracks().isNotEmpty ?? false) &&
          (_shared ?? this)._renderers
      ? RTCVideoView((_shared ?? this)._localRenderer,
          mirror: true,
          objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitCover)
      : null;

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    _flush?.cancel();
    _grace?.cancel();
    // A forked session leaves the shared microphone and camera to its owner.
    if (_shared == null) {
      for (final track in _local?.getTracks() ?? const <MediaStreamTrack>[]) {
        await track.stop();
      }
      await _local?.dispose();
    }
    await _channel?.close();
    await _pc?.close();
    if (_renderers) {
      _remoteRenderer.srcObject = null;
      _localRenderer.srcObject = null;
      await _remoteRenderer.dispose();
      await _localRenderer.dispose();
    }
  }
}
