import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'attendance_controller.dart';
import 'attendance_data.dart';
import 'attendance_report_screen.dart';
import 'attendance_strings.dart';

const eventKinds = [
  'arrive', 'leave', 'break_start', 'break_end', 'remote_start', //
  'remote_end',
];

String _time(BuildContext context, String iso) {
  final parsed = DateTime.tryParse(iso);
  if (parsed == null) return iso;
  return MaterialLocalizations.of(context)
      .formatTimeOfDay(TimeOfDay.fromDateTime(parsed.toLocal()));
}

String _hhmm(DateTime at) =>
    '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';

/// Attendance on the phone (docs/attendance.md#mobile): today's status and
/// manual check in and out, the days by week, daily reports and
/// corrections with a reason. Everything here is the person's own record.
class AttendanceScreen extends StatefulWidget {
  const AttendanceScreen({required this.controller, this.files, super.key});
  final AttendanceController controller;

  /// Attaching files to a daily report (the workspace's Files); null hides it.
  final AttendanceFiles? files;

  @override
  State<AttendanceScreen> createState() => _AttendanceScreenState();
}

class _AttendanceScreenState extends State<AttendanceScreen> {
  AttendanceController get c => widget.controller;
  String? _site;

  @override
  void initState() {
    super.initState();
    if (c.phase == RecordsPhase.loading) c.load();
  }

  Future<void> _check(String kind) async {
    final s = AttendanceStrings.of(context);
    try {
      await c.check(kind,
          siteId: kind == 'arrive' || kind == 'leave' ? _site : null);
      if (mounted) {
        recordsToast(context, s.t('recorded', {'what': s.label('kind', kind)}));
      }
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _correct({String action = 'add', AttendanceEvent? event}) async {
    final s = AttendanceStrings.of(context);
    final at = event == null
        ? DateTime.now()
        : DateTime.tryParse(event.occurredAt)?.toLocal() ?? DateTime.now();
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t(switch (action) {
          'change' => 'changeTime',
          'void' => 'remove',
          _ => 'addMissing',
        }),
        intro: s.t('correctionsHelp'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          if (action != 'void') ...[
            RecordsInput.choice('kind', s.t('what'),
                initial: event?.kind ?? 'arrive',
                options: {for (final k in eventKinds) k: s.label('kind', k)}),
            RecordsInput.date('day', s.t('day'),
                required: true, initial: isoDay(at)),
            RecordsInput.time('time', s.t('time'),
                required: true, initial: _hhmm(at)),
            if (c.sites.isNotEmpty)
              RecordsInput.choice('site_id', s.t('site'),
                  initial: event?.siteId != null &&
                          c.sites.any((x) => x.id == event!.siteId)
                      ? event!.siteId
                      : _noSite,
                  options: {
                    _noSite: s.t('noSite'),
                    for (final site in c.sites) site.id: site.name,
                  }),
          ],
          RecordsInput.text('reason', s.t('reason'),
              required: true, maxLength: 500),
        ],
        onSubmit: (v) => c.correct({
          'action': action,
          if (event != null) 'event_id': event.id,
          if (action != 'void') ...{
            'kind': v['kind'],
            'occurred_at': instantOf(v['day']! as String, v['time'] as String?),
            'site_id': c.sites.isEmpty
                ? event?.siteId
                : (v['site_id'] == _noSite ? null : v['site_id']),
          },
          'reason': v['reason'],
        }),
      ),
    );
    if (saved && mounted) recordsToast(context, s.t('correctionSaved'));
  }

