import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'calendar_controller.dart';
import 'calendar_models.dart';
import 'event_form.dart';
import 'event_screen.dart';

/// Named routes the calendar opens (see `workspace_tool_routes.dart`).
abstract final class CalendarRoutes {
  static const calendar = '/calendar';
  static String event(String id) =>
      '/calendar/events/${Uri.encodeComponent(id)}';
  static String task(String id) => '/tasks/${Uri.encodeComponent(id)}';
  static const urgentEmail = '/email/summary?tab=urgent';
  static const approvals = '/approvals';
}

String timeOf(BuildContext context, DateTime value) =>
    MaterialLocalizations.of(context).formatTimeOfDay(
        TimeOfDay.fromDateTime(value.toLocal()),
        alwaysUse24HourFormat:
            MediaQuery.maybeAlwaysUse24HourFormatOf(context) ?? false);

String dateOf(BuildContext context, DateTime value) =>
    MaterialLocalizations.of(context).formatFullDate(value.toLocal());

/// Join (an https meeting link) or Call (a phone number), when the event has
/// one. Opening fails visibly, never silently.
Future<void> openEventLink(
    BuildContext context, LinkOpener open, Uri uri) async {
  final l = AnumLocalizations.maybeOf(context);
  final messenger = ScaffoldMessenger.maybeOf(context);
  final ok = await open(uri);
  if (!ok) {
    messenger?.showSnackBar(SnackBar(
        content: Text(l.f('calendarLinkFailed', {'link': uri.toString()}))));
  }
}

AnumTone kindTone(String kind) => switch (kind) {
      'call' => AnumTone.ok,
      'review' => AnumTone.warn,
      'reminder' => AnumTone.accent,
      'focus' => AnumTone.neutral,
      _ => AnumTone.info,
    };

IconData kindIcon(String kind) => switch (kind) {
      'call' => Icons.call_outlined,
      'review' => Icons.fact_check_outlined,
      'reminder' => Icons.notifications_none,
      'focus' => Icons.center_focus_strong_outlined,
      _ => Icons.groups_outlined,
    };

class CalendarScreen extends StatefulWidget {
  const CalendarScreen(
      {required this.controller, this.openLink = openInBrowser, super.key});
  final CalendarController controller;
  final LinkOpener openLink;

  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  CalendarController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_announce);
    Future.microtask(c.load);
  }

  @override
  void dispose() {
    c.removeListener(_announce);
    super.dispose();
  }

  void _announce() {
    final text = c.notice;
    if (text == null || !mounted) return;
    c.clearNotice();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _add() async {
    final day = c.selectedDay;
    final now = DateTime.now();
    final start = DateTime(day.year, day.month, day.day,
        day == dayOf(now) ? (now.hour + 1).clamp(0, 23) : 10);
    final draft = await showEventForm(context,
        draft: EventDraft(
            title: '',
            kind: 'meeting',
            startsAt: start,
            endsAt: start.add(const Duration(hours: 1))));
    if (draft == null) return;
    final created = await c.create(draft);
    if (created != null && mounted) {
      c.selectDay(created.startsAt);
      await _openEvent(created);
    }
  }

  Future<void> _openEvent(CalendarEvent event) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => EventScreen(
          controller: c, eventId: event.id, openLink: widget.openLink),
    ));
    if (mounted) await c.loadSteps();
  }

  Future<void> _runStep(NextStep step) async {
    final navigator = Navigator.of(context);
    if (!step.isTask) {
      final route = switch (step.destination) {
        'email_urgent' => CalendarRoutes.urgentEmail,
        'approvals' => CalendarRoutes.approvals,
        _ => null,
      };
      if (route != null) await navigator.pushNamed(route);
      return;
    }
    final task = await c.runStep(step);
    if (task != null && mounted) {
      await navigator.pushNamed(CalendarRoutes.task(task));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(l.t('calendar')),
          actions: [
            TextButton(onPressed: c.today, child: Text(l.t('calendarToday'))),
            IconButton(
                tooltip: l.t('refresh'),
                onPressed: c.load,
                icon: const Icon(Icons.refresh)),
          ],
        ),
        floatingActionButton: c.canWrite
            ? FloatingActionButton.extended(
                onPressed: c.busy == null ? _add : null,
                icon: const Icon(Icons.add),
                label: Text(l.t('calendarAdd')),
              )
            : null,
        body: _body(context, l),
      ),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l) {
    if (c.phase == CalendarPhase.idle ||
        c.phase == CalendarPhase.loading && c.events.isEmpty) {
      return Center(
          child: Semantics(
              label: l.t('calendarLoading'),
              child: const CircularProgressIndicator()));
    }
    if (c.phase == CalendarPhase.denied) {
      return AnumPage(children: [
        AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('calendarDeniedTitle'),
            message: l.t('calendarDeniedText')),
      ]);
    }
    if (c.phase == CalendarPhase.error) {
      return AnumPage(children: [
        AnumEmpty(
          icon: Icons.error_outline,
          title: l.t('calendarError'),
          message: l.f('reportsErrorHint', {'message': c.error ?? ''}),
          action: l.t('retry'),
          onAction: c.load,
        ),
      ]);
    }
    final timeline = _Timeline(
        controller: c,
        onOpen: _openEvent,
        onAdd: c.canWrite ? _add : null,
        openLink: widget.openLink);
    final steps = _Steps(controller: c, onRun: _runStep);
    return AnumPage(
      onRefresh: c.load,
      children: [
        AnumHeader(
          eyebrow: l.t('calendarEyebrow'),
          title: l.t('calendarNextActions'),
          subtitle: dateOf(context, c.selectedDay),
        ),
        _DayStrip(controller: c),
        LayoutBuilder(builder: (context, constraints) {
          if (constraints.maxWidth < AnumBreakpoints.expanded) {
            return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  timeline,
                  const SizedBox(height: AnumSpacing.md),
                  steps,
                  const SizedBox(height: AnumSpacing.xxxl),
                ]);
          }
          return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(flex: 3, child: timeline),
            const SizedBox(width: AnumSpacing.md),
            Expanded(flex: 2, child: steps),
          ]);
        }),
      ],
    );
  }
}

