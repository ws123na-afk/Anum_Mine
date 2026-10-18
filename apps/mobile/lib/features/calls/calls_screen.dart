import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'calls_controller.dart';
import 'calls_models.dart';
import 'calls_repository.dart';

String _outcome(AnumLocalizations l, String? outcome) =>
    l.t(outcome == null ? 'calls.outcome.none' : 'calls.outcome.$outcome');

AnumTone _tone(String? outcome) => switch (outcome) {
      'answered' => AnumTone.ok,
      'failed' || 'missed' => AnumTone.stop,
      'busy' || 'no_answer' || 'voicemail' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String _when(BuildContext context, DateTime value) {
  final local = value.toLocal();
  final m = MaterialLocalizations.of(context);
  return '${m.formatMediumDate(local)} ${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}

String _message(Object error) =>
    error is ApiException ? error.readable : '$error';

/// Phone calls (docs/calls.md#mobile): Contacts, the Dialpad and Recents.
/// "Call" opens the phone's own dialer (a `tel:` link, free); when the person
/// comes back to ANUM it asks to log the call: direction, outcome, the time
/// away as the duration (editable) and notes, which the workspace model can
/// summarise into a follow-up task.
class CallsScreen extends StatefulWidget {
  const CallsScreen({
    required this.controller,
    this.dialer = openPhoneDialer,
    this.pickContact = pickDeviceContact,
    this.now = DateTime.now,
    super.key,
  });
  final CallsController controller;
  final PhoneDialer dialer;
  final ContactPicker pickContact;
  final DateTime Function() now;

  @override
  State<CallsScreen> createState() => _CallsScreenState();
}

class _CallsScreenState extends State<CallsScreen>
    with WidgetsBindingObserver, SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);
  bool _away = false, _asking = false;
  String? _note;

  CallsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(c.load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _tabs.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.inactive) {
      if (c.pending != null) _away = true;
    } else if (state == AppLifecycleState.resumed && _away) {
      _away = false;
      unawaited(_askToLog());
    }
  }

  /// Open the phone's dialer and remember the call, to log it on return.
  Future<void> _dial(String phone, CallContact? contact) async {
    final uri = telUri(phone);
    final l = AnumLocalizations.of(context);
    if (uri == null) {
      setState(() => _note = l.t('calls.dial.invalid'));
      return;
    }
    c.dialed(dialable(phone), contact, widget.now());
    bool opened;
    try {
      opened = await widget.dialer(uri);
    } on Object {
      opened = false;
    }
    if (!mounted) return;
    if (!opened) {
      c.clearPending();
      setState(() => _note = l.t('calls.dial.failed'));
    }
  }

  Future<void> _askToLog() async {
    final pending = c.pending;
    if (pending == null || _asking || !mounted) return;
    _asking = true;
    final logged = await showModalBottomSheet<CallEntry>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => LogCallSheet(
        repository: c.repository,
        pending: pending,
        now: widget.now,
      ),
    );
    _asking = false;
    c.clearPending();
    if (logged != null && mounted) {
      setState(() => _note = AnumLocalizations.of(context).t('calls.log.saved'));
      _tabs.animateTo(2);
      unawaited(c.recents.load());
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(l.t('calls.title')),
          bottom: c.setupDenied
              ? null
              : TabBar(
                  controller: _tabs,
                  isScrollable: true,
                  tabAlignment: TabAlignment.start,
                  tabs: [
                    Tab(text: l.t('calls.tab.contacts')),
                    Tab(text: l.t('calls.tab.dial')),
                    Tab(text: l.t('calls.tab.recents')),
                  ],
                ),
        ),
        body: SafeArea(
          child: c.setupDenied
              ? _Centered(
                  child: AnumEmpty(
                    icon: Icons.lock_outline,
                    title: l.t('calls.title'),
                    message: c.setupError ?? l.t('calls.denied'),
                  ),
                )
              : Column(children: [
                  if (_note != null)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(
                          AnumSpacing.md, AnumSpacing.xs, AnumSpacing.md, 0),
                      child: _Note(text: _note!),
                    ),
                  Expanded(
                    child: TabBarView(controller: _tabs, children: [
                      _ContactsTab(
                        controller: c,
                        onDial: _dial,
                        pickContact: widget.pickContact,
                      ),
                      _DialTab(controller: c, onDial: _dial),
                      _RecentsTab(controller: c, onDial: _dial),
                    ]),
                  ),
                ]),
        ),
      ),
    );
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 560), child: child),
        ),
      );
}

