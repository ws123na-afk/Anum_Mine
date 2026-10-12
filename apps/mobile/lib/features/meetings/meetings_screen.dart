import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'meetings_controller.dart';
import 'meetings_models.dart';
import 'meetings_repository.dart';

/// Picks one file on the device: (path, name, size), or null when cancelled.
typedef MeetingFilePicker = Future<(String, String, int)?> Function(
  List<String> extensions,
);

Future<(String, String, int)?> pickMeetingFile(List<String> extensions) async {
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: [for (final e in extensions) e.replaceFirst('.', '')],
    withData: false,
  );
  final file = picked?.files.firstOrNull;
  if (file?.path == null) return null;
  return (file!.path!, file.name, file.size);
}

String _statusKey(String status) => 'meetings.status.$status';

AnumTone _tone(String status) => switch (status) {
      'ready' => AnumTone.ok,
      'failed' => AnumTone.stop,
      'needs_model' => AnumTone.neutral,
      _ => AnumTone.warn,
    };

String _date(BuildContext context, DateTime value) =>
    MaterialLocalizations.of(context).formatMediumDate(value.toLocal());

/// The Calls & Meetings tab (Resources): list, search, recent activity, import.
class MeetingsTab extends StatefulWidget {
  const MeetingsTab({
    required this.controller,
    this.currentUserId,
    this.pickFile = pickMeetingFile,
    super.key,
  });
  final MeetingsController controller;
  final String? currentUserId;
  final MeetingFilePicker pickFile;

  @override
  State<MeetingsTab> createState() => _MeetingsTabState();
}