  Future<void> _report(String day, {AttendanceReport? existing}) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => AttendanceReportScreen(
              repository: c.repository,
              day: day,
              existing: existing,
              sites: c.sites,
              files: widget.files,
              save: c.saveReport,
              remove: c.deleteReport,
            )));
    if (saved == true) await c.reports.load(query: c.reports.query);
  }

  static const _noSite = '-';

  /// Picks one of the person's entries of the last 14 days to change or
  /// remove (the web's list), then opens the correction for it.
  Future<void> _pickEntry(String action) async {
    final s = AttendanceStrings.of(context);
    final today = c.today?.day ?? isoDay(DateTime.now());
    final end = parseDay(today) ?? DateTime.now();
    final picked = await showRecordsSheet<AttendanceEvent>(
        context,
        (_) => _EntryPicker(
              title: s.t(action == 'void' ? 'pickRemove' : 'pickChange'),
              load: () => c.repository.events(
                  isoDay(end.subtract(const Duration(days: 13))), today),
              siteName: {for (final site in c.sites) site.id: site.name},
            ));
    if (picked != null && mounted) {
      await _correct(action: action, event: picked);
    }
  }

  Future<void> _deleteReport(AttendanceReport report) async {
    final s = AttendanceStrings.of(context);
    final k = KitStrings.of(context);
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        content: Text(
            s.t('deleteConfirm', {'day': recordsDate(context, report.day)})),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(k.t('cancel'))),
          FilledButton(
              key: const Key('confirm-delete'),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(k.t('delete'))),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    try {
      await c.deleteReport(report.day);
      if (mounted) recordsToast(context, s.t('reportDeleted'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = AttendanceStrings.of(context);
    return ListenableBuilder(
      listenable: Listenable.merge([c, c.reports, c.corrections]),
      builder: (context, _) {
        if (c.phase != RecordsPhase.ready || c.today == null) {
          return Scaffold(
            appBar: AppBar(title: Text(s.t('title'))),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(title: s.t('title'), subtitle: s.t('subtitle')),
              RecordsStateView(
                  phase: c.phase,
                  failure: c.failure,
                  deniedHint: s.t('denied'),
                  onRetry: c.load),
            ]),
          );
        }
        return DefaultTabController(
          length: 4,
          child: Scaffold(
            appBar: AppBar(
              title: Text(s.t('title')),
              bottom: TabBar(
                isScrollable: true,
                tabAlignment: TabAlignment.start,
                tabs: [
                  for (final tab in [
                    'today',
                    'history',
                    'reports',
                    'corrections'
                  ])
                    Tab(key: Key('tab-$tab'), text: s.t('tab.$tab')),
                ],
              ),
            ),
            body: TabBarView(children: [
              _today(context),
              _history(context),
              _reports(context),
              _corrections(context),
            ]),
          ),
        );
      },
    );
  }

  Widget _today(BuildContext context) {
    final s = AttendanceStrings.of(context);
    final today = c.today!;
    final siteName = {for (final site in c.sites) site.id: site.name};
    final status = today.onBreak
        ? 'break'
        : today.atSite
            ? 'site'
            : today.remote
                ? 'remote'
                : 'off';
    final buttons = <String>[
      if (!today.working) ...['arrive', 'remote_start'],
      if (today.atSite && !today.onBreak) 'leave',
      if (today.remote && !today.onBreak) 'remote_end',
      if (today.working && !today.onBreak) 'break_start',
      if (today.onBreak) 'break_end',
    ];
    final label = <String, String>{
      'arrive': s.t('checkIn'),
      'leave': s.t('checkOut'),
      'break_start': s.t('breakStart'),
      'break_end': s.t('breakEnd'),
      'remote_start': s.t('remoteStart'),
      'remote_end': s.t('remoteEnd'),
    };
    final totals = today.totals;
    return AnumPage(onRefresh: c.load, children: [
      RecordsSection(
        title: s.t('status'),
        children: [
          Semantics(
            liveRegion: true,
            child: Text(
              [
                s.t('status.$status'),
                if (today.siteId != null && siteName[today.siteId] != null)
                  s.t('atSite', {'site': siteName[today.siteId]!}),
                if (today.since != null)
                  s.t('since', {'time': _time(context, today.since!)}),
              ].join(' '),
              key: const Key('attendance-status'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
          if (today.trackingPaused) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(s.t('paused'), style: TextStyle(color: context.palette.warn)),
          ],
          if (c.sites.isNotEmpty && !today.atSite) ...[
            const SizedBox(height: AnumSpacing.sm),
            DropdownButtonFormField<String?>(
              key: const Key('attendance-site'),
              initialValue: _site,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('site')),
              items: [
                DropdownMenuItem(value: null, child: Text(s.t('noSite'))),
                for (final site in c.sites)
                  DropdownMenuItem(value: site.id, child: Text(site.name)),
              ],
              onChanged: (value) => setState(() => _site = value),
            ),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              for (final kind in buttons)
                (kind == buttons.first ? FilledButton.new : OutlinedButton.new)(
                  key: Key('check-$kind'),
                  onPressed: c.checking == null ? () => _check(kind) : null,
                  child: Text(c.checking == kind
                      ? KitStrings.of(context).t('saving')
                      : label[kind]!),
                ),
            ],
          ),
        ],
      ),
      if (totals != null)
        AnumMetricGrid(children: [
          AnumMetric(
              label: s.t('worked'),
              value: s.hours(totals.workedSeconds),
              icon: Icons.timer_outlined),
          AnumMetric(
              label: s.t('presence'),
              value: s.hours(totals.presenceSeconds),
              icon: Icons.place_outlined),
          AnumMetric(
              label: s.t('remote'),
              value: s.hours(totals.remoteSeconds),
              icon: Icons.home_work_outlined),
          AnumMetric(
              label: s.t('breaks'),
              value: s.hours(totals.breakSeconds),
              icon: Icons.free_breakfast_outlined),
        ]),
      RecordsSection(
        title: s.t('events'),
        children: [
          if (today.events.isEmpty)
            Text(s.t('eventsEmpty'),
                style: TextStyle(color: context.palette.muted))
          else
            for (final event in today.events.reversed)
              AnumRow(
                key: Key('event-${event.id}'),
                icon: Icons.schedule,
                tone: event.quality == 'poor' ? AnumTone.warn : null,
                title: s.label('kind', event.kind),
                subtitle: [
                  recordsDateTime(context, event.occurredAt),
                  if (event.siteId != null && siteName[event.siteId] != null)
                    siteName[event.siteId]!,
                  s.label('source', event.source),
                ].join(' · '),
                detail: event.quality == 'poor' ? s.t('poor') : null,
                trailing: PopupMenuButton<String>(
                  key: Key('event-menu-${event.id}'),
                  tooltip: s.t('changeTime'),
                  onSelected: (action) =>
                      _correct(action: action, event: event),
                  itemBuilder: (_) => [
                    PopupMenuItem(
                        key: const Key('menu-change'),
                        value: 'change',
                        child: Text(s.t('changeTime'))),
                    PopupMenuItem(
                        key: const Key('menu-void'),
                        value: 'void',
                        child: Text(s.t('remove'))),
                  ],
                ),
              ),
          const SizedBox(height: AnumSpacing.xs),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
              key: const Key('attendance-write-today'),
              onPressed: () => _report(today.day),
              icon: const Icon(Icons.edit_note),
              label: Text(s.t('writeReport')),
            ),
          ),
        ],
      ),
    ]);
  }

  Widget _history(BuildContext context) {
    final s = AttendanceStrings.of(context);
    return AnumPage(onRefresh: c.loadWeek, children: [
      RecordsSection(
        title: s.t('weekOf', {
          'from': recordsDate(context, isoDay(c.weekStart)),
          'to': recordsDate(context, isoDay(c.weekEnd)),
        }),
        children: [
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            OutlinedButton.icon(
              key: const Key('week-earlier'),
              onPressed: () => c.shiftWeek(-1),
              icon: const Icon(Icons.chevron_left),
              label: Text(s.t('earlier')),
            ),
            if (!c.atLatestWeek)
              OutlinedButton.icon(
                key: const Key('week-later'),
                onPressed: () => c.shiftWeek(1),
                icon: const Icon(Icons.chevron_right),
                label: Text(s.t('later')),
              ),
          ]),
        ],
      ),
      if (c.weekPhase != RecordsPhase.ready)
        RecordsStateView(
            phase: c.weekPhase, failure: c.weekFailure, onRetry: c.loadWeek)
      else if (c.week.every((d) =>
          d.workedSeconds == 0 && d.firstArrival == null && d.flags.isEmpty))
        AnumEmpty(
          title: s.t('historyEmpty'),
          message: s.t('correctionsEmptyText'),
          icon: Icons.event_busy_outlined,
          action: s.t('addMissing'),
          onAction: _correct,
        )
      else
        AnumSurface(
          child: Column(children: [
            for (final day in c.week)
              AnumRow(
                key: Key('day-${day.day}'),
                icon: Icons.today_outlined,
                tone: day.flags.isNotEmpty ? AnumTone.warn : null,
                title: recordsDate(context, day.day),
                subtitle: [
                  s.t('dayLine', {'worked': s.hours(day.workedSeconds)}),
                  if (day.firstArrival != null)
                    s.t('firstLast', {
                      'first': _time(context, day.firstArrival!),
                      'last': day.lastDeparture == null
                          ? '-'
                          : _time(context, day.lastDeparture!),
                    }),
                ].join(' · '),
                detail: day.flags.isEmpty
                    ? null
                    : day.flags.map((f) => s.label('flag', f)).join(' · '),
                trailing: const Icon(Icons.edit_note),
                onTap: () => _report(day.day),
              ),
          ]),
        ),
    ]);
  }

  Widget _reports(BuildContext context) {
    final s = AttendanceStrings.of(context);
    final k = KitStrings.of(context);
    final list = c.reports;
    return AnumPage(onRefresh: list.load, children: [
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: FilledButton.icon(
          key: const Key('attendance-write-report'),
          onPressed: () => _report(c.today!.day),
          icon: const Icon(Icons.edit_note),
          label: Text(s.t('writeReport')),
        ),
      ),
      RecordsSearchField(
          initial: list.query,
          hint: s.t('reportSearch'),
          onSearch: (q) => list.load(query: q)),
      if (list.phase != RecordsPhase.ready)
        RecordsStateView(
            phase: list.phase, failure: list.failure, onRetry: list.load)
      else if (list.items.isEmpty)
        list.narrowed
            ? AnumEmpty(
                title: k.t('noMatch'),
                message: k.t('noMatchText'),
                icon: Icons.search_off,
                action: k.t('clearSearch'),
                onAction: () => list.load(query: ''))
            : AnumEmpty(
                key: const Key('reports-empty'),
                title: s.t('reportsEmpty'),
                message: s.t('reportsEmptyText'),
                icon: Icons.edit_note,
                action: s.t('writeReport'),
                onAction: () => _report(c.today!.day))
      else ...[
        AnumSurface(
          child: Column(children: [
            for (final report in list.items)
              AnumRow(
                key: Key('report-${report.day}'),
                icon: Icons.description_outlined,
                title: recordsDate(context, report.day),
                subtitle: [
                  if (report.project != null) report.project!,
                  s.t('doneCount', {'count': report.tasksDone.length}),
                  s.t('pendingCount', {'count': report.tasksPending.length}),
                  s.label('priority', report.priority),
                ].join(' · '),
                detail: report.issues,
                onTap: () => _report(report.day, existing: report),
                trailing: IconButton(
                  key: Key('report-delete-${report.day}'),
                  tooltip: s.t('deleteReport'),
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () => _deleteReport(report),
                ),
              ),
          ]),
        ),
        RecordsMore(list: list),
      ],
    ]);
  }

  Widget _corrections(BuildContext context) {
    final s = AttendanceStrings.of(context);
    final list = c.corrections;
    String side(String? kind, String? at) => kind == null
        ? '-'
        : '${s.label('kind', kind)} ${at == null ? '' : recordsDateTime(context, at)}'
            .trim();
    return AnumPage(onRefresh: list.load, children: [
      Text(s.t('correctionsHelp'),
          style: TextStyle(color: context.palette.muted)),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        FilledButton.icon(
          key: const Key('attendance-add-missing'),
          onPressed: _correct,
          icon: const Icon(Icons.add),
          label: Text(s.t('addMissing')),
        ),
        OutlinedButton.icon(
          key: const Key('attendance-change-entry'),
          onPressed: () => _pickEntry('change'),
          icon: const Icon(Icons.edit_calendar_outlined),
          label: Text(s.t('changeEntry')),
        ),
        OutlinedButton.icon(
          key: const Key('attendance-remove-entry'),
          onPressed: () => _pickEntry('void'),
          icon: const Icon(Icons.remove_circle_outline),
          label: Text(s.t('removeEntry')),
        ),
      ]),
      if (list.phase != RecordsPhase.ready)
        RecordsStateView(
            phase: list.phase, failure: list.failure, onRetry: list.load)
      else if (list.items.isEmpty)
        AnumEmpty(
            key: const Key('corrections-empty'),
            title: s.t('correctionsEmpty'),
            message: s.t('correctionsEmptyText'),
            icon: Icons.rule,
            action: s.t('addMissing'),
            onAction: _correct)
      else ...[
        AnumSurface(
          child: Column(children: [
            for (final item in list.items)
              AnumRow(
                key: Key('correction-${item.id}'),
                icon: Icons.rule,
                title: s.label('action', item.action),
                subtitle: s.t('beforeAfter', {
                  'before': side(item.beforeKind, item.beforeAt),
                  'after': side(item.afterKind, item.afterAt),
                }),
                detail: s.t('reasonLine', {'reason': item.reason}),
              ),
          ]),
        ),
        RecordsMore(list: list),
      ],
    ]);
  }
}