class _Note extends StatelessWidget {
  const _Note({required this.text, this.error = false});
  final String text;
  final bool error;
  @override
  Widget build(BuildContext context) {
    final color =
        error ? Theme.of(context).colorScheme.error : context.palette.muted;
    return Semantics(
      liveRegion: true,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(error ? Icons.error_outline : Icons.info_outline,
            size: 18, color: color),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(child: Text(text, style: TextStyle(color: color))),
      ]),
    );
  }
}

/// A list page width-capped for tablets and desktops, scrolling as one.
class _ListPage extends StatelessWidget {
  const _ListPage({required this.children, this.onRefresh});
  final List<Widget> children;
  final Future<void> Function()? onRefresh;
  @override
  Widget build(BuildContext context) {
    final list = ListView(
      padding: const EdgeInsets.all(AnumSpacing.md),
      children: [
        for (final child in children)
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                child: child,
              ),
            ),
          ),
      ],
    );
    return onRefresh == null
        ? list
        : RefreshIndicator(onRefresh: onRefresh!, child: list);
  }
}

class _ListState<T> extends StatelessWidget {
  const _ListState(
      {required this.list,
      required this.emptyTitle,
      required this.emptyText,
      required this.noMatch,
      required this.icon});
  final CallsList<T> list;
  final String emptyTitle, emptyText, noMatch;
  final IconData icon;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return switch (list.phase) {
      CallsPhase.loading => Semantics(
          liveRegion: true,
          label: l.t('calls.loading'),
          child: const Center(
              child: Padding(
                  padding: EdgeInsets.all(AnumSpacing.lg),
                  child: CircularProgressIndicator())),
        ),
      CallsPhase.denied => AnumEmpty(
          icon: Icons.lock_outline,
          title: l.t('calls.title'),
          message: list.error ?? l.t('calls.denied')),
      CallsPhase.error => AnumEmpty(
          icon: Icons.cloud_off_outlined,
          title: l.f('calls.loadFailed', {'message': list.error ?? ''}),
          message: '',
          action: l.t('calls.retry'),
          onAction: () => unawaited(list.load())),
      CallsPhase.ready when list.items.isEmpty => AnumEmpty(
          icon: icon,
          title: list.query.isEmpty ? emptyTitle : noMatch,
          message: list.query.isEmpty ? emptyText : '',
        ),
      CallsPhase.ready => const SizedBox.shrink(),
    };
  }
}

class _Search extends StatefulWidget {
  const _Search({required this.label, required this.onSearch});
  final String label;
  final void Function(String query) onSearch;
  @override
  State<_Search> createState() => _SearchState();
}

class _SearchState extends State<_Search> {
  final _text = TextEditingController();
  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => TextField(
        controller: _text,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          labelText: widget.label,
          prefixIcon: const Icon(Icons.search),
          border: const OutlineInputBorder(),
        ),
        onSubmitted: (value) => widget.onSearch(value.trim()),
        onChanged: (value) {
          if (value.isEmpty) widget.onSearch('');
        },
      );
}

// -- contacts ----------------------------------------------------------------

class _ContactsTab extends StatelessWidget {
  const _ContactsTab(
      {required this.controller,
      required this.onDial,
      required this.pickContact});
  final CallsController controller;
  final Future<void> Function(String phone, CallContact? contact) onDial;
  final ContactPicker pickContact;

  Future<void> _add(BuildContext context, {DeviceContact? from}) async {
    final l = AnumLocalizations.of(context);
    final saved = await showModalBottomSheet<CallContact>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => ContactFormSheet(
          repository: controller.repository, from: from),
    );
    if (saved != null && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l.f('calls.contact.added', {'name': saved.name}))));
      unawaited(controller.contacts.load());
    }
  }

  Future<void> _pick(BuildContext context) async {
    final l = AnumLocalizations.of(context);
    DeviceContact? picked;
    try {
      picked = await pickContact();
    } on PlatformException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(l.f('calls.pickFailed', {'message': e.message ?? e.code}))));
      }
      return;
    }
    if (picked == null || !context.mounted) return;
    if (!isDialable(picked.phone)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l.t('calls.pickNoNumber'))));
      return;
    }
    await _add(context, from: picked);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final list = controller.contacts;
    final canLog = controller.setup?.canLog ?? false;
    return ListenableBuilder(
      listenable: list,
      builder: (context, _) => _ListPage(
        onRefresh: list.load,
        children: [
          Text(l.t('calls.intro'),
              style: TextStyle(color: context.palette.muted)),
          if (canLog)
            Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
              FilledButton.icon(
                onPressed: () => _add(context),
                icon: const Icon(Icons.person_add_alt_1_outlined),
                label: Text(l.t('calls.add')),
              ),
              OutlinedButton.icon(
                onPressed: () => _pick(context),
                icon: const Icon(Icons.contacts_outlined),
                label: Text(l.t('calls.pickFromPhone')),
              ),
            ]),
          _Search(
              label: l.t('calls.search.contacts'),
              onSearch: (q) => unawaited(list.load(query: q))),
          _ListState(
            list: list,
            emptyTitle: l.t('calls.contacts.empty'),
            emptyText: l.t('calls.contacts.emptyText'),
            noMatch: l.t('calls.contacts.noMatch'),
            icon: Icons.contacts_outlined,
          ),
          if (list.phase == CallsPhase.ready)
            for (final contact in list.items)
              _ContactRow(
                contact: contact,
                canLog: canLog,
                onCall: () => onDial(contact.phone, contact),
                onStar: () async {
                  await controller.repository.updateContact(
                      contact.id, {'favourite': !contact.favourite});
                  await list.load();
                },
                onOpen: () => Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => ContactDetailScreen(
                    controller: controller,
                    contactId: contact.id,
                    onDial: onDial,
                  ),
                )),
              ),
          if (list.hasMore && list.phase == CallsPhase.ready)
            Center(
              child: OutlinedButton(
                onPressed: list.loadingMore ? null : () => unawaited(list.more()),
                child: Text(l.t('calls.more')),
              ),
            ),
        ],
      ),
    );
  }
}

