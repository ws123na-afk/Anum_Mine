import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'calendar_models.dart';
import 'calendar_screen.dart' show dateOf, timeOf;

/// Add or edit an event; returns the filled draft or null when cancelled.
/// [scope] says which occurrences of a series the edit is for: one
/// occurrence cannot carry a repeat rule of its own.
Future<EventDraft?> showEventForm(BuildContext context,
        {required EventDraft draft, EditScope? scope}) =>
    Navigator.of(context).push<EventDraft>(MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) => EventForm(draft: draft, scope: scope),
    ));

/// "10 minutes before", "1 day before", "At the start".
String reminderLabel(AnumLocalizations l, int minutes) {
  if (minutes == 0) return l.t('calendarReminderAtStart');
  for (final (unit, name) in [(10080, 'Week'), (1440, 'Day'), (60, 'Hour')]) {
    if (minutes % unit == 0) {
      final count = minutes ~/ unit;
      return count == 1
          ? l.t('calendarReminder$name')
          : l.f('calendarReminder${name}s', {'count': count});
    }
  }
  return minutes == 1
      ? l.t('calendarReminderMinute')
      : l.f('calendarReminderMinutes', {'count': minutes});
}

/// The weekday's name in the app's language: the full date of a known
/// Monday (5 January 2026) plus the code's offset starts with it.
String weekdayName(BuildContext context, String code) {
  final day = DateTime(2026, 1, 5 + weekdayCodes.indexOf(code));
  final text = MaterialLocalizations.of(context).formatFullDate(day);
  return text.split(RegExp(r'[,،]')).first.trim();
}

/// A readable sentence for a rule: "Every 2 weeks on Monday and Wednesday,
/// until 31 December 2026".
String recurrenceSummary(BuildContext context, Recurrence? rule) {
  final l = AnumLocalizations.maybeOf(context);
  if (rule == null) return l.t('calendarRepeat_none');
  final every = rule.interval == 1
      ? l.t('calendarSummary${_cap(rule.freq)}')
      : l.f('calendarSummaryEvery${_cap(rule.freq)}', {'count': rule.interval});
  var base = every;
  if (rule.freq == 'weekly' && rule.byWeekday.isNotEmpty) {
    base = l.f('calendarSummaryOn', {
      'rule': every,
      'days': rule.byWeekday.map((d) => weekdayName(context, d)).join(', '),
    });
  } else if (rule.freq == 'monthly' &&
      rule.byWeekday.isNotEmpty &&
      rule.bySetpos != null) {
    base = l.f('calendarSummaryOnWeekday', {
      'rule': every,
      'position': l.t(
          'calendarPosition_${rule.bySetpos == -1 ? 'last' : rule.bySetpos}'),
      'day': weekdayName(context, rule.byWeekday.first),
    });
  } else if (rule.freq == 'monthly' && rule.byMonthday != null) {
    base = l.f('calendarSummaryOnDay', {
      'rule': every,
      'day': rule.byMonthday == -1
          ? l.t('calendarLastDay')
          : rule.byMonthday.toString(),
    });
  }
  if (rule.until != null) {
    return l.f('calendarSummaryUntil', {
      'rule': base,
      'date': MaterialLocalizations.of(context)
          .formatMediumDate(DateTime.parse(rule.until!)),
    });
  }
  if (rule.count != null) {
    return l.f('calendarSummaryTimes', {'rule': base, 'count': rule.count!});
  }
  return base;
}

String _cap(String value) => value[0].toUpperCase() + value.substring(1);

/// Asks which occurrences of a series an action applies to; null = cancelled.
Future<EditScope?> askEditScope(BuildContext context, String title,
    {bool allowFollowing = true}) {
  final l = AnumLocalizations.maybeOf(context);
  return showModalBottomSheet<EditScope>(
    context: context,
    showDragHandle: true,
    builder: (context) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(
            title: Text(title,
                style: const TextStyle(fontWeight: FontWeight.w700))),
        for (final scope in EditScope.values)
          if (scope != EditScope.following || allowFollowing)
            ListTile(
              leading: Icon(switch (scope) {
                EditScope.thisOne => Icons.event_outlined,
                EditScope.following => Icons.event_repeat_outlined,
                EditScope.all => Icons.repeat,
              }),
              title: Text(l.t('calendarScope_${scope.api}')),
              onTap: () => Navigator.pop(context, scope),
            ),
        ListTile(
          leading: const Icon(Icons.close),
          title: Text(l.t('toolsCancel')),
          onTap: () => Navigator.pop(context),
        ),
      ]),
    ),
  );
}

