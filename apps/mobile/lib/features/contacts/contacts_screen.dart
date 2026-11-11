import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'contacts_controller.dart';
import 'contacts_models.dart';
import 'contacts_repository.dart';

/// The Contacts directory (docs/contacts.md#mobile): everyone once over the
/// Phone calls, Business and WhatsApp contacts. Search, the list 25 at a time,
/// and a person page with the channel facts and the do-not-contact controls.
/// Possible duplicates, merging, editing and export stay on the web.
class ContactsScreen extends StatefulWidget {
  const ContactsScreen(
      {required this.controller, required this.repository, super.key});
  final ContactsController controller;
  final ContactsRepository repository;

  @override
  State<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends State<ContactsScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase != ContactsPhase.ready) {
      unawaited(widget.controller.load());
    }
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final c = widget.controller;
    return AnimatedBuilder(
      animation: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('contacts.title'))),
        body: SafeArea(
          child: switch (c.phase) {
            ContactsPhase.loading => Center(
                child: Semantics(
                    label: l.t('contacts.loading'),
                    child: const CircularProgressIndicator())),
            ContactsPhase.denied => _centered(AnumEmpty(
                icon: Icons.lock_outline,
                title: l.t('contacts.title'),
                message: c.error ?? l.t('contacts.denied'))),
            ContactsPhase.error => _centered(AnumEmpty(
                icon: Icons.cloud_off_outlined,
                title: l.f('contacts.loadFailed', {'message': c.error ?? ''}),
                message: '',
                action: l.t('contacts.retry'),
                onAction: () => unawaited(c.load()))),
            ContactsPhase.ready => _list(context, l, c),
          },
        ),
      ),
    );
  }

  Widget _centered(Widget child) => Center(
      child:
          Padding(padding: const EdgeInsets.all(AnumSpacing.lg), child: child));

  Widget _list(
      BuildContext context, AnumLocalizations l, ContactsController c) {
    final filtered = c.query.trim().isNotEmpty;
    final children = <Widget>[
      Text(l.t('contacts.intro'),
          style: Theme.of(context).textTheme.bodyMedium),
      Text(
          l.f('contacts.figures', {
            'people': c.people,
            'marked': c.marked,
            'proposals': c.openProposals
          }),
          style: Theme.of(context).textTheme.bodySmall),
      TextField(
        key: const Key('contacts-search'),
        controller: _search,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          labelText: l.t('contacts.search'),
          prefixIcon: const Icon(Icons.search),
          suffixIcon: filtered
              ? IconButton(
                  tooltip: l.t('contacts.dnc.cancel'),
                  icon: const Icon(Icons.close),
                  onPressed: () {
                    _search.clear();
                    unawaited(c.search(''));
                  })
              : null,
        ),
        onSubmitted: (value) => unawaited(c.search(value)),
      ),
      Text(
          l.f(filtered ? 'contacts.countMatch' : 'contacts.count',
              {'count': c.total}),
          style: Theme.of(context).textTheme.titleSmall),
      if (c.items.isEmpty)
        AnumEmpty(
          icon: Icons.people_outline,
          title: l.t(filtered ? 'contacts.noMatch' : 'contacts.empty'),
          message:
              l.t(filtered ? 'contacts.noMatchText' : 'contacts.emptyText'),
        ),
      for (final person in c.items)
        AnumSurface(
          key: Key('person-${person.id}'),
          semanticLabel: person.name,
          onTap: () => _open(context, person),
          child: _PersonRow(person: person),
        ),
      if (c.hasMore)
        OutlinedButton(
          onPressed: c.loadingMore ? null : () => unawaited(c.more()),
          child: Text(l.t('contacts.more')),
        ),
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

  Future<void> _open(BuildContext context, Person person) async {
    final controller = PersonController(widget.repository, person.id);
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => PersonScreen(controller: controller)));
    controller.dispose();
    unawaited(widget.controller.load());
  }
}

String channelLabel(AnumLocalizations l, String channel) =>
    l.t('contacts.channel.$channel');