class _ContactRow extends StatelessWidget {
  const _ContactRow(
      {required this.contact,
      required this.canLog,
      required this.onCall,
      required this.onStar,
      required this.onOpen});
  final CallContact contact;
  final bool canLog;
  final VoidCallback onCall, onOpen;
  final Future<void> Function() onStar;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      onTap: onOpen,
      semanticLabel: contact.name,
      padding: const EdgeInsets.symmetric(
          horizontal: AnumSpacing.sm, vertical: AnumSpacing.xs),
      child: Row(children: [
        CircleAvatar(
          backgroundColor: p.violet.withValues(alpha: 0.18),
          child: Text(contact.name.characters.first.toUpperCase(),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        ),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(contact.name,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(contact.phone,
                textDirection: TextDirection.ltr,
                style: TextStyle(color: p.muted, fontSize: 13)),
            if (contact.company != null)
              Text(contact.company!,
                  style: TextStyle(color: p.muted, fontSize: 13)),
          ]),
        ),
        if (canLog)
          IconButton(
            tooltip: l.f(contact.favourite ? 'calls.unstar' : 'calls.star',
                {'name': contact.name}),
            onPressed: () => unawaited(onStar()),
            icon: Icon(contact.favourite ? Icons.star : Icons.star_border,
                color: contact.favourite ? p.warn : p.muted),
          ),
        if (canLog)
          IconButton.filledTonal(
            tooltip: l.f('calls.callName', {'name': contact.name}),
            onPressed: onCall,
            icon: const Icon(Icons.call_outlined),
          ),
      ]),
    );
  }
}

// -- dialpad -----------------------------------------------------------------

class _DialTab extends StatefulWidget {
  const _DialTab({required this.controller, required this.onDial});
  final CallsController controller;
  final Future<void> Function(String phone, CallContact? contact) onDial;
  @override
  State<_DialTab> createState() => _DialTabState();
}

class _DialTabState extends State<_DialTab> {
  String _number = '';
  final _field = TextEditingController();

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  void _set(String value) {
    setState(() => _number = value);
    _field.value = TextEditingValue(
        text: value, selection: TextSelection.collapsed(offset: value.length));
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    if (data?.text != null) _set(pasteNumber(_number, data!.text!));
  }