class _MeetingsTabState extends State<MeetingsTab> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (widget.controller.items.isEmpty) unawaited(widget.controller.load());
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _open(Meeting meeting) => Navigator.push(
        context,
        MaterialPageRoute<void>(
          builder: (_) => MeetingDetailScreen(
            repository: widget.controller.repository,
            meetingId: meeting.id,
            setup: widget.controller.setup,
            currentUserId: widget.currentUserId,
            onChanged: widget.controller.replace,
            onDeleted: widget.controller.removed,
          ),
        ),
      );

  Future<void> _import() async {
    final created = await showModalBottomSheet<Meeting>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => MeetingImportSheet(
        controller: widget.controller,
        pickFile: widget.pickFile,
      ),
    );
    if (created != null && mounted) await _open(created);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final setup = c.setup;
        final searching = c.query.isNotEmpty || c.status.isNotEmpty;
        final children = <Widget>[
          Text(
            l.t('meetings.intro'),
            style: TextStyle(color: context.palette.muted),
          ),
          if (setup != null && !setup.canCreate)
            _Note(icon: Icons.lock_outline, text: l.t('meetings.viewerNote')),
          if (setup != null && !setup.modelReady)
            _Note(icon: Icons.auto_awesome, text: l.t('meetings.noModel')),
          if (c.canCreate)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                onPressed: c.uploadProgress == null ? _import : null,
                icon: const Icon(Icons.upload_file),
                label: Text(l.t('meetings.import')),
              ),
            ),
          if (c.notice != null && c.notice != 'cancelled')
            _Note(icon: Icons.error_outline, text: c.notice!, error: true),
          if (c.recentActivity.isNotEmpty)
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Semantics(
                    header: true,
                    child: Text(
                      l.t('meetings.recentActivity'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  for (final m in c.recentActivity)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      leading: const Icon(Icons.phone_in_talk_outlined),
                      title: Text(
                        l.f('meetings.callSummary', {'title': m.title}),
                      ),
                      subtitle: m.agentSkillId == null
                          ? null
                          : Text(
                              m.agentSkillId!,
                              textDirection: TextDirection.ltr,
                            ),
                      onTap: () => _open(m),
                    ),
                ],
              ),
            ),
          if (c.phase != MeetingsPhase.denied && (c.total > 0 || searching))
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 520),
                  child: TextField(
                    controller: _search,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      labelText: l.t('meetings.search'),
                      prefixIcon: const Icon(Icons.search),
                    ),
                    onSubmitted: c.search,
                  ),
                ),
                DropdownButton<String>(
                  isExpanded: true,
                  value: c.status,
                  onChanged: (v) => c.filter(v ?? ''),
                  items: [
                    DropdownMenuItem(
                      value: '',
                      child: Text(l.t('meetings.allStatuses')),
                    ),
                    for (final s in const [
                      'queued',
                      'transcribing',
                      'analyzing',
                      'ready',
                      'needs_model',
                      'failed',
                    ])
                      DropdownMenuItem(
                        value: s,
                        child: Text(l.t(_statusKey(s))),
                      ),
                  ],
                ),
              ],
            ),
          if (c.phase == MeetingsPhase.loading)
            Semantics(
              liveRegion: true,
              child: Row(
                children: [
                  const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: AnumSpacing.sm),
                  Expanded(child: Text(l.t('meetings.loading'))),
                ],
              ),
            ),
          if (c.phase == MeetingsPhase.denied)
            AnumSurface(
              child: AnumEmpty(
                icon: Icons.lock_outline,
                title: l.t('meetings.deniedTitle'),
                message: l.t('meetings.denied'),
              ),
            ),
          if (c.phase == MeetingsPhase.error)
            AnumSurface(
              child: AnumEmpty(
                icon: Icons.cloud_off_outlined,
                title: l.t('meetings.loadFailed'),
                message: c.error ?? '',
                action: l.t('retry'),
                onAction: c.load,
              ),
            ),
          if (c.phase == MeetingsPhase.ready && c.items.isEmpty)
            AnumSurface(
              child: searching
                  ? AnumEmpty(
                      icon: Icons.search_off,
                      title: l.t('meetings.noMatch'),
                      message: l.t('meetings.noMatchText'),
                      action: l.t('meetings.clearSearch'),
                      onAction: () {
                        _search.clear();
                        c.query = '';
                        unawaited(c.filter(''));
                      },
                    )
                  : AnumEmpty(
                      icon: Icons.phone_in_talk_outlined,
                      title: l.t('meetings.empty'),
                      message: c.canCreate
                          ? l.t('meetings.emptyText')
                          : l.t('meetings.emptyReadOnly'),
                      action: c.canCreate ? l.t('meetings.import') : null,
                      onAction: c.canCreate ? _import : null,
                    ),
            ),
          for (final m in c.items)
            AnumSurface(
              onTap: () => _open(m),
              semanticLabel: '${m.title}, ${l.t(_statusKey(m.status))}',
              child: Row(
                children: [
                  const Icon(Icons.phone_in_talk_outlined),
                  const SizedBox(width: AnumSpacing.sm),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          m.title,
                          style: Theme.of(context).textTheme.titleSmall,
                        ),
                        Text(
                          '${l.t('meetings.source.${m.source}')} · ${_date(context, m.occurredAt ?? m.createdAt)}',
                          style: TextStyle(color: context.palette.muted),
                        ),
                        if (m.summary != null)
                          Text(
                            m.summary!,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            textDirection: m.isArabic
                                ? TextDirection.rtl
                                : TextDirection.ltr,
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(width: AnumSpacing.xs),
                  AnumPill(
                    label: l.t(_statusKey(m.status)),
                    tone: _tone(m.status),
                  ),
                ],
              ),
            ),
          if (c.hasMore)
            Center(
              child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMore,
                child: Text(
                  c.loadingMore
                      ? l.t('meetings.loading')
                      : l.f('meetings.loadMore', {
                          'shown': c.items.length,
                          'total': c.total,
                        }),
                ),
              ),
            ),
        ];
        return RefreshIndicator(
          onRefresh: c.load,
          child: ListView.separated(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            itemCount: children.length,
            separatorBuilder: (_, __) => const SizedBox(height: AnumSpacing.sm),
            itemBuilder: (_, i) => children[i],
          ),
        );
      },
    );
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.icon, required this.text, this.error = false});
  final IconData icon;
  final String text;
  final bool error;
  @override
  Widget build(BuildContext context) => Semantics(
        liveRegion: true,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(
              icon,
              size: 18,
              color: error
                  ? Theme.of(context).colorScheme.error
                  : context.palette.muted,
            ),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
              child: Text(
                text,
                style: TextStyle(
                  color: error
                      ? Theme.of(context).colorScheme.error
                      : context.palette.muted,
                ),
              ),
            ),
          ],
        ),
      );
}

