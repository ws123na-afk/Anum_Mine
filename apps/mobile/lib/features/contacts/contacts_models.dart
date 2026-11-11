import '../../data/json_read.dart';

/// The Contacts directory (docs/contacts.md#mobile): the API's shapes, kept
/// free of Flutter so they are tested on their own.
const contactsPageSize = 25;
const contactChannels = ['phone', 'email', 'whatsapp', 'all'];

class ContactIdentifier {
  const ContactIdentifier(
      {required this.kind, required this.value, this.label = ''});
  factory ContactIdentifier.fromJson(JsonReader j) => ContactIdentifier(
      kind: j.string('kind'),
      value: j.string('value'),
      label: j.optString('label') ?? '');
  final String kind, value, label;
}

class DoNotContactMark {
  const DoNotContactMark(
      {required this.channel,
      required this.reason,
      required this.setBy,
      required this.setAt});
  factory DoNotContactMark.fromJson(JsonReader j) => DoNotContactMark(
      channel: j.string('channel'),
      reason: j.string('reason'),
      setBy: j.string('set_by'),
      setAt: j.date('set_at'));
  final String channel, reason, setBy;
  final DateTime setAt;
}

class Person {
  const Person({
    required this.id,
    required this.name,
    required this.organisation,
    required this.notes,
    required this.source,
    required this.phones,
    required this.emails,
    required this.sources,
    required this.doNotContact,
    required this.openProposals,
    required this.createdBy,
    required this.createdAt,
    required this.updatedAt,
  });
  factory Person.fromJson(JsonReader j) => Person(
        id: j.string('id'),
        name: j.string('name'),
        organisation: j.optString('organisation') ?? '',
        notes: j.optString('notes') ?? '',
        source: j.string('source'),
        phones: j.objects('phones', ContactIdentifier.fromJson),
        emails: j.objects('emails', ContactIdentifier.fromJson),
        sources: (j.optList('sources') ?? const []).map((v) => '$v').toList(),
        doNotContact: j.optObjects('do_not_contact', DoNotContactMark.fromJson),
        openProposals: j.optInt('open_proposals') ?? 0,
        createdBy: j.string('created_by'),
        createdAt: j.date('created_at'),
        updatedAt: j.date('updated_at'),
      );
  final String id, name, organisation, notes, source, createdBy;
  final List<ContactIdentifier> phones, emails;
  final List<String> sources;
  final List<DoNotContactMark> doNotContact;
  final int openProposals;
  final DateTime createdAt, updatedAt;

  /// The channels the directory blocks (`all` covers every channel).
  List<String> get blockedChannels {
    final channels = doNotContact.map((m) => m.channel).toSet();
    if (channels.contains('all')) return const ['phone', 'email', 'whatsapp'];
    return ['phone', 'email', 'whatsapp'].where(channels.contains).toList();
  }
}

class ContactLink {
  const ContactLink(
      {required this.id,
      required this.kind,
      required this.found,
      required this.title,
      required this.detail});
  factory ContactLink.fromJson(JsonReader j) => ContactLink(
      id: j.string('id'),
      kind: j.string('kind'),
      found: j.boolean('found'),
      title: j.optString('title') ?? '',
      detail: j.optString('detail') ?? '');
  final String id, kind, title, detail;
  final bool found;
}

class ConsentFact {
  const ConsentFact(
      {required this.source,
      required this.state,
      required this.detail,
      this.at});
  factory ConsentFact.fromJson(JsonReader j) => ConsentFact(
      source: j.string('source'),
      state: j.string('state'),
      detail: j.optString('detail') ?? '',
      at: j.optDate('at'));
  final String source, state, detail;
  final DateTime? at;
  bool get bad => state == 'blocked' || state == 'withdrawn';
}

class ChannelSummary {
  const ChannelSummary(
      {required this.channel,
      required this.identifiers,
      required this.mark,
      required this.facts,
      required this.canContact});
  factory ChannelSummary.fromJson(JsonReader j) {
    final mark = j.optObject('do_not_contact');
    return ChannelSummary(
      channel: j.string('channel'),
      identifiers:
          (j.optList('identifiers') ?? const []).map((v) => '$v').toList(),
      mark: mark == null ? null : DoNotContactMark.fromJson(mark),
      facts: j.optObjects('facts', ConsentFact.fromJson),
      canContact: j.boolean('can_contact'),
    );
  }
  final String channel;
  final List<String> identifiers;
  final DoNotContactMark? mark;
  final List<ConsentFact> facts;
  final bool canContact;
}

class PersonDetail {
  const PersonDetail(
      {required this.person,
      required this.links,
      required this.channels,
      required this.openProposals,
      required this.canWrite,
      required this.canManage});
  factory PersonDetail.fromJson(JsonReader j) => PersonDetail(
        person: Person.fromJson(j.object('person')),
        links: j.objects('links', ContactLink.fromJson),
        channels: j.objects('channels', ChannelSummary.fromJson),
        openProposals: j.optList('proposals')?.length ?? 0,
        canWrite: j.boolean('can_write'),
        canManage: j.boolean('can_manage'),
      );
  final Person person;
  final List<ContactLink> links;
  final List<ChannelSummary> channels;
  final int openProposals;
  final bool canWrite, canManage;
}