  CallContact? get _known {
    for (final contact in widget.controller.contacts.items) {
      if (dialable(contact.phone) == _number) return contact;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final canLog = widget.controller.setup?.canLog ?? false;
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'];
    final known = _known;
    return _ListPage(children: [
      Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(children: [
            Row(children: [
              Expanded(
                child: TextField(
                  controller: _field,
                  keyboardType: TextInputType.phone,
                  textDirection: TextDirection.ltr,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 22, letterSpacing: 1),
                  decoration: InputDecoration(
                    labelText: l.t('calls.dial.number'),
                    border: const OutlineInputBorder(),
                  ),
                  onChanged: (value) => _set(pasteNumber('', value)),
                ),
              ),
              IconButton(
                tooltip: l.t('calls.dial.paste'),
                onPressed: _paste,
                icon: const Icon(Icons.content_paste),
              ),
              IconButton(
                tooltip: l.t('calls.dial.delete'),
                onPressed: _number.isEmpty ? null : () => _set(backspace(_number)),
                onLongPress: _number.isEmpty ? null : () => _set(''),
                icon: const Icon(Icons.backspace_outlined),
              ),
            ]),
            if (known != null)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(known.name,
                    style: TextStyle(color: context.palette.muted)),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Directionality(
              textDirection: TextDirection.ltr,
              child: GridView.count(
                crossAxisCount: 3,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: AnumSpacing.xs,
                crossAxisSpacing: AnumSpacing.xs,
                childAspectRatio: 1.9,
                children: [
                  for (final key in keys)
                    Semantics(
                      button: true,
                      label: key == '0' ? '0 +' : key,
                      excludeSemantics: true,
                      child: OutlinedButton(
                        key: Key('dial-$key'),
                        onPressed: () => _set(pressKey(_number, key)),
                        onLongPress:
                            key == '0' ? () => _set(longPressZero(_number)) : null,
                        child: Column(mainAxisSize: MainAxisSize.min, children: [
                          Text(key, style: const TextStyle(fontSize: 22)),
                          if (key == '0')
                            const Text('+', style: TextStyle(fontSize: 11)),
                        ]),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('calls.dial.hint'),
                textAlign: TextAlign.center,
                style: TextStyle(color: context.palette.muted)),
            const SizedBox(height: AnumSpacing.sm),
            if (canLog)
              FilledButton.icon(
                key: const Key('dial-call'),
                onPressed: isDialable(_number)
                    ? () => widget.onDial(_number, known)
                    : null,
                icon: const Icon(Icons.call),
                label: Text(l.t('calls.dial.call')),
              ),
            if (!isDialable(_number))
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(l.t('calls.dial.invalid'),
                    style: TextStyle(color: context.palette.muted)),
              ),
          ]),
        ),
      ),
    ]);
  }
}

// -- recents -----------------------------------------------------------------

class _RecentsTab extends StatelessWidget {
  const _RecentsTab({required this.controller, required this.onDial});
  final CallsController controller;
  final Future<void> Function(String phone, CallContact? contact) onDial;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final list = controller.recents;
    return ListenableBuilder(
      listenable: list,
      builder: (context, _) => _ListPage(
        onRefresh: list.load,
        children: [
          _Search(
              label: l.t('calls.search.calls'),
              onSearch: (q) => unawaited(list.load(query: q))),
          _ListState(
            list: list,
            emptyTitle: l.t('calls.recents.empty'),
            emptyText: l.t('calls.recents.emptyText'),
            noMatch: l.t('calls.recents.noMatch'),
            icon: Icons.history,
          ),
          if (list.phase == CallsPhase.ready)
            for (final call in list.items)
              _CallRow(
                call: call,
                onOpen: () => Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => CallDetailScreen(
                    controller: controller,
                    callId: call.id,
                    onDial: onDial,
                  ),
                )),
              ),
          if (list.hasMore && list.phase == CallsPhase.ready)
            Center(
              child: OutlinedButton(
                onPressed: list.loadingMore ? null : () => unawaited(list.more()),
                child: Text(l.t('calls.more')),
              ),
            ),
        ],
      ),
    );
  }
}

class _CallRow extends StatelessWidget {
  const _CallRow({required this.call, required this.onOpen});
  final CallEntry call;
  final VoidCallback onOpen;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final who = call.who.isEmpty ? l.t('calls.unknown') : call.who;
    return AnumSurface(
      onTap: onOpen,
      semanticLabel: who,
      padding: const EdgeInsets.all(AnumSpacing.sm),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(
            call.kind.startsWith('ai')
                ? Icons.smart_toy_outlined
                : call.direction == 'inbound'
                    ? Icons.call_received
                    : Icons.call_made,
            color: p.sky),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(who, style: const TextStyle(fontWeight: FontWeight.w600)),
                  AnumPill(
                      label: call.live
                          ? l.t('calls.live')
                          : _outcome(l, call.outcome),
                      tone: _tone(call.outcome)),
                ]),
            Text(
              [
                l.t('calls.kind.${call.kind}'),
                _when(context, call.startedAt),
                durationText(call.durationSeconds),
              ].where((part) => part.isNotEmpty).join(' · '),
              style: TextStyle(color: p.muted, fontSize: 13),
            ),
            if ((call.summary ?? call.notes) != null)
              Text(call.summary ?? call.notes!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: p.muted, fontSize: 13)),
          ]),
        ),
      ]),
    );
  }
}

// -- log the call --------------------------------------------------------------

/// After the dialer: direction, outcome, duration (the time away, editable)
/// and notes; "Save and summarise" asks the workspace model for a summary.
class LogCallSheet extends StatefulWidget {
  const LogCallSheet(
      {required this.repository,
      required this.pending,
      this.now = DateTime.now,
      super.key});
  final CallsRepository repository;
  final PendingDial pending;
  final DateTime Function() now;
  @override
  State<LogCallSheet> createState() => _LogCallSheetState();
}