/// Import a transcript file, a recording, or a Zoom/Teams transcript.
class MeetingImportSheet extends StatefulWidget {
  const MeetingImportSheet({
    required this.controller,
    required this.pickFile,
    super.key,
  });
  final MeetingsController controller;
  final MeetingFilePicker pickFile;
  @override
  State<MeetingImportSheet> createState() => _MeetingImportSheetState();
}

class _MeetingImportSheetState extends State<MeetingImportSheet> {
  String _way = 'transcript', _language = 'auto';
  final _title = TextEditingController();
  String? _problem;
  List<ExternalMeeting>? _external;
  String? _externalError;
  String? _busy;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _pick(bool recording) async {
    final l = AnumLocalizations.maybeOf(context);
    final setup = widget.controller.setup!;
    final picked = await widget.pickFile(
      recording ? setup.recordingExtensions : setup.transcriptExtensions,
    );
    if (picked == null || !mounted) return;
    final (path, name, size) = picked;
    final problem = meetingUploadProblem(
      name: name,
      size: size,
      recording: recording,
      setup: setup,
    );
    if (problem != null) {
      setState(
        () => _problem = l.f('meetings.upload.$problem', {
          'name': name,
          'allowed': (recording
                  ? setup.recordingExtensions
                  : setup.transcriptExtensions)
              .join(', '),
        }),
      );
      return;
    }
    setState(() => _problem = null);
    final created = await widget.controller.importFile(
      recording: recording,
      path: path,
      name: name,
      title: _title.text,
      language: _language,
    );
    if (!mounted) return;
    if (created != null) {
      Navigator.pop(context, created);
    } else {
      setState(
        () => _problem = widget.controller.notice == 'cancelled'
            ? l.t('meetings.upload.cancelled')
            : widget.controller.notice,
      );
    }
  }

  Future<void> _loadExternal(String provider) async {
    setState(() {
      _external = null;
      _externalError = null;
    });
    try {
      final found = await widget.controller.repository.external(provider);
      if (mounted) setState(() => _external = found);
    } on ApiException catch (e) {
      if (mounted) setState(() => _externalError = e.message);
    }
  }