/// The person's entries of the last 14 days, newest first, to pick one.
class _EntryPicker extends StatefulWidget {
  const _EntryPicker(
      {required this.title, required this.load, required this.siteName});
  final String title;
  final Future<List<AttendanceEvent>> Function() load;
  final Map<String, String> siteName;

  @override
  State<_EntryPicker> createState() => _EntryPickerState();
}

class _EntryPickerState extends State<_EntryPicker> {
  List<AttendanceEvent>? events;
  RecordsFailure? failure;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      events = null;
      failure = null;
    });
    try {
      final found = await widget.load();
      if (mounted) setState(() => events = found.reversed.toList());
    } on Object catch (e) {
      if (mounted) setState(() => failure = RecordsFailure.from(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = AttendanceStrings.of(context);
    final found = events;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(widget.title, style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: AnumSpacing.xs),
        if (failure != null)
          RecordsStateView(
              phase: failure!.denied ? RecordsPhase.denied : RecordsPhase.error,
              failure: failure,
              onRetry: _load)
        else if (found == null)
          const Padding(
              padding: EdgeInsets.all(AnumSpacing.md),
              child: Center(child: CircularProgressIndicator()))
        else if (found.isEmpty)
          Text(s.t('pickEmpty'),
              key: const Key('entries-empty'),
              style: TextStyle(color: context.palette.muted))
        else
          for (final event in found)
            AnumRow(
              key: Key('entry-${event.id}'),
              icon: Icons.schedule,
              title: s.label('kind', event.kind),
              subtitle: [
                recordsDateTime(context, event.occurredAt),
                if (widget.siteName[event.siteId] case final String name) name,
              ].join(' · '),
              onTap: () => Navigator.of(context).pop(event),
            ),
      ],
    );
  }
}