class _LogCallSheetState extends State<LogCallSheet> {
  String _direction = 'outbound', _outcome = 'answered';
  late final _duration = TextEditingController(
      text: durationText(elapsedSeconds(widget.pending.startedAt, widget.now())));
  final _notes = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _duration.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _save({required bool summarise}) async {
    final l = AnumLocalizations.of(context);
    final seconds =
        _duration.text.trim().isEmpty ? null : parseDuration(_duration.text);
    if (_duration.text.trim().isNotEmpty && seconds == null) {
      setState(() => _error = l.t('calls.log.badDuration'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final contact = widget.pending.contact;
      var call = await widget.repository.logCall(
        contactId: contact?.id,
        phone: contact == null ? widget.pending.phone : null,
        direction: _direction,
        outcome: _outcome,
        durationSeconds: seconds,
        startedAt: widget.pending.startedAt,
        notes: _notes.text,
      );
      if (summarise && _notes.text.trim().isNotEmpty) {
        try {
          call = await widget.repository.summarize(call.id);
        } on Object {
          // The call is logged; the summary can be asked for from the call.
        }
      }
      if (mounted) Navigator.of(context).pop(call);
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = _message(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final contact = widget.pending.contact;
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom +
              MediaQuery.paddingOf(context).bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Semantics(
              header: true,
              child: Text(
                contact != null
                    ? l.f('calls.log.with', {'name': contact.name})
                    : l.f('calls.log.with', {'name': widget.pending.phone}),
                style: Theme.of(context).textTheme.titleLarge,
              ),
            ),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('calls.log.direction')),
            Wrap(spacing: AnumSpacing.xs, children: [
              for (final value in ['outbound', 'inbound'])
                ChoiceChip(
                  label: Text(l.t('calls.direction.$value')),
                  selected: _direction == value,
                  onSelected: (_) => setState(() => _direction = value),
                ),
            ]),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('calls.log.outcome')),
            Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
              for (final value in logOutcomes)
                ChoiceChip(
                  label: Text(l.t('calls.outcome.$value')),
                  selected: _outcome == value,
                  onSelected: (_) => setState(() => _outcome = value),
                ),
            ]),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('log-duration'),
              controller: _duration,
              keyboardType: TextInputType.datetime,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                labelText: l.t('calls.log.duration'),
                helperText: l.t('calls.log.durationHint'),
                helperMaxLines: 2,
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('log-notes'),
              controller: _notes,
              minLines: 3,
              maxLines: 6,
              maxLength: 8000,
              decoration: InputDecoration(
                labelText: l.t('calls.log.notes'),
                border: const OutlineInputBorder(),
              ),
            ),
            if (_error != null) _Note(text: _error!, error: true),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                alignment: WrapAlignment.end,
                children: [
                  TextButton(
                    onPressed: _busy ? null : () => Navigator.of(context).pop(),
                    child: Text(l.t('calls.log.skip')),
                  ),
                  OutlinedButton(
                    onPressed: _busy ? null : () => _save(summarise: true),
                    child: Text(l.t('calls.log.saveSummarise')),
                  ),
                  FilledButton(
                    key: const Key('log-save'),
                    onPressed: _busy ? null : () => _save(summarise: false),
                    child: Text(
                        _busy ? l.t('calls.saving') : l.t('calls.log.save')),
                  ),
                ]),
          ],
        ),
      ),
    );
  }
}

// -- add a contact -------------------------------------------------------------

class ContactFormSheet extends StatefulWidget {
  const ContactFormSheet({required this.repository, this.from, super.key});
  final CallsRepository repository;

  /// Filled from the phone's address book (only the picked contact).
  final DeviceContact? from;
  @override
  State<ContactFormSheet> createState() => _ContactFormSheetState();
}