class _DayStrip extends StatelessWidget {
  const _DayStrip({required this.controller});
  final CalendarController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final m = MaterialLocalizations.of(context);
    final c = controller;
    final rtl = Directionality.of(context) == TextDirection.rtl;
    return Row(children: [
      IconButton(
        tooltip: l.t('calendarPreviousWeek'),
        onPressed: () => c.shiftWeek(-1),
        icon: Icon(rtl ? Icons.chevron_right : Icons.chevron_left),
      ),
      Expanded(
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(children: [
            for (final day in c.days)
              Padding(
                padding: const EdgeInsetsDirectional.only(end: AnumSpacing.xs),
                child: Builder(builder: (context) {
                  final selected = day == c.selectedDay;
                  final count = c.countOn(day);
                  final weekday = m.narrowWeekdays[day.weekday % 7];
                  return Semantics(
                    button: true,
                    selected: selected,
                    label: '${dateOf(context, day)}, '
                        '${l.f('calendarEventsCount', {'count': count})}',
                    excludeSemantics: true,
                    child: InkWell(
                      onTap: () => c.selectDay(day),
                      borderRadius: BorderRadius.circular(14),
                      child: Container(
                        constraints:
                            const BoxConstraints(minWidth: 52, minHeight: 64),
                        padding: const EdgeInsets.symmetric(
                            horizontal: AnumSpacing.xs,
                            vertical: AnumSpacing.xs),
                        decoration: BoxDecoration(
                          color: selected ? p.violet : p.surface2,
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: p.line),
                        ),
                        child:
                            Column(mainAxisSize: MainAxisSize.min, children: [
                          Text(weekday,
                              style: TextStyle(
                                  color: selected ? p.background : p.muted,
                                  fontSize: 12)),
                          Text('${day.day}',
                              style: TextStyle(
                                  color: selected ? p.background : p.text,
                                  fontSize: 18,
                                  fontWeight: FontWeight.w700)),
                          Container(
                            width: 6,
                            height: 6,
                            decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: count == 0
                                    ? Colors.transparent
                                    : (selected ? p.background : p.sky)),
                          ),
                        ]),
                      ),
                    ),
                  );
                }),
              ),
          ]),
        ),
      ),
      IconButton(
        tooltip: l.t('calendarNextWeek'),
        onPressed: () => c.shiftWeek(1),
        icon: Icon(rtl ? Icons.chevron_left : Icons.chevron_right),
      ),
    ]);
  }
}

class _Timeline extends StatelessWidget {
  const _Timeline(
      {required this.controller,
      required this.onOpen,
      required this.onAdd,
      required this.openLink});
  final CalendarController controller;
  final ValueChanged<CalendarEvent> onOpen;
  final VoidCallback? onAdd;
  final LinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final events = controller.dayEvents;
    if (events.isEmpty) {
      return AnumSurface(
        child: AnumEmpty(
          icon: Icons.event_available_outlined,
          title: l.t('calendarDayEmpty'),
          message: onAdd == null
              ? l.t('calendarDayEmptyViewer')
              : l.t('calendarDayEmptyText'),
          action: onAdd == null ? null : l.t('calendarAdd'),
          onAction: onAdd,
        ),
      );
    }
    return AnumSurface(
      padding: const EdgeInsets.symmetric(
          horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
      child: Column(children: [
        for (var i = 0; i < events.length; i++) ...[
          if (i > 0) Divider(height: 1, color: p.line),
          _EventRow(
              event: events[i],
              onTap: () => onOpen(events[i]),
              openLink: openLink),
        ],
        if (controller.truncated)
          Padding(
            padding: const EdgeInsets.all(AnumSpacing.xs),
            child: Text(l.t('calendarTruncated'),
                style: TextStyle(color: p.faint, fontSize: 12)),
          ),
      ]),
    );
  }
}

