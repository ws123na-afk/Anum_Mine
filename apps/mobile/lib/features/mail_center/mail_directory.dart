import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../workspace/workspace_models.dart';
import 'mail_automation.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';
import 'mail_center_repository.dart';
import 'mail_center_screens.dart';
import 'mail_directory_models.dart';

/// The person's address book, a mailbox's shares and its sender identities
/// on the phone (docs/email.md#email-center): the same `/api/v1/mail` routes
/// as the web's Contacts drawer and mailbox settings.
class MailDirectoryController extends ChangeNotifier with MailNoticeSource {
  MailDirectoryController(this.repository);
  final MailCenterRepository repository;

  String? busy;

  // -- contacts --------------------------------------------------------------

  MailPhase contactsPhase = MailPhase.idle;
  String? contactsError;
  List<MailContact> contacts = const [];
  int contactsTotal = 0;
  String? nextCursor;
  bool loadingMore = false;
  String contactQuery = '';

  Future<void> loadContacts({String? query}) async {
    if (query != null) contactQuery = query;
    contactsPhase = MailPhase.loading;
    contactsError = null;
    notifyListeners();
    try {
      final page = await repository.contacts(query: contactQuery);
      contacts = page.items;
      contactsTotal = page.total;
      nextCursor = page.nextCursor;
      contactsPhase = MailPhase.ready;
    } on Object catch (e) {
      contactsPhase = phaseFor(e);
      contactsError = describe(e);
    }
    notifyListeners();
  }