class _ContactFormSheetState extends State<ContactFormSheet> {
  late final _name = TextEditingController(text: widget.from?.name ?? '');
  late final _phone = TextEditingController(text: widget.from?.phone ?? '');
  final _company = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _phone.dispose();
    _company.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final l = AnumLocalizations.of(context);
    if (_name.text.trim().isEmpty) {
      setState(() => _error = l.t('calls.contact.nameRequired'));
      return;
    }
    if (!isDialable(_phone.text)) {
      setState(() => _error = l.t('calls.contact.badPhone'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final saved = await widget.repository.createContact(
        name: _name.text.trim(),
        phone: _phone.text.trim(),
        company: _company.text.trim(),
        source: widget.from == null ? 'manual' : 'device',
      );
      if (mounted) Navigator.of(context).pop(saved);
    } on Object catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = _message(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom +
              MediaQuery.paddingOf(context).bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Semantics(
                header: true,
                child: Text(l.t('calls.add'),
                    style: Theme.of(context).textTheme.titleLarge)),
            if (widget.from != null)
              Text(l.t('calls.contact.fromPhone'),
                  style: TextStyle(color: context.palette.muted)),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('contact-name'),
              controller: _name,
              maxLength: 200,
              decoration: InputDecoration(
                  labelText: l.t('calls.contact.name'),
                  border: const OutlineInputBorder()),
            ),
            TextField(
              key: const Key('contact-phone'),
              controller: _phone,
              keyboardType: TextInputType.phone,
              textDirection: TextDirection.ltr,
              maxLength: 40,
              decoration: InputDecoration(
                  labelText: l.t('calls.contact.phone'),
                  helperText: l.t('calls.contact.phoneHint'),
                  helperMaxLines: 2,
                  border: const OutlineInputBorder()),
            ),
            TextField(
              controller: _company,
              maxLength: 200,
              decoration: InputDecoration(
                  labelText: l.t('calls.contact.company'),
                  border: const OutlineInputBorder()),
            ),
            if (_error != null) _Note(text: _error!, error: true),
            const SizedBox(height: AnumSpacing.xs),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: FilledButton(
                key: const Key('contact-save'),
                onPressed: _busy ? null : _save,
                child: Text(
                    _busy ? l.t('calls.saving') : l.t('calls.contact.save')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// -- details -------------------------------------------------------------------

Future<bool> _confirm(BuildContext context, String title, String text) async {
  final l = AnumLocalizations.of(context);
  final yes = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(text),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l.t('calls.cancel'))),
        FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l.t('calls.delete'))),
      ],
    ),
  );
  return yes ?? false;
}

class CallDetailScreen extends StatefulWidget {
  const CallDetailScreen(
      {required this.controller,
      required this.callId,
      required this.onDial,
      super.key});
  final CallsController controller;
  final String callId;
  final Future<void> Function(String phone, CallContact? contact) onDial;
  @override
  State<CallDetailScreen> createState() => _CallDetailScreenState();
}

class _CallDetailScreenState extends State<CallDetailScreen> {
  CallEntry? _call;
  String? _error, _note, _busy;
  final _notes = TextEditingController(), _follow = TextEditingController();

