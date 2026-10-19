// Favorites, knowledge base and reports on the phone (docs/knowledge-base.md,
// docs/reports.md): the pure rules, the chat's report card, and the screens
// against a fake repository in English and Arabic, at phone to desktop widths
// and 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/chat/chat_models.dart';
import 'package:anum_mobile/features/library/library_models.dart';
import 'package:anum_mobile/features/library/library_repository.dart';
import 'package:anum_mobile/features/library/library_screens.dart';
import 'package:anum_mobile/features/reports/report_sections.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.utc(2026, 10, 11, 9);

Report _report(String id, {bool canManage = true, int rows = 2}) => Report(
      id: id,
      type: 'tyre',
      title: 'Tyre cost $id',
      summary: 'Costs rose on puncture repairs.',
      metrics: const [
        ReportMetric(
            label: 'Total cost',
            value: '12,400',
            unit: 'SAR',
            changePercent: 8.5)
      ],
      visibility: 'workspace',
      createdByKind: 'agent',
      createdAt: _now,
      periodStart: '2026-09-01',
      periodEnd: '2026-09-30',
      highlights: const ['Puncture repairs doubled'],
      columns: const ['reason', 'cost'],
      rows: [
        for (var i = 0; i < rows; i++) ['Puncture $i', 4200 + i]
      ],
      bodyMarkdown: '# Tyre cost',
      canManage: canManage,
      createdBy: 'user_local',
    );

class FakeLibrary implements LibraryRepository {
  List<Favorite> stars = [
    Favorite(
        id: 'fav_1',
        itemType: 'report',
        itemId: 'report_1',
        label: 'Tyre cost report_1',
        createdAt: _now),
  ];
  final reportsById = {'report_1': _report('report_1')};
  final collectionsList = <KnowledgeCollection>[];
  final itemsByCollection = <String, List<KnowledgeItem>>{};
  Object? failReports;
  final saved = <String>[];
  final calls = <String>[];

  @override
  Future<LibraryPage<Favorite>> favorites(
      {String? type, int offset = 0}) async {
    final rows =
        stars.where((f) => type == null || f.itemType == type).toList();
    return LibraryPage(rows, rows.length);
  }

  @override
  Future<void> star(String type, String id) async => stars.add(Favorite(
      id: 'fav_$id', itemType: type, itemId: id, label: id, createdAt: _now));

  @override
  Future<void> unstar(String type, String id) async =>
      stars.removeWhere((f) => f.itemType == type && f.itemId == id);

  @override
  Future<ReportPage> reports(
      {String? query, String? type, int offset = 0}) async {
    final failure = failReports;
    if (failure != null) {
      failReports = null;
      throw failure;
    }
    final rows = reportsById.values.toList();
    return ReportPage(rows, rows.length, const ['tyre']);
  }

  @override
  Future<Report> report(String id) async {
    final found = reportsById[id];
    if (found == null) throw const ApiException(404, 'Report not found');
    return found;
  }

  @override
  Future<Report> generate(String type, int periodDays) async {
    calls.add('generate:$type:$periodDays');
    return reportsById['report_new'] = _report('report_new');
  }

  @override
  Future<Report> updateReport(String id,
      {String? title, String? visibility}) async {
    calls.add('update:$id:$visibility');
    return reportsById[id]!;
  }

  @override
  Future<void> deleteReport(String id) async {
    calls.add('delete:$id');
    reportsById.remove(id);
  }

  @override
  Future<bool> saveReport(Report report, String format) async {
    saved.add(format == 'csv' ? reportCsv(report) : report.bodyMarkdown);
    return true;
  }

  @override
  Future<ReportExport> saveReportToFiles(String id) async {
    calls.add('files:$id');
    return ReportExport(
        fileId: 'file_1',
        fileName: 'tyre-cost.csv',
        link: Uri.parse('https://api.example.com/api/v1/files/links/example'));
  }

  @override
  Future<String> askAboutReport(String id, String question) async {
    calls.add('ask:$id:$question');
    return 'task_1';
  }

  @override
  Future<List<KnowledgeCollection>> collections() async => collectionsList;

