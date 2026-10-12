import 'dart:convert';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../privacy/privacy_repository.dart' show PrivacyExportSaver;
import '../reports/report_sections.dart';
import 'library_models.dart';

/// Favorites, knowledge base and reports API (docs/knowledge-base.md,
/// docs/reports.md).
abstract interface class LibraryRepository {
  Future<LibraryPage<Favorite>> favorites({String? type, int offset = 0});
  Future<void> star(String type, String id);
  Future<void> unstar(String type, String id);

  Future<ReportPage> reports({String? query, String? type, int offset = 0});
  Future<Report> report(String id);
  Future<Report> generate(String type, int periodDays);
  Future<Report> updateReport(String id, {String? title, String? visibility});
  Future<void> deleteReport(String id);

  /// Saves the report as CSV or Markdown; false when the save was cancelled.
  Future<bool> saveReport(Report report, String format);

  /// Saves the report's CSV in the workspace's files; returns a share link.
  Future<ReportExport> saveReportToFiles(String id);

  /// "Ask ANUM to analyze further": the id of the task it created.
  Future<String> askAboutReport(String id, String question);

  Future<List<KnowledgeCollection>> collections();
  Future<KnowledgeCollection> createCollection(String name, String visibility);
  Future<void> deleteCollection(String id);
  Future<LibraryPage<KnowledgeItem>> items(
    String collectionId, {
    String? query,
    int offset = 0,
  });
  Future<KnowledgeItem> addNote(
    String collectionId,
    String title,
    String content,
    List<String> tags,
  );
  Future<void> removeItem(String id);
  Future<KnowledgeAnswer> ask(String question, {String? collectionId});
}

class ApiLibraryRepository implements LibraryRepository {
  ApiLibraryRepository(this.api, {required this.saver});
  final AnumApiClient api;
  final PrivacyExportSaver saver;

  static const pageSize = 20;

  String _query(Map<String, Object?> params) {
    final kept = {
      for (final e in params.entries)
        if (e.value != null && '${e.value}'.isNotEmpty) e.key: '${e.value}',
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonReader> _get(String path) async =>
      JsonReader(await api.request('GET', path));

  @override
  Future<LibraryPage<Favorite>> favorites(
      {String? type, int offset = 0}) async {
    final r = await _get(
      '/api/v1/favorites${_query({
            'item_type': type,
            'offset': offset,
            'limit': pageSize
          })}',
    );
    return LibraryPage(
      r.optObjects('items', Favorite.fromReader),
      r.optInt('total') ?? 0,
    );
  }

  @override
  Future<void> star(String type, String id) => api.request(
        'POST',
        '/api/v1/favorites',
        body: {'item_type': type, 'item_id': id},
      );

  @override
  Future<void> unstar(String type, String id) => api.request(
        'DELETE',
        '/api/v1/favorites${_query({'item_type': type, 'item_id': id})}',
      );

  @override
  Future<ReportPage> reports({
    String? query,
    String? type,
    int offset = 0,
  }) async {
    final r = await _get(
      '/api/v1/reports${_query({
            'q': query,
            'type': type,
            'offset': offset,
            'limit': pageSize
          })}',
    );
    return ReportPage(
      r.optObjects('items', ReportSummary.fromReader),
      r.optInt('total') ?? 0,
      r.optObjects('types', (t) => t.string('type')),
    );
  }

  @override
  Future<Report> report(String id) async => Report.fromReader(
        await _get('/api/v1/reports/${Uri.encodeComponent(id)}'),
      );

  @override
  Future<Report> generate(String type, int periodDays) async =>
      Report.fromReader(
        JsonReader(
          await api.request(
            'POST',
            '/api/v1/reports/generate',
            body: {'type': type, 'period_days': periodDays},
          ),
        ),
      );

  @override
  Future<Report> updateReport(
    String id, {
    String? title,
    String? visibility,
  }) async =>
      Report.fromReader(
        JsonReader(
          await api.request(
            'PATCH',
            '/api/v1/reports/${Uri.encodeComponent(id)}',
            body: {
              if (title != null) 'title': title,
              if (visibility != null) 'visibility': visibility,
            },
          ),
        ),
      );

  @override
  Future<void> deleteReport(String id) =>
      api.request('DELETE', '/api/v1/reports/${Uri.encodeComponent(id)}');

  @override
  Future<bool> saveReport(Report report, String format) {
    final csv = format == 'csv';
    // CSV starts with a byte order mark so spreadsheets read Arabic text.
    final bytes = csv
        ? [0xEF, 0xBB, 0xBF, ...utf8.encode(reportCsv(report))]
        : utf8.encode(report.bodyMarkdown);
    return saver.save(reportFileName(report, csv ? 'csv' : 'md'), bytes);
  }

  @override
  Future<ReportExport> saveReportToFiles(String id) async {
    final value = JsonReader(await api.request(
        'POST', '/api/v1/reports/${Uri.encodeComponent(id)}/files'));
    final file = value.object('file');
    return ReportExport(
      fileId: file.string('id'),
      fileName: file.string('name'),
      link: api.baseUri.resolve(
          value.object('link').string('url').replaceFirst(RegExp(r'^/'), '')),
    );
  }

  @override
  Future<String> askAboutReport(String id, String question) async =>
      JsonReader(await api.request(
              'POST', '/api/v1/reports/${Uri.encodeComponent(id)}/ask',
              body: {'question': question.trim()}))
          .string('id');

  @override
  Future<List<KnowledgeCollection>> collections() async =>
      (await _get('/api/v1/knowledge/collections'))
          .optObjects('data', KnowledgeCollection.fromReader);

  @override
  Future<KnowledgeCollection> createCollection(
    String name,
    String visibility,
  ) async =>
      KnowledgeCollection.fromReader(
        JsonReader(
          await api.request(
            'POST',
            '/api/v1/knowledge/collections',
            body: {'name': name, 'description': '', 'visibility': visibility},
          ),
        ),
      );

  @override
  Future<void> deleteCollection(String id) => api.request(
        'DELETE',
        '/api/v1/knowledge/collections/${Uri.encodeComponent(id)}',
      );

  @override
  Future<LibraryPage<KnowledgeItem>> items(
    String collectionId, {
    String? query,
    int offset = 0,
  }) async {
    final r = await _get(
      '/api/v1/knowledge/collections/${Uri.encodeComponent(collectionId)}/items${_query({
            'q': query,
            'offset': offset,
            'limit': pageSize
          })}',
    );
    return LibraryPage(
      r.optObjects('items', KnowledgeItem.fromReader),
      r.optInt('total') ?? 0,
    );
  }

  @override
  Future<KnowledgeItem> addNote(
    String collectionId,
    String title,
    String content,
    List<String> tags,
  ) async =>
      KnowledgeItem.fromReader(
        JsonReader(
          await api.request(
            'POST',
            '/api/v1/knowledge/collections/${Uri.encodeComponent(collectionId)}/notes',
            body: {'title': title, 'content': content, 'tags': tags},
          ),
        ),
      );

  @override
  Future<void> removeItem(String id) => api.request(
        'DELETE',
        '/api/v1/knowledge/items/${Uri.encodeComponent(id)}',
      );

  @override
  Future<KnowledgeAnswer> ask(String question, {String? collectionId}) async =>
      KnowledgeAnswer.fromReader(
        JsonReader(
          await api.request(
            'POST',
            '/api/v1/knowledge/ask',
            body: {'question': question, 'collection_id': collectionId},
          ),
        ),
      );
}