  Future<void> loadMoreContacts() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page =
          await repository.contacts(query: contactQuery, cursor: cursor);
      contacts = [...contacts, ...page.items];
      nextCursor = page.nextCursor;
    } on Object catch (e) {
      say(describe(e), error: true);
    }
    loadingMore = false;
    notifyListeners();
  }

  /// Adds a contact or saves [existing]; true when the API accepted it.
  Future<bool> saveContact(MailContact? existing, MailContactEdit edit) =>
      _run('contact', () async {
        if (existing == null) {
          await repository.createContact(edit);
          say('mc.c.added');
        } else {
          await repository.updateContact(existing.id, edit);
          say('mc.c.saved');
        }
        await loadContacts();
      });

  Future<bool> deleteContact(MailContact contact) => _run('contact', () async {
        await repository.deleteContact(contact.id);
        contacts = [
          for (final c in contacts)
            if (c.id != contact.id) c
        ];
        contactsTotal = contactsTotal > 0 ? contactsTotal - 1 : 0;
        say('mc.c.deleted');
      });

  /// Contacts matching what is typed in To, Cc or Bcc (at most 5); empty on
  /// any failure, so typing an address never stops on the address book.
  Future<List<MailContact>> suggest(String text) async {
    final query = text.trim();
    if (query.length < 2) return const [];
    try {
      return (await repository.contacts(query: query, limit: 5)).items;
    } on Object {
      return const [];
    }
  }

  // -- one mailbox: shares and identities --------------------------------------

  MailAccount? account;
  MailPhase sharesPhase = MailPhase.idle;
  String? sharesError;
  List<MailGrant> grants = const [];
  List<MailShareCandidate> candidates = const [];
  MailPhase identitiesPhase = MailPhase.idle;
  String? identitiesError;
  List<MailIdentity> identities = const [];

  Future<void> openAccount(MailAccount target) async {
    account = target;
    grants = const [];
    candidates = const [];
    identities = const [];
    settings = null;
    rules = const [];
    settingsStale = false;
    await Future.wait([
      loadSettings(),
      if (target.isOwn) ...[loadShares(), loadIdentities(), loadRules()],
    ]);
  }

  // -- retention and sync ---------------------------------------------------

  MailPhase settingsPhase = MailPhase.idle;
  String? settingsError;
  MailSettings? settings;

  /// Someone changed the settings after they were shown: they were read
  /// again and the person picks again.
  bool settingsStale = false;

  Future<void> loadSettings() async {
    final open = account;
    if (open == null) return;
    settingsPhase = MailPhase.loading;
    settingsError = null;
    notifyListeners();
    try {
      settings = await repository.settings(open.id);
      settingsPhase = MailPhase.ready;
    } on Object catch (e) {
      settingsPhase = phaseFor(e);
      settingsError = describe(e);
    }
    notifyListeners();
  }

  /// Asks to change to [edit]; nothing changes until an owner approves.
  Future<bool> askSettings(MailSettingsEdit edit) async {
    final current = settings, open = account;
    if (current == null || open == null) return false;
    settingsStale = false;
    return _run('settings', () async {
      try {
        await repository.changeSettings(open.id, edit, current.version);
        say('mc.rs.asked');
      } on ApiException catch (e) {
        if (e.statusCode == 409) {
          settingsStale = true;
          await loadSettings();
        }
        rethrow;
      }
      await loadSettings();
    });
  }

  Future<bool> withdrawSettings() => _run('settings', () async {
        await repository.withdrawSettings(account!.id);
        say('mc.rs.withdrawn');
        await loadSettings();
      });

  /// Approves a settings change or a rule with its hash, then reads both
  /// again (the API refuses a stale approval and says why).
  Future<bool> approve(WorkspaceApproval approval) => _run('approve', () async {
        final result = await repository.approve(approval);
        say(result ?? 'mc.n.approved');
        await Future.wait([loadSettings(), loadRules()]);
      });

  // -- forwarding rules -------------------------------------------------------

  MailPhase rulesPhase = MailPhase.idle;
  String? rulesError;
  List<MailRule> rules = const [];

  Future<void> loadRules() async {
    final open = account;
    if (open == null || !open.isOwn) return;
    rulesPhase = MailPhase.loading;
    rulesError = null;
    notifyListeners();
    try {
      rules = await repository.rules(open.id);
      rulesPhase = MailPhase.ready;
    } on Object catch (e) {
      rulesPhase = phaseFor(e);
      rulesError = describe(e);
    }
    notifyListeners();
  }

  Future<bool> saveRule(MailRule? existing, MailRuleEdit edit) =>
      _run('rule', () async {
        if (existing == null) {
          await repository.createRule(account!.id, edit);
        } else {
          await repository.updateRule(existing.id, edit);
        }
        say('mc.fr.asked');
        await loadRules();
      });

  Future<bool> pauseRule(MailRule rule) => _run('rule', () async {
        await repository.pauseRule(rule.id);
        say('mc.fr.paused');
        await loadRules();
      });

  Future<bool> resumeRule(MailRule rule) => _run('rule', () async {
        await repository.resumeRule(rule.id);
        say('mc.fr.asked');
        await loadRules();
      });

  Future<bool> deleteRule(MailRule rule) => _run('rule', () async {
        await repository.deleteRule(rule.id);
        say('mc.fr.removed');
        await loadRules();
      });

  Future<void> loadShares() async {
    final open = account;
    if (open == null) return;
    sharesPhase = MailPhase.loading;
    sharesError = null;
    notifyListeners();
    try {
      final found = await Future.wait([
        repository.grants(open.id),
        repository.shareCandidates(open.id),
      ]);
      grants = found[0] as List<MailGrant>;
      candidates = found[1] as List<MailShareCandidate>;
      sharesPhase = MailPhase.ready;
    } on Object catch (e) {
      sharesPhase = phaseFor(e);
      sharesError = describe(e);
    }
    notifyListeners();
  }

  Future<void> loadIdentities() async {
    final open = account;
    if (open == null) return;
    identitiesPhase = MailPhase.loading;
    identitiesError = null;
    notifyListeners();
    try {
      identities = await repository.identities(open.id);
      identitiesPhase = MailPhase.ready;
    } on Object catch (e) {
      identitiesPhase = phaseFor(e);
      identitiesError = describe(e);
    }
    notifyListeners();
  }

  Future<bool> share(MailShareCandidate person, String access) =>
      _run('share', () async {
        await repository.share(account!.id, person.userId, access);
        say('mc.sh.shared');
        await loadShares();
      });

  Future<bool> changeAccess(MailGrant grant, String access) =>
      _run('share', () async {
        await repository.updateGrant(grant.id, access);
        say('mc.sh.changed');
        await loadShares();
      });

  Future<bool> stopSharing(MailGrant grant) => _run('share', () async {
        await repository.removeGrant(grant.id);
        say('mc.sh.stopped');
        await loadShares();
      });

  Future<bool> saveIdentity(MailIdentity? existing, MailIdentityEdit edit) =>
      _run('identity', () async {
        if (existing == null) {
          await repository.createIdentity(account!.id, edit);
          say('mc.id.added');
        } else {
          await repository.updateIdentity(existing.id, edit);
          say('mc.id.saved');
        }
        await loadIdentities();
      });

  Future<bool> deleteIdentity(MailIdentity identity) =>
      _run('identity', () async {
        await repository.deleteIdentity(identity.id);
        say('mc.id.deleted');
        await loadIdentities();
      });

  Future<bool> _run(String kind, Future<void> Function() action) async {
    busy = kind;
    notice = null;
    notifyListeners();
    var ok = false;
    try {
      await action();
      ok = true;
    } on Object catch (e) {
      say(describe(e), error: true);
    }
    busy = null;
    notifyListeners();
    return ok;
  }

  void reset() {
    contactsPhase = sharesPhase =
        identitiesPhase = settingsPhase = rulesPhase = MailPhase.idle;
    settings = null;
    rules = const [];
    settingsError = rulesError = null;
    settingsStale = false;
    contacts = const [];
    grants = const [];
    candidates = const [];
    identities = const [];
    account = null;
    nextCursor = contactsError = sharesError = identitiesError = null;
    contactQuery = '';
    notifyListeners();
  }
}

