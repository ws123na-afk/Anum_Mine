// In-app calls in the mobile app (docs/in-app-calls.md#flutter): the overlay
// that shows the incoming ring and the call in progress above every screen,
// and the Calls screen (teammates to call, recent and missed calls).
import '../../data/readable_error.dart';
import 'package:flutter/material.dart';

import '../../src/theme/anum_type.dart';
import '../../src/widgets/anum_components.dart';
import 'call_controller.dart';
import 'call_media.dart';
import 'call_models.dart';
import 'call_strings.dart';

/// Makes the app's [AppCallsController] reachable from any screen.
class AppCallsScope extends InheritedNotifier<AppCallsController> {
  const AppCallsScope(
      {required AppCallsController controller, required super.child, super.key})
      : super(notifier: controller);

  static AppCallsController? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppCallsScope>()?.notifier;
}

String _initials(String name) {
  final words =
      name.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty).toList();
  if (words.isEmpty) return '?';
  if (words.length == 1) {
    return words.first.characters.take(2).toString().toUpperCase();
  }
  return '${words.first.characters.first}${words.last.characters.first}'
      .toUpperCase();
}

/// The call layer above the app's navigator.
class AppCallOverlay extends StatelessWidget {
  const AppCallOverlay(
      {required this.controller, required this.child, super.key});
  final AppCallsController controller;
  final Widget child;

  @override
  Widget build(BuildContext context) => AppCallsScope(
        controller: controller,
        child: ListenableBuilder(
          listenable: controller,
          builder: (context, _) {
            final s = TeamCallText.of(context);
            final notice = controller.notice;
            return Stack(children: [
              child,
              if (controller.phase != TeamCallPhase.idle)
                Positioned.fill(
                    child: controller.picking
                        ? Material(
                            color: Theme.of(context).colorScheme.surface,
                            child: SafeArea(
                                child: Align(
                                    alignment: Alignment.topCenter,
                                    child: ConstrainedBox(
                                        constraints:
                                            const BoxConstraints(maxWidth: 560),
                                        // The call layer sits above the
                                        // navigator, so the search field
                                        // gets its own Overlay.
                                        child: Overlay(initialEntries: [
                                          OverlayEntry(
                                              builder: (_) =>
                                                  TeamCallPeoplePicker(
                                                      controller: controller))
                                        ])))))
                        : TeamCallScreen(controller: controller)),
              if (notice != null)
                Positioned(
                  left: 16,
                  right: 16,
                  bottom: 24,
                  child: SafeArea(
                    child: Material(
                      elevation: 6,
                      borderRadius: BorderRadius.circular(12),
                      color: Theme.of(context).colorScheme.inverseSurface,
                      child: Padding(
                        padding:
                            const EdgeInsetsDirectional.fromSTEB(16, 8, 4, 8),
                        child: Row(children: [
                          Expanded(
                            child: Semantics(
                              liveRegion: true,
                              child: Text(s.f(notice.key, notice.values),
                                  style: TextStyle(
                                      color: Theme.of(context)
                                          .colorScheme
                                          .onInverseSurface)),
                            ),
                          ),
                          // No tooltip: this layer sits above the navigator's Overlay.
                          Semantics(
                            button: true,
                            label: s.t('close'),
                            excludeSemantics: true,
                            child: IconButton(
                              onPressed: controller.clearNotice,
                              icon: Icon(Icons.close,
                                  color: Theme.of(context)
                                      .colorScheme
                                      .onInverseSurface),
                            ),
                          ),
                        ]),
                      ),
                    ),
                  ),
                ),
            ]);
          },
        ),
      );
}

