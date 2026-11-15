import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../contacts/contacts_models.dart';
import '../contacts/contacts_repository.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_loan_contacts_strings.dart';
import 'finance_personal_models.dart';

/// Lent and borrowed with people from Contacts on the phone
/// (docs/finance.md#people-from-contacts): pick the person from the Contacts
/// directory on the loan form (or add them there when the server allows),
/// and see each person's loans and repayments together. Contacts are read and
/// created only through the directory's own API, so its permissions apply; a
/// loan keeps just the contact's id and the name typed on it.

// -- models ------------------------------------------------------------------

/// One person's loans together (`FinanceLoanPerson`): by the Contacts person
/// when linked, else by the typed name.
class LoanPerson {
  const LoanPerson({
    required this.key,
    required this.name,
    required this.names,
    required this.loans,
    required this.openLoans,
    required this.overdue,
    required this.owed,
    required this.lastOn,
    this.contactId,
    this.contactState,
  });
  final String key, name, lastOn;
  final String? contactId, contactState;
  final List<String> names;
  final int loans, openLoans, overdue;
  final List<OwedTotal> owed;

  factory LoanPerson.fromJson(JsonReader r) => LoanPerson(
        key: r.string('key'),
        name: r.string('name'),
        names: r.optStrings('names') ?? const [],
        loans: r.optInt('loans') ?? 0,
        openLoans: r.optInt('open_loans') ?? 0,
        overdue: r.optInt('overdue') ?? 0,
        owed: r.optObjects('owed', OwedTotal.fromJson),
        lastOn: r.string('last_on'),
        contactId: r.optString('contact_id'),
        contactState: r.optString('contact_state'),
      );
}

class LoanPersonPage {
  const LoanPersonPage({required this.items, required this.total});
  final List<LoanPerson> items;
  final int total;
  factory LoanPersonPage.fromJson(JsonReader r) => LoanPersonPage(
      items: r.optObjects('items', LoanPerson.fromJson),
      total: r.optInt('total') ?? 0);
}

/// One linked transaction of one of the person's loans.
class LoanPersonEvent {
  const LoanPersonEvent(
      {required this.loanId, required this.direction, required this.movement});
  final String loanId;
  final LoanDirection direction;
  final LoanMovement movement;
  factory LoanPersonEvent.fromJson(JsonReader r) => LoanPersonEvent(
        loanId: r.string('loan_id'),
        direction: r.string('direction') == 'borrowed'
            ? LoanDirection.borrowed
            : LoanDirection.lent,
        movement: LoanMovement.fromJson(r.object('movement')),
      );
}

/// `GET /people-loans/people/detail?key=` (`FinanceLoanPersonDetail`).
class LoanPersonDetail {
  const LoanPersonDetail(
      {required this.person, required this.loans, required this.history});
  final LoanPerson person;
  final List<PersonLoan> loans;
  final List<LoanPersonEvent> history;
  factory LoanPersonDetail.fromJson(JsonReader r) => LoanPersonDetail(
        person: LoanPerson.fromJson(r),
        loans: r.optObjects('items', PersonLoan.fromJson),
        history: r.optObjects('history', LoanPersonEvent.fromJson),
      );
}

/// The person picked on the loan form: a Contacts id, its name and state.
class PickedContact {
  const PickedContact({this.id, this.name, this.state});
  final String? id, name, state;
  static const none = PickedContact();
  factory PickedContact.of(PersonLoan? loan) => PickedContact(
      id: loan?.contactId, name: loan?.contactName, state: loan?.contactState);
}

// -- the source ----------------------------------------------------------------

/// Contacts search and creation (the directory's API) and the loans by person.
abstract interface class LoanPeopleSource {
  /// People whose name, number or address match [query], at most a few.
  Future<PeoplePage> searchContacts(String query);

  /// Adds a person by name to Contacts (`business:write`; the API refuses
  /// otherwise).
  Future<Person> addContact(String name);

  Future<LoanPersonPage> people({String query = '', int offset = 0});
  Future<LoanPersonDetail> person(String key);
}