Future<bool> _confirm(BuildContext context, String title, String text,
    {required String yes}) async {
  final l = AnumLocalizations.maybeOf(context);
  final answer = await showDialog<bool>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(title),
      content: Text(text),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(l.t('mc.cancel'))),
        FilledButton(
            onPressed: () => Navigator.pop(dialog, true), child: Text(yes)),
      ],
    ),
  );
  return answer == true;
}

final _emailPattern = RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$');

// ============================================================================
// Contacts
// ============================================================================

class MailContactsScreen extends StatefulWidget {
  const MailContactsScreen({super.key, required this.controller});
  final MailDirectoryController controller;

  @override
  State<MailContactsScreen> createState() => _MailContactsScreenState();
}

class _MailContactsScreenState extends State<MailContactsScreen> {
  final _search = TextEditingController();
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    _search.text = widget.controller.contactQuery;
    Future.microtask(widget.controller.loadContacts);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  void _typed(String text) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300),
        () => widget.controller.loadContacts(query: text));
  }

  Future<void> _edit(MailContact? contact) => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) =>
            _ContactSheet(controller: widget.controller, contact: contact),
      );

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('mc.c.title'))),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _edit(null),
        icon: const Icon(Icons.person_add_alt),
        label: Text(l.t('mc.c.add')),
      ),
      body: MailNotices(
        controller: c,
        child: ListenableBuilder(
          listenable: c,
          builder: (context, _) => mailPage(_body(context, l, c)),
        ),
      ),
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailDirectoryController c) {
    final state = stateFor(context, c.contactsPhase, c.contactsError,
        onRetry: c.loadContacts, hasData: c.contacts.isNotEmpty);
    if (state != null) return state;
    final searching = c.contactQuery.trim().isNotEmpty;
    return RefreshIndicator(
      onRefresh: c.loadContacts,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.md, AnumSpacing.md, AnumSpacing.md, 96),
        children: [
          TextField(
            controller: _search,
            onChanged: _typed,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              labelText: l.t('mc.c.search'),
              prefixIcon: const Icon(Icons.search),
            ),
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.contactsPhase == MailPhase.loading)
            const LinearProgressIndicator(),
          if (c.contacts.isEmpty && c.contactsPhase == MailPhase.ready)
            MailState(
              icon: searching ? Icons.search_off : Icons.contacts_outlined,
              title: searching ? l.t('mc.c.noMatch') : l.t('mc.c.empty'),
              text: searching ? null : l.t('mc.c.emptyText'),
              action: searching
                  ? OutlinedButton(
                      onPressed: () {
                        _search.clear();
                        c.loadContacts(query: '');
                      },
                      child: Text(l.t('mc.clear')),
                    )
                  : null,
            ),
          for (final contact in c.contacts)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: ListTile(
                leading: const Icon(Icons.person_outline),
                title: Text(contact.label,
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(
                  [
                    if (contact.name.trim().isNotEmpty) contact.email,
                    if (contact.company.trim().isNotEmpty) contact.company,
                  ].join(' · '),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () => _edit(contact),
                trailing: IconButton(
                  tooltip: l.t('mc.c.delete'),
                  icon: const Icon(Icons.delete_outline),
                  onPressed: c.busy != null
                      ? null
                      : () async {
                          if (await _confirm(context, l.t('mc.c.deleteTitle'),
                              l.f('mc.c.deleteText', {'name': contact.label}),
                              yes: l.t('mc.delete'))) {
                            await c.deleteContact(contact);
                          }
                        },
                ),
              ),
            ),
          if (c.contacts.isNotEmpty)
            Text(
                l.f('mc.shown',
                    {'shown': c.contacts.length, 'total': c.contactsTotal}),
                style: Theme.of(context).textTheme.bodySmall),
          if (c.nextCursor != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.sm),
              child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMoreContacts,
                child: Text(l.t('mc.loadMore')),
              ),
            ),
        ],
      ),
    );
  }
}