  @override
  Future<KnowledgeCollection> createCollection(
      String name, String visibility) async {
    final c = KnowledgeCollection(
        id: 'kbc_${collectionsList.length + 1}',
        name: name,
        description: '',
        visibility: visibility,
        itemCount: 0,
        canManage: true);
    collectionsList.add(c);
    itemsByCollection[c.id] = [];
    return c;
  }

  @override
  Future<void> deleteCollection(String id) async =>
      collectionsList.removeWhere((c) => c.id == id);

  @override
  Future<LibraryPage<KnowledgeItem>> items(String collectionId,
      {String? query, int offset = 0}) async {
    final rows = itemsByCollection[collectionId] ?? [];
    return LibraryPage(rows, rows.length);
  }

  @override
  Future<KnowledgeItem> addNote(String collectionId, String title,
      String content, List<String> tags) async {
    final item = KnowledgeItem(
        id: 'kbi_${title.hashCode}',
        collectionId: collectionId,
        kind: 'note',
        title: title,
        tags: tags,
        indexState: 'indexed',
        available: true,
        createdAt: _now,
        content: content);
    itemsByCollection[collectionId]!.add(item);
    return item;
  }

  @override
  Future<void> removeItem(String id) async {
    for (final rows in itemsByCollection.values) {
      rows.removeWhere((i) => i.id == id);
    }
  }

  @override
  Future<KnowledgeAnswer> ask(String question, {String? collectionId}) async =>
      const KnowledgeAnswer(
          status: 'answered',
          answer: 'Fit winter tyres from November to March [1].',
          citations: [
            Citation(
                n: 1,
                title: 'Winter policy',
                provenance: 'memory',
                snippet: 'Fit winter tyres from November.',
                score: 0.9),
            Citation(
                n: 2,
                title: 'manual.pdf',
                provenance: 'file',
                snippet: 'Load index 91 carries 615 kg.',
                score: 0.8,
                page: 3,
                pageUnit: 'page'),
          ]);
}

