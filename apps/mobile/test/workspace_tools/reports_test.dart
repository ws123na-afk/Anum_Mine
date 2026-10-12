// Typed report sections on the library's reports (docs/reports.md): parsing,
// the CSV rows, the charts, and the report screen with tabs, Save to Files and
// "Ask ANUM", from 320 px at 200 percent text to 1440 px, English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/library/library_models.dart';
import 'package:anum_mobile/features/library/library_repository.dart';
import 'package:anum_mobile/features/library/library_screens.dart';
import 'package:anum_mobile/features/reports/report_charts.dart';
import 'package:anum_mobile/features/reports/report_sections.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'harness.dart';

final _now = DateTime.utc(2026, 10, 11, 9);

Map<String, Object?> reportJson({bool canManage = true}) => {
      'id': 'report_1',
      'type': 'tyre',
      'title': 'PMV Tyre Analysis',
      'summary': 'Tyre cost rose with high production.',
      'period_start': '2026-10-01',
      'period_end': '2026-10-31',
      'metrics': [
        {
          'label': 'Total cost',
          'value': '319,742',
          'unit': 'SAR',
          'change_percent': 66.6
        }
      ],
      'highlights': ['Qiddiya mixers lead the rise'],
      'data': {'columns': <String>[], 'rows': <Object?>[]},
      'body_markdown': '# PMV',
      'visibility': 'workspace',
      'created_by': 'user_local',
      'created_by_kind': 'agent',
      'created_at': _now.toIso8601String(),
      'can_manage': canManage,
      'tabs': ['Overview', 'High cost'],
      'sections': [
        {
          'type': 'metrics',
          'id': 'headline',
          'items': [
            {
              'label': 'Total cost',
              'value': 319742,
              'prefix': 'SAR',
              'change_pct': 66.6,
              'higher_is_better': false
            },
            {'label': 'Total tyres', 'value': 186, 'change_pct': 112},
            {
              'label': 'Cost per m2',
              'value': 5.1,
              'suffix': 'SAR',
              'decimals': 2,
              'change_pct': 4.9,
              'higher_is_better': false
            },
            {
              'label': 'Average tyre life',
              'value': 42000,
              'suffix': 'km',
              'change_pct': 15
            },
          ]
        },
        {
          'type': 'series',
          'id': 'trend',
          'title': 'Monthly cost trend',
          'prefix': 'SAR',
          'points': [
            for (final (i, m)
                in ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct'].indexed)
              {'label': m, 'value': 100000 + i * 10000}
          ]
        },
        {
          'type': 'breakdown',
          'id': 'category',
          'title': 'Cost by category',
          'items': [
            {'label': 'Tyre wear', 'value': 55},
            {'label': 'Puncture', 'value': 20},
            {'label': 'Sidewall', 'value': 15},
            {'label': 'Other', 'value': 10},
          ]
        },
        {
          'type': 'ranked',
          'id': 'vehicles',
          'title': 'Top high-cost vehicles',
          'tab': 'High cost',
          'higher_is_better': false,
          'items': [
            for (var i = 0; i < 7; i++)
              {
                'label': 'Vehicle group $i',
                'value': 90 - i,
                'change_pct': 82 - i
              }
          ]
        },
        {'type': 'gauge', 'id': 'future', 'title': 'Something newer'},
      ],
    };

class FakeReports implements LibraryRepository {
  final calls = <String>[];
  bool viewer = false;

  @override
  Future<Report> report(String id) async {
    if (id != 'report_1') throw const ApiException(404, 'Report not found');
    return Report.fromReader(JsonReader(reportJson(canManage: !viewer)));
  }

  @override
  Future<LibraryPage<Favorite>> favorites(
          {String? type, int offset = 0}) async =>
      const LibraryPage([], 0);

  @override
  Future<ReportExport> saveReportToFiles(String id) async {
    calls.add('files:$id');
    return ReportExport(
        fileId: 'file_1',
        fileName: 'pmv-tyre-analysis-2026-10-11.csv',
        link: Uri.parse('https://api.example.com/api/v1/files/links/example'));
  }

  @override
  Future<String> askAboutReport(String id, String question) async {
    calls.add('ask:$id:$question');
    return 'task_1';
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName}');
}

void main() {
  group('models', () {
    test('typed sections parse on the library report; unknown types are kept',
        () {
      final report = Report.fromReader(JsonReader(reportJson()));
      final layout = report.layout;
      expect(layout.sections.map((s) => s.runtimeType.toString()), [
        'MetricsSection',
        'SeriesSection',
        'BreakdownSection',
        'RankedSection',
        'UnsupportedSection',
      ]);
      expect(layout.sectionsFor('Overview').length, 4);
      expect(layout.sectionsFor('High cost').single.id, 'vehicles');
      final metric = (layout.sections.first as MetricsSection).items.first;
      expect(metric.style.format(metric.value), 'SAR 319,742');
      expect(metric.changeIsGood, isFalse);
      final perArea = (layout.sections.first as MetricsSection).items[2];
      expect(perArea.style.format(perArea.value), '5.10 SAR');
      // A library report without sections keeps an empty layout.
      final plain = Report.fromReader(
          JsonReader({...reportJson(), 'sections': null, 'tabs': null}));
      expect(plain.layout.isEmpty, isTrue);
    });

    test('the CSV holds the section rows when there is no data table', () {
      final report = Report.fromReader(JsonReader(reportJson()));
      final lines = reportCsv(report).split('\r\n');
      expect(
          lines.first, 'tab,section,type,label,value,unit,change_pct,detail');
      expect(lines,
          contains('Overview,headline,metrics,Total cost,319742,SAR,66.6,'));
      expect(
          lines,
          contains(
              'High cost,Top high-cost vehicles,ranked,Vehicle group 0,90,,82,'));
    });

    test('wrong shapes fail with the path', () {
      expect(
          () =>
              Report.fromReader(JsonReader({...reportJson(), 'sections': 'x'})),
          throwsA(isA<JsonShapeException>()));
      expect(() => SectionMetric.fromJson(JsonReader({'label': 'x'})),
          throwsA(isA<JsonShapeException>()));
    });

    test('number helpers', () {
      expect(formatNumber(1234567.891, decimals: 2), '1,234,567.89');
      expect(formatNumber(-42), '-42');
      expect(formatChange(66.6), '+66.6%');
      expect(formatChange(-4), '-4%');
      expect(const NumberStyle().compact(120000), '120k');
      expect(
          BreakdownDonut.folded([
            for (var i = 0; i < 8; i++) SectionPoint('p$i', 1),
          ], 'Other')
              .last
              .value,
          3);
    });
  });

  group('screen', () {
    testWidgets('report layouts at every width, 200% text and Arabic',
        (tester) async {
      await checkLayouts(
          tester,
          () => ReportDetailScreen(repository: FakeReports(), id: 'report_1'),
          find.text('Monthly cost trend'));
    });

    testWidgets('tabs, bars, save to Files and ask', (tester) async {
      final repo = FakeReports();
      String? copied;
      tester.binding.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') {
          copied = (call.arguments as Map)['text'] as String?;
        }
        return null;
      });
      addTearDown(() => tester.binding.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, null));
      final opened = await pumpScreen(
          tester, ReportDetailScreen(repository: repo, id: 'report_1'),
          width: 1024, height: 2600);
      expect(find.text('SAR 319,742'), findsOneWidget);
      expect(find.text('Cost by category'), findsOneWidget);
      expect(find.textContaining('cannot show a "gauge"'), findsOneWidget);
      expect(find.text('Oct: SAR 160,000'), findsOneWidget);
      await tester.tap(find.bySemanticsLabel('Apr: SAR 100,000'));
      await tester.pump();
      expect(find.text('Apr: SAR 100,000'), findsOneWidget);

      await tester.tap(find.text('High cost'));
      await tester.pump();
      expect(find.text('Top high-cost vehicles'), findsOneWidget);
      expect(find.text('Vehicle group 5'), findsNothing);
      await tester.tap(find.text('Show all 7'));
      await tester.pump();
      expect(find.text('Vehicle group 6'), findsOneWidget);

      await tester.tap(find.text('Save to Files and copy a link'));
      await tester.pump();
      await tester.pump();
      expect(repo.calls, ['files:report_1']);
      expect(copied, 'https://api.example.com/api/v1/files/links/example');
      expect(find.textContaining('Saved pmv-tyre-analysis'), findsOneWidget);

      await tester.enterText(find.byType(TextField), 'Why did cost rise?');
      await tester.tap(find.byTooltip('Ask ANUM (creates a task)'));
      await tester.pumpAndSettle();
      expect(repo.calls.last, 'ask:report_1:Why did cost rise?');
      expect(opened, ['/tasks/task_1']);
    });
  });
}