class _ContactSheet extends StatefulWidget {
  const _ContactSheet({required this.controller, this.contact});
  final MailDirectoryController controller;
  final MailContact? contact;

  @override
  State<_ContactSheet> createState() => _ContactSheetState();
}

class _ContactSheetState extends State<_ContactSheet> {
  final _form = GlobalKey<FormState>();
  late final _name = TextEditingController(text: widget.contact?.name);
  late final _email = TextEditingController(text: widget.contact?.email);
  late final _company = TextEditingController(text: widget.contact?.company);
  late final _phone = TextEditingController(text: widget.contact?.phone);
  late final _notes = TextEditingController(text: widget.contact?.notes);

  @override
  void dispose() {
    for (final c in [_name, _email, _company, _phone, _notes]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final ok = await widget.controller.saveContact(
        widget.contact,
        MailContactEdit(
          email: _email.text,
          name: _name.text,
          company: _company.text,
          phone: _phone.text,
          notes: _notes.text,
        ));
    if (ok && mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Sheet(
      title: widget.contact == null ? l.t('mc.c.add') : l.t('mc.c.edit'),
      listenable: widget.controller,
      busy: () => widget.controller.busy != null,
      onSave: _save,
      child: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextFormField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              maxLength: 320,
              decoration: InputDecoration(labelText: l.t('mc.c.email')),
              validator: (v) => _emailPattern.hasMatch((v ?? '').trim())
                  ? null
                  : l.t('mc.addressInvalid'),
            ),
            TextFormField(
              controller: _name,
              maxLength: 200,
              decoration: InputDecoration(labelText: l.t('mc.c.name')),
            ),
            TextFormField(
              controller: _company,
              maxLength: 200,
              decoration: InputDecoration(labelText: l.t('mc.c.company')),
            ),
            TextFormField(
              controller: _phone,
              maxLength: 60,
              keyboardType: TextInputType.phone,
              decoration: InputDecoration(labelText: l.t('mc.c.phone')),
            ),
            TextFormField(
              controller: _notes,
              maxLength: 2000,
              minLines: 2,
              maxLines: 5,
              decoration: InputDecoration(labelText: l.t('mc.c.notes')),
            ),
          ],
        ),
      ),
    );
  }
}

/// A bottom sheet with a title, a form and Cancel / Save.
class _Sheet extends StatelessWidget {
  const _Sheet({
    required this.title,
    required this.child,
    required this.listenable,
    required this.busy,
    required this.onSave,
  });
  final String title;
  final Widget child;
  final Listenable listenable;
  final bool Function() busy;
  final VoidCallback onSave;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom,
          left: AnumSpacing.md,
          right: AnumSpacing.md,
          top: AnumSpacing.md),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: AnumSpacing.sm),
            child,
            const SizedBox(height: AnumSpacing.sm),
            ListenableBuilder(
              listenable: listenable,
              builder: (context, _) => Wrap(
                alignment: WrapAlignment.end,
                spacing: AnumSpacing.xs,
                children: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: Text(l.t('mc.cancel'))),
                  FilledButton(
                      onPressed: busy() ? null : onSave,
                      child: Text(l.t('mc.save'))),
                ],
              ),
            ),
            const SizedBox(height: AnumSpacing.md),
          ],
        ),
      ),
    );
  }
}

// ============================================================================
// One mailbox: sharing and send as (owner only)
// ============================================================================