  CallsRepository get _repo => widget.controller.repository;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _notes.dispose();
    _follow.dispose();
    super.dispose();
  }

  void _show(CallEntry call) {
    _call = call;
    _notes.text = call.notes ?? '';
    _follow.text = call.followUp?.text ?? '';
  }

  Future<void> _load() async {
    try {
      final call = await _repo.call(widget.callId);
      if (mounted) setState(() => _show(call));
    } on Object catch (e) {
      if (mounted) setState(() => _error = _message(e));
    }
  }

  Future<void> _act(String name, Future<CallEntry> Function() run,
      String done) async {
    setState(() {
      _busy = name;
      _note = null;
      _error = null;
    });
    try {
      final call = await run();
      if (!mounted) return;
      setState(() {
        _show(call);
        _note = done;
      });
      unawaited(widget.controller.recents.load());
    } on Object catch (e) {
      if (mounted) setState(() => _error = _message(e));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final call = _call;
    final setup = widget.controller.setup;
    final p = context.palette;
    final who = call == null
        ? l.t('calls.title')
        : (call.who.isEmpty ? l.t('calls.unknown') : call.who);
    return Scaffold(
      appBar: AppBar(title: Text(who)),
      body: SafeArea(
        child: call == null
            ? _Centered(
                child: _error == null
                    ? const CircularProgressIndicator()
                    : AnumEmpty(
                        icon: Icons.error_outline,
                        title: l.f('calls.loadFailed', {'message': _error!}),
                        message: '',
                        action: l.t('calls.retry'),
                        onAction: () {
                          setState(() => _error = null);
                          unawaited(_load());
                        }),
              )
            : _ListPage(children: [
                Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
                  AnumPill(
                      label: call.live ? l.t('calls.live') : _outcome(l, call.outcome),
                      tone: _tone(call.outcome)),
                  AnumPill(label: l.t('calls.direction.${call.direction}')),
                  AnumPill(label: l.t('calls.kind.${call.kind}')),
                ]),
                Text(
                  [
                    _when(context, call.startedAt),
                    durationText(call.durationSeconds)
                  ].where((part) => part.isNotEmpty).join(' · '),
                  style: TextStyle(color: p.muted),
                ),
                if (call.phone != null)
                  Text(call.phone!, textDirection: TextDirection.ltr),
                if (call.phone != null && (setup?.canLog ?? false))
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: FilledButton.tonalIcon(
                      onPressed: () => widget.onDial(call.phone!, null),
                      icon: const Icon(Icons.call_outlined),
                      label: Text(l.f('calls.callName', {'name': who})),
                    ),
                  ),
                if (!call.canEdit) _Note(text: l.t('calls.detail.readOnly')),
                if (_error != null) _Note(text: _error!, error: true),
                if (_note != null) _Note(text: _note!),
                _Section(title: l.t('calls.detail.notes'), children: [
                  if (call.canEdit) ...[
                    TextField(
                      key: const Key('detail-notes'),
                      controller: _notes,
                      minLines: 3,
                      maxLines: 8,
                      maxLength: 8000,
                      decoration:
                          const InputDecoration(border: OutlineInputBorder()),
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton(
                        onPressed: _busy != null
                            ? null
                            : () => _act(
                                'notes',
                                () => _repo.updateCall(call.id, {'notes': _notes.text}),
                                l.t('calls.detail.notesSaved')),
                        child: Text(l.t('calls.detail.saveNotes')),
                      ),
                    ),
                  ] else
                    Text(call.notes ?? ''),
                ]),
                _Section(title: l.t('calls.detail.summary'), children: [
                  if (call.summary != null) ...[
                    Text(call.summary!),
                    if (call.model != null)
                      Text(l.f('calls.detail.aiLabel', {'model': call.model!}),
                          style: TextStyle(color: p.muted, fontSize: 12.5)),
                  ] else
                    Text(l.t('calls.detail.noSummary'),
                        style: TextStyle(color: p.muted)),
                  if (setup != null && !setup.modelReady)
                    _Note(text: l.t('calls.detail.noModel')),
                  if (call.canEdit && (setup?.modelReady ?? false))
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton.icon(
                        key: const Key('detail-summarise'),
                        onPressed: _busy != null ||
                                (call.notes == null && call.transcript == null)
                            ? null
                            : () => _act('summary', () => _repo.summarize(call.id),
                                l.t('calls.detail.summary')),
                        icon: const Icon(Icons.auto_awesome),
                        label: Text(call.summary == null
                            ? l.t('calls.detail.summarise')
                            : l.t('calls.detail.summariseAgain')),
                      ),
                    ),
                ]),
                _Section(title: l.t('calls.detail.followUp'), children: [
                  if (call.followUp?.taskId != null)
                    _Note(text: l.t('calls.detail.isTask')),
                  if (call.canEdit) ...[
                    TextField(
                      key: const Key('detail-follow'),
                      controller: _follow,
                      maxLength: 500,
                      decoration: InputDecoration(
                          labelText: l.t('calls.detail.followUpField'),
                          border: const OutlineInputBorder()),
                    ),
                    Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
                      OutlinedButton(
                        onPressed: _busy != null
                            ? null
                            : () {
                                if (_follow.text.trim().isEmpty) return;
                                unawaited(_act(
                                    'follow',
                                    () => _repo.saveFollowUp(call.id,
                                        _follow.text.trim(), call.followUp?.dueDate),
                                    l.t('calls.detail.followUpSaved')));
                              },
                        child: Text(l.t('calls.detail.saveFollowUp')),
                      ),
                      if (call.followUp != null &&
                          call.followUp!.taskId == null &&
                          (setup?.canCreateTasks ?? false))
                        FilledButton(
                          key: const Key('detail-task'),
                          onPressed: _busy != null
                              ? null
                              : () => _act('task', () => _repo.followUpTask(call.id),
                                  l.t('calls.detail.taskSaved')),
                          child: Text(l.t('calls.detail.task')),
                        ),
                    ]),
                  ] else
                    Text(call.followUp?.text ?? l.t('calls.detail.noFollowUp')),
                  if (call.followUp == null && call.canEdit)
                    Text(l.t('calls.detail.noFollowUp'),
                        style: TextStyle(color: p.muted)),
                ]),
                if (call.transcript != null)
                  _Section(
                      title: l.t('calls.detail.transcript'),
                      children: [Text(call.transcript!)]),
                if (call.canEdit && !call.live)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton.icon(
                      onPressed: () async {
                        final navigator = Navigator.of(context);
                        if (!await _confirm(context, l.t('calls.detail.deleteTitle'),
                            l.t('calls.detail.deleteText'))) {
                          return;
                        }
                        try {
                          await _repo.deleteCall(call.id);
                          unawaited(widget.controller.recents.load());
                          navigator.pop();
                        } on Object catch (e) {
                          if (mounted) setState(() => _error = _message(e));
                        }
                      },
                      icon: Icon(Icons.delete_outline,
                          color: Theme.of(context).colorScheme.error),
                      label: Text(l.t('calls.detail.delete'),
                          style: TextStyle(
                              color: Theme.of(context).colorScheme.error)),
                    ),
                  ),
              ]),
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.children});
  final String title;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
                header: true,
                child: Text(title,
                    style: Theme.of(context).textTheme.titleMedium)),
            const SizedBox(height: AnumSpacing.xs),
            for (final child in children)
              Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                  child: child),
          ],
        ),
      );
}