class _RoundButton extends StatelessWidget {
  const _RoundButton(
      {required this.icon,
      required this.label,
      required this.onPressed,
      this.color,
      this.selected = false});
  final IconData icon;
  final String label;
  final VoidCallback? onPressed;
  final Color? color;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final background = color ??
        (selected ? scheme.primaryContainer : scheme.surfaceContainerHighest);
    final foreground = color != null
        ? Colors.white
        : (selected ? scheme.onPrimaryContainer : scheme.onSurface);
    return Semantics(
      button: true,
      toggled: color == null && onPressed != null ? selected : null,
      label: label,
      excludeSemantics: true,
      child: SizedBox(
        width: 84,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Material(
            color: background,
            shape: const CircleBorder(),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onPressed,
              child: SizedBox.square(
                  dimension: 60,
                  child: Icon(icon, color: foreground, size: 28)),
            ),
          ),
          const SizedBox(height: 6),
          Text(label,
              textAlign: TextAlign.center,
              maxLines: 2,
              style: Theme.of(context).textTheme.labelMedium),
        ]),
      ),
    );
  }
}

/// Incoming, ringing, connecting, connected (timer) and ended.
class TeamCallScreen extends StatelessWidget {
  const TeamCallScreen({required this.controller, super.key});
  final AppCallsController controller;

  String _status(TeamCallText s) {
    final call = controller.call;
    switch (controller.phase) {
      case TeamCallPhase.preparing:
        return s.t('preparing');
      case TeamCallPhase.ringing:
        return s.t('ringing');
      case TeamCallPhase.incoming:
        return s.t('incoming');
      case TeamCallPhase.connecting:
      case TeamCallPhase.idle:
        return s.t('connecting');
      case TeamCallPhase.reconnecting:
        return s.t('reconnecting');
      case TeamCallPhase.connected:
        return formatCallDuration(controller.elapsed);
      case TeamCallPhase.ended:
        if (controller.endedGroup) return s.t('word.completed');
        final word = call?.word;
        return s.t('word.${(word ?? CallWord.completed).name}');
    }
  }

