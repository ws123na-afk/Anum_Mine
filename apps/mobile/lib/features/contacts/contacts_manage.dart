import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'contacts_controller.dart';
import 'contacts_models.dart';
import 'contacts_repository.dart';

/// Add or edit a person (docs/contacts.md#api): name, organisation, notes,
/// numbers and addresses one per line, checked like the API before sending.
class PersonFormScreen extends StatefulWidget {
  const PersonFormScreen(
      {required this.save, this.initial, this.title, super.key});

  /// Sends the draft; answers null when saved, else the sentence to show.
  final Future<String?> Function(PersonDraft draft) save;
  final PersonDraft? initial;
  final String? title;

  @override
  State<PersonFormScreen> createState() => _PersonFormScreenState();
}

class _PersonFormScreenState extends State<PersonFormScreen> {
  late final _name = TextEditingController(text: widget.initial?.name);
  late final _organisation =
      TextEditingController(text: widget.initial?.organisation);
  late final _notes = TextEditingController(text: widget.initial?.notes);
  late final _phones = TextEditingController(text: widget.initial?.phones);
  late final _emails = TextEditingController(text: widget.initial?.emails);
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_name, _organisation, _notes, _phones, _emails]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final error = await widget.save(PersonDraft(
        name: _name.text,
        organisation: _organisation.text,
        notes: _notes.text,
        phones: _phones.text,
        emails: _emails.text));
    if (!mounted) return;
    if (error == null) {
      Navigator.of(context).pop(true);
      return;
    }
    setState(() {
      _saving = false;
      _error = error;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return Scaffold(
      appBar:
          AppBar(title: Text(widget.title ?? l.t('contacts.form.addTitle'))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 640),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                          key: const Key('person-name'),
                          controller: _name,
                          maxLength: 200,
                          textInputAction: TextInputAction.next,
                          decoration: InputDecoration(
                              labelText: l.t('contacts.form.name'))),
                      TextField(
                          key: const Key('person-organisation'),
                          controller: _organisation,
                          maxLength: 200,
                          textInputAction: TextInputAction.next,
                          decoration: InputDecoration(
                              labelText: l.t('contacts.form.organisation'))),
                      TextField(
                          key: const Key('person-phones'),
                          controller: _phones,
                          minLines: 2,
                          maxLines: 6,
                          keyboardType: TextInputType.multiline,
                          textDirection: TextDirection.ltr,
                          decoration: InputDecoration(
                              labelText: l.t('contacts.form.phones'),
                              helperText: l.t('contacts.form.phonesHint'),
                              helperMaxLines: 3)),
                      const SizedBox(height: AnumSpacing.sm),
                      TextField(
                          key: const Key('person-emails'),
                          controller: _emails,
                          minLines: 2,
                          maxLines: 6,
                          keyboardType: TextInputType.multiline,
                          textDirection: TextDirection.ltr,
                          decoration: InputDecoration(
                              labelText: l.t('contacts.form.emails'),
                              helperText: l.t('contacts.form.emailsHint'),
                              helperMaxLines: 3)),
                      const SizedBox(height: AnumSpacing.sm),
                      TextField(
                          key: const Key('person-notes'),
                          controller: _notes,
                          minLines: 2,
                          maxLines: 6,
                          maxLength: 4000,
                          decoration: InputDecoration(
                              labelText: l.t('contacts.form.notes'))),
                      if (_error != null)
                        Semantics(
                          liveRegion: true,
                          child: Text(_error!,
                              key: const Key('person-form-error'),
                              style: TextStyle(color: p.stop)),
                        ),
                      const SizedBox(height: AnumSpacing.sm),
                      Align(
                        alignment: AlignmentDirectional.centerEnd,
                        child: FilledButton.icon(
                            key: const Key('person-save'),
                            onPressed: _saving ? null : _submit,
                            icon: const Icon(Icons.save_outlined),
                            label: Text(l.t(_saving
                                ? 'contacts.form.saving'
                                : 'contacts.form.save'))),
                      ),
                    ]),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Picks the person to merge into [keep]: search the directory, then confirm.
Future<Person?> pickPersonToMerge(
        BuildContext context, ContactsRepository repository, Person keep) =>
    showModalBottomSheet<Person>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (_) => _MergePicker(repository: repository, keep: keep));

class _MergePicker extends StatefulWidget {
  const _MergePicker({required this.repository, required this.keep});
  final ContactsRepository repository;
  final Person keep;
  @override
  State<_MergePicker> createState() => _MergePickerState();
}

