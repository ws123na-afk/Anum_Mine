import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import 'contacts_models.dart';
import 'contacts_repository.dart';

enum ContactsPhase { loading, ready, denied, error }

/// The people list: search, 25 at a time with Show more, every state.
class ContactsController extends ChangeNotifier {
  ContactsController(this.repository);
  final ContactsRepository repository;

  ContactsPhase phase = ContactsPhase.loading;
  String? error;
  List<Person> items = const [];
  int total = 0, people = 0, marked = 0, openProposals = 0;
  bool canWrite = false, canManage = false, canExport = false;
  String query = '';

  /// The CSV export: running, and its last result.
  bool exporting = false;
  String? exportNotice;
  bool exportFailed = false;
  bool loadingMore = false;
  String? moreError;
  bool _disposed = false;
  int _request = 0;

  bool get hasMore => items.length < total;

  Future<void> search(String query) {
    this.query = query;
    return load();
  }

  Future<void> load() async {
    final id = ++_request;
    phase = ContactsPhase.loading;
    error = moreError = null;
    _notify();
    try {
      final page = await repository.people(query: query);
      if (id != _request) return;
      items = page.items;
      total = page.total;
      people = page.people;
      marked = page.marked;
      openProposals = page.openProposals;
      canWrite = page.canWrite;
      canManage = page.canManage;
      canExport = page.canExport;
      phase = ContactsPhase.ready;
    } on ApiException catch (e) {
      if (id != _request) return;
      phase = e.isPermissionDenied ? ContactsPhase.denied : ContactsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (id != _request) return;
      phase = ContactsPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final page = await repository.people(query: query, offset: items.length);
      items = [...items, ...page.items];
      total = page.total;
    } on Object catch (e) {
      moreError = readableError(e);
    }
    loadingMore = false;
    _notify();
  }

  /// Saves the whole directory as CSV through the system save dialog.
  Future<void> export(AnumLocalizations l) async {
    if (exporting) return;
    exporting = true;
    exportNotice = null;
    exportFailed = false;
    _notify();
    try {
      final saved = await repository.exportCsv(l.t('contacts.export'));
      exportNotice =
          l.t(saved ? 'contacts.exported' : 'contacts.exportCancelled');
    } on ApiException catch (e) {
      exportFailed = true;
      exportNotice = e.isPermissionDenied
          ? l.t('contacts.exportOwners')
          : l.f('contacts.exportFailed', {'message': e.readable});
    } on Object catch (e) {
      exportFailed = true;
      exportNotice =
          l.f('contacts.exportFailed', {'message': readableError(e)});
    } finally {
      exporting = false;
      _notify();
    }
  }