  String? _endDetail(TeamCallText s) {
    final call = controller.call;
    if (call == null || controller.endedGroup) return null;
    final name = call.peerName;
    return switch (call.word) {
      CallWord.declinedByPeer => s.f('ended.declinedByPeer', {'name': name}),
      CallWord.busyPeer => s.f('ended.busyPeer', {'name': name}),
      CallWord.noAnswer => s.f('ended.noAnswer', {'name': name}),
      CallWord.failed => s.t('ended.failed'),
      CallWord.dropped => s.t('ended.dropped'),
      CallWord.completed when call.durationSeconds != null => s.f(
          'ended.duration',
          {'duration': formatCallDuration(call.durationSeconds!)}),
      _ => null,
    };
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamCallText.of(context);
    final scheme = Theme.of(context).colorScheme;
    final phase0 = controller.phase;
    final group = controller.group && phase0 != TeamCallPhase.incoming;
    final name = group
        ? s.t('group.title')
        : controller.peer?.name ?? controller.call?.peerName ?? '';
    final video = controller.media == CallMediaKind.video;
    final phase = controller.phase;
    final remote = controller.session?.remoteView();
    final local = controller.session?.localView();
    const red = Color(0xFFBE123C);
    const green = Color(0xFF15803D);
    final controls = <Widget>[
      if (phase == TeamCallPhase.incoming) ...[
        _RoundButton(
            icon: Icons.call,
            label: s.t('answer'),
            color: green,
            onPressed: () => controller.answer()),
        if (video)
          _RoundButton(
              icon: Icons.videocam,
              label: s.t('answerVideo'),
              color: green,
              onPressed: () => controller.answer(video: true)),
        _RoundButton(
            icon: Icons.call_end,
            label: s.t('decline'),
            color: red,
            onPressed: controller.decline),
      ] else if (phase == TeamCallPhase.ended) ...[
        if (!controller.endedGroup)
          _RoundButton(
              icon: Icons.call,
              label: s.t('callAgain'),
              color: green,
              onPressed: controller.peer == null
                  ? null
                  : () {
                      final person = controller.peer!;
                      final again = video;
                      controller.dismiss();
                      controller.place(person, video: again);
                    }),
        _RoundButton(
            icon: Icons.close,
            label: s.t('close'),
            onPressed: controller.dismiss),
      ] else ...[
        _RoundButton(
            icon: controller.muted ? Icons.mic_off : Icons.mic,
            label: controller.muted ? s.t('unmute') : s.t('mute'),
            selected: controller.muted,
            onPressed:
                controller.session == null ? null : controller.toggleMute),
        _RoundButton(
            icon: controller.speaker ? Icons.volume_up : Icons.volume_off,
            label: controller.speaker ? s.t('speakerOff') : s.t('speakerOn'),
            selected: controller.speaker,
            onPressed:
                controller.session == null ? null : controller.toggleSpeaker),
        if (video)
          _RoundButton(
              icon: controller.camera ? Icons.videocam_off : Icons.videocam,
              label: controller.camera ? s.t('cameraOff') : s.t('cameraOn'),
              selected: !controller.camera,
              onPressed:
                  controller.session == null ? null : controller.toggleCamera)
        else if (controller.config?.videoEnabled ?? false)
          _RoundButton(
              icon: Icons.videocam_outlined,
              label: s.t('video.turnOn'),
              onPressed: controller.canTurnOnVideo
                  ? () => controller.turnOnVideo()
                  : null),
        _RoundButton(
            icon: Icons.person_add_alt_1,
            label: s.t('group.add'),
            onPressed:
                phase == TeamCallPhase.connected && controller.freeSlots > 0
                    ? controller.openPicker
                    : null),
        _RoundButton(
            icon: Icons.call_end,
            label: s.t('hangUp'),
            color: red,
            onPressed: phase == TeamCallPhase.preparing
                ? null
                : () => controller.hangUp()),
      ],
    ];
    final detail = phase == TeamCallPhase.ended ? _endDetail(s) : null;
    return Material(
      color: scheme.surface,
      child: SafeArea(
        child: LayoutBuilder(builder: (context, constraints) {
          final avatar = CircleAvatar(
            radius: 48,
            backgroundColor: scheme.primary,
            child: Text(_initials(name),
                style: TextStyle(
                    fontSize: AnumType.x5lPlus,
                    fontWeight: FontWeight.w800,
                    color: scheme.onPrimary)),
          );
          final tiles = group && phase != TeamCallPhase.ended
              ? _GroupTiles(controller: controller)
              : null;
          final Widget media = tiles ??
              ((video && remote != null && phase != TeamCallPhase.ended)
                  ? AspectRatio(
                      aspectRatio: 3 / 4,
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(16),
                        child: Stack(fit: StackFit.expand, children: [
                          ColoredBox(color: Colors.black, child: remote),
                          if (local != null)
                            PositionedDirectional(
                              end: 12,
                              bottom: 12,
                              width: 96,
                              height: 128,
                              child: ClipRRect(
                                  borderRadius: BorderRadius.circular(12),
                                  child: local),
                            ),
                        ]),
                      ),
                    )
                  : group
                      ? CircleAvatar(
                          radius: 48,
                          backgroundColor: scheme.primary,
                          child: Icon(Icons.groups,
                              size: 48, color: scheme.onPrimary))
                      : avatar);
          return SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints:
                  BoxConstraints(minHeight: constraints.maxHeight - 48),
              child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 420),
                        child: media),
                    const SizedBox(height: 16),
                    Semantics(
                      header: true,
                      child: Text(name,
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.headlineSmall),
                    ),
                    const SizedBox(height: 4),
                    Semantics(
                      liveRegion: true,
                      child: Text(
                          '${group && phase != TeamCallPhase.ended ? s.f('group.count', {
                                  'count': controller.liveCalls.length + 1
                                }) : phase == TeamCallPhase.incoming && controller.group ? s.t('group.incoming') : s.t(video ? 'videoCall' : 'voiceCall')} · ${_status(s)}',
                          key: const ValueKey('call-status'),
                          textAlign: TextAlign.center,
                          style: Theme.of(context)
                              .textTheme
                              .bodyLarge
                              ?.copyWith(color: scheme.onSurfaceVariant)),
                    ),
                    if (detail != null) ...[
                      const SizedBox(height: 8),
                      Text(detail, textAlign: TextAlign.center),
                    ],
                    const SizedBox(height: 32),
                    Wrap(
                        alignment: WrapAlignment.center,
                        spacing: 12,
                        runSpacing: 16,
                        children: controls),
                  ]),
            ),
          );
        }),
      ),
    );
  }
}

