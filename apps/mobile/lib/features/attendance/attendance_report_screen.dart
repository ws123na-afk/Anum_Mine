import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'attendance_data.dart';
import 'attendance_strings.dart';

/// One daily report on the phone, as on the web (docs/attendance.md#daily-reports):
/// project, done and pending tasks, site visits, issues, time, priority,
/// follow-up and notes; files from the workspace's Files; an AI draft of the
/// day or the week from the person's own entries; Save as a draft, or
/// Review and submit (a submitted report stays submitted after later
/// edits). Every save sends the version that was opened; a change made
/// elsewhere meanwhile is refused and said so. Pops `true` once saved.
class AttendanceReportScreen extends StatefulWidget {
  const AttendanceReportScreen({
    required this.repository,
    required this.day,
    this.existing,
    this.sites = const [],
    this.files,
    this.save,
    this.remove,
    super.key,
  });
  final AttendanceRepository repository;

  /// Saves and deletes through the caller (the list follows); the
  /// repository's own calls when null.
  final Future<AttendanceReport> Function(
      String day, Map<String, Object?> body)? save;
  final Future<void> Function(String day)? remove;
  final String day;
  final AttendanceReport? existing;
  final List<AttendanceSite> sites;
  final AttendanceFiles? files;

  @override
  State<AttendanceReportScreen> createState() => _AttendanceReportScreenState();
}

class _AttendanceReportScreenState extends State<AttendanceReportScreen> {
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  AttendanceReport? report;
  final text = <String, TextEditingController>{
    for (final name in [
      'project', 'tasks_done', 'tasks_pending', 'site_visits', 'issues', //
      'time_spent_minutes', 'follow_up_on', 'notes',
    ])
      name: TextEditingController(),
  };
  String priority = 'normal';
  List<String> fileIds = [];
  bool ai = false;

  /// The action in progress: save, submit, upload, ai-day, ai-week.
  String? busy;
  String? error;
  String? aiText;

  @override
  void initState() {
    super.initState();
    if (widget.existing != null) {
      _fill(widget.existing);
    } else {
      _load();
    }
  }