class _PersonRow extends StatelessWidget {
  const _PersonRow({required this.person});
  final Person person;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final theme = Theme.of(context).textTheme;
    final blocked = person.blockedChannels;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(person.name, style: theme.titleMedium),
      if (person.organisation.isNotEmpty)
        Text(person.organisation, style: theme.bodySmall),
      for (final i in [...person.phones, ...person.emails])
        Directionality(
            textDirection: TextDirection.ltr,
            child: Text(i.value, style: theme.bodyMedium)),
      const SizedBox(height: AnumSpacing.xxs),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        if (person.sources.isEmpty)
          AnumPill(label: l.t('contacts.source.${person.source}')),
        for (final kind in person.sources)
          AnumPill(label: l.t('contacts.link.$kind')),
        if (blocked.isNotEmpty)
          AnumPill(
              tone: AnumTone.stop,
              icon: Icons.notifications_off_outlined,
              label: l.f('contacts.dncShort', {
                'channels': blocked.map((c) => channelLabel(l, c)).join(', ')
              })),
      ]),
    ]);
  }
}

/// One person: numbers and addresses, linked records, the per-channel facts
/// and the do-not-contact controls.
class PersonScreen extends StatefulWidget {
  const PersonScreen({required this.controller, super.key});
  final PersonController controller;

  @override
  State<PersonScreen> createState() => _PersonScreenState();
}

class _PersonScreenState extends State<PersonScreen> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.load());
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final c = widget.controller;
    return AnimatedBuilder(
      animation: c,
      builder: (context, _) {
        final detail = c.detail;
        return Scaffold(
          appBar:
              AppBar(title: Text(detail?.person.name ?? l.t('contacts.title'))),
          body: SafeArea(
            child: switch (c.phase) {
              ContactsPhase.loading => Center(
                  child: Semantics(
                      label: l.t('contacts.person.loading'),
                      child: const CircularProgressIndicator())),
              ContactsPhase.denied => Center(
                  child: AnumEmpty(
                      icon: Icons.lock_outline,
                      title: l.t('contacts.title'),
                      message: c.error ?? l.t('contacts.denied'))),
              ContactsPhase.error => Center(
                  child: AnumEmpty(
                      icon: Icons.cloud_off_outlined,
                      title: l
                          .f('contacts.loadFailed', {'message': c.error ?? ''}),
                      message: '',
                      action: l.t('contacts.retry'),
                      onAction: () => unawaited(c.load()))),
              ContactsPhase.ready => _body(context, l, c, detail!),
            },
          ),
        );
      },
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l, PersonController c,
      PersonDetail d) {
    final theme = Theme.of(context).textTheme;
    final p = d.person;
    final flash = c.flash;
    final children = <Widget>[
      if (flash != null)
        Semantics(
          liveRegion: true,
          child: Text(
              l.f(
                  flash.startsWith('cleared')
                      ? 'contacts.dnc.cleared'
                      : 'contacts.dnc.marked',
                  {'channel': channelLabel(l, flash.split(':').last)}),
              style: theme.bodyMedium),
        ),
      _PersonRow(person: p),
      if (p.notes.isNotEmpty)
        AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('contacts.person.notes'), style: theme.titleSmall),
          Text(p.notes),
        ])),
      Text(l.t('contacts.person.links'), style: theme.titleMedium),
      if (d.links.isEmpty) Text(l.t('contacts.person.noLinks')),
      for (final link in d.links)
        AnumRow(
          icon: switch (link.kind) {
            'call_contact' => Icons.phone_outlined,
            'business_contact' => Icons.handshake_outlined,
            _ => Icons.chat_outlined,
          },
          title: l.t('contacts.link.${link.kind}'),
          subtitle: link.found ? link.title : l.t('contacts.link.gone'),
          detail: link.found ? link.detail : null,
        ),
      Text(l.t('contacts.person.channels'), style: theme.titleMedium),
      for (final channel in d.channels)
        AnumSurface(
          key: Key('channel-${channel.channel}'),
          accent: channel.canContact ? null : context.palette.stop,
          child: _ChannelCard(summary: channel, detail: d, controller: c),
        ),
      Text(l.t('contacts.person.webOnly'), style: theme.bodySmall),
    ];
    // One scroll view with every part built: the page is bounded (a handful of cards).
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AnumSpacing.md),
      child: Column(children: [
        for (final child in children)
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                  child: child),
            ),
          ),
      ]),
    );
  }
}