class ContactDetailScreen extends StatefulWidget {
  const ContactDetailScreen(
      {required this.controller,
      required this.contactId,
      required this.onDial,
      super.key});
  final CallsController controller;
  final String contactId;
  final Future<void> Function(String phone, CallContact? contact) onDial;
  @override
  State<ContactDetailScreen> createState() => _ContactDetailScreenState();
}

class _ContactDetailScreenState extends State<ContactDetailScreen> {
  CallContact? _contact;
  List<CallEntry>? _calls;
  String? _error;

  CallsRepository get _repo => widget.controller.repository;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final page = await _repo.contacts();
      final found = page.items.where((c) => c.id == widget.contactId);
      final calls = await _repo.calls(contactId: widget.contactId);
      if (!mounted) return;
      setState(() {
        _contact = found.isEmpty ? null : found.first;
        _calls = calls.items;
        _error = found.isEmpty ? '404' : null;
      });
    } on Object catch (e) {
      if (mounted) setState(() => _error = _message(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final contact = _contact ??
        widget.controller.contacts.items
            .where((c) => c.id == widget.contactId)
            .firstOrNull;
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(contact?.name ?? l.t('calls.contact.title'))),
      body: SafeArea(
        child: contact == null
            ? _Centered(
                child: _error == null
                    ? const CircularProgressIndicator()
                    : AnumEmpty(
                        icon: Icons.error_outline,
                        title: l.f('calls.loadFailed', {'message': _error!}),
                        message: '',
                        action: l.t('calls.retry'),
                        onAction: () => unawaited(_load())),
              )
            : _ListPage(children: [
                Text(contact.phone,
                    textDirection: TextDirection.ltr,
                    style: Theme.of(context).textTheme.titleMedium),
                if (contact.company != null)
                  Text(contact.company!, style: TextStyle(color: p.muted)),
                if (contact.notes != null) Text(contact.notes!),
                if (widget.controller.setup?.canLog ?? false)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: FilledButton.icon(
                      onPressed: () => widget.onDial(contact.phone, contact),
                      icon: const Icon(Icons.call),
                      label: Text(l.f('calls.callName', {'name': contact.name})),
                    ),
                  ),
                AnumPill(
                  icon: Icons.smart_toy_outlined,
                  tone: contact.optedOut
                      ? AnumTone.stop
                      : contact.consent != null
                          ? AnumTone.ok
                          : AnumTone.neutral,
                  label: contact.optedOut
                      ? l.t('calls.contact.optedOut')
                      : contact.consent != null
                          ? l.t('calls.contact.aiConsent')
                          : l.t('calls.contact.noAiConsent'),
                ),
                _Section(
                    title: l.f('calls.contact.calls', {'name': contact.name}),
                    children: [
                      if (_calls == null)
                        const LinearProgressIndicator()
                      else if (_calls!.isEmpty)
                        Text(l.t('calls.contact.noCalls'),
                            style: TextStyle(color: p.muted))
                      else
                        for (final call in _calls!)
                          _CallRow(
                            call: call,
                            onOpen: () => Navigator.of(context).push(
                                MaterialPageRoute<void>(
                                    builder: (_) => CallDetailScreen(
                                        controller: widget.controller,
                                        callId: call.id,
                                        onDial: widget.onDial))),
                          ),
                    ]),
                if (contact.canEdit)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton.icon(
                      onPressed: () async {
                        final navigator = Navigator.of(context);
                        final messenger = ScaffoldMessenger.of(context);
                        if (!await _confirm(
                            context,
                            l.f('calls.contact.deleteTitle', {'name': contact.name}),
                            l.t('calls.contact.deleteText'))) {
                          return;
                        }
                        try {
                          await _repo.deleteContact(contact.id);
                          unawaited(widget.controller.contacts.load());
                          messenger.showSnackBar(SnackBar(
                              content: Text(l.f(
                                  'calls.contact.deleted', {'name': contact.name}))));
                          navigator.pop();
                        } on Object catch (e) {
                          if (mounted) setState(() => _error = _message(e));
                        }
                      },
                      icon: Icon(Icons.delete_outline,
                          color: Theme.of(context).colorScheme.error),
                      label: Text(l.t('calls.delete'),
                          style: TextStyle(
                              color: Theme.of(context).colorScheme.error)),
                    ),
                  ),
              ]),
      ),
    );
  }
}