class MailAccountSettingsScreen extends StatefulWidget {
  const MailAccountSettingsScreen(
      {super.key, required this.controller, required this.account});
  final MailDirectoryController controller;
  final MailAccount account;

  @override
  State<MailAccountSettingsScreen> createState() =>
      _MailAccountSettingsScreenState();
}

class _MailAccountSettingsScreenState extends State<MailAccountSettingsScreen> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => widget.controller.openAccount(widget.account));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.account.address, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(
            tooltip: l.t('refresh'),
            onPressed: () => c.openAccount(widget.account),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: MailNotices(
        controller: c,
        child: ListenableBuilder(
          listenable: c,
          builder: (context, _) => mailPage(ListView(
            padding: const EdgeInsets.all(AnumSpacing.md),
            children: [
              if (widget.account.isOwn) ...[
                _section(context, l.t('mc.sh.title'), l.t('mc.sh.text')),
                ..._shares(context, l, c),
                const SizedBox(height: AnumSpacing.lg),
                _section(context, l.t('mc.id.title'), l.t('mc.id.text')),
                ..._identities(context, l, c),
                const SizedBox(height: AnumSpacing.lg),
              ],
              _section(context, l.t('mc.rs.title'), l.t('mc.rs.text')),
              MailRetentionSection(controller: c),
              const SizedBox(height: AnumSpacing.lg),
              _section(context, l.t('mc.fr.title'), l.t('mc.fr.text')),
              if (widget.account.isOwn)
                MailRulesSection(controller: c)
              else
                Text(l.t('mc.fr.ownerOnly')),
            ],
          )),
        ),
      ),
    );
  }

  Widget _section(BuildContext context, String title, String text) => Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              header: true,
              child:
                  Text(title, style: Theme.of(context).textTheme.titleMedium),
            ),
            Text(text, style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      );

  List<Widget> _shares(
      BuildContext context, AnumLocalizations l, MailDirectoryController c) {
    final state = stateFor(context, c.sharesPhase, c.sharesError,
        onRetry: c.loadShares, hasData: c.grants.isNotEmpty);
    if (state != null) return [state];
    final shared = {for (final g in c.grants) g.granteeUserId};
    final others = [
      for (final p in c.candidates)
        if (!shared.contains(p.userId)) p
    ];
    return [
      if (c.grants.isEmpty) Text(l.t('mc.sh.none')),
      for (final grant in c.grants)
        Card(
          margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
          child: Padding(
            padding: const EdgeInsets.all(AnumSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(grant.label, maxLines: 1, overflow: TextOverflow.ellipsis),
                const SizedBox(height: AnumSpacing.xs),
                Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xs,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    SegmentedButton<String>(
                      segments: [
                        ButtonSegment(
                            value: 'read', label: Text(l.t('mc.sh.read'))),
                        ButtonSegment(
                            value: 'send', label: Text(l.t('mc.sh.send'))),
                      ],
                      selected: {grant.access},
                      onSelectionChanged: c.busy != null
                          ? null
                          : (value) => c.changeAccess(grant, value.first),
                    ),
                    TextButton.icon(
                      onPressed: c.busy != null
                          ? null
                          : () async {
                              if (await _confirm(
                                  context,
                                  l.t('mc.sh.stopTitle'),
                                  l.f('mc.sh.stopText', {'name': grant.label}),
                                  yes: l.t('mc.sh.stop'))) {
                                await c.stopSharing(grant);
                              }
                            },
                      icon: const Icon(Icons.person_remove_outlined),
                      label: Text(l.t('mc.sh.stop')),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      const SizedBox(height: AnumSpacing.xs),
      if (others.isEmpty)
        Text(l.t('mc.sh.noCandidates'),
            style: Theme.of(context).textTheme.bodySmall)
      else
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            onPressed:
                c.busy != null ? null : () => _shareWith(context, others),
            icon: const Icon(Icons.person_add_alt),
            label: Text(l.t('mc.sh.add')),
          ),
        ),
    ];
  }

  Future<void> _shareWith(
      BuildContext context, List<MailShareCandidate> people) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (_) =>
          _ShareSheet(controller: widget.controller, people: people),
    );
  }

  List<Widget> _identities(
      BuildContext context, AnumLocalizations l, MailDirectoryController c) {
    final state = stateFor(context, c.identitiesPhase, c.identitiesError,
        onRetry: c.loadIdentities, hasData: c.identities.isNotEmpty);
    if (state != null) return [state];
    return [
      if (c.identities.isEmpty) Text(l.t('mc.id.none')),
      for (final identity in c.identities)
        Card(
          margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
          child: ListTile(
            leading: Icon(
                identity.isDefault ? Icons.star : Icons.alternate_email,
                semanticLabel:
                    identity.isDefault ? l.t('mc.id.default') : null),
            title: Text(identity.label,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text(
              [
                if (identity.isDefault) l.t('mc.id.default'),
                identity.signature.trim().isEmpty
                    ? l.t('mc.id.noSignature')
                    : identity.signature.trim().split('\n').first,
              ].join(' · '),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            onTap: () => _editIdentity(context, identity),
            trailing: IconButton(
              tooltip: l.t('mc.id.delete'),
              icon: const Icon(Icons.delete_outline),
              onPressed: c.busy != null
                  ? null
                  : () async {
                      if (await _confirm(
                          context,
                          l.t('mc.id.deleteTitle'),
                          l.f('mc.id.deleteText',
                              {'address': identity.address}),
                          yes: l.t('mc.delete'))) {
                        await c.deleteIdentity(identity);
                      }
                    },
            ),
          ),
        ),
      if (c.identities.length < 20)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            onPressed:
                c.busy != null ? null : () => _editIdentity(context, null),
            icon: const Icon(Icons.add),
            label: Text(l.t('mc.id.add')),
          ),
        ),
    ];
  }

  Future<void> _editIdentity(BuildContext context, MailIdentity? identity) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) =>
            _IdentitySheet(controller: widget.controller, identity: identity),
      );
}