  Future<void> _importExternal(String provider, ExternalMeeting m) async {
    setState(() => _busy = m.externalId);
    try {
      final created = await widget.controller.repository.importExternal(
        provider,
        m.externalId,
        language: _language,
      );
      unawaited(widget.controller.load());
      if (mounted) Navigator.pop(context, created);
    } on ApiException catch (e) {
      if (mounted) setState(() => _externalError = e.message);
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final setup = widget.controller.setup;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final progress = widget.controller.uploadProgress;
        final connector =
            setup?.connectors.where((c) => c.provider == _way).firstOrNull;
        return SafeArea(
          child: Padding(
            padding: EdgeInsets.fromLTRB(
              16,
              0,
              16,
              16 + MediaQuery.viewInsetsOf(context).bottom,
            ),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Semantics(
                    header: true,
                    child: Text(
                      l.t('meetings.import'),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.sm),
                  Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      for (final way in const [
                        'transcript',
                        'recording',
                        'zoom',
                        'teams',
                      ])
                        ChoiceChip(
                          label: Text(l.t('meetings.way.$way')),
                          selected: _way == way,
                          onSelected: progress != null
                              ? null
                              : (_) {
                                  setState(() {
                                    _way = way;
                                    _problem = null;
                                  });
                                  final c = setup?.connectors
                                      .where((x) => x.provider == way)
                                      .firstOrNull;
                                  if (c != null && c.available && c.connected) {
                                    unawaited(_loadExternal(way));
                                  }
                                },
                        ),
                    ],
                  ),
                  const SizedBox(height: AnumSpacing.sm),
                  if (_way == 'transcript' || _way == 'recording')
                    TextField(
                      controller: _title,
                      decoration: InputDecoration(
                        labelText: l.t('meetings.title'),
                      ),
                    ),
                  DropdownButtonFormField<String>(
                    initialValue: _language,
                    decoration: InputDecoration(
                      labelText: l.t('meetings.language'),
                    ),
                    items: [
                      for (final value in const ['auto', 'en', 'ar'])
                        DropdownMenuItem(
                          value: value,
                          child: Text(l.t('meetings.language.$value')),
                        ),
                    ],
                    onChanged: (v) => setState(() => _language = v ?? 'auto'),
                  ),
                  const SizedBox(height: AnumSpacing.sm),
                  if (setup == null)
                    Text(l.t('meetings.loading'))
                  else if (_way == 'recording' &&
                      !setup.transcriptionConfigured)
                    _Explain(
                      title: l.t('meetings.setup.transcriptionTitle'),
                      text: l.t('meetings.setup.transcriptionText'),
                      detail: setup.transcriptionProblem,
                    )
                  else if (_way == 'transcript' || _way == 'recording') ...[
                    Text(
                      l.t(
                        _way == 'recording'
                            ? 'meetings.recordingHint'
                            : 'meetings.transcriptHint',
                      ),
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    if (progress != null) ...[
                      LinearProgressIndicator(
                        value:
                            progress.$2 > 0 ? progress.$1 / progress.$2 : null,
                        semanticsLabel: l.t('meetings.uploading'),
                      ),
                      TextButton(
                        onPressed: widget.controller.cancelUpload,
                        child: Text(l.t('cancel')),
                      ),
                    ] else
                      FilledButton.icon(
                        onPressed: () => _pick(_way == 'recording'),
                        icon: const Icon(Icons.attach_file),
                        label: Text(
                          l.t(
                            _way == 'recording'
                                ? 'meetings.chooseRecording'
                                : 'meetings.chooseTranscript',
                          ),
                        ),
                      ),
                  ] else if (connector == null || !connector.available)
                    _Explain(
                      title: l.f('meetings.connector.notSetUp', {
                        'provider': connector?.name ?? _way,
                      }),
                      text: l.t('meetings.connector.adminSetup'),
                      detail: connector?.problem,
                    )
                  else if (!connector.connected)
                    _Explain(
                      title: l.f('meetings.connector.connectWeb', {
                        'provider': connector.name,
                      }),
                      text: l.t('meetings.connector.connectWebText'),
                    )
                  else ...[
                    Text(
                      l.f('meetings.connector.connectedAs', {
                        'provider': connector.name,
                        'account': connector.accountLabel ?? '',
                      }),
                    ),
                    if (_externalError != null)
                      _Note(
                        icon: Icons.error_outline,
                        text: _externalError!,
                        error: true,
                      ),
                    if (_external == null && _externalError == null)
                      const Padding(
                        padding: EdgeInsets.all(AnumSpacing.sm),
                        child: Center(child: CircularProgressIndicator()),
                      ),
                    if (_external != null && _external!.isEmpty)
                      Text(
                        l.f('meetings.connector.none', {
                          'provider': connector.name,
                        }),
                      ),
                    for (final m in _external ?? const <ExternalMeeting>[])
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(m.title),
                        subtitle: m.startedAt == null
                            ? null
                            : Text(_date(context, m.startedAt!)),
                        trailing: m.importedMeetingId != null
                            ? AnumPill(
                                label: l.t('meetings.connector.imported'),
                                tone: AnumTone.ok,
                              )
                            : OutlinedButton(
                                onPressed: _busy == null
                                    ? () => _importExternal(_way, m)
                                    : null,
                                child: Text(
                                  _busy == m.externalId
                                      ? l.t('meetings.importing')
                                      : l.t('meetings.connector.import'),
                                ),
                              ),
                      ),
                  ],
                  if (_problem != null)
                    _Note(
                      icon: Icons.error_outline,
                      text: _problem!,
                      error: true,
                    ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _Explain extends StatelessWidget {
  const _Explain({required this.title, required this.text, this.detail});
  final String title, text;
  final String? detail;
  @override
  Widget build(BuildContext context) => AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: AnumSpacing.xxs),
            Text(text),
            if (detail != null) ...[
              const SizedBox(height: AnumSpacing.xxs),
              Text(
                detail!,
                textDirection: TextDirection.ltr,
                style: TextStyle(color: context.palette.muted),
              ),
            ],
          ],
        ),
      );
}

/// One meeting: AI notes with provenance, action items, the follow-up draft
/// and the searchable transcript (right to left for Arabic meetings).
class MeetingDetailScreen extends StatefulWidget {
  const MeetingDetailScreen({
    required this.repository,
    required this.meetingId,
    this.setup,
    this.currentUserId,
    this.onChanged,
    this.onDeleted,
    this.pollEvery = const Duration(seconds: 3),
    super.key,
  });
  final MeetingsRepository repository;
  final String meetingId;
  final MeetingSetup? setup;
  final String? currentUserId;
  final void Function(Meeting meeting)? onChanged;
  final void Function(String id)? onDeleted;
  final Duration? pollEvery;