class _ChannelCard extends StatelessWidget {
  const _ChannelCard(
      {required this.summary, required this.detail, required this.controller});
  final ChannelSummary summary;
  final PersonDetail detail;
  final PersonController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final theme = Theme.of(context).textTheme;
    final mark = summary.mark;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      // Wraps under the name on a narrow screen or with large text.
      Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(channelLabel(l, summary.channel), style: theme.titleMedium),
            AnumPill(
                tone: summary.canContact ? AnumTone.ok : AnumTone.stop,
                label: l.t(summary.canContact
                    ? 'contacts.person.can'
                    : 'contacts.person.cannot')),
          ]),
      if (summary.identifiers.isEmpty)
        Text(l.t('contacts.person.none'), style: theme.bodySmall),
      for (final value in summary.identifiers)
        Directionality(textDirection: TextDirection.ltr, child: Text(value)),
      for (final fact in summary.facts)
        Text(
          _factText(l, fact),
          style: theme.bodySmall
              ?.copyWith(color: fact.bad ? context.palette.stop : null),
        ),
      if (mark != null)
        Text(
            l.f('contacts.person.dncLine', {
              'channel': channelLabel(l, mark.channel),
              'reason': mark.reason,
              'who': mark.setBy
            }),
            style: theme.bodySmall?.copyWith(color: context.palette.stop)),
      const SizedBox(height: AnumSpacing.xs),
      if (mark == null && detail.canWrite)
        OutlinedButton.icon(
          key: Key('mark-${summary.channel}'),
          onPressed: controller.busy
              ? null
              : () => _ask(context, l, clear: false, channel: summary.channel),
          icon: const Icon(Icons.notifications_off_outlined),
          label: Text(l.t('contacts.person.mark')),
        ),
      if (mark != null && detail.canManage)
        OutlinedButton.icon(
          key: Key('clear-${summary.channel}'),
          onPressed: controller.busy
              ? null
              : () => _ask(context, l, clear: true, channel: mark.channel),
          icon: const Icon(Icons.check),
          label: Text(l.t('contacts.person.clear')),
        ),
      if (mark != null && !detail.canManage)
        Text(l.t('contacts.person.ownersOnly'), style: theme.bodySmall),
    ]);
  }

  String _factText(AnumLocalizations l, ConsentFact fact) {
    final key = 'contacts.fact.${fact.source}.${fact.state}';
    final text = l.f(key, {'detail': fact.detail});
    return text == key ? '${fact.source}: ${fact.state}' : text;
  }

  Future<void> _ask(BuildContext context, AnumLocalizations l,
      {required bool clear, required String channel}) async {
    final result = await showDialog<({String channel, String reason})>(
      context: context,
      builder: (_) => _DoNotContactDialog(clear: clear, channel: channel),
    );
    if (result == null) return;
    final problem =
        await controller.setMark(result.channel, result.reason, clear: clear);
    if (problem != null && context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(problem)));
    }
  }
}

class _DoNotContactDialog extends StatefulWidget {
  const _DoNotContactDialog({required this.clear, required this.channel});
  final bool clear;
  final String channel;

  @override
  State<_DoNotContactDialog> createState() => _DoNotContactDialogState();
}

class _DoNotContactDialogState extends State<_DoNotContactDialog> {
  late String _channel = widget.channel;
  final _reason = TextEditingController();
  String? _problem;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return AlertDialog(
      title: Text(widget.clear
          ? l.f('contacts.dnc.clearTitle',
              {'channel': channelLabel(l, widget.channel)})
          : l.t('contacts.dnc.title')),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(l.t(
              widget.clear ? 'contacts.dnc.clearBody' : 'contacts.dnc.body')),
          const SizedBox(height: AnumSpacing.sm),
          if (!widget.clear)
            DropdownButtonFormField<String>(
              key: const Key('dnc-channel'),
              initialValue: _channel,
              decoration:
                  InputDecoration(labelText: l.t('contacts.dnc.channel')),
              items: [
                for (final c in contactChannels)
                  DropdownMenuItem(value: c, child: Text(channelLabel(l, c))),
              ],
              onChanged: (v) => setState(() => _channel = v ?? _channel),
            ),
          TextField(
            key: const Key('dnc-reason'),
            controller: _reason,
            maxLength: 300,
            decoration: InputDecoration(
                labelText: l.t('contacts.dnc.reason'), errorText: _problem),
          ),
        ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: Text(l.t('contacts.dnc.cancel'))),
        FilledButton(
          key: const Key('dnc-confirm'),
          onPressed: () {
            if (reasonProblem(_reason.text) != null) {
              setState(() => _problem = l.t('contacts.dnc.reasonProblem'));
              return;
            }
            Navigator.of(context)
                .pop((channel: _channel, reason: _reason.text.trim()));
          },
          child: Text(l.t(widget.clear
              ? 'contacts.dnc.clearConfirm'
              : 'contacts.dnc.confirm')),
        ),
      ],
    );
  }
}