class _MergePickerState extends State<_MergePicker> {
  final _query = TextEditingController();
  List<Person> _found = const [];
  bool _loading = false;
  String? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_search(''));
  }

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search(String q) async {
    final id = ++_request;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await widget.repository.people(query: q);
      if (!mounted || id != _request) return;
      setState(() {
        _found = page.items.where((p) => p.id != widget.keep.id).toList();
        _loading = false;
      });
    } on Object catch (e) {
      if (!mounted || id != _request) return;
      setState(() {
        _loading = false;
        _error = readableError(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return SafeArea(
      child: ConstrainedBox(
        constraints:
            BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.85),
        child: ListView(
          shrinkWrap: true,
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
          children: [
            Text(l.f('contacts.merge.title', {'name': widget.keep.name}),
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: AnumSpacing.xs),
            Text(l.f('contacts.merge.body', {'name': widget.keep.name}),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('merge-search'),
              controller: _query,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                  labelText: l.t('contacts.merge.search'),
                  prefixIcon: const Icon(Icons.search)),
              onSubmitted: (v) => unawaited(_search(v)),
            ),
            const SizedBox(height: AnumSpacing.sm),
            if (_loading) const LinearProgressIndicator(),
            if (_error != null) Text(_error!, style: TextStyle(color: p.stop)),
            if (!_loading && _error == null && _found.isEmpty)
              Text(l.t('contacts.merge.none'),
                  style: TextStyle(color: p.muted)),
            for (final other in _found)
              ListTile(
                key: Key('merge-pick-${other.id}'),
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.call_merge),
                title: Text(other.name),
                subtitle: Text(
                    [
                      if (other.organisation.isNotEmpty) other.organisation,
                      ...[...other.phones, ...other.emails].map((i) => i.value),
                    ].join(' · '),
                    textDirection: TextDirection.ltr),
                onTap: () async {
                  final ok = await showDialog<bool>(
                      context: context,
                      builder: (context) => AlertDialog(
                            title: Text(l.f('contacts.merge.confirmTitle', {
                              'other': other.name,
                              'name': widget.keep.name
                            })),
                            content: Text(l.f('contacts.merge.body',
                                {'name': widget.keep.name})),
                            actions: [
                              TextButton(
                                  onPressed: () =>
                                      Navigator.of(context).pop(false),
                                  child: Text(l.t('contacts.dnc.cancel'))),
                              FilledButton(
                                  key: const Key('merge-confirm'),
                                  onPressed: () =>
                                      Navigator.of(context).pop(true),
                                  child: Text(l.t('contacts.merge.confirm'))),
                            ],
                          ));
                  if (ok == true && context.mounted) {
                    Navigator.of(context).pop(other);
                  }
                },
              ),
          ],
        ),
      ),
    );
  }
}

/// Possible duplicates: each pair with Keep A, Keep B and Different people,
/// 25 at a time with Show more.
class DuplicatesScreen extends StatefulWidget {
  const DuplicatesScreen({required this.controller, super.key});
  final DuplicatesController controller;
  @override
  State<DuplicatesScreen> createState() => _DuplicatesScreenState();
}

class _DuplicatesScreenState extends State<DuplicatesScreen> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.load());
  }

  Future<void> _report(Future<String> work) async {
    final messenger = ScaffoldMessenger.of(context);
    final text = await work;
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final c = widget.controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('contacts.dup.title'))),
        body: SafeArea(
          child: switch (c.phase) {
            ContactsPhase.loading => Center(
                child: Semantics(
                    label: l.t('contacts.dup.loading'),
                    child: const CircularProgressIndicator())),
            ContactsPhase.denied => Center(
                child: AnumEmpty(
                    icon: Icons.lock_outline,
                    title: l.t('contacts.dup.title'),
                    message: c.error ?? l.t('contacts.denied'))),
            ContactsPhase.error => Center(
                child: AnumEmpty(
                    icon: Icons.cloud_off_outlined,
                    title:
                        l.f('contacts.loadFailed', {'message': c.error ?? ''}),
                    message: '',
                    action: l.t('contacts.retry'),
                    onAction: () => unawaited(c.load()))),
            ContactsPhase.ready => _list(context, l, c),
          },
        ),
      ),
    );
  }

  Widget _list(
      BuildContext context, AnumLocalizations l, DuplicatesController c) {
    final theme = Theme.of(context).textTheme;
    final children = <Widget>[
      Text(l.t('contacts.dup.intro'), style: theme.bodyMedium),
      Text(l.f('contacts.dup.count', {'count': c.total}),
          style: theme.titleSmall),
      if (!c.canWrite)
        Text(l.t('contacts.dup.readOnly'), style: theme.bodySmall),
      if (c.items.isEmpty)
        AnumEmpty(
            icon: Icons.people_outline,
            title: l.t('contacts.dup.emptyTitle'),
            message: l.t('contacts.dup.emptyText')),
      for (final proposal in c.items)
        AnumSurface(
          key: Key('proposal-${proposal.id}'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(
                l.f('contacts.dup.shared', {
                  'reason': l.t('contacts.dup.${proposal.reason}'),
                  'value': proposal.matchedValue,
                }),
                style: theme.titleSmall),
            const SizedBox(height: AnumSpacing.xs),
            for (final person in [proposal.a, proposal.b])
              if (person != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(person.name, style: theme.titleMedium),
                        if (person.organisation.isNotEmpty)
                          Text(person.organisation, style: theme.bodySmall),
                        for (final i in [...person.phones, ...person.emails])
                          Directionality(
                              textDirection: TextDirection.ltr,
                              child: Text(i.value, style: theme.bodySmall)),
                        if (c.canWrite)
                          Tooltip(
                            message: l.t('contacts.dup.keepHint'),
                            child: OutlinedButton(
                                key: Key('keep-${proposal.id}-${person.id}'),
                                onPressed: c.busy
                                    ? null
                                    : () =>
                                        _report(c.keep(proposal, person, l)),
                                child: Text(l.f('contacts.dup.keep',
                                    {'name': person.name}))),
                          ),
                      ]),
                ),
            if (proposal.a == null || proposal.b == null)
              Text(l.t('contacts.dup.gone'), style: theme.bodySmall),
            if (c.canWrite)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(
                    key: Key('dismiss-${proposal.id}'),
                    onPressed:
                        c.busy ? null : () => _report(c.dismiss(proposal, l)),
                    child: Text(l.t('contacts.dup.dismiss'))),
              ),
          ]),
        ),
      if (c.hasMore)
        OutlinedButton(
            onPressed: c.loadingMore ? null : () => unawaited(c.more()),
            child: Text(l.t('contacts.more'))),
      if (c.moreError != null)
        Text(l.f('contacts.moreFailed', {'message': c.moreError!}),
            style: TextStyle(color: context.palette.stop)),
    ];
    return RefreshIndicator(
      onRefresh: c.load,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          for (final child in children)
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 760),
                child: Padding(
                    padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                    child: child),
              ),
            ),
        ],
      ),
    );
  }
}