Future<void> _pump(WidgetTester tester, Widget home,
    {Locale locale = const Locale('en'),
    Size size = const Size(390, 844),
    double textScale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: home,
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('rules', () {
    test('citations name the page, slide or sheet of a document', () {
      final cited = Citation.fromReader(JsonReader({
        'n': 1,
        'title': 'loads.xlsx',
        'provenance': 'file',
        'snippet': 'Load index 91',
        'score': 0.8,
        'page': 2,
        'page_unit': 'sheet',
        'page_label': 'Budget',
      }));
      const en = AnumLocalizations(Locale('en'));
      const ar = AnumLocalizations(Locale('ar'));
      expect(citationPlace(cited, en.t), 'sheet 2 (Budget)');
      expect(citationPlace(cited, ar.t), 'ورقة 2 (Budget)');
      final note = Citation.fromReader(JsonReader(
          {'n': 2, 'title': 'Winter', 'provenance': 'memory', 'score': 0.5}));
      expect(note.page, isNull);
      expect(citationPlace(note, en.t), '');
    });

    test('a scanned PDF says it has no extractable text', () {
      KnowledgeItem item(String state, String? error) =>
          KnowledgeItem.fromReader(JsonReader({
            'id': 'item_1',
            'collection_id': 'c',
            'kind': 'file',
            'title': 'scan.pdf',
            'index': {'state': state, 'error': error},
            'created_at': '2026-10-19T10:00:00Z',
          }));
      const en = AnumLocalizations(Locale('en'));
      expect(en.t(item('skipped', 'no_text_layer').indexLabelKey),
          'No extractable text, OCR not available');
      expect(
          en.t(item('skipped', 'encrypted').indexLabelKey), 'Not searchable');
      expect(en.t(item('indexed', null).indexLabelKey), 'Indexed');
    });

    test('the report block is hidden, also while it streams', () {
      expect(visibleReply('Rose 8%.\n```anum-report\n{"title":"x"}\n```'),
          'Rose 8%.');
      expect(visibleReply('Rose.\n```anum-report\n{"tit'), 'Rose.');
      expect(visibleReply('Rose.\n```anum-rep'), 'Rose.');
      expect(visibleReply('Run:\n```bash\nls\n```'), 'Run:\n```bash\nls\n```');
    });

    test('changes, tags and CSV', () {
      expect(changeLabel(8.46), '+8.5%');
      expect(changeLabel(-3), '-3%');
      expect(changeLabel(null), isNull);
      expect(parseTags(' Tyres, policy ,tyres,, Winter'),
          ['tyres', 'policy', 'winter']);
      final csv = reportCsv(_report('r'));
      expect(csv.split('\r\n').first, 'reason,cost');
      final risky = Report(
          id: 'r',
          type: 'custom',
          title: 'x',
          summary: '',
          metrics: const [],
          visibility: 'workspace',
          createdByKind: 'user',
          createdAt: _now,
          highlights: const [],
          columns: const ['a'],
          rows: const [
            ['=SUM(A1)'],
            ['a, "b"']
          ],
          bodyMarkdown: '',
          canManage: true,
          createdBy: 'u');
      expect(reportCsv(risky), "a\r\n'=SUM(A1)\r\n\"a, \"\"b\"\"\"\r\n");
      expect(reportFileName(_report('r'), 'csv'), 'tyre-cost-r-2026-10-11.csv');
    });

    test('a stored conversation and a done event carry report cards', () {
      final card = ReportCard.fromReader(JsonReader({
        'report_id': 'report_1',
        'turn_id': 't2',
        'title': 'Tyre cost',
        'type': 'tyre',
        'metrics': [
          {'label': 'Total', 'value': '12,400', 'unit': 'SAR'}
        ],
        'highlights': ['Punctures doubled'],
      }));
      final turns = turnsFromStored([
        ChatTurn.fromReader(JsonReader({
          'id': 't2',
          'conversation_id': 'c',
          'role': 'assistant',
          'content': 'Rose.',
          'status': 'complete',
          'created_at': '2026-10-11T09:00:00Z'
        }))
      ], [
        card
      ]);
      expect(turns.single.report?.reportId, 'report_1');

      var live = startAsk(const [], 'Cost?');
      live = applyAskEvent(
          live,
          SseEvent('done', {
            'assistant_turn': {
              'id': 't3',
              'conversation_id': 'c',
              'role': 'assistant',
              'content': 'Rose.',
              'status': 'complete',
              'created_at': '2026-10-11T09:00:00Z'
            },
            'report': {
              'report_id': 'report_2',
              'title': 'Tyre cost',
              'type': 'tyre',
              'metrics': <Object?>[],
              'highlights': <Object?>[]
            },
          }));
      expect(live.last.report?.reportId, 'report_2');
      expect(ReportCard.tryParse({'title': 'no id'}), isNull);
    });
  });

  group('screens', () {
    testWidgets('favorites: open a report, remove it, then the empty state',
        (tester) async {
      final repo = FakeLibrary();
      await _pump(
          tester, FavoritesScreen(repository: repo, onOpenOther: (_) {}));
      expect(find.text('Tyre cost report_1'), findsOneWidget);
      await tester.tap(find.text('Tyre cost report_1'));
      await tester.pumpAndSettle();
      expect(find.text('12,400 SAR'), findsOneWidget);
      expect(find.text('+8.5%'), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester
          .tap(find.byTooltip('Remove Tyre cost report_1 from favorites'));
      await tester.pumpAndSettle();
      expect(find.text('Nothing starred yet'), findsOneWidget);
    });

    testWidgets('reports: a failed load retries; generate opens the report',
        (tester) async {
      final repo = FakeLibrary()
        ..failReports = const ApiException(503, 'The database is unavailable');
      await _pump(tester, ReportsScreen(repository: repo));
      expect(
          find.textContaining('The database is unavailable'), findsOneWidget);
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Tyre cost report_1'), findsOneWidget);
      await tester.tap(find.text('Task summary').first);
      await tester.pumpAndSettle();
      expect(repo.calls, contains('generate:task_summary:7'));
      expect(find.text('Tyre cost report_new'), findsWidgets);
    });

    testWidgets('report detail: save CSV, make private, delete',
        (tester) async {
      final repo = FakeLibrary();
      await _pump(
          tester,
          Navigator(
              onGenerateRoute: (_) => MaterialPageRoute<void>(
                  builder: (_) =>
                      ReportDetailScreen(repository: repo, id: 'report_1'))));
      await tester.tap(find.text('Save CSV'));
      await tester.pumpAndSettle();
      expect(repo.saved.single, startsWith('reason,cost'));
      await tester.ensureVisible(find.text('Make private'));
      await tester.tap(find.text('Make private'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('update:report_1:private'));
      await tester.ensureVisible(find.text('Delete'));
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('delete:report_1'));
    });

    testWidgets('report detail: a missing report says so', (tester) async {
      await _pump(tester,
          ReportDetailScreen(repository: FakeLibrary(), id: 'report_gone'));
      expect(find.textContaining('This report is gone'), findsOneWidget);
    });

    testWidgets('knowledge base: create, add a note, ask with citations',
        (tester) async {
      final repo = FakeLibrary();
      await _pump(tester, KnowledgeScreen(repository: repo));
      expect(find.text('Start your knowledge base'), findsOneWidget);
      await tester.tap(find.text('New collection').first);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'Policies');
      await tester.tap(find.text('Create'));
      await tester.pumpAndSettle();
      expect(find.text('This collection is empty'), findsOneWidget);
      await tester.tap(find.text('Add a note').first);
      await tester.pumpAndSettle();
      final fields = find.descendant(
          of: find.byType(AlertDialog), matching: find.byType(TextField));
      await tester.enterText(fields.at(0), 'Winter policy');
      await tester.enterText(fields.at(1), 'Fit winter tyres from November.');
      await tester.enterText(fields.at(2), 'Policy, winter');
      await tester.tap(find.text('Save note'));
      await tester.pumpAndSettle();
      expect(find.text('Winter policy'), findsOneWidget);
      expect(
          repo.itemsByCollection['kbc_1']!.single.tags, ['policy', 'winter']);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'When?');
      await tester.tap(find.byTooltip('Ask'));
      await tester.pumpAndSettle();
      expect(find.textContaining('November to March [1]'), findsOneWidget);
      expect(find.textContaining('[1] Winter policy · Note'), findsOneWidget);
      expect(find.textContaining('[2] manual.pdf, page 3 · Document'),
          findsOneWidget);
    });

    testWidgets('the chat card opens the full report', (tester) async {
      String? opened;
      await _pump(
          tester,
          Scaffold(
              body: ReportAnswerCard(
                  card: const ReportCard(
                      reportId: 'report_1',
                      title: 'Tyre cost, September',
                      type: 'tyre',
                      metrics: [
                        ReportMetric(
                            label: 'Total cost',
                            value: '12,400',
                            unit: 'SAR',
                            changePercent: 8.5)
                      ],
                      highlights: [
                        'Punctures doubled'
                      ]),
                  onOpen: (id) => opened = id)));
      expect(find.text('12,400 SAR'), findsOneWidget);
      await tester.tap(find.text('View Full Report'));
      expect(opened, 'report_1');
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        testWidgets(
            '${locale.languageCode} at $width wide and 200% text lays out',
            (tester) async {
          final repo = FakeLibrary();
          await repo.createCollection('Tyre specifications', 'private');
          for (final screen in <Widget>[
            FavoritesScreen(repository: repo, onOpenOther: (_) {}),
            ReportsScreen(repository: repo),
            ReportDetailScreen(repository: repo, id: 'report_1'),
            KnowledgeScreen(repository: repo),
            Scaffold(
                body: SingleChildScrollView(
                    child:
                        LibrarySection(repository: repo, onOpenOther: (_) {}))),
          ]) {
            await _pump(tester, screen,
                locale: locale, size: Size(width, 900), textScale: 2);
            expect(tester.takeException(), isNull);
          }
          if (locale.languageCode == 'ar') {
            expect(find.text('وصول سريع'), findsOneWidget);
          }
        });
      }
    }
  });
}