  void reset() {
    items = const [];
    total = 0;
    query = '';
    phase = ContactsPhase.loading;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// One person's page with the do-not-contact actions.
class PersonController extends ChangeNotifier {
  PersonController(this.repository, this.id);
  final ContactsRepository repository;
  final String id;
  ContactsPhase phase = ContactsPhase.loading;
  String? error;
  PersonDetail? detail;
  bool busy = false;
  String? flash;
  bool _disposed = false;

  Future<void> load() async {
    phase = ContactsPhase.loading;
    error = null;
    _notify();
    try {
      detail = await repository.person(id);
      phase = ContactsPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 404
          ? ContactsPhase.error
          : e.isPermissionDenied
              ? ContactsPhase.denied
              : ContactsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = ContactsPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  /// Marks or clears a channel; answers the API's sentence on failure, null on success.
  Future<String?> setMark(String channel, String reason,
      {required bool clear}) async {
    busy = true;
    flash = null;
    _notify();
    try {
      if (clear) {
        await repository.clearDoNotContact(id, channel, reason);
      } else {
        await repository.markDoNotContact(id, channel, reason);
      }
      busy = false;
      flash = clear ? 'cleared:$channel' : 'marked:$channel';
      await load();
      return null;
    } on Object catch (e) {
      busy = false;
      _notify();
      return readableError(e);
    }
  }

  /// Saves the person form; null when saved, else the sentence to show.
  Future<String?> save(PersonDraft draft, AnumLocalizations l) async {
    final checked = personBody(draft);
    if (checked.body == null) {
      return l.f(checked.error!, {'value': checked.value ?? ''});
    }
    busy = true;
    _notify();
    try {
      detail = await repository.update(id, checked.body!);
      return null;
    } on Object catch (e) {
      return l.f('contacts.form.failed', {'message': readableError(e)});
    } finally {
      busy = false;
      _notify();
    }
  }

  /// Merges [other] into this person; null when done, else why not.
  Future<String?> merge(Person other, AnumLocalizations l) async {
    busy = true;
    _notify();
    try {
      detail = await repository.merge(id, other.id);
      phase = ContactsPhase.ready;
      flash = null;
      return null;
    } on Object catch (e) {
      return l.f('contacts.merge.failed', {'message': readableError(e)});
    } finally {
      busy = false;
      _notify();
    }
  }

  /// Deletes this person (owners); null when done, else why not.
  Future<String?> delete(AnumLocalizations l) async {
    final name = detail?.person.name ?? '';
    busy = true;
    _notify();
    try {
      await repository.delete(id);
      return null;
    } on Object catch (e) {
      return l.f('contacts.person.deleteFailed',
          {'name': name, 'message': readableError(e)});
    } finally {
      busy = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// Possible duplicates: paged, each decided by Keep one (merge) or
/// Different people (dismiss).
class DuplicatesController extends ChangeNotifier {
  DuplicatesController(this.repository);
  final ContactsRepository repository;
  ContactsPhase phase = ContactsPhase.loading;
  String? error;
  List<MergeProposal> items = const [];
  int total = 0;
  bool canWrite = false;
  bool loadingMore = false, busy = false;
  String? moreError;
  bool _disposed = false;

  bool get hasMore => items.length < total;

  Future<void> load() async {
    phase = ContactsPhase.loading;
    error = moreError = null;
    _notify();
    try {
      final page = await repository.proposals();
      items = page.items;
      total = page.total;
      canWrite = page.canWrite;
      phase = ContactsPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? ContactsPhase.denied : ContactsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = ContactsPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final page = await repository.proposals(offset: items.length);
      items = [...items, ...page.items];
      total = page.total;
    } on Object catch (e) {
      moreError = readableError(e);
    }
    loadingMore = false;
    _notify();
  }

  /// Keeps [keep] and merges the other of [proposal] into it; answers the
  /// sentence to show.
  Future<String> keep(
      MergeProposal proposal, Person keep, AnumLocalizations l) async {
    final other = proposal.a?.id == keep.id ? proposal.b : proposal.a;
    if (other == null) return l.t('contacts.dup.gone');
    return _decide(
        () => repository.merge(keep.id, other.id),
        l.f('contacts.merge.done', {'other': other.name, 'name': keep.name}),
        l);
  }

  Future<String> dismiss(MergeProposal proposal, AnumLocalizations l) =>
      _decide(() => repository.dismissProposal(proposal.id),
          l.t('contacts.dup.dismissed'), l);

  Future<String> _decide(
      Future<Object?> Function() work, String done, AnumLocalizations l) async {
    busy = true;
    _notify();
    try {
      await work();
      await load();
      return done;
    } on ApiException catch (e) {
      await load();
      return e.statusCode == 404
          ? l.t('contacts.dup.gone')
          : l.f('contacts.dup.failed', {'message': e.readable});
    } on Object catch (e) {
      return l.f('contacts.dup.failed', {'message': readableError(e)});
    } finally {
      busy = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// Adds a person; answers the new person, or throws the sentence to show.
Future<PersonDetail> createPerson(ContactsRepository repository,
    PersonDraft draft, AnumLocalizations l) async {
  final checked = personBody(draft);
  if (checked.body == null) {
    throw FormatException(l.f(checked.error!, {'value': checked.value ?? ''}));
  }
  try {
    return await repository.create(checked.body!);
  } on Object catch (e) {
    throw FormatException(
        l.f('contacts.form.failed', {'message': readableError(e)}));
  }
}