class _EventRow extends StatelessWidget {
  const _EventRow(
      {required this.event, required this.onTap, required this.openLink});
  final CalendarEvent event;
  final VoidCallback onTap;
  final LinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final color = anumToneColor(context, kindTone(event.kind));
    final join = event.joinLink;
    final call = event.callLink;
    final action = join != null
        ? FilledButton.tonal(
            onPressed: () => openEventLink(context, openLink, join),
            child: Text(l.t('calendarJoin')))
        : call != null
            ? FilledButton.tonal(
                onPressed: () => openEventLink(context, openLink, call),
                child: Text(l.t('calendarCall')))
            : null;
    final where = [
      l.t('calendarKind_${event.kind}'),
      if (event.location != null) event.location!,
    ].join(' · ');
    final time = Text(timeOf(context, event.startsAt),
        style: TextStyle(
            color: p.muted, fontSize: 13, fontWeight: FontWeight.w600));
    final details =
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(event.title,
          style: TextStyle(
              color: p.text, fontSize: 15, fontWeight: FontWeight.w600)),
      Text(where, style: TextStyle(color: p.muted, fontSize: 13)),
      if (event.attendees.isNotEmpty)
        Text(l.f('calendarAttendeesCount', {'count': event.attendees.length}),
            style: TextStyle(color: p.faint, fontSize: 12)),
    ]);
    final bar = Container(
      width: 3,
      height: 40,
      decoration:
          BoxDecoration(color: color, borderRadius: BorderRadius.circular(2)),
    );
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.sm),
        child: LayoutBuilder(builder: (context, constraints) {
          // Narrow or large text: the time and the action move inside the
          // column, so nothing is squeezed off the row.
          final roomy = constraints.maxWidth >= 360 &&
              MediaQuery.textScalerOf(context).scale(14) <= 21;
          if (!roomy) {
            return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              bar,
              const SizedBox(width: AnumSpacing.sm),
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      time,
                      details,
                      if (action != null) ...[
                        const SizedBox(height: AnumSpacing.xs),
                        action,
                      ],
                    ]),
              ),
            ]);
          }
          return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            ConstrainedBox(
                constraints: const BoxConstraints(minWidth: 56), child: time),
            const SizedBox(width: AnumSpacing.xs),
            bar,
            const SizedBox(width: AnumSpacing.sm),
            Expanded(child: details),
            if (action != null) ...[
              const SizedBox(width: AnumSpacing.xs),
              action,
            ],
          ]);
        }),
      ),
    );
  }
}

class _Steps extends StatelessWidget {
  const _Steps({required this.controller, required this.onRun});
  final CalendarController controller;
  final ValueChanged<NextStep> onRun;

  static IconData _icon(String icon) => switch (icon) {
        'follow_up' => Icons.mark_email_unread_outlined,
        'summary' => Icons.summarize_outlined,
        'email' => Icons.priority_high,
        'approval' => Icons.verified_user_outlined,
        _ => Icons.event_note_outlined,
      };

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final c = controller;
    Widget content;
    if (c.stepsLoading && c.steps.isEmpty) {
      content = Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Center(
            child: Semantics(
                label: l.t('calendarStepsLoading'),
                child: const CircularProgressIndicator())),
      );
    } else if (c.stepsError != null) {
      content = AnumEmpty(
        icon: Icons.error_outline,
        title: l.t('calendarStepsError'),
        message: c.stepsError!,
        action: l.t('retry'),
        onAction: c.loadSteps,
      );
    } else if (c.steps.isEmpty) {
      content = AnumEmpty(
        icon: Icons.task_alt,
        title: l.t('calendarStepsEmpty'),
        message: l.t('calendarStepsEmptyText'),
      );
    } else {
      content = Column(children: [
        for (var i = 0; i < c.steps.length; i++) ...[
          if (i > 0) Divider(height: 1, color: p.line),
          AnumRow(
            icon: _icon(c.steps[i].icon),
            tone: c.steps[i].icon == 'email' ? AnumTone.stop : AnumTone.info,
            title: c.steps[i].title,
            subtitle: c.steps[i].detail,
            detail: c.steps[i].isTask ? l.t('calendarStepStartsTask') : null,
            trailing: c.busy == 'step:${c.steps[i].id}'
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : Icon(Directionality.of(context) == TextDirection.rtl
                    ? Icons.chevron_left
                    : Icons.chevron_right),
            onTap: c.busy == null ? () => onRun(c.steps[i]) : null,
          ),
        ],
      ]);
    }
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Semantics(
          header: true,
          child: Text(l.t('calendarSuggested'),
              style: TextStyle(
                  color: p.text, fontWeight: FontWeight.w700, fontSize: 15)),
        ),
        const SizedBox(height: AnumSpacing.xs),
        content,
      ]),
    );
  }
}