class ApiLoanPeopleSource implements LoanPeopleSource {
  ApiLoanPeopleSource(this.api) : contacts = ApiContactsRepository(api);
  final AnumApiClient api;
  final ContactsRepository contacts;
  static const pageSize = 25;

  @override
  Future<PeoplePage> searchContacts(String query) =>
      contacts.people(query: query, limit: 6);

  @override
  Future<Person> addContact(String name) async =>
      (await contacts.create({'name': name})).person;

  @override
  Future<LoanPersonPage> people({String query = '', int offset = 0}) async =>
      LoanPersonPage.fromJson(JsonReader.of(await api.request(
          'GET',
          Uri(path: '/api/v1/finance/people-loans/people', queryParameters: {
            'limit': '$pageSize',
            'offset': '$offset',
            if (query.trim().isNotEmpty) 'q': query.trim(),
          }).toString())));

  @override
  Future<LoanPersonDetail> person(String key) async =>
      LoanPersonDetail.fromJson(JsonReader.of(await api.request(
          'GET',
          Uri(
              path: '/api/v1/finance/people-loans/people/detail',
              queryParameters: {'key': key}).toString())));
}

String _fold(String value) =>
    value.trim().replaceAll(RegExp(r'\s+'), ' ').toLowerCase();

/// A line under a loan's name: the typed name kept after a rename, or that
/// the person left Contacts or cannot be seen; null otherwise.
String? loanContactNote(PersonLoan loan, FinanceLoanContactsStrings t) {
  if (loan.contactId == null) return null;
  return switch (loan.contactState) {
    'deleted' => t.t('contact.deleted'),
    'hidden' => t.t('contact.hidden'),
    _ => loan.typedAs == null
        ? null
        : t.t('contact.typedAs', {'name': loan.typedAs!}),
  };
}

// -- the picker on the loan form ----------------------------------------------

/// Under the loan form's name field: the typed name searches Contacts; a tap
/// links the person; "Add to Contacts" shows only when the API says the caller
/// may create people and nobody has exactly that name. Without access the
/// loan keeps the typed name.
class LoanContactPicker extends StatefulWidget {
  const LoanContactPicker(
      {required this.source,
      required this.person,
      required this.picked,
      super.key});
  final LoanPeopleSource source;
  final TextEditingController person;
  final ValueNotifier<PickedContact> picked;

  @override
  State<LoanContactPicker> createState() => _LoanContactPickerState();
}

enum _Phase { idle, loading, ready, denied, error }

class _LoanContactPickerState extends State<LoanContactPicker> {
  _Phase _phase = _Phase.idle;
  List<Person> _people = const [];
  bool _canWrite = false, _adding = false;
  String? _error, _addError;
  String _searched = '';
  Timer? _timer;
  int _ticket = 0;

  @override
  void initState() {
    super.initState();
    widget.person.addListener(_typed);
    widget.picked.addListener(_typed);
    _typed();
  }

  @override
  void dispose() {
    _timer?.cancel();
    widget.person.removeListener(_typed);
    widget.picked.removeListener(_typed);
    super.dispose();
  }

  void _typed() {
    final query = widget.person.text.trim();
    if (widget.picked.value.id != null || query.length < 2) {
      _timer?.cancel();
      _ticket++;
      if (_phase != _Phase.idle) setState(() => _phase = _Phase.idle);
      return;
    }
    if (query == _searched && _phase != _Phase.idle) return;
    _timer?.cancel();
    _timer = Timer(const Duration(milliseconds: 250), () => _search(query));
  }