/// One tile per person of a group call: their picture, or their initials,
/// and how their connection is doing.
class _GroupTiles extends StatelessWidget {
  const _GroupTiles({required this.controller});
  final AppCallsController controller;

  @override
  Widget build(BuildContext context) {
    final s = TeamCallText.of(context);
    final scheme = Theme.of(context).colorScheme;
    final people = <({String name, String status, Widget? view})>[];
    String word(CallLinkState state, CallStatus status) =>
        status == CallStatus.ringing
            ? s.t('ringing')
            : switch (state) {
                CallLinkState.connected => s.t('connected'),
                CallLinkState.reconnecting => s.t('reconnecting'),
                _ => s.t('connecting'),
              };
    final primary = controller.call;
    if (primary != null && controller.liveCalls.contains(primary)) {
      people.add((
        name: primary.peerName,
        status: word(
            controller.phase == TeamCallPhase.connected
                ? CallLinkState.connected
                : CallLinkState.connecting,
            primary.status),
        view: controller.session?.remoteView(),
      ));
    }
    for (final link in controller.links.values.where((l) => l.live)) {
      people.add((
        name: link.call.peerName,
        status: word(link.state, link.call.status),
        view: link.session?.remoteView(),
      ));
    }
    return Semantics(
      label: s.t('group.people'),
      container: true,
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        alignment: WrapAlignment.center,
        children: [
          for (final person in people)
            SizedBox(
              width: 150,
              height: 112,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: ColoredBox(
                  color: scheme.surfaceContainerHighest,
                  child: Stack(fit: StackFit.expand, children: [
                    person.view ??
                        Center(
                          child: CircleAvatar(
                            radius: 24,
                            backgroundColor: scheme.primary,
                            child: Text(_initials(person.name),
                                style: TextStyle(
                                    fontWeight: FontWeight.w800,
                                    color: scheme.onPrimary)),
                          ),
                        ),
                    PositionedDirectional(
                      start: 6,
                      end: 6,
                      bottom: 6,
                      child: Text('${person.name} · ${person.status}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.labelMedium),
                    ),
                  ]),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// The people picker of "Add person", shown in place of the call screen (the
/// call layer sits above the app's navigator): searchable; you and the people
/// already in the call are left out; the server checks membership again.
class TeamCallPeoplePicker extends StatefulWidget {
  const TeamCallPeoplePicker({required this.controller, super.key});
  final AppCallsController controller;

  @override
  State<TeamCallPeoplePicker> createState() => _TeamCallPeoplePickerState();
}

class _TeamCallPeoplePickerState extends State<TeamCallPeoplePicker> {
  List<CallPerson>? _people;
  String? _error;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final people = await widget.controller.repository.people();
      if (mounted) setState(() => _people = people);
    } on Object catch (error) {
      if (mounted) setState(() => _error = readableError(error));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamCallText.of(context);
    final c = widget.controller;
    final inCall = {
      c.selfUserId,
      for (final live in c.liveCalls) live.peerUserId,
      for (final member in c.room?.members ?? const <CallRoomMember>[])
        if (member.active) member.userId,
    };
    final words = _query.trim().toLowerCase();
    final candidates = (_people ?? const <CallPerson>[])
        .where((p) => !inCall.contains(p.userId))
        .where((p) => words.isEmpty || p.name.toLowerCase().contains(words))
        .toList()
      ..sort((a, b) => a.name.compareTo(b.name));
    return Padding(
      padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 16,
          bottom: 16 + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
              header: true,
              child: Text(s.t('group.addTitle'),
                  style: Theme.of(context).textTheme.titleLarge)),
          const SizedBox(height: 4),
          Text(s.f('group.pickHint', {'count': c.freeSlots})),
          const SizedBox(height: 12),
          if (_error != null) ...[
            Text(s.f('group.peopleFailed', {'message': _error!})),
            TextButton(onPressed: _load, child: Text(s.t('retry'))),
          ] else if (_people == null)
            const Center(child: CircularProgressIndicator())
          else ...[
            TextField(
              decoration: InputDecoration(
                  labelText: s.t('group.search'),
                  prefixIcon: const Icon(Icons.search)),
              onChanged: (value) => setState(() => _query = value),
            ),
            const SizedBox(height: 8),
            if (candidates.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: Text(_query.isEmpty
                    ? s.t('group.nobodyLeft')
                    : s.t('group.noMatch')),
              )
            else
              Flexible(
                child: ListView(shrinkWrap: true, children: [
                  for (final person in candidates)
                    ListTile(
                      leading: const Icon(Icons.person_add_alt_1),
                      title: Text(person.name),
                      onTap: () {
                        c.closePicker();
                        c.invite(person);
                      },
                    ),
                ]),
              ),
          ],
          TextButton(
              onPressed: c.closePicker, child: Text(s.t('group.cancel'))),
        ],
      ),
    );
  }
}

/// Call and Video buttons for one teammate.
class TeamCallButtons extends StatelessWidget {
  const TeamCallButtons({required this.person, super.key});
  final CallPerson person;

  @override
  Widget build(BuildContext context) {
    final controller = AppCallsScope.maybeOf(context);
    if (controller == null) return const SizedBox.shrink();
    final s = TeamCallText.of(context);
    final disabled = controller.unavailableKey != null ||
        controller.busy ||
        controller.configPhase == CallConfigPhase.loading;
    final video = controller.config?.videoEnabled ?? true;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      IconButton(
        tooltip: s.f('callPerson', {'name': person.name}),
        onPressed: disabled ? null : () => controller.place(person),
        icon: const Icon(Icons.call_outlined),
      ),
      if (video)
        IconButton(
          tooltip: s.f('videoPerson', {'name': person.name}),
          onPressed:
              disabled ? null : () => controller.place(person, video: true),
          icon: const Icon(Icons.videocam_outlined),
        ),
    ]);
  }
}

/// Teammates to call and the recent calls, with the missed filter.
class TeamCallsScreen extends StatefulWidget {
  const TeamCallsScreen({required this.controller, super.key});
  final AppCallsController controller;

