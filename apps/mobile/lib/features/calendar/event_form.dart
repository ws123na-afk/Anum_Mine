import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'calendar_models.dart';
import 'calendar_screen.dart' show dateOf, timeOf;

/// Add or edit an event; returns the filled draft or null when cancelled.
Future<EventDraft?> showEventForm(BuildContext context,
        {required EventDraft draft}) =>
    Navigator.of(context).push<EventDraft>(MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) => EventForm(draft: draft),
    ));

class EventForm extends StatefulWidget {
  const EventForm({required this.draft, super.key});
  final EventDraft draft;

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

  @override
  void dispose() {
    for (final c in [_title, _location, _link, _phone, _notes]) {
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

  Future<void> _addAttendee() async {
    final added = await showDialog<Attendee>(
        context: context, builder: (_) => const _AttendeeDialog());
    if (added != null) setState(() => _attendees = [..._attendees, added]);
  }

  void _save() {
    final l = AnumLocalizations.maybeOf(context);
    if (!(_form.currentState?.validate() ?? false)) return;
    if (!_end.isAfter(_start)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l.t('calendarEndAfterStart'))));
      return;
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
    ));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final editing = widget.draft.title.isNotEmpty;
    return Scaffold(
      appBar: AppBar(
        title: Text(editing ? l.t('calendarEdit') : l.t('calendarAdd')),
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
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.today_outlined),
                        title: Text(l.t('calendarFieldDate')),
                        subtitle: Text(dateOf(context, _start)),
                        onTap: _pickDate,
                      ),
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