  Future<void> _search(String query) async {
    final ticket = ++_ticket;
    setState(() {
      _phase = _Phase.loading;
      _searched = query;
    });
    try {
      final page = await widget.source.searchContacts(query);
      if (!mounted || ticket != _ticket) return;
      setState(() {
        _people = page.items;
        _canWrite = page.canWrite;
        _phase = _Phase.ready;
      });
    } on Object catch (error) {
      if (!mounted || ticket != _ticket) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _phase = failure.denied ? _Phase.denied : _Phase.error;
        _error = failure.message;
      });
    }
  }

  Future<void> _add(String name) async {
    setState(() {
      _adding = true;
      _addError = null;
    });
    try {
      final made = await widget.source.addContact(name);
      widget.picked.value =
          PickedContact(id: made.id, name: made.name, state: 'linked');
    } on Object catch (error) {
      if (mounted) {
        setState(() => _addError = RecordsFailure.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _adding = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceLoanContactsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final picked = widget.picked.value;
    if (picked.id != null) {
      final name = picked.name ?? widget.person.text.trim();
      final label = switch (picked.state) {
        'deleted' => t.t('contact.deleted'),
        'hidden' => t.t('contact.hidden'),
        _ => t.t('contact.linked', {'name': name}),
      };
      return Wrap(
        key: const Key('loan-contact-linked'),
        spacing: AnumSpacing.xs,
        runSpacing: AnumSpacing.xxs,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          AnumPill(label: label, icon: Icons.contacts_outlined),
          TextButton.icon(
            key: const Key('loan-contact-unlink'),
            onPressed: () => widget.picked.value = PickedContact.none,
            icon: const Icon(Icons.link_off),
            label: Semantics(
                label: t.t('contact.unlinkLabel', {'name': name}),
                excludeSemantics: true,
                child: Text(t.t('contact.unlink'))),
          ),
        ],
      );
    }
    final query = widget.person.text.trim();
    final exact = _people.any((p) => _fold(p.name) == _fold(query));
    final children = <Widget>[
      switch (_phase) {
        _Phase.idle => Text(t.t('contact.help'), style: muted),
        _Phase.loading => Text(t.t('contact.searching'), style: muted),
        _Phase.denied => Text(t.t('contact.denied'),
            key: const Key('loan-contact-denied'), style: muted),
        _Phase.error => Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: AnumSpacing.xs,
            children: [
              Text(t.t('contact.error', {'message': _error ?? ''}),
                  style: TextStyle(color: context.palette.stop)),
              TextButton(
                  onPressed: () => _search(query),
                  child: Text(t.t('contact.retry'))),
            ],
          ),
        _Phase.ready => _people.isEmpty
            ? Text(t.t('contact.none', {'name': query}), style: muted)
            : Semantics(
                label: t.t('contact.results'),
                container: true,
                child: Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xs,
                  children: [
                    for (final p in _people)
                      ActionChip(
                        key: Key('loan-contact-${p.id}'),
                        avatar: const Icon(Icons.contacts_outlined, size: 18),
                        tooltip: t.t('contact.pick', {'name': p.name}),
                        label: Text(
                            p.organisation.isEmpty
                                ? p.name
                                : '${p.name} · ${p.organisation}',
                            overflow: TextOverflow.ellipsis),
                        onPressed: () => widget.picked.value = PickedContact(
                            id: p.id, name: p.name, state: 'linked'),
                      ),
                  ],
                ),
              ),
      },
      if (_phase == _Phase.ready && _canWrite && !exact && query.isNotEmpty)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            key: const Key('loan-contact-add'),
            onPressed: _adding ? null : () => _add(query),
            icon: const Icon(Icons.person_add_alt),
            label: Text(t.t('contact.add', {'name': query}),
                overflow: TextOverflow.ellipsis),
          ),
        ),
      if (_phase == _Phase.ready && !_canWrite && _people.isEmpty)
        Text(t.t('contact.cannotAdd'), style: muted),
      if (_addError != null)
        Text(_addError!, style: TextStyle(color: context.palette.stop)),
    ];
    return Semantics(
      liveRegion: true,
      child: Column(
        key: const Key('loan-contact-picker'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const SizedBox(height: AnumSpacing.xs),
            children[i],
          ],
        ],
      ),
    );
  }
}

// -- loans by person -------------------------------------------------------------

String _owedLine(
    BuildContext context, LoanPerson p, FinanceLoanContactsStrings t) {
  final locale = Localizations.localeOf(context);
  final parts = <String>[
    for (final o in p.owed)
      if ((double.tryParse(o.theyOweMe) ?? 0) > 0)
        t.t('people.theyOwe', {
          'amount': formatMoneyText(o.theyOweMe, o.currency, locale: locale)
        }),
    for (final o in p.owed)
      if ((double.tryParse(o.iOwe) ?? 0) > 0)
        t.t('people.iOwe',
            {'amount': formatMoneyText(o.iOwe, o.currency, locale: locale)}),
  ];
  return parts.join(' · ');
}