  @override
  State<TeamCallsScreen> createState() => _TeamCallsScreenState();
}

class _TeamCallsScreenState extends State<TeamCallsScreen> {
  List<CallPerson>? _people;
  String? _peopleError;
  List<AppCall> _calls = const [];
  String? _cursor;
  String? _historyError;
  bool _loading = true;
  bool _missed = false;
  int _revision = -1;

  AppCallsController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    _c.addListener(_changed);
    _loadPeople();
    _loadHistory();
  }

  @override
  void dispose() {
    _c.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (_c.revision != _revision && mounted) _loadHistory();
  }

  Future<void> _loadPeople() async {
    try {
      final people = await _c.repository.people();
      if (mounted) {
        setState(() {
          _people = people;
          _peopleError = null;
        });
      }
    } on Object catch (error) {
      if (mounted) setState(() => _peopleError = readableError(error));
    }
  }

  Future<void> _loadHistory({bool more = false}) async {
    _revision = _c.revision;
    if (!more) setState(() => _loading = true);
    try {
      final page = await _c.repository
          .history(cursor: more ? _cursor : null, missed: _missed);
      if (!mounted) return;
      setState(() {
        _calls = more ? [..._calls, ...page.items] : page.items;
        _cursor = page.nextCursor;
        _historyError = null;
        _loading = false;
      });
    } on Object catch (error) {
      if (mounted) {
        setState(() {
          _historyError = readableError(error);
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamCallText.of(context);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(s.t('calls')),
          bottom: TabBar(
              tabs: [Tab(text: s.t('people')), Tab(text: s.t('recent'))]),
        ),
        body: ListenableBuilder(
          listenable: _c,
          builder: (context, _) {
            final banner = _c.unavailableKey ??
                (_c.config?.mode == CallMode.stunOnly ? 'stunOnly' : null);
            final header = banner == null
                ? const SizedBox.shrink()
                : Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                    child: AnumFeedback(
                      kind: _c.unavailableKey == 'offline'
                          ? AnumFeedbackKind.offline
                          : _c.unavailableKey == 'denied'
                              ? AnumFeedbackKind.permission
                              : AnumFeedbackKind.empty,
                      message: s.t(banner),
                    ),
                  );
            return TabBarView(children: [
              RefreshIndicator(
                onRefresh: () async {
                  await _c.start();
                  await _loadPeople();
                },
                child: ListView(
                    padding: const EdgeInsets.only(bottom: 24),
                    children: [
                      header,
                      if (_peopleError != null)
                        AnumFeedback(
                            kind: AnumFeedbackKind.error,
                            message:
                                s.f('loadFailed', {'message': _peopleError!}),
                            onRetry: _loadPeople)
                      else if (_people == null)
                        AnumFeedback(
                            kind: AnumFeedbackKind.loading,
                            message: s.t('loading'))
                      else if (_people!
                          .where((p) => p.userId != _c.selfUserId)
                          .isEmpty)
                        AnumFeedback(
                            kind: AnumFeedbackKind.empty,
                            message: s.t('noPeople'))
                      else
                        for (final person in _people!)
                          if (person.userId != _c.selfUserId)
                            ListTile(
                              leading: CircleAvatar(
                                  child: Text(_initials(person.name))),
                              title: Text(person.name),
                              trailing: TeamCallButtons(person: person),
                            ),
                    ]),
              ),
              RefreshIndicator(
                onRefresh: () => _loadHistory(),
                child: ListView(
                    padding: const EdgeInsets.only(bottom: 24),
                    children: [
                      header,
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                        child: Wrap(spacing: 8, children: [
                          ChoiceChip(
                              label: Text(s.t('all')),
                              selected: !_missed,
                              onSelected: (_) {
                                setState(() => _missed = false);
                                _loadHistory();
                              }),
                          ChoiceChip(
                              label: Text(s.t('missed')),
                              selected: _missed,
                              onSelected: (_) {
                                setState(() => _missed = true);
                                _loadHistory();
                              }),
                        ]),
                      ),
                      if (_loading)
                        AnumFeedback(
                            kind: AnumFeedbackKind.loading,
                            message: s.t('loading'))
                      else if (_historyError != null)
                        AnumFeedback(
                            kind: AnumFeedbackKind.error,
                            message:
                                s.f('loadFailed', {'message': _historyError!}),
                            onRetry: _loadHistory)
                      else if (_calls.isEmpty)
                        AnumFeedback(
                            kind: AnumFeedbackKind.empty,
                            message: _missed
                                ? s.t('emptyMissed')
                                : '${s.t('empty')}. ${s.t('emptyText')}')
                      else ...[
                        for (final call in _calls) _CallTile(call: call),
                        if (_cursor != null)
                          Padding(
                            padding: const EdgeInsets.all(16),
                            child: OutlinedButton(
                                onPressed: () => _loadHistory(more: true),
                                child: Text(s.t('loadOlder'))),
                          ),
                      ],
                    ]),
              ),
            ]);
          },
        ),
      ),
    );
  }
}