class EventForm extends StatefulWidget {
  const EventForm({required this.draft, this.scope, super.key});
  final EventDraft draft;
  final EditScope? scope;

  @override
  State<EventForm> createState() => _EventFormState();
}

class _EventFormState extends State<EventForm> {
  final _form = GlobalKey<FormState>();
  late final _title = TextEditingController(text: widget.draft.title);
  late final _location = TextEditingController(text: widget.draft.location);
  late final _link = TextEditingController(text: widget.draft.conferenceUrl);
  late final _phone = TextEditingController(text: widget.draft.phone);
  late final _notes = TextEditingController(text: widget.draft.description);
  late String _kind = widget.draft.kind;
  late DateTime _start = widget.draft.startsAt;
  late DateTime _end = widget.draft.endsAt;
  late List<Attendee> _attendees = [...widget.draft.attendees];
  late bool _allDay = widget.draft.allDay;
  late String? _timezone = widget.draft.timezone;
  late Recurrence? _rule = widget.draft.recurrence;
  late List<int> _reminders = [...widget.draft.reminders];
  late final _count =
      TextEditingController(text: '${widget.draft.recurrence?.count ?? 10}');
  late final _interval =
      TextEditingController(text: '${widget.draft.recurrence?.interval ?? 1}');

  bool get _editingOne => widget.scope == EditScope.thisOne;

