import 'package:flutter/material.dart';

import '../../data/api_client.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'calendar_controller.dart';
import 'calendar_models.dart';
import 'calendar_screen.dart';
import 'event_form.dart';

AnumTone responseTone(String response) => switch (response) {
      'accepted' => AnumTone.ok,
      'declined' => AnumTone.stop,
      'tentative' => AnumTone.warn,
      _ => AnumTone.info,
    };

/// One event, as the "Meeting scheduled" screen: when and where, Join or
/// Call, the attendees with their replies, and (for its organiser or an
/// owner) edit, record a reply, cancel and delete.
class EventScreen extends StatefulWidget {
  const EventScreen({
    required this.controller,
    required this.eventId,
    this.openLink = openInBrowser,
    this.fromCalendar = true,
    super.key,
  });
  final CalendarController controller;
  final String eventId;
  final LinkOpener openLink;

  /// Opened from the calendar (View in Calendar goes back) or by a link.
  final bool fromCalendar;

  @override
  State<EventScreen> createState() => _EventScreenState();
}

class _EventScreenState extends State<EventScreen> {
  CalendarEvent? _event;
  String? _error;
  bool _missing = false;
  bool _loading = true;

  CalendarController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_sync);
    _load();
  }

  @override
  void dispose() {
    c.removeListener(_sync);
    super.dispose();
  }

  void _sync() {
    final notice = c.notice;
    if (notice != null && mounted) {
      c.clearNotice();
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(notice)));
    }
    if (mounted) setState(() {});
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      _event = await c.repository.event(widget.eventId);
      _missing = false;
    } on ApiException catch (e) {
      _missing = e.statusCode == 404;
      _error = e.readable;
    } on Object catch (e) {
      _missing = false;
      _error = '$e';
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _edit() async {
    final event = _event;
    if (event == null) return;
    final draft = await showEventForm(context, draft: EventDraft.from(event));
    if (draft == null) return;
    final saved = await c.save(event.id, draft.toJson());
    if (saved != null && mounted) setState(() => _event = saved);
  }

  Future<void> _cancel() async {
    final event = _event;
    if (event == null) return;
    final l = AnumLocalizations.maybeOf(context);
    final sure = await _confirm(l.t('calendarCancelTitle'),
        l.t('calendarCancelText'), l.t('calendarCancelEvent'));
    if (!sure) return;
    final saved = await c.save(event.id, {'status': 'cancelled'});
    if (saved != null && mounted) setState(() => _event = saved);
  }

  Future<void> _delete() async {
    final event = _event;
    if (event == null) return;
    final l = AnumLocalizations.maybeOf(context);
    final navigator = Navigator.of(context);
    final sure = await _confirm(l.t('calendarDeleteTitle'),
        l.t('calendarDeleteText'), l.t('calendarDelete'));
    if (!sure) return;
    if (await c.delete(event.id) && mounted) navigator.pop();
  }

  Future<bool> _confirm(String title, String text, String action) async {
    final l = AnumLocalizations.maybeOf(context);
    final answer = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(text),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(l.t('toolsCancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(action)),
        ],
      ),
    );
    return answer ?? false;
  }

  Future<void> _respond(Attendee attendee) async {
    final event = _event;
    if (event == null) return;
    final l = AnumLocalizations.maybeOf(context);
    final response = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            title: Text(l.f('calendarRecordReply', {'name': attendee.name})),
          ),
          for (final value in attendeeResponses)
            ListTile(
              leading: Icon(value == attendee.response
                  ? Icons.radio_button_checked
                  : Icons.radio_button_unchecked),
              title: Text(l.t('calendarReply_$value')),
              onTap: () => Navigator.pop(context, value),
            ),
        ]),
      ),
    );
    if (response == null || response == attendee.response) return;
    final saved = await c.respond(event, attendee, response);
    if (saved != null && mounted) setState(() => _event = saved);
  }

  void _viewInCalendar() {
    final event = _event;
    if (event != null) c.selectDay(event.startsAt);
    if (widget.fromCalendar) {
      Navigator.of(context).pop();
    } else {
      Navigator.of(context).pushNamed(CalendarRoutes.calendar);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final event = _event;
    final title = event == null
        ? l.t('calendarEvent')
        : event.cancelled
            ? l.t('calendarCancelled')
            : event.kind == 'meeting'
                ? l.t('calendarMeetingScheduled')
                : l.t('calendarKind_${event.kind}');
    return Scaffold(
      appBar: AppBar(
        title: Text(title),
        actions: [
          if (event != null && event.canManage)
            PopupMenuButton<String>(
              tooltip: l.t('reportMore'),
              onSelected: (value) => switch (value) {
                'edit' => _edit(),
                'cancel' => _cancel(),
                _ => _delete(),
              },
              itemBuilder: (context) => [
                PopupMenuItem(value: 'edit', child: Text(l.t('calendarEdit'))),
                if (!event.cancelled)
                  PopupMenuItem(
                      value: 'cancel', child: Text(l.t('calendarCancelEvent'))),
                PopupMenuItem(
                    value: 'delete', child: Text(l.t('calendarDelete'))),
              ],
            ),
        ],
      ),
      body: _body(context, l),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l) {
    if (_loading && _event == null) {
      return Center(
          child: Semantics(
              label: l.t('calendarLoading'),
              child: const CircularProgressIndicator()));
    }
    final event = _event;
    if (event == null) {
      return AnumPage(children: [
        AnumEmpty(
          icon: _missing ? Icons.search_off : Icons.error_outline,
          title: _missing ? l.t('calendarMissing') : l.t('calendarError'),
          message: _missing ? l.t('calendarMissingText') : (_error ?? ''),
          action: _missing ? l.t('reportBack') : l.t('retry'),
          onAction: _missing ? () => Navigator.of(context).maybePop() : _load,
        ),
      ]);
    }
    final p = context.palette;
    final join = event.joinLink;
    final call = event.callLink;
    final busy = c.busy != null;
    return AnumPage(onRefresh: _load, children: [
      Semantics(
        header: true,
        child: AnumHeader(
          title: event.title,
          subtitle: dateOf(context, event.startsAt),
        ),
      ),
      AnumSurface(
        accent: event.cancelled ? p.stop : null,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            AnumIconChip(
                icon: kindIcon(event.kind), tone: kindTone(event.kind)),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(event.title,
                        style: TextStyle(
                            color: p.text,
                            fontSize: 16,
                            fontWeight: FontWeight.w700)),
                    Text(
                        l.f('calendarTimeRange', {
                          'start': timeOf(context, event.startsAt),
                          'end': timeOf(context, event.endsAt)
                        }),
                        style: TextStyle(color: p.muted)),
                    if (event.location != null)
                      Text(event.location!, style: TextStyle(color: p.muted)),
                  ]),
            ),
          ]),
          if (event.cancelled) ...[
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: AnumPill(
                  label: l.t('calendarCancelled'),
                  tone: AnumTone.stop,
                  icon: Icons.event_busy),
            ),
          ],
          if (!event.cancelled && (join != null || call != null)) ...[
            const SizedBox(height: AnumSpacing.md),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  if (join != null)
                    FilledButton.icon(
                      onPressed: () =>
                          openEventLink(context, widget.openLink, join),
                      icon: const Icon(Icons.videocam_outlined),
                      label: Text(l.t('calendarJoin')),
                    ),
                  if (call != null)
                    FilledButton.tonalIcon(
                      onPressed: () =>
                          openEventLink(context, widget.openLink, call),
                      icon: const Icon(Icons.call_outlined),
                      label: Text(l.t('calendarCall')),
                    ),
                ]),
          ],
          if (event.description != null) ...[
            const SizedBox(height: AnumSpacing.md),
            Text(event.description!, style: TextStyle(color: p.muted)),
          ],
        ]),
      ),
      AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.md, vertical: AnumSpacing.sm),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Semantics(
            header: true,
            child: Text(
                l.f('calendarAttendees', {'count': event.attendees.length}),
                style: TextStyle(
                    color: p.text, fontWeight: FontWeight.w700, fontSize: 15)),
          ),
          if (event.attendees.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AnumSpacing.sm),
              child: Text(
                  event.canManage
                      ? l.t('calendarNoAttendeesEdit')
                      : l.t('calendarNoAttendees'),
                  style: TextStyle(color: p.muted)),
            ),
          for (final attendee in event.attendees)
            _AttendeeRow(
              attendee: attendee,
              onTap: event.canManage && !busy && attendee.id != null
                  ? () => _respond(attendee)
                  : null,
            ),
          if (event.attendees.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Text(l.t('calendarInvitesNote'),
                  style: TextStyle(color: p.faint, fontSize: 12)),
            ),
        ]),
      ),
      OutlinedButton.icon(
        onPressed: _viewInCalendar,
        icon: const Icon(Icons.calendar_month_outlined),
        label: Text(l.t('calendarView')),
      ),
    ]);
  }
}