class _CallTile extends StatelessWidget {
  const _CallTile({required this.call});
  final AppCall call;

  @override
  Widget build(BuildContext context) {
    final s = TeamCallText.of(context);
    final scheme = Theme.of(context).colorScheme;
    final m = MaterialLocalizations.of(context);
    final local = call.createdAt.toLocal();
    final when =
        '${m.formatShortDate(local)} ${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
    final word = call.word;
    final parts = [
      s.t(call.incoming ? 'incomingCall' : 'outgoingCall'),
      // One row per person of a group call, each marked as such.
      if (call.roomId != null) s.t('group.title'),
      s.t(call.video ? 'videoCall' : 'voiceCall'),
      when,
      if (call.answeredAt != null && call.durationSeconds != null)
        formatCallDuration(call.durationSeconds!),
    ];
    return ListTile(
      leading: Icon(
        call.missed
            ? Icons.call_missed
            : call.incoming
                ? Icons.call_received
                : Icons.call_made,
        color: call.missed ? scheme.error : scheme.onSurfaceVariant,
      ),
      title: Text(call.peerName),
      subtitle: Text(
          '${word == null ? s.t('ringing') : s.t('word.${word.name}')} · ${parts.join(' · ')}'),
      trailing: TeamCallButtons(
          person: CallPerson(userId: call.peerUserId, name: call.peerName)),
    );
  }
}