  @override
  State<MeetingDetailScreen> createState() => _MeetingDetailScreenState();
}

class _MeetingDetailScreenState extends State<MeetingDetailScreen> {
  Meeting? _meeting;
  String? _error, _actionError, _actionNote, _busy;
  bool _missing = false, _denied = false;
  Timer? _poll;
  TranscriptPage? _transcript;
  String _transcriptQuery = '';
  final _to = TextEditingController(),
      _subject = TextEditingController(),
      _body = TextEditingController();

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _to.dispose();
    _subject.dispose();
    _body.dispose();
    super.dispose();
  }

  void _show(Meeting m) {
    final hadFollowUp = _meeting?.followUp != null;
    _meeting = m;
    if (!hadFollowUp && m.followUp != null) {
      _to.text = m.followUp!.to.join(', ');
      _subject.text = m.followUp!.subject;
      _body.text = m.followUp!.body;
    }
    widget.onChanged?.call(m);
  }

  Future<void> _load() async {
    try {
      final m = await widget.repository.get(widget.meetingId);
      if (!mounted) return;
      setState(() {
        _show(m);
        _error = null;
      });
      if (m.segmentCount > 0 && _transcript == null) {
        unawaited(_loadTranscript());
      }
      _poll?.cancel();
      final every = widget.pollEvery;
      if (m.isWorking && every != null) _poll = Timer(every, _load);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _missing = e.statusCode == 404;
        _denied = e.isPermissionDenied;
        _error = e.message;
      });
    }
  }

  Future<void> _loadTranscript() async {
    try {
      final page = await widget.repository.transcript(
        widget.meetingId,
        query: _transcriptQuery,
      );
      if (mounted) setState(() => _transcript = page);
    } on ApiException catch (e) {
      if (mounted) setState(() => _actionError = e.message);
    }
  }

  Future<void> _loadMoreLines() async {
    final current = _transcript;
    if (current == null) return;
    try {
      final page = await widget.repository.transcript(
        widget.meetingId,
        query: _transcriptQuery,
        offset: current.lines.length,
      );
      if (mounted) {
        setState(
          () => _transcript = TranscriptPage(
            [...current.lines, ...page.lines],
            page.total,
            current.speakers,
          ),
        );
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _actionError = e.message);
    }
  }

  Future<void> _act(
    String name,
    Future<Meeting> Function() work, [
    String? note,
  ]) async {
    final l = AnumLocalizations.maybeOf(context);
    setState(() {
      _busy = name;
      _actionError = null;
      _actionNote = null;
    });
    try {
      final m = await work();
      if (!mounted) return;
      setState(() {
        _show(m);
        _actionNote = note;
      });
      if (m.isWorking) unawaited(_load());
    } on ApiException catch (e) {
      if (mounted) {
        setState(
          () => _actionError =
              e.isPermissionDenied ? l.t('meetings.editDenied') : e.message,
        );
      }
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  String? _mailboxId;
  List<EmailAccountChoice> get _mailboxes =>
      widget.setup?.emailAccounts ?? const [];
  EmailAccountChoice? get _mailbox =>
      _mailboxes.where((m) => m.id == _mailboxId).firstOrNull ??
      _mailboxes.firstOrNull;

  bool get _canEdit {
    final m = _meeting, s = widget.setup;
    if (m == null || s == null) return false;
    return s.canManageAll ||
        (s.canCreate && m.createdBy == widget.currentUserId);
  }

  Future<void> _delete() async {
    final l = AnumLocalizations.maybeOf(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('meetings.deleteTitle')),
        content: Text(l.t('meetings.deleteText')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l.t('cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l.t('meetings.delete')),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await widget.repository.delete(widget.meetingId);
      widget.onDeleted?.call(widget.meetingId);
      if (mounted) Navigator.pop(context);
    } on ApiException catch (e) {
      if (mounted) setState(() => _actionError = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final m = _meeting;
    final textDir =
        m?.isArabic ?? false ? TextDirection.rtl : TextDirection.ltr;
    Widget section(String title, List<Widget> children, {Widget? badge}) =>
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: AnumSpacing.xs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Semantics(
                    header: true,
                    child: Text(
                      title,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  if (badge != null) badge,
                ],
              ),
              const SizedBox(height: AnumSpacing.xs),
              ...children,
            ],
          ),
        );
    final body = <Widget>[];
    if (m == null) {
      body.add(
        _error == null
            ? Center(
                child: Semantics(
                  label: l.t('meetings.loading'),
                  child: const CircularProgressIndicator(),
                ),
              )
            : AnumSurface(
                child: AnumEmpty(
                  icon: _denied ? Icons.lock_outline : Icons.error_outline,
                  title: _missing
                      ? l.t('meetings.missing')
                      : _denied
                          ? l.t('meetings.deniedTitle')
                          : l.t('meetings.loadFailed'),
                  message: _denied ? l.t('meetings.denied') : _error!,
                  action: _missing || _denied ? null : l.t('retry'),
                  onAction: _missing || _denied ? null : _load,
                ),
              ),
      );
    } else {
      body.addAll([
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            AnumPill(label: l.t(_statusKey(m.status)), tone: _tone(m.status)),
            AnumPill(label: l.t('meetings.source.${m.source}')),
            AnumPill(label: _date(context, m.occurredAt ?? m.createdAt)),
            if (m.agentSkillId != null)
              AnumPill(label: m.agentSkillId!, icon: Icons.smart_toy_outlined),
          ],
        ),
        if (m.isWorking)
          _Note(
            icon: Icons.hourglass_top,
            text: l.t('meetings.working.${m.status}'),
          ),
        if (m.error != null)
          _Note(
            icon: Icons.error_outline,
            text: m.error!,
            error: m.status == 'failed',
          ),
        if (_actionError != null)
          _Note(icon: Icons.error_outline, text: _actionError!, error: true),
        if (_actionNote != null)
          _Note(icon: Icons.check_circle_outline, text: _actionNote!),
        if (_canEdit)
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              if (!m.isWorking)
                FilledButton.icon(
                  onPressed: _busy == null
                      ? () => _act(
                            'process',
                            () => widget.repository.reprocess(m.id),
                            l.t('meetings.processing'),
                          )
                      : null,
                  icon: const Icon(Icons.refresh),
                  label: Text(
                    l.t(
                      m.status == 'failed'
                          ? 'meetings.retry'
                          : m.summary == null
                              ? 'meetings.summarize'
                              : 'meetings.summarizeAgain',
                    ),
                  ),
                ),
              OutlinedButton.icon(
                onPressed: _busy == null ? _delete : null,
                icon: const Icon(Icons.delete_outline),
                label: Text(l.t('meetings.delete')),
              ),
            ],
          )
        else if (widget.setup != null)
          _Note(icon: Icons.lock_outline, text: l.t('meetings.editDenied')),
        if (m.summary != null)
          section(
            l.t('meetings.summary'),
            [
              Text(m.summary!, textDirection: textDir),
              if (m.truncated)
                Text(
                  l.t('meetings.truncated'),
                  style: TextStyle(color: context.palette.muted),
                ),
              Text(
                l.t('meetings.checkNotes'),
                style: TextStyle(color: context.palette.muted),
              ),
            ],
            badge: m.model == null
                ? null
                : AnumPill(
                    label: l.f('meetings.aiLabel', {'model': m.model!}),
                    icon: Icons.auto_awesome,
                    tone: AnumTone.accent,
                  ),
          ),
        if (m.summary != null)
          section(l.t('meetings.decisions'), [
            if (m.decisions.isEmpty) Text(l.t('meetings.noDecisions')),
            for (final d in m.decisions) Text('• $d', textDirection: textDir),
          ]),
        if (m.summary != null || m.actionItems.isNotEmpty)
          section(l.t('meetings.actionItems'), [
            if (m.actionItems.isEmpty) Text(l.t('meetings.noActions')),
            for (final item in m.actionItems)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Checkbox(
                      value: item.done,
                      semanticLabel: l.f('meetings.doneOf', {
                        'text': item.text,
                      }),
                      onChanged: _canEdit && _busy == null
                          ? (v) => _act(
                                'done',
                                () => widget.repository.setDone(
                                  m.id,
                                  item.id,
                                  v ?? false,
                                ),
                              )
                          : null,
                    ),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            item.text,
                            textDirection: textDir,
                            style: item.done
                                ? const TextStyle(
                                    decoration: TextDecoration.lineThrough,
                                  )
                                : null,
                          ),
                          Text(
                            '${item.owner ?? l.t('meetings.noOwner')} · ${item.dueDate ?? l.t('meetings.noDate')}',
                            style: TextStyle(color: context.palette.muted),
                          ),
                          if (item.taskId != null)
                            AnumPill(
                              label: l.t('meetings.taskCreated'),
                              tone: AnumTone.ok,
                            )
                          else if (_canEdit &&
                              (widget.setup?.canCreate ?? false))
                            TextButton(
                              onPressed: _busy == null
                                  ? () => _act(
                                        'task',
                                        () => widget.repository.createTask(
                                          m.id,
                                          item.id,
                                        ),
                                        l.t('meetings.taskDone'),
                                      )
                                  : null,
                              child: Text(l.t('meetings.createTask')),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            if (_canEdit && m.actionItems.isNotEmpty)
              Text(
                l.t('meetings.approvalNote'),
                style: TextStyle(color: context.palette.muted),
              ),
          ]),
        if (m.followUp != null)
          section(l.t('meetings.followUp'), [
            TextField(
              controller: _to,
              enabled: _canEdit,
              keyboardType: TextInputType.emailAddress,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                labelText: l.t('meetings.to'),
                helperText: l.t('meetings.toHint'),
              ),
            ),
            TextField(
              controller: _subject,
              enabled: _canEdit,
              decoration: InputDecoration(labelText: l.t('meetings.subject')),
            ),
            TextField(
              controller: _body,
              enabled: _canEdit,
              minLines: 4,
              maxLines: 12,
              textDirection: textDir,
              decoration: InputDecoration(labelText: l.t('meetings.body')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                if (_canEdit)
                  FilledButton(
                    onPressed: _busy == null
                        ? () => _act(
                              'save',
                              () => widget.repository.saveFollowUp(
                                m.id,
                                [
                                  for (final part in _to.text.split(
                                    RegExp('[,;\n]'),
                                  ))
                                    if (part.trim().isNotEmpty) part.trim(),
                                ],
                                _subject.text.trim(),
                                _body.text.trim(),
                              ),
                              l.t('meetings.saved'),
                            )
                        : null,
                    child: Text(l.t('meetings.saveDraft')),
                  ),
                OutlinedButton.icon(
                  onPressed: () async {
                    await Clipboard.setData(
                      ClipboardData(
                        text:
                            '${l.t('meetings.subject')}: ${_subject.text}\n\n${_body.text}',
                      ),
                    );
                    if (mounted) {
                      setState(() => _actionNote = l.t('meetings.copied'));
                    }
                  },
                  icon: const Icon(Icons.copy),
                  label: Text(l.t('meetings.copy')),
                ),
                if (_canEdit && m.followUp!.approvalId == null)
                  OutlinedButton.icon(
                    onPressed: _mailbox != null && _busy == null
                        ? () => _act(
                              'send',
                              () => widget.repository
                                  .sendFollowUp(m.id, _mailbox!.id),
                              l.t('meetings.sent'),
                            )
                        : null,
                    icon: const Icon(Icons.send),
                    label: Text(l.t('meetings.send')),
                  ),
              ],
            ),
            if (_canEdit &&
                m.followUp!.approvalId == null &&
                _mailboxes.length > 1)
              DropdownButton<String>(
                isExpanded: true,
                value: _mailbox?.id,
                onChanged: (id) => setState(() => _mailboxId = id),
                items: [
                  for (final box in _mailboxes)
                    DropdownMenuItem(value: box.id, child: Text(box.address)),
                ],
              ),
            Text(
              m.followUp!.approvalId != null
                  ? l.f('meetings.handedOff',
                      {'mailbox': m.followUp!.sentFrom ?? ''})
                  : _mailbox != null
                      ? l.f('meetings.sendNote', {'mailbox': _mailbox!.address})
                      : l.t('meetings.noEmailTool'),
              style: TextStyle(color: context.palette.muted),
            ),
          ]),
        if (m.segmentCount > 0)
          section(l.f('meetings.transcript', {'count': m.segmentCount}), [
            TextField(
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                labelText: l.t('meetings.searchTranscript'),
                prefixIcon: const Icon(Icons.search),
              ),
              onSubmitted: (v) {
                _transcriptQuery = v.trim();
                unawaited(_loadTranscript());
              },
            ),
            const SizedBox(height: AnumSpacing.xs),
            if (_transcript == null)
              const LinearProgressIndicator()
            else if (_transcript!.lines.isEmpty)
              Text(l.t('meetings.noLines'))
            else
              Directionality(
                textDirection: textDir,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final line in _transcript!.lines)
                      Padding(
                        padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              [
                                transcriptClock(line.startMs),
                                if (line.speaker != null) line.speaker!,
                              ].where((x) => x.isNotEmpty).join(' · '),
                              style: TextStyle(
                                color: context.palette.sky,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            Text(line.text),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            if (_transcript != null &&
                _transcript!.total > _transcript!.lines.length)
              Text(
                l.f('meetings.moreLines', {
                  'shown': _transcript!.lines.length,
                  'total': _transcript!.total,
                }),
                style: TextStyle(color: context.palette.muted),
              ),
            if (_transcript != null &&
                _transcript!.total > _transcript!.lines.length)
              TextButton(
                onPressed: _loadMoreLines,
                child: Text(l.t('meetings.loadMoreLines')),
              ),
          ]),
      ]);
    }
    return Scaffold(
      appBar: AppBar(title: Text(m?.title ?? l.t('meetings.meeting'))),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView.separated(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          itemCount: body.length,
          separatorBuilder: (_, __) => const SizedBox(height: AnumSpacing.sm),
          itemBuilder: (_, i) => body[i],
        ),
      ),
    );
  }
}