class _ShareSheet extends StatefulWidget {
  const _ShareSheet({required this.controller, required this.people});
  final MailDirectoryController controller;
  final List<MailShareCandidate> people;

  @override
  State<_ShareSheet> createState() => _ShareSheetState();
}

class _ShareSheetState extends State<_ShareSheet> {
  MailShareCandidate? _person;
  String _access = 'read';

  Future<void> _save() async {
    final person = _person;
    if (person == null) return;
    if (await widget.controller.share(person, _access) && mounted) {
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Sheet(
      title: l.t('mc.sh.add'),
      listenable: widget.controller,
      busy: () => widget.controller.busy != null || _person == null,
      onSave: _save,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          DropdownButtonFormField<MailShareCandidate>(
            isExpanded: true,
            initialValue: _person,
            decoration: InputDecoration(labelText: l.t('mc.sh.person')),
            items: [
              for (final p in widget.people)
                DropdownMenuItem(
                  value: p,
                  child: Text('${p.label} (${l.t('mc.role.${p.role}')})',
                      overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: (value) => setState(() => _person = value),
          ),
          const SizedBox(height: AnumSpacing.sm),
          SegmentedButton<String>(
            segments: [
              ButtonSegment(value: 'read', label: Text(l.t('mc.sh.read'))),
              ButtonSegment(value: 'send', label: Text(l.t('mc.sh.send'))),
            ],
            selected: {_access},
            onSelectionChanged: (value) =>
                setState(() => _access = value.first),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t(_access == 'read' ? 'mc.sh.readText' : 'mc.sh.sendText'),
              style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}

class _IdentitySheet extends StatefulWidget {
  const _IdentitySheet({required this.controller, this.identity});
  final MailDirectoryController controller;
  final MailIdentity? identity;

  @override
  State<_IdentitySheet> createState() => _IdentitySheetState();
}

class _IdentitySheetState extends State<_IdentitySheet> {
  final _form = GlobalKey<FormState>();
  late final _address = TextEditingController(text: widget.identity?.address);
  late final _name = TextEditingController(text: widget.identity?.displayName);
  late final _signature =
      TextEditingController(text: widget.identity?.signature);
  late bool _default = widget.identity?.isDefault ?? false;

  @override
  void dispose() {
    for (final c in [_address, _name, _signature]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final ok = await widget.controller.saveIdentity(
        widget.identity,
        MailIdentityEdit(
          address: _address.text,
          displayName: _name.text,
          signature: _signature.text,
          isDefault: _default,
        ));
    if (ok && mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final adding = widget.identity == null;
    return _Sheet(
      title: adding ? l.t('mc.id.add') : l.t('mc.id.edit'),
      listenable: widget.controller,
      busy: () => widget.controller.busy != null,
      onSave: _save,
      child: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (adding)
              TextFormField(
                controller: _address,
                keyboardType: TextInputType.emailAddress,
                maxLength: 320,
                decoration: InputDecoration(
                    labelText: l.t('mc.id.address'),
                    helperText: l.t('mc.id.addressHint'),
                    helperMaxLines: 3),
                validator: (v) => _emailPattern.hasMatch((v ?? '').trim())
                    ? null
                    : l.t('mc.addressInvalid'),
              )
            else
              Text(widget.identity!.address),
            TextFormField(
              controller: _name,
              maxLength: 200,
              decoration: InputDecoration(labelText: l.t('mc.id.name')),
            ),
            TextFormField(
              controller: _signature,
              maxLength: 2000,
              minLines: 2,
              maxLines: 6,
              decoration: InputDecoration(
                  labelText: l.t('mc.id.signature'), alignLabelWithHint: true),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _default,
              onChanged: (value) => setState(() => _default = value),
              title: Text(l.t('mc.id.makeDefault')),
            ),
          ],
        ),
      ),
    );
  }
}

// ============================================================================
// Recipient field with contact suggestions
// ============================================================================

/// To, Cc or Bcc: comma separated addresses; while typing the last one, the
/// person's contacts that match are offered below the field.
class MailRecipientField extends StatefulWidget {
  const MailRecipientField({
    super.key,
    required this.controller,
    required this.label,
    required this.suggest,
    this.enabled = true,
    this.helperText,
    this.validator,
    this.suffixIcon,
  });
  final TextEditingController controller;
  final String label;
  final String? helperText;
  final bool enabled;
  final Future<List<MailContact>> Function(String text) suggest;
  final FormFieldValidator<String>? validator;
  final Widget? suffixIcon;

  @override
  State<MailRecipientField> createState() => _MailRecipientFieldState();
}

class _MailRecipientFieldState extends State<MailRecipientField> {
  Timer? _debounce;
  List<MailContact> _found = const [];
  int _asked = 0;

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  String get _last {
    final parts = widget.controller.text.split(RegExp(r'[,;]'));
    return parts.isEmpty ? '' : parts.last.trim();
  }

  void _changed(String _) {
    _debounce?.cancel();
    final text = _last;
    if (text.length < 2) {
      if (_found.isNotEmpty) setState(() => _found = const []);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 250), () async {
      final ask = ++_asked;
      final found = await widget.suggest(text);
      if (!mounted || ask != _asked) return;
      final already = MailDraftEdit.split(widget.controller.text)
          .map((a) => a.toLowerCase())
          .toSet();
      setState(() => _found = [
            for (final c in found)
              if (!already.contains(c.email.toLowerCase())) c
          ]);
    });
  }

  void _pick(MailContact contact) {
    final parts = widget.controller.text.split(RegExp(r'[,;]'));
    final kept = [
      for (final p in parts.take(parts.length - 1))
        if (p.trim().isNotEmpty) p.trim()
    ];
    final text = [...kept, contact.email].join(', ');
    widget.controller.value = TextEditingValue(
      text: '$text, ',
      selection: TextSelection.collapsed(offset: text.length + 2),
    );
    setState(() => _found = const []);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextFormField(
          controller: widget.controller,
          enabled: widget.enabled,
          keyboardType: TextInputType.emailAddress,
          onChanged: _changed,
          decoration: InputDecoration(
            labelText: widget.label,
            helperText: widget.helperText,
            helperMaxLines: 2,
            suffixIcon: widget.suffixIcon,
          ),
          validator: widget.validator,
        ),
        if (_found.isNotEmpty)
          Semantics(
            liveRegion: true,
            label: l.f('mc.c.suggestions', {'count': _found.length}),
            child: Card(
              margin: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Column(
                children: [
                  for (final contact in _found)
                    ListTile(
                      dense: true,
                      leading: const Icon(Icons.person_outline),
                      title: Text(contact.label,
                          maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: contact.name.trim().isEmpty
                          ? null
                          : Text(contact.email,
                              maxLines: 1, overflow: TextOverflow.ellipsis),
                      onTap: () => _pick(contact),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