  @override
  void dispose() {
    for (final c in [
      _title,
      _location,
      _link,
      _phone,
      _notes,
      _count,
      _interval
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _start,
      firstDate: DateTime(_start.year - 1),
      lastDate: DateTime(_start.year + 3),
    );
    if (picked == null) return;
    final length = _end.difference(_start);
    setState(() {
      _start = DateTime(
          picked.year, picked.month, picked.day, _start.hour, _start.minute);
      _end = _start.add(length);
    });
  }

  Future<void> _pickTime({required bool start}) async {
    final current = start ? _start : _end;
    final picked = await showTimePicker(
        context: context, initialTime: TimeOfDay.fromDateTime(current));
    if (picked == null) return;
    setState(() {
      if (start) {
        final length = _end.difference(_start);
        _start = DateTime(
            _start.year, _start.month, _start.day, picked.hour, picked.minute);
        _end = _start.add(length);
      } else {
        _end = DateTime(
            _start.year, _start.month, _start.day, picked.hour, picked.minute);
      }
    });
  }

  Future<void> _pickEndDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _end.isBefore(_start) ? _start : _end,
      firstDate: DateTime(_start.year, _start.month, _start.day),
      lastDate: DateTime(_start.year + 3),
    );
    if (picked != null) {
      setState(() => _end = DateTime(
          picked.year, picked.month, picked.day, _end.hour, _end.minute));
    }
  }

  Future<void> _pickUntil() async {
    final current = _rule?.until == null
        ? _start.add(const Duration(days: 30))
        : DateTime.parse(_rule!.until!);
    final picked = await showDatePicker(
      context: context,
      initialDate: current.isBefore(_start) ? _start : current,
      firstDate: DateTime(_start.year, _start.month, _start.day),
      lastDate: DateTime(_start.year + 10),
    );
    if (picked != null) {
      setState(() => _rule = _rule!
          .copyWith(until: dayText(picked), clearEnd: false)
          .copyWith(count: null));
    }
  }

  void _setRepeat(String value) {
    setState(() {
      if (value == 'none') {
        _rule = null;
        return;
      }
      _rule = Recurrence(
        freq: value,
        interval: int.tryParse(_interval.text) ?? 1,
        byWeekday:
            value == 'weekly' ? [weekdayCodes[_start.weekday - 1]] : const [],
        until: _rule?.until,
        count: _rule?.count,
      );
    });
  }

  String get _endMode => _rule?.until != null
      ? 'until'
      : _rule?.count != null
          ? 'count'
          : 'never';

  void _setEndMode(String mode) {
    final rule = _rule;
    if (rule == null) return;
    setState(() => _rule = switch (mode) {
          'until' => rule
              .copyWith(clearEnd: true)
              .copyWith(until: dayText(_start.add(const Duration(days: 30)))),
          'count' => rule
              .copyWith(clearEnd: true)
              .copyWith(count: int.tryParse(_count.text) ?? 10),
          _ => rule.copyWith(clearEnd: true),
        });
  }

  Future<void> _addReminder() async {
    final l = AnumLocalizations.maybeOf(context);
    final picked = await showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          for (final minutes in reminderPresets)
            if (!_reminders.contains(minutes))
              ListTile(
                title: Text(reminderLabel(l, minutes)),
                onTap: () => Navigator.pop(context, minutes),
              ),
        ]),
      ),
    );
    if (picked != null) setState(() => _reminders = [..._reminders, picked]);
  }

  Future<void> _addAttendee() async {
    final added = await showDialog<Attendee>(
        context: context, builder: (_) => const _AttendeeDialog());
    if (added != null) setState(() => _attendees = [..._attendees, added]);
  }

  void _save() {
    final l = AnumLocalizations.maybeOf(context);
    if (!(_form.currentState?.validate() ?? false)) return;
    if (_allDay ? _end.isBefore(dayOf(_start)) : !_end.isAfter(_start)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l.t('calendarEndAfterStart'))));
      return;
    }
    var rule = _rule;
    if (rule != null && !_editingOne) {
      final count = rule.count == null ? null : int.tryParse(_count.text);
      if (rule.count != null && (count == null || count < 1 || count > 730)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l.t('calendarCountInvalid'))));
        return;
      }
      rule = rule.copyWith(
          interval: (int.tryParse(_interval.text) ?? 1).clamp(1, 99),
          count: count);
    }
    Navigator.of(context).pop(EventDraft(
      title: _title.text,
      kind: _kind,
      startsAt: _start,
      endsAt: _end,
      location: _location.text,
      conferenceUrl: _link.text,
      phone: _phone.text,
      description: _notes.text,
      attendees: _attendees,
      allDay: _allDay,
      timezone: _timezone ?? _localZone(),
      recurrence: _editingOne ? widget.draft.recurrence : rule,
      reminders: _reminders,
    ));
  }

  String _localZone() => DateTime.now().timeZoneName.contains('/')
      ? DateTime.now().timeZoneName
      : 'Asia/Dubai';

  List<String> _zoneChoices() {
    final chosen = _timezone;
    return {
      if (chosen != null) chosen,
      _localZone(),
      'Asia/Dubai',
      'Asia/Riyadh',
      'Asia/Karachi',
      'Asia/Kolkata',
      'Europe/London',
      'Europe/Berlin',
      'America/New_York',
      'UTC',
    }.toList();
  }

  List<Widget> _repeatFields(
      BuildContext context, AnumLocalizations l, AnumPalette p) {
    final rule = _rule;
    final freq = rule?.freq ?? 'none';
    return [
      DropdownButtonFormField<String>(
        initialValue: freq,
        decoration: InputDecoration(labelText: l.t('calendarFieldRepeat')),
        items: [
          for (final value in ['none', ...repeatFrequencies])
            DropdownMenuItem(
                value: value, child: Text(l.t('calendarRepeat_$value'))),
        ],
        onChanged: (value) => _setRepeat(value ?? 'none'),
      ),
      if (rule != null) ...[
        const SizedBox(height: AnumSpacing.sm),
        Row(children: [
          Text(l.t('calendarFieldInterval'), style: TextStyle(color: p.muted)),
          const SizedBox(width: AnumSpacing.sm),
          SizedBox(
            width: 72,
            child: TextFormField(
              controller: _interval,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                  labelText: l.t('calendarFieldInterval'), isDense: true),
              onChanged: (_) => setState(() {}),
            ),
          ),
          const SizedBox(width: AnumSpacing.sm),
          Text(l.t('calendarUnit_$freq'), style: TextStyle(color: p.muted)),
        ]),
        if (freq == 'weekly') ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('calendarFieldWeekdays'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            for (final code in weekdayCodes)
              FilterChip(
                label: Text(weekdayName(context, code)),
                selected: rule.byWeekday.contains(code),
                onSelected: (on) =>
                    setState(() => _rule = rule.copyWith(byWeekday: [
                          for (final c in weekdayCodes)
                            if (on
                                ? c == code || rule.byWeekday.contains(c)
                                : c != code && rule.byWeekday.contains(c))
                              c
                        ])),
              ),
          ]),
        ],
        if (freq == 'monthly') ...[
          const SizedBox(height: AnumSpacing.sm),
          RadioGroup<String>(
            groupValue: rule.bySetpos != null ? 'weekday' : 'day',
            onChanged: (value) => setState(() => _rule = value == 'weekday'
                ? rule.copyWith(
                    clearMonthly: true,
                    byWeekday: [weekdayCodes[_start.weekday - 1]],
                    bySetpos:
                        (_start.day + 6) ~/ 7 > 4 ? -1 : (_start.day + 6) ~/ 7)
                : rule.copyWith(clearMonthly: true, byWeekday: const [])),
            child: Column(children: [
              RadioListTile<String>(
                  contentPadding: EdgeInsets.zero,
                  value: 'day',
                  title:
                      Text(l.f('calendarMonthlyByDay', {'day': _start.day}))),
              RadioListTile<String>(
                  contentPadding: EdgeInsets.zero,
                  value: 'weekday',
                  title: Text(l.f('calendarMonthlyByWeekday', {
                    'position': l.t(
                        'calendarPosition_${(_start.day + 6) ~/ 7 > 4 ? 'last' : (_start.day + 6) ~/ 7}'),
                    'day':
                        weekdayName(context, weekdayCodes[_start.weekday - 1]),
                  }))),
            ]),
          ),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Text(l.t('calendarFieldRepeatEnds'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        RadioGroup<String>(
          groupValue: _endMode,
          onChanged: (value) => _setEndMode(value ?? 'never'),
          child: Column(children: [
            for (final mode in ['never', 'until', 'count'])
              RadioListTile<String>(
                  contentPadding: EdgeInsets.zero,
                  value: mode,
                  title: Text(l.t('calendarEnds_$mode'))),
          ]),
        ),
        if (_endMode == 'until')
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.event_outlined),
            title: Text(l.f('calendarFieldUntil', {
              'date': MaterialLocalizations.of(context)
                  .formatMediumDate(DateTime.parse(rule.until!))
            })),
            onTap: _pickUntil,
          ),
        if (_endMode == 'count')
          TextFormField(
            controller: _count,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(labelText: l.t('calendarFieldCount')),
            onChanged: (_) => setState(() {}),
          ),
        const SizedBox(height: AnumSpacing.xs),
        Row(children: [
          Icon(Icons.repeat, size: 16, color: p.muted),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
              child: Text(
                  recurrenceSummary(
                      context,
                      rule.copyWith(
                          interval: int.tryParse(_interval.text) ?? 1,
                          count: rule.count == null
                              ? null
                              : int.tryParse(_count.text) ?? rule.count)),
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
        ]),
        Text(l.t('calendarSeriesNotSynced'),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        const SizedBox(height: AnumSpacing.sm),
      ],
    ];
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final editing = widget.draft.title.isNotEmpty;
    final title = widget.scope == EditScope.thisOne
        ? l.t('calendarEditThis')
        : widget.scope == EditScope.following
            ? l.t('calendarEditFollowing')
            : editing
                ? l.t('calendarEdit')
                : l.t('calendarAdd');
    return Scaffold(
      appBar: AppBar(
        title: Text(title),
        actions: [
          TextButton(onPressed: _save, child: Text(l.t('toolsSave'))),
        ],
      ),
      body: SafeArea(
        child: Form(
          key: _form,
          child: ListView(
            padding: const EdgeInsets.all(AnumSpacing.md),
            children: [
              Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 640),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextFormField(
                        controller: _title,
                        maxLength: 160,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldTitle')),
                        validator: (value) => (value ?? '').trim().isEmpty
                            ? l.t('calendarTitleRequired')
                            : null,
                      ),
                      DropdownButtonFormField<String>(
                        initialValue: _kind,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldKind')),
                        items: [
                          for (final kind in eventKinds)
                            DropdownMenuItem(
                                value: kind,
                                child: Text(l.t('calendarKind_$kind'))),
                        ],
                        onChanged: (value) =>
                            setState(() => _kind = value ?? _kind),
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      if (_editingOne)
                        Padding(
                          padding:
                              const EdgeInsets.only(bottom: AnumSpacing.sm),
                          child: Text(l.t('calendarThisOnlyNote'),
                              style: TextStyle(color: p.muted)),
                        ),
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.today_outlined),
                        title: Text(l.t('calendarFieldDate')),
                        subtitle: Text(dateOf(context, _start)),
                        onTap: _pickDate,
                      ),
                      SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(l.t('calendarFieldAllDay')),
                        value: _allDay,
                        onChanged: (value) => setState(() {
                          _allDay = value;
                          if (value && _end.isBefore(_start)) _end = _start;
                        }),
                      ),
                      if (_allDay)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: const Icon(Icons.event_outlined),
                          title: Text(l.f('calendarFieldEndDate',
                              {'date': dateOf(context, _end)})),
                          onTap: _pickEndDate,
                        )
                      else
                        Wrap(spacing: AnumSpacing.xs, children: [
                          ActionChip(
                            avatar: const Icon(Icons.schedule, size: 18),
                            label: Text(l.f('calendarFieldStarts',
                                {'time': timeOf(context, _start)})),
                            onPressed: () => _pickTime(start: true),
                          ),
                          ActionChip(
                            avatar: const Icon(Icons.schedule, size: 18),
                            label: Text(l.f('calendarFieldEnds',
                                {'time': timeOf(context, _end)})),
                            onPressed: () => _pickTime(start: false),
                          ),
                        ]),
                      const SizedBox(height: AnumSpacing.sm),
                      if (!_editingOne) ..._repeatFields(context, l, p),
                      if (_allDay || _rule != null || _editingOne)
                        DropdownButtonFormField<String>(
                          initialValue: _timezone ?? _localZone(),
                          isExpanded: true,
                          decoration: InputDecoration(
                              labelText: l.t('calendarFieldTimezone'),
                              helperText: l.t('calendarFieldTimezoneHelp'),
                              helperMaxLines: 3),
                          items: [
                            for (final zone in _zoneChoices())
                              DropdownMenuItem(value: zone, child: Text(zone)),
                          ],
                          onChanged: (value) =>
                              setState(() => _timezone = value),
                        ),
                      const SizedBox(height: AnumSpacing.sm),
                      Text(l.t('calendarFieldReminders'),
                          style: TextStyle(
                              color: p.text, fontWeight: FontWeight.w700)),
                      Wrap(
                          spacing: AnumSpacing.xs,
                          runSpacing: AnumSpacing.xs,
                          children: [
                            for (final minutes in _reminders)
                              InputChip(
                                avatar: const Icon(Icons.notifications_none,
                                    size: 18),
                                label: Text(reminderLabel(l, minutes)),
                                deleteButtonTooltipMessage: l.f(
                                    'calendarRemoveReminder',
                                    {'label': reminderLabel(l, minutes)}),
                                onDeleted: () => setState(() => _reminders = [
                                      for (final m in _reminders)
                                        if (m != minutes) m
                                    ]),
                              ),
                            if (_reminders.length < maxReminders)
                              ActionChip(
                                avatar: const Icon(Icons.add_alert_outlined,
                                    size: 18),
                                label: Text(l.t('calendarAddReminder')),
                                onPressed: _addReminder,
                              ),
                          ]),
                      Text(l.t('calendarRemindersHelp'),
                          style:
                              TextStyle(color: p.faint, fontSize: AnumType.xs)),
                      const SizedBox(height: AnumSpacing.sm),
                      TextFormField(
                        controller: _location,
                        maxLength: 200,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldLocation')),
                      ),
                      TextFormField(
                        controller: _link,
                        keyboardType: TextInputType.url,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldLink'),
                            helperText: l.t('calendarFieldLinkHelp')),
                        validator: (value) {
                          final text = (value ?? '').trim();
                          if (text.isEmpty) return null;
                          final uri = Uri.tryParse(text);
                          return uri != null &&
                                  uri.scheme == 'https' &&
                                  uri.host.isNotEmpty
                              ? null
                              : l.t('calendarLinkInvalid');
                        },
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      TextFormField(
                        controller: _phone,
                        keyboardType: TextInputType.phone,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldPhone')),
                        validator: (value) {
                          final text = (value ?? '').trim();
                          if (text.isEmpty) return null;
                          return RegExp(r'^\+?[0-9][0-9 ()-]{3,30}$')
                                  .hasMatch(text)
                              ? null
                              : l.t('calendarPhoneInvalid');
                        },
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      TextFormField(
                        controller: _notes,
                        minLines: 2,
                        maxLines: 6,
                        maxLength: 2000,
                        decoration: InputDecoration(
                            labelText: l.t('calendarFieldNotes')),
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      Row(children: [
                        Expanded(
                          child: Text(
                              l.f('calendarAttendees',
                                  {'count': _attendees.length}),
                              style: TextStyle(
                                  color: p.text, fontWeight: FontWeight.w700)),
                        ),
                        TextButton.icon(
                          onPressed: _addAttendee,
                          icon: const Icon(Icons.person_add_alt),
                          label: Text(l.t('calendarAddAttendee')),
                        ),
                      ]),
                      for (final attendee in _attendees)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: CircleAvatar(child: Text(attendee.initials)),
                          title: Text(attendee.name),
                          subtitle:
                              attendee.role == null && attendee.email == null
                                  ? null
                                  : Text(attendee.role ?? attendee.email!),
                          trailing: IconButton(
                            tooltip: l.f('calendarRemoveAttendee',
                                {'name': attendee.name}),
                            icon: const Icon(Icons.close),
                            onPressed: () => setState(() => _attendees = [
                                  for (final a in _attendees)
                                    if (!identical(a, attendee)) a
                                ]),
                          ),
                        ),
                      const SizedBox(height: AnumSpacing.md),
                      FilledButton(
                          onPressed: _save, child: Text(l.t('toolsSave'))),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AttendeeDialog extends StatefulWidget {
  const _AttendeeDialog();

  @override
  State<_AttendeeDialog> createState() => _AttendeeDialogState();
}

class _AttendeeDialogState extends State<_AttendeeDialog> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _role = TextEditingController();

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _role.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return AlertDialog(
      title: Text(l.t('calendarAddAttendee')),
      content: Form(
        key: _form,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            TextFormField(
              controller: _name,
              autofocus: true,
              decoration: InputDecoration(labelText: l.t('calendarFieldName')),
              validator: (v) =>
                  (v ?? '').trim().isEmpty ? l.t('calendarNameRequired') : null,
            ),
            TextFormField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              decoration: InputDecoration(labelText: l.t('calendarFieldEmail')),
              validator: (v) {
                final text = (v ?? '').trim();
                return text.isEmpty ||
                        RegExp(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
                            .hasMatch(text)
                    ? null
                    : l.t('calendarEmailInvalid');
              },
            ),
            TextFormField(
              controller: _role,
              decoration: InputDecoration(labelText: l.t('calendarFieldRole')),
            ),
          ]),
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('toolsCancel'))),
        FilledButton(
          onPressed: () {
            if (!(_form.currentState?.validate() ?? false)) return;
            Navigator.pop(
                context,
                Attendee(
                  name: _name.text.trim(),
                  email: _email.text.trim().isEmpty ? null : _email.text.trim(),
                  role: _role.text.trim().isEmpty ? null : _role.text.trim(),
                ));
          },
          child: Text(l.t('calendarAddAttendee')),
        ),
      ],
    );
  }
}