  @override
  void dispose() {
    for (final c in text.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      phase = RecordsPhase.loading;
      failure = null;
    });
    try {
      final found = await widget.repository.report(widget.day);
      if (mounted) _fill(found);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        failure = RecordsFailure.from(e);
        phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  void _fill(AttendanceReport? found) {
    report = found;
    text['project']!.text = found?.project ?? '';
    text['tasks_done']!.text = found?.tasksDone.join('\n') ?? '';
    text['tasks_pending']!.text = found?.tasksPending.join('\n') ?? '';
    text['site_visits']!.text = [
      for (final visit in found?.siteVisits ?? const <Map<String, Object?>>[])
        if (visit['name'] is String) visit['name']! as String
    ].join('\n');
    text['issues']!.text = found?.issues ?? '';
    text['time_spent_minutes']!.text =
        found?.timeSpentMinutes?.toString() ?? '';
    text['follow_up_on']!.text = found?.followUpOn ?? '';
    text['notes']!.text = found?.notes ?? '';
    priority = found?.priority ?? 'normal';
    fileIds = [...?found?.fileIds];
    ai = found?.draftedWithAi ?? false;
    setState(() => phase = RecordsPhase.ready);
  }

  List<String> _lines(String name) => [
        for (final line in text[name]!.text.split('\n'))
          if (line.trim().isNotEmpty) line.trim()
      ];

  /// The body to send, or the message key of what is wrong.
  (Map<String, Object?>?, String?) _body(bool submit) {
    final minutesText = text['time_spent_minutes']!.text.trim();
    final minutes = minutesText.isEmpty
        ? null
        : int.tryParse(decimalText(minutesText) ?? '');
    if (minutesText.isNotEmpty &&
        (minutes == null || minutes < 0 || minutes > 1440)) {
      return (null, 'reportMinutes');
    }
    final followUp = text['follow_up_on']!.text.trim();
    if (followUp.isNotEmpty && parseDay(followUp) == null) {
      return (null, 'reportFollowUp');
    }
    final done = _lines('tasks_done'), pending = _lines('tasks_pending');
    final notes = text['notes']!.text.trim(),
        issues = text['issues']!.text.trim();
    if (done.isEmpty && pending.isEmpty && notes.isEmpty && issues.isEmpty) {
      return (null, 'reportEmpty');
    }
    // A visit keeps its site and note when its name did not change.
    final before = {
      for (final visit in report?.siteVisits ?? const <Map<String, Object?>>[])
        if (visit['name'] is String)
          (visit['name']! as String).toLowerCase(): visit
    };
    return (
      {
        'project': text['project']!.text.trim().isEmpty
            ? null
            : text['project']!.text.trim(),
        'tasks_done': done,
        'tasks_pending': pending,
        'site_visits': [
          for (final name in _lines('site_visits'))
            before[name.toLowerCase()] ??
                {
                  'name': name,
                  'site_id': widget.sites
                      .where((s) => s.name.toLowerCase() == name.toLowerCase())
                      .firstOrNull
                      ?.id,
                  'note': null,
                }
        ],
        'issues': issues.isEmpty ? null : issues,
        'time_spent_minutes': minutes,
        'priority': priority,
        'follow_up_on': followUp.isEmpty ? null : followUp,
        'notes': notes.isEmpty ? null : notes,
        'file_ids': fileIds,
        'drafted_with_ai': ai,
        'expected_version': report?.version,
        'submit': submit,
      },
      null
    );
  }

  Future<void> _save({required bool submit}) async {
    final s = AttendanceStrings.of(context);
    final (body, problem) = _body(submit);
    if (problem != null) {
      setState(() => error = s.t(problem));
      return;
    }
    if (submit && !await _review(body!)) return;
    setState(() {
      busy = submit ? 'submit' : 'save';
      error = null;
    });
    try {
      await (widget.save ?? widget.repository.saveReport)(widget.day, body!);
      if (!mounted) return;
      recordsToast(context, s.t(submit ? 'reportSubmitted' : 'reportSaved'));
      Navigator.of(context).pop(true);
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          error = RecordsFailure.from(e).message;
          busy = null;
        });
      }
    }
  }

  /// What goes to the manager: done, blockers and next, before submitting.
  Future<bool> _review(Map<String, Object?> body) async {
    final s = AttendanceStrings.of(context);
    String joined(Object? value) =>
        value is List && value.isNotEmpty ? value.join('; ') : '-';
    return confirmRecords(context,
        title: s.t('reviewTitle', {'day': recordsDate(context, widget.day)}),
        message: [
          '${s.t('reviewDone')}: ${joined(body['tasks_done'])}',
          '${s.t('reviewBlocker')}: ${(body['issues'] as String?) ?? '-'}',
          '${s.t('reviewNext')}: ${joined(body['tasks_pending'])}',
          '',
          s.t('reviewNote'),
        ].join('\n'),
        action: s.t('submit'));
  }

  Future<void> _draft(String period) async {
    setState(() {
      busy = 'ai-$period';
      error = null;
      aiText = null;
    });
    try {
      final language =
          AnumLocalizations.maybeOf(context).isArabic ? 'ar' : 'en';
      final drafted =
          await widget.repository.draftSummary(period, widget.day, language);
      if (mounted) setState(() => aiText = drafted);
    } on Object catch (e) {
      if (mounted) setState(() => error = RecordsFailure.from(e).message);
    }
    if (mounted) setState(() => busy = null);
  }

  Future<void> _attach() async {
    final s = AttendanceStrings.of(context);
    setState(() {
      busy = 'upload';
      error = null;
    });
    try {
      final uploaded = await widget.files!.pickAndUpload();
      if (uploaded != null && mounted) {
        setState(() => fileIds = [...fileIds, uploaded.id].take(20).toList());
        recordsToast(context, s.t('fileAttached', {'name': uploaded.name}));
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => error =
            s.t('uploadFailed', {'reason': RecordsFailure.from(e).message}));
      }
    }
    if (mounted) setState(() => busy = null);
  }

  Future<void> _delete() async {
    final s = AttendanceStrings.of(context);
    final sure = await confirmRecords(context,
        title: s.t('deleteReport'),
        message:
            s.t('deleteConfirm', {'day': recordsDate(context, widget.day)}),
        action: KitStrings.of(context).t('delete'));
    if (!sure || !mounted) return;
    try {
      await (widget.remove ?? widget.repository.deleteReport)(widget.day);
      if (!mounted) return;
      recordsToast(context, s.t('reportDeleted'));
      Navigator.of(context).pop(true);
    } on Object catch (e) {
      if (mounted) setState(() => error = RecordsFailure.from(e).message);
    }
  }

  Widget _field(String name, String label,
      {bool multi = false,
      TextInputType? keyboard,
      String? help,
      int? maxLength,
      TextDirection? direction}) {
    return TextFormField(
      key: Key('field-$name'),
      controller: text[name],
      minLines: multi ? 2 : 1,
      maxLines: multi ? 6 : 1,
      maxLength: maxLength,
      keyboardType: keyboard ?? (multi ? TextInputType.multiline : null),
      textDirection: direction,
      enabled: busy == null,
      decoration: InputDecoration(labelText: label, helperText: help),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = AttendanceStrings.of(context);
    final k = KitStrings.of(context);
    final p = context.palette;
    final submitted = report?.submittedAt != null;
    return Scaffold(
      // Save and submit stay in reach at the bottom of a long form.
      bottomNavigationBar: phase != RecordsPhase.ready
          ? null
          : SafeArea(
              child: Material(
                color: p.surface1,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(AnumSpacing.md,
                      AnumSpacing.xs, AnumSpacing.md, AnumSpacing.xs),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (error != null)
                        Semantics(
                          liveRegion: true,
                          child: Text(error!,
                              key: const Key('report-error'),
                              style: TextStyle(color: p.stop)),
                        ),
                      Wrap(
                          spacing: AnumSpacing.xs,
                          runSpacing: AnumSpacing.xs,
                          children: [
                            (submitted ? FilledButton.new : OutlinedButton.new)(
                              key: const Key('form-submit'),
                              onPressed: busy == null
                                  ? () => _save(submit: false)
                                  : null,
                              child: Text(busy == 'save'
                                  ? k.t('saving')
                                  : s.t(
                                      submitted ? 'saveChanges' : 'saveDraft')),
                            ),
                            if (!submitted)
                              FilledButton(
                                key: const Key('report-submit'),
                                onPressed: busy == null
                                    ? () => _save(submit: true)
                                    : null,
                                child: Text(busy == 'submit'
                                    ? k.t('saving')
                                    : s.t('reviewSubmit')),
                              ),
                            if (report != null)
                              TextButton.icon(
                                key: const Key('report-delete'),
                                onPressed: busy == null ? _delete : null,
                                icon: const Icon(Icons.delete_outline),
                                label: Text(s.t('deleteReport')),
                              ),
                          ]),
                    ],
                  ),
                ),
              ),
            ),
      appBar: AppBar(
          title: Text(
              s.t('reportFor', {'day': recordsDate(context, widget.day)}))),
      body: phase != RecordsPhase.ready
          ? AnumPage(children: [
              RecordsStateView(
                  phase: phase,
                  failure: failure,
                  deniedHint: s.t('denied'),
                  onRetry: _load),
            ])
          : AnumPage(children: [
              if (report != null)
                Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      AnumPill(
                          key: const Key('report-state'),
                          label: s.t(submitted ? 'submitted' : 'draft'),
                          tone: submitted ? AnumTone.ok : AnumTone.info),
                      Text(
                          submitted
                              ? s.t('submittedOn', {
                                  'when': recordsDateTime(
                                      context, report!.submittedAt)
                                })
                              : s.t('draftNote'),
                          style:
                              TextStyle(color: p.muted, fontSize: AnumType.sm)),
                    ]),
              AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _field('project', s.t('project'), maxLength: 160),
                    _field('tasks_done', s.t('tasksDone'), multi: true),
                    _field('tasks_pending', s.t('tasksPending'), multi: true),
                    _field('site_visits', s.t('siteVisits'),
                        multi: true, help: s.t('siteVisitsHelp')),
                    _field('issues', s.t('issues'),
                        multi: true, maxLength: 4000),
                    _field('time_spent_minutes', s.t('timeSpent'),
                        keyboard: TextInputType.number,
                        direction: TextDirection.ltr),
                    DropdownButtonFormField<String>(
                      key: const Key('field-priority'),
                      initialValue: priority,
                      isExpanded: true,
                      decoration: InputDecoration(labelText: s.t('priority')),
                      items: [
                        for (final value in ['low', 'normal', 'high', 'urgent'])
                          DropdownMenuItem(
                              value: value,
                              child: Text(s.label('priority', value))),
                      ],
                      onChanged: busy == null
                          ? (v) => setState(() => priority = v ?? 'normal')
                          : null,
                    ),
                    _field('follow_up_on', s.t('followUp'),
                        keyboard: TextInputType.datetime,
                        help: 'YYYY-MM-DD',
                        direction: TextDirection.ltr),
                    _field('notes', s.t('notes'), multi: true, maxLength: 8000),
                  ],
                ),
              ),
              RecordsSection(
                title: s.t('files'),
                trailing: widget.files == null
                    ? null
                    : IconButton(
                        key: const Key('report-attach'),
                        tooltip: s.t('attachFile'),
                        onPressed: busy == null ? _attach : null,
                        icon: const Icon(Icons.attach_file)),
                children: [
                  if (fileIds.isEmpty)
                    Text(s.t(widget.files == null ? 'filesNone' : 'filesEmpty'),
                        style: TextStyle(color: p.muted))
                  else
                    Wrap(
                        spacing: AnumSpacing.xs,
                        runSpacing: AnumSpacing.xs,
                        children: [
                          for (final id in fileIds)
                            InputChip(
                              key: Key('report-file-$id'),
                              label: Text(ltrValue(context, id)),
                              deleteButtonTooltipMessage:
                                  s.t('removeFile', {'id': id}),
                              onDeleted: busy == null
                                  ? () => setState(() => fileIds =
                                      fileIds.where((f) => f != id).toList())
                                  : null,
                            ),
                        ]),
                  if (busy == 'upload')
                    Text(s.t('uploading'), style: TextStyle(color: p.muted)),
                ],
              ),
              RecordsSection(
                title: s.t('aiTitle'),
                children: [
                  Text(s.t('aiHelp'), style: TextStyle(color: p.muted)),
                  const SizedBox(height: AnumSpacing.xs),
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        for (final period in const ['day', 'week'])
                          OutlinedButton.icon(
                            key: Key('report-ai-$period'),
                            onPressed:
                                busy == null ? () => _draft(period) : null,
                            icon: const Icon(Icons.auto_awesome_outlined,
                                size: 18),
                            label: Text(busy == 'ai-$period'
                                ? s.t('drafting')
                                : s.t(period == 'day' ? 'aiDay' : 'aiWeek')),
                          ),
                      ]),
                  if (aiText != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    AnumSurface(
                      key: const Key('report-ai-text'),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Text(aiText!),
                            Wrap(spacing: AnumSpacing.xs, children: [
                              TextButton(
                                key: const Key('report-ai-use'),
                                onPressed: () => setState(() {
                                  final notes = text['notes']!.text.trim();
                                  text['notes']!.text = notes.isEmpty
                                      ? aiText!
                                      : '$notes\n\n$aiText';
                                  ai = true;
                                  aiText = null;
                                }),
                                child: Text(s.t('aiUse')),
                              ),
                              TextButton(
                                onPressed: () => setState(() => aiText = null),
                                child: Text(s.t('aiDiscard')),
                              ),
                            ]),
                          ]),
                    ),
                  ],
                ],
              ),
            ]),
    );
  }
}