class PeoplePage {
  const PeoplePage(
      {required this.items,
      required this.total,
      required this.people,
      required this.marked,
      required this.openProposals,
      required this.canWrite,
      required this.canManage,
      this.canExport = false});
  factory PeoplePage.fromJson(JsonReader j) {
    final counts = j.object('counts');
    return PeoplePage(
      items: j.objects('items', Person.fromJson),
      total: j.integer('total'),
      people: counts.integer('people'),
      marked: counts.integer('do_not_contact'),
      openProposals: counts.integer('open_proposals'),
      canWrite: j.boolean('can_write'),
      canManage: j.boolean('can_manage'),
      canExport: j.optBool('can_export') ?? j.boolean('can_manage'),
    );
  }
  final List<Person> items;
  final int total, people, marked, openProposals;
  final bool canWrite, canManage;

  /// Owners export the directory as CSV (`GET /contacts/export`).
  final bool canExport;
}

/// A do-not-contact reason as the API takes it: 3 to 300 characters.
String? reasonProblem(String reason) {
  final text = reason.trim();
  return text.length < 3 || text.length > 300 ? 'reason' : null;
}

/// Two people who share a number or an address under different names
/// (docs/contacts.md#merging-and-proposals); either may be gone (null).
class MergeProposal {
  const MergeProposal(
      {required this.id,
      required this.reason,
      required this.matchedValue,
      required this.status,
      this.a,
      this.b});
  factory MergeProposal.fromJson(JsonReader j) {
    final a = j.optObject('a');
    final b = j.optObject('b');
    return MergeProposal(
        id: j.string('id'),
        reason: j.string('reason'),
        matchedValue: j.optString('matched_value') ?? '',
        status: j.optString('status') ?? 'open',
        a: a == null ? null : Person.fromJson(a),
        b: b == null ? null : Person.fromJson(b));
  }
  final String id, reason, matchedValue, status;
  final Person? a, b;
}

class ProposalPage {
  const ProposalPage(
      {required this.items, required this.total, required this.canWrite});
  factory ProposalPage.fromJson(JsonReader j) => ProposalPage(
      items: j.objects('items', MergeProposal.fromJson),
      total: j.integer('total'),
      canWrite: j.optBool('can_write') ?? false);
  final List<MergeProposal> items;
  final int total;
  final bool canWrite;
}

/// The person form: one value per line (or comma) in the two lists.
class PersonDraft {
  const PersonDraft(
      {this.name = '',
      this.organisation = '',
      this.notes = '',
      this.phones = '',
      this.emails = ''});
  factory PersonDraft.of(Person p) => PersonDraft(
      name: p.name,
      organisation: p.organisation,
      notes: p.notes,
      phones:
          p.phones.map((i) => i.label.isEmpty ? i.value : i.label).join('\n'),
      emails:
          p.emails.map((i) => i.label.isEmpty ? i.value : i.label).join('\n'));
  final String name, organisation, notes, phones, emails;
}

/// One value per line, comma or semicolon; blanks dropped.
List<String> splitValues(String text) => text
    .split(RegExp(r'[\n,;]+'))
    .map((v) => v.trim())
    .where((v) => v.isNotEmpty)
    .toList();

/// A phone number by the API's rule: 8 to 15 digits once the formatting is
/// gone (`00` and UAE `05x` are read by the API too).
bool looksLikePhone(String value) {
  final digits = value.replaceAll(RegExp(r'\D'), '');
  return digits.length >= 8 && digits.length <= 15;
}

bool looksLikeEmail(String value) =>
    RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$').hasMatch(value.trim());

/// The `POST /contacts` or `PATCH /contacts/{id}` body, or the first problem
/// as a message key with its values (`contacts.err.*`).
({Map<String, Object?>? body, String? error, String? value}) personBody(
    PersonDraft draft) {
  final name = draft.name.trim().replaceAll(RegExp(r'\s+'), ' ');
  if (name.isEmpty || name.length > 200) {
    return (body: null, error: 'contacts.err.name', value: null);
  }
  final phones = splitValues(draft.phones), emails = splitValues(draft.emails);
  if (phones.length > 10 || emails.length > 10) {
    return (body: null, error: 'contacts.err.tooMany', value: null);
  }
  for (final v in phones) {
    if (!looksLikePhone(v)) {
      return (body: null, error: 'contacts.err.phone', value: v);
    }
  }
  for (final v in emails) {
    if (!looksLikeEmail(v)) {
      return (body: null, error: 'contacts.err.email', value: v);
    }
  }
  return (
    body: {
      'name': name,
      'organisation': draft.organisation.trim(),
      'notes': draft.notes.trim(),
      'phones': phones,
      'emails': emails,
    },
    error: null,
    value: null
  );
}