/// Each person's loans together, searchable and paged, with what they owe you
/// and what you owe them per currency; a tap opens their loans and history.
class LoanPeopleScreen extends StatefulWidget {
  const LoanPeopleScreen({required this.source, super.key});
  final LoanPeopleSource source;

  @override
  State<LoanPeopleScreen> createState() => _LoanPeopleScreenState();
}

class _LoanPeopleScreenState extends State<LoanPeopleScreen> {
  final _query = TextEditingController();
  Timer? _timer;
  List<LoanPerson> _items = const [];
  int _total = 0;
  bool _loading = true, _loadingMore = false;
  RecordsFailure? _failure;
  String? _moreError;
  int _ticket = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _query.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final ticket = ++_ticket;
    setState(() {
      _loading = true;
      _failure = null;
    });
    try {
      final page = await widget.source.people(query: _query.text);
      if (!mounted || ticket != _ticket) return;
      setState(() {
        _items = page.items;
        _total = page.total;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted || ticket != _ticket) return;
      setState(() {
        _failure = RecordsFailure.from(error);
        _loading = false;
      });
    }
  }

  Future<void> _more() async {
    setState(() {
      _loadingMore = true;
      _moreError = null;
    });
    try {
      final page =
          await widget.source.people(query: _query.text, offset: _items.length);
      if (!mounted) return;
      setState(() {
        _items = [..._items, ...page.items];
        _total = page.total;
      });
    } on Object catch (error) {
      if (mounted) {
        setState(() => _moreError = RecordsFailure.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceLoanContactsStrings.of(context);
    final failure = _failure;
    Widget body;
    if (_loading && _items.isEmpty) {
      body = AnumFeedback(
          kind: AnumFeedbackKind.loading, message: t.t('people.loading'));
    } else if (failure != null) {
      body = AnumFeedback(
          kind: failure.denied
              ? AnumFeedbackKind.permission
              : failure.offline
                  ? AnumFeedbackKind.offline
                  : AnumFeedbackKind.error,
          message: failure.denied
              ? t.t('people.denied')
              : t.t('people.failed', {'message': failure.message}),
          onRetry: failure.denied ? null : _load);
    } else if (_items.isEmpty) {
      body = AnumEmpty(
          title: t.t(
              _query.text.trim().isEmpty ? 'people.empty' : 'people.noMatch'),
          message: t.t('people.emptyText'),
          icon: Icons.handshake_outlined);
    } else {
      body = AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final p in _items)
            AnumRow(
              key: Key('loan-person-${p.key}'),
              icon: p.contactState == 'linked'
                  ? Icons.contacts_outlined
                  : Icons.person_outline,
              tone: p.overdue > 0 ? AnumTone.stop : null,
              title: p.name,
              subtitle: [
                t.t('people.count', {'open': p.openLoans, 'count': p.loans}),
                if (p.overdue > 0) t.t('people.overdue', {'count': p.overdue}),
                if (p.contactState == 'linked') t.t('people.fromContacts'),
                if (p.contactState == 'deleted') t.t('contact.deleted'),
              ].join(' · '),
              detail: _owedLine(context, p, t),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => showRecordsSheet<void>(context,
                  (_) => LoanPersonSheet(source: widget.source, person: p)),
            ),
        ]),
      );
    }
    return Scaffold(
      appBar: AppBar(title: Text(t.t('people.title'))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Text(t.t('people.intro'),
                style: TextStyle(color: context.palette.muted)),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('loan-people-search'),
              controller: _query,
              decoration: InputDecoration(
                  labelText: t.t('people.search'),
                  prefixIcon: const Icon(Icons.search)),
              onChanged: (_) {
                _timer?.cancel();
                _timer = Timer(const Duration(milliseconds: 300), _load);
              },
            ),
            const SizedBox(height: AnumSpacing.md),
            body,
            if (_moreError != null) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(_moreError!, style: TextStyle(color: context.palette.stop)),
            ],
            if (failure == null && _items.length < _total) ...[
              const SizedBox(height: AnumSpacing.sm),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton(
                  onPressed: _loadingMore ? null : _more,
                  child: Text(
                      t.t(_loadingMore ? 'people.loadingMore' : 'people.more')),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// One person's loans and every transaction linked to them, newest first.
class LoanPersonSheet extends StatefulWidget {
  const LoanPersonSheet(
      {required this.source, required this.person, super.key});
  final LoanPeopleSource source;
  final LoanPerson person;

  @override
  State<LoanPersonSheet> createState() => _LoanPersonSheetState();
}

class _LoanPersonSheetState extends State<LoanPersonSheet> {
  late Future<LoanPersonDetail> _detail =
      widget.source.person(widget.person.key);

  @override
  Widget build(BuildContext context) {
    final t = FinanceLoanContactsStrings.of(context);
    final locale = Localizations.localeOf(context);
    final muted = TextStyle(color: context.palette.muted);
    return FutureBuilder<LoanPersonDetail>(
      future: _detail,
      builder: (context, snap) {
        if (snap.hasError) {
          final failure = RecordsFailure.from(snap.error!);
          return AnumFeedback(
              kind: failure.denied
                  ? AnumFeedbackKind.permission
                  : AnumFeedbackKind.error,
              message: t.t('people.failed', {'message': failure.message}),
              onRetry: () => setState(
                  () => _detail = widget.source.person(widget.person.key)));
        }
        final d = snap.data;
        if (d == null) {
          return AnumFeedback(
              kind: AnumFeedbackKind.loading, message: t.t('people.loading'));
        }
        final showNames = d.person.names.length > 1 ||
            (d.person.contactState == 'linked' &&
                d.person.names.isNotEmpty &&
                d.person.names.first != d.person.name);
        return Column(
          key: const Key('loan-person-sheet'),
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
                header: true,
                child: Text(t.t('people.detail', {'name': d.person.name}),
                    style: Theme.of(context).textTheme.titleLarge)),
            if (showNames) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(t.t('people.names', {'names': d.person.names.join(', ')}),
                  style: muted),
            ],
            const SizedBox(height: AnumSpacing.md),
            AnumHeader(title: t.t('people.loans')),
            for (final loan in d.loans)
              AnumRow(
                key: Key('loan-person-loan-${loan.id}'),
                icon: loan.direction == LoanDirection.lent
                    ? Icons.call_received
                    : Icons.call_made,
                title: recordsDate(context, loan.lentOn),
                subtitle: [
                  t.t(loan.direction == LoanDirection.lent
                      ? 'people.lent'
                      : 'people.borrowed'),
                  if (loan.typedAs != null) loan.person,
                ].join(' · '),
                detail: t.t('people.left', {
                  'left': formatMoneyText(loan.outstanding, loan.currency,
                      locale: locale),
                  'amount': formatMoneyText(loan.amount, loan.currency,
                      locale: locale),
                }),
              ),
            const SizedBox(height: AnumSpacing.md),
            AnumHeader(title: t.t('people.history')),
            if (d.history.isEmpty)
              Text(t.t('people.noHistory'), style: muted)
            else
              for (final h in d.history)
                AnumRow(
                  key: Key('loan-person-event-${h.movement.linkId}'),
                  icon: h.movement.role == 'deposit'
                      ? Icons.south_west
                      : Icons.north_east,
                  title: h.movement.description ?? '-',
                  subtitle: [
                    recordsDate(context, h.movement.postedOn),
                    t.t(h.movement.role == 'deposit'
                        ? 'people.paidIn'
                        : 'people.paidOut'),
                    if (!h.movement.active) t.t('people.review'),
                  ].join(' · '),
                  detail:
                      h.movement.amount == null || h.movement.currency == null
                          ? '-'
                          : formatMoneyText(
                              h.movement.amount!, h.movement.currency!,
                              locale: locale),
                ),
          ],
        );
      },
    );
  }
}
