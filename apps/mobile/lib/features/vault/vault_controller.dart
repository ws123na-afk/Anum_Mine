import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'vault_data.dart';

/// The vault list: what the caller may do (meta), search, a category, an
/// expiry filter, the trash, and cursor paging.
class VaultController extends ChangeNotifier {
  VaultController(this.repository) {
    documents = RecordsList<VaultDocument>((query, _, token) => repository.list(
        query: query,
        category: category,
        expiry: expiry,
        trash: trash,
        token: token));
  }
  final VaultRepository repository;
  late final RecordsList<VaultDocument> documents;

  VaultMeta? meta;
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  String category = '', expiry = '';
  bool trash = false;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  String get _filterKey => [category, expiry, if (trash) 'trash']
      .where((part) => part.isNotEmpty)
      .join('|');

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    try {
      meta = await repository.meta();
      failure = null;
      phase = RecordsPhase.ready;
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase == RecordsPhase.ready) await documents.load(filter: _filterKey);
  }

  Future<void> filterBy({String? category, String? expiry, bool? trash}) {
    if (category != null) this.category = category;
    if (expiry != null) this.expiry = expiry;
    if (trash != null) this.trash = trash;
    _notify();
    return documents.load(filter: _filterKey);
  }

  Future<void> clearFilters() {
    category = '';
    expiry = '';
    trash = false;
    _notify();
    return documents.load(query: '', filter: '');
  }

  /// A document was added, changed, trashed or restored on its page.
  Future<void> changed(VaultDocument document) async {
    final belongs = document.inTrash == trash;
    if (belongs) {
      documents.upsert(document, (d) => d.id == document.id);
    } else {
      documents.remove((d) => d.id == document.id);
    }
    try {
      meta = await repository.meta();
      _notify();
    } on Object {
      // The summary refreshes on the next load.
    }
  }

  @override
  void dispose() {
    _disposed = true;
    documents.dispose();
    super.dispose();
  }
}

/// One document with its versions.
class VaultDocumentController extends ChangeNotifier {
  VaultDocumentController(this.repository, this.documentId, {this.onChanged});
  final VaultRepository repository;
  final String documentId;
  final ValueChanged<VaultDocument>? onChanged;

  VaultDocument? document;
  VaultVersions? versions;
  String? versionsError;
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    try {
      document = await repository.get(documentId);
      failure = null;
      phase = RecordsPhase.ready;
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase == RecordsPhase.ready && !document!.inTrash) await loadVersions();
  }

  Future<void> loadVersions() async {
    try {
      versions = await repository.versions(documentId);
      versionsError = null;
    } on Object catch (error) {
      versionsError = RecordsFailure.from(error).message;
    }
    _notify();
  }

  Future<void> _replace(VaultDocument changed) async {
    onChanged?.call(changed);
    await load();
  }

  Future<void> trash() async => _replace(await repository.trash(documentId));
  Future<void> restore() async =>
      _replace(await repository.restore(documentId));
  Future<void> restoreVersion(String versionId) async =>
      _replace(await repository.restoreVersion(documentId, versionId));
  Future<void> uploadVersion(String path,
          {void Function(int sent, int total)? onProgress}) async =>
      _replace(await repository.uploadVersion(documentId, path,
          onProgress: onProgress));

  Future<void> addLink(String type, String target, String label) async {
    await repository.addLink(documentId,
        targetType: type, targetId: target, label: label);
    await load();
  }

  Future<void> removeLink(String linkId) async {
    await repository.removeLink(documentId, linkId);
    await load();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