class _AttendeeRow extends StatelessWidget {
  const _AttendeeRow({required this.attendee, this.onTap});
  final Attendee attendee;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final reply = l.t('calendarReply_${attendee.response}');
    final row = Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Row(children: [
        CircleAvatar(
          radius: 20,
          backgroundColor: p.surface3,
          child: Text(attendee.initials,
              style: TextStyle(
                  color: p.text, fontWeight: FontWeight.w700, fontSize: 13)),
        ),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(attendee.name,
                style: TextStyle(
                    color: p.text, fontWeight: FontWeight.w600, fontSize: 14)),
            if (attendee.role != null || attendee.email != null)
              Text(attendee.role ?? attendee.email!,
                  style: TextStyle(color: p.muted, fontSize: 12.5)),
          ]),
        ),
        const SizedBox(width: AnumSpacing.xs),
        Flexible(
          child: AnumPill(label: reply, tone: responseTone(attendee.response)),
        ),
      ]),
    );
    return Semantics(
      button: onTap != null,
      label: [
        attendee.name,
        if (attendee.role != null) attendee.role!,
        reply,
        if (onTap != null) l.t('calendarTapToRecord'),
      ].join(', '),
      excludeSemantics: true,
      child: onTap == null
          ? row
          : InkWell(
              onTap: onTap,
              borderRadius: BorderRadius.circular(12),
              child: ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 48),
                  child: row)),
    );
  }
}
