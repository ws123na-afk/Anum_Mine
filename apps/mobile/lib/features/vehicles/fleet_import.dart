import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/infrastructure/picked_file_size.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_export.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';

/// A client fleet imported on the phone, as on the web's Vehicles, Import
/// (docs/vehicles.md#fleet-import): what the file holds (vehicles, fuel,
/// service or odometer readings), the country for rows without one and the
/// date order; an Excel (.xlsx) or CSV file picked on the phone and read into
/// memory (nothing is stored on the phone); the server's preview with the
/// column for each field (chosen again and checked again), the counts, the
/// rows with their problems and the action for each; then Import after a
/// confirmation, all or nothing, with the preview's fingerprint. The same
/// routes and rules as the web: workspace owners only (`vehicle:manage`),
/// and every refusal is shown in the API's words.

const fleetKinds = ['vehicles', 'fuel', 'service', 'odometer'];
const fleetStatuses = [
  'new',
  'exists',
  'duplicate_in_file',
  'imported',
  'invalid'
];
const fleetImportPage = 50;
const fleetMaxWorkbookBytes = 5000000, fleetMaxTextBytes = 6000000;

/// The header row of each kind's template (English column names the server
/// recognises), the same as the web's `TEMPLATE_HEADERS`.
const fleetTemplateHeaders = <String, List<String>>{
  'vehicles': [
    'Registration',
    'Country',
    'Fleet number',
    'Make',
    'Model',
    'Year',
    'VIN',
    'Vehicle type',
    'Fuel type',
    'Tank litres',
    'Odometer',
    'Odometer date',
    'Insurance expiry',
    'Registration expiry',
    'Inspection expiry',
    'Notes'
  ],
  'fuel': [
    'Registration',
    'Date',
    'Litres',
    'Total',
    'Price per litre',
    'Odometer',
    'Station',
    'Fuel type',
    'Full tank',
    'Currency',
    'Notes'
  ],
  'service': [
    'Registration',
    'Date',
    'Service type',
    'Total',
    'Odometer',
    'Workshop',
    'Description',
    'Reference',
    'Next due date',
    'Next due km',
    'Currency'
  ],
  'odometer': ['Registration', 'Date', 'Odometer'],
};

class FleetIssue {
  const FleetIssue(this.code, this.field, this.value, this.text);
  final String code, value, text;
  final String? field;
}

class FleetImportRow {
  const FleetImportRow({
    required this.row,
    required this.registration,
    required this.summary,
    required this.status,
    required this.action,
    required this.actions,
    required this.issues,
    this.duplicateOf,
  });
  factory FleetImportRow.fromJson(JsonReader j) => FleetImportRow(
        row: j.integer('row'),
        registration: j.optString('registration') ?? '',
        summary: {
          for (final MapEntry(:key, :value)
              in (j.optMap('summary') ?? const {}).entries)
            if (value != null && '$value'.isNotEmpty) key: '$value'
        },
        status: j.string('status'),
        action: j.string('action'),
        actions: j.optStrings('actions') ?? const [],
        duplicateOf: j.optInt('duplicate_of'),
        issues: j.optObjects(
            'issues',
            (i) => FleetIssue(
                i.optString('code') ?? 'other',
                i.optString('field'),
                i.optString('value') ?? '',
                i.optString('text') ?? '')),
      );
  final int row;
  final String registration, status, action;
  final Map<String, String> summary;
  final List<String> actions;
  final List<FleetIssue> issues;
  final int? duplicateOf;
}

class FleetImportPreview {
  const FleetImportPreview({
    required this.columns,
    required this.fields,
    required this.mapping,
    required this.rows,
    required this.counts,
    required this.fingerprint,
  });
  factory FleetImportPreview.fromJson(JsonReader j) => FleetImportPreview(
        columns: j.optStrings('columns') ?? const [],
        fields: j.optObjects(
            'fields',
            (f) => (
                  field: f.string('field'),
                  required: f.optBool('required') ?? false
                )),
        mapping: {
          for (final MapEntry(:key, :value)
              in (j.optMap('mapping') ?? const {}).entries)
            key: '$value'
        },
        rows: j.optObjects('rows', FleetImportRow.fromJson),
        counts: {
          for (final MapEntry(:key, :value)
              in (j.optMap('counts') ?? const {}).entries)
            if (value is num) key: value.toInt()
        },
        fingerprint: j.string('fingerprint'),
      );
  final List<String> columns;
  final List<({String field, bool required})> fields;
  final Map<String, String> mapping;
  final List<FleetImportRow> rows;
  final Map<String, int> counts;
  final String fingerprint;
}

class FleetImportResult {
  const FleetImportResult(
      this.created, this.updated, this.skipped, this.readings);
  factory FleetImportResult.fromJson(JsonReader j) => FleetImportResult(
      j.optInt('created') ?? 0,
      j.optInt('updated') ?? 0,
      j.optInt('skipped') ?? 0,
      j.optInt('readings') ?? 0);
  final int created, updated, skipped, readings;
}

/// A file read for the import: CSV text, or the workbook as base64.
class FleetFile {
  const FleetFile(this.name, this.format, this.content);
  final String name, format, content;

  Map<String, Object?> body() => format == 'xlsx'
      ? {'format': 'xlsx', 'file_base64': content, 'file_name': name}
      : {'format': 'csv', 'csv': content, 'file_name': name};
}

/// The picked bytes as a [FleetFile], or a [FormatException] whose message is
/// a code: `type`, `empty`, `workbookSize`, `size` or `read`.
FleetFile readFleetFile(String name, Uint8List bytes) {
  final dot = name.lastIndexOf('.');
  final extension = dot < 0 ? '' : name.substring(dot + 1).toLowerCase();
  final format = switch (extension) {
    'csv' || 'txt' => 'csv',
    'xlsx' => 'xlsx',
    _ => null,
  };
  if (format == null) throw const FormatException('type');
  if (bytes.isEmpty) throw const FormatException('empty');
  if (format == 'xlsx') {
    if (bytes.length > fleetMaxWorkbookBytes) {
      throw const FormatException('workbookSize');
    }
    return FleetFile(name, format, base64Encode(bytes));
  }
  if (bytes.length > fleetMaxTextBytes) throw const FormatException('size');
  try {
    // A byte order mark from Excel's CSV export is dropped.
    final start = bytes.length >= 3 &&
            bytes[0] == 0xEF &&
            bytes[1] == 0xBB &&
            bytes[2] == 0xBF
        ? 3
        : 0;
    return FleetFile(name, format, utf8.decode(bytes.sublist(start)));
  } on FormatException {
    throw const FormatException('read');
  }
}

typedef FleetFilePicker = Future<({String name, Uint8List bytes})?> Function();

/// The system picker, limited to CSV and Excel. The file is read into memory
/// and the picker's temporary copy removed.
Future<({String name, Uint8List bytes})?> pickFleetFile() async {
  final file = await FilePicker.pickFile(
      type: FileType.custom, allowedExtensions: const ['csv', 'txt', 'xlsx']);
  if (file == null) return null;
  try {
    final size = await pickedFileSize(file);
    if (size != null && size > fleetMaxTextBytes) {
      throw const FormatException('size');
    }
    return (name: file.name, bytes: await file.readAsBytes());
  } finally {
    try {
      await FilePicker.clearTemporaryFiles();
    } on Object {
      // Best effort: the copy is in the app's own cache, never shared.
    }
  }
}

class FleetImportApi {
  const FleetImportApi(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/vehicles/fleet/import';

  Map<String, Object?> _body(FleetFile file, String kind, String country,
          String dateOrder, Map<String, String>? mapping) =>
      {
        'kind': kind,
        ...file.body(),
        'mapping': mapping,
        'country': country,
        'date_order': dateOrder,
      };

  Future<FleetImportPreview> preview(FleetFile file,
          {required String kind,
          required String country,
          required String dateOrder,
          Map<String, String>? mapping}) async =>
      FleetImportPreview.fromJson(JsonReader(await api.request(
          'POST', '$_base/preview',
          body: _body(file, kind, country, dateOrder, mapping))));

  /// Sends only the decisions that differ from the preview's suggestion.
  Future<FleetImportResult> apply(FleetFile file,
          {required String kind,
          required String country,
          required String dateOrder,
          required Map<String, String> mapping,
          required String fingerprint,
          required List<Map<String, Object?>> decisions}) async =>
      FleetImportResult.fromJson(
          JsonReader(await api.request('POST', '$_base/apply', body: {
        ..._body(file, kind, country, dateOrder, mapping),
        'fingerprint': fingerprint,
        'decisions': decisions,
      })));
}

/// Every row's action as the person left it (the suggestion unless they
/// chose another allowed one).
String fleetActionFor(FleetImportRow row, Map<int, String> choices) {
  final chosen = choices[row.row];
  return chosen != null && row.actions.contains(chosen) ? chosen : row.action;
}

class FleetImportStrings extends ModuleStrings {
  const FleetImportStrings(bool arabic) : super(arabic, _en, _ar);
  static FleetImportStrings of(BuildContext context) =>
      FleetImportStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  /// A field's name, or the API's own name for one this catalog lacks.
  String field(String name) =>
      maybe('field.$name') ?? name.replaceAll('_', ' ');

  /// A row's problem in the reader's language (the server's sentence for
  /// anything it words itself).
  String issue(FleetIssue issue, String registration) {
    final text = maybe('issue.${issue.code}');
    if (issue.code == 'other' || text == null) return issue.text;
    return t('issue.${issue.code}', {
      'field': issue.field == null ? '' : field(issue.field!),
      'value': issue.value,
      'registration': registration,
    });
  }

  static const _en = <String, String>{
    'open': 'Import a fleet',
    'title': 'Import a client\'s fleet',
    'ownersTitle': 'Importing a fleet is for workspace owners',
    'ownersOnly':
        'A file brings many vehicles and records in at once, so only people who manage this workspace\'s vehicles (workspace owners) can import one. Ask a workspace owner to import the file.',
    'intro':
        'Choose what the file holds, then an Excel (.xlsx) or CSV file whose first row names the columns. ANUM shows what it would do with each row before anything is saved, and saves every row or none.',
    'limits':
        'Up to 5,000 rows and 5 MB per file. Import the vehicles first, then their fuel, service and odometer files. Imported vehicles are business vehicles.',
    'settings': 'About the file',
    'kind': 'The file holds',
    'country': 'Country for rows without one',
    'country.AE': 'United Arab Emirates',
    'country.SA': 'Saudi Arabia',
    'dates': 'Dates such as 03/04/2026 mean',
    'dmy': '3 April (day first)',
    'mdy': 'March 4 (month first)',
    'hint.vehicles':
        'One row per vehicle: registration, and any of fleet number, make, model, year, VIN, type, fuel, odometer and the insurance, registration and inspection expiry dates.',
    'hint.fuel':
        'One row per fill: registration, date and litres, and any of total, price per litre, odometer and station. Totals are the record\'s figure, not a payment.',
    'hint.service':
        'One row per job or fee: registration and date, and any of type (oil, tyres, repair, fine, toll...), total, odometer, workshop and next due date.',
    'hint.odometer': 'One row per reading: registration, date and kilometres.',
    'template': 'Save a {kind} template',
    'choose': 'Choose a file',
    'file': 'File: {name}',
    'another': 'Choose another file',
    'reading': 'Reading the file...',
    'done':
        'Imported: {created} added, {updated} updated, {skipped} skipped, {readings} odometer readings.',
    'mapping': 'Columns',
    'mappingIntro':
        'ANUM matched the columns from their names. Change any that are wrong, then check again.',
    'mappingNone':
        'ANUM could not tell which column holds each required field. Choose them, then check the file.',
    'required': 'required',
    'none': 'Not in the file',
    'check': 'Check again',
    'checking': 'Checking...',
    'summary': 'What the file would do: {rows} rows',
    'search': 'Search rows',
    'allRows': 'All rows',
    'noMatch': 'No row matches.',
    'row': 'Row {row}',
    'repeats': 'Repeats row {row}',
    'actionFor': 'Action for row {row}',
    'totals': '{create} to add, {update} to update, {skip} to skip',
    'apply': 'Import',
    'applying': 'Importing...',
    'nothing': 'Nothing to import: every row is skipped.',
    'confirmTitle': 'Import this file?',
    'confirmText':
        '{kind}: {create} rows will be added and {update} updated; {skip} are skipped. Everything is saved together, or nothing if a row is refused.',
    'showing': 'Showing {shown} of {total}',
    'more': 'Show more',
    'failed': 'The file could not be checked: {message}',
    'applyFailed': 'Nothing was imported: {message}',
    'err.type': 'Choose an Excel (.xlsx) or CSV file.',
    'err.empty': 'That file is empty.',
    'err.workbookSize':
        'The Excel file is larger than 5 MB. Split it, or save it as CSV.',
    'err.size':
        'The file is too large. Split it into files of at most 5,000 rows.',
    'err.read': 'The file could not be read as text. Save it as CSV (UTF-8).',
    'err.pick': 'The file could not be opened: {message}',
    'kind.vehicles': 'Vehicles',
    'kind.fuel': 'Fuel entries',
    'kind.service': 'Service and fees',
    'kind.odometer': 'Odometer readings',
    'status.new': 'New',
    'status.exists': 'Already here',
    'status.duplicate_in_file': 'Repeated in the file',
    'status.imported': 'Imported before',
    'status.invalid': 'Has a problem',
    'action.create': 'Add',
    'action.update': 'Update',
    'action.skip': 'Skip',
    'issue.required': '{field} is empty.',
    'issue.number': '{field}: "{value}" is not a number.',
    'issue.date':
        '{field}: "{value}" is not a date (use 2026-03-31 or 31/03/2026).',
    'issue.future': '{field}: the date is in the future.',
    'issue.range': '{field}: the value is outside what ANUM accepts.',
    'issue.choice': '{field}: "{value}" is not one of the values ANUM knows.',
    'issue.unknown_vehicle':
        'No vehicle {registration} in this workspace. Import the vehicles first.',
    'issue.ambiguous_vehicle':
        'Two vehicles have the registration {registration}. Add a Country column.',
    'issue.archived_vehicle':
        'The vehicle {registration} is archived. Restore it first, or skip the row.',
    'issue.odometer':
        'The odometer would go down here: another reading of this vehicle is higher. Fix the kilometres or skip the row.',
    'issue.registration':
        'Registration: use letters, digits, spaces or dashes, up to 20 characters.',
    'issue.vin': 'VIN: 11 to 17 letters and digits, without I, O or Q.',
    'issue.currency': '{field}: use a currency code such as AED or SAR.',
    'field.registration': 'Registration',
    'field.country': 'Country',
    'field.nickname': 'Fleet number',
    'field.make': 'Make',
    'field.model': 'Model',
    'field.year': 'Year',
    'field.vin': 'VIN',
    'field.vehicle_type': 'Vehicle type',
    'field.fuel_type': 'Fuel type',
    'field.tank_litres': 'Tank litres',
    'field.currency': 'Currency',
    'field.odometer_km': 'Odometer km',
    'field.odometer_on': 'Odometer date',
    'field.acquisition_date': 'Acquired on',
    'field.purchase_price': 'Purchase price',
    'field.insurance_expires_on': 'Insurance expiry',
    'field.registration_expires_on': 'Registration expiry',
    'field.inspection_expires_on': 'Inspection expiry',
    'field.notes': 'Notes',
    'field.date': 'Date',
    'field.litres': 'Litres',
    'field.total': 'Total',
    'field.price_per_litre': 'Price per litre',
    'field.station': 'Station',
    'field.full_tank': 'Full tank',
    'field.kind': 'Service type',
    'field.workshop': 'Workshop',
    'field.description': 'Description',
    'field.reference': 'Reference',
    'field.next_due_on': 'Next due date',
    'field.next_due_km': 'Next due km',
  };

  static const _ar = <String, String>{
    'open': 'استيراد أسطول',
    'title': 'استيراد أسطول عميل',
    'ownersTitle': 'استيراد الأسطول لمالكي مساحة العمل',
    'ownersOnly':
        'يُدخل الملف مركبات وسجلات كثيرة دفعة واحدة، لذلك لا يستورده إلا من يدير مركبات مساحة العمل هذه (مالكو مساحة العمل). اطلب من مالك مساحة العمل استيراد الملف.',
    'intro':
        'اختر ما يحتويه الملف، ثم ملف Excel (.xlsx) أو CSV يسمّي صفه الأول الأعمدة. يعرض ANUM ما سيفعله بكل صف قبل حفظ أي شيء، ويحفظ كل الصفوف أو لا شيء.',
    'limits':
        'حتى 5,000 صف و5 ميغابايت لكل ملف. استورد المركبات أولًا، ثم ملفات الوقود والخدمة والعداد. المركبات المستوردة مركبات أعمال.',
    'settings': 'عن الملف',
    'kind': 'يحتوي الملف على',
    'country': 'الدولة للصفوف التي لا تذكرها',
    'country.AE': 'الإمارات',
    'country.SA': 'السعودية',
    'dates': 'تاريخ مثل 03/04/2026 يعني',
    'dmy': '3 أبريل (اليوم أولًا)',
    'mdy': '4 مارس (الشهر أولًا)',
    'hint.vehicles':
        'صف لكل مركبة: رقم اللوحة، وأي من رقم الأسطول والماركة والطراز والسنة ورقم الهيكل والنوع والوقود والعداد وتواريخ انتهاء التأمين والتسجيل والفحص.',
    'hint.fuel':
        'صف لكل تعبئة: رقم اللوحة والتاريخ واللترات، وأي من الإجمالي وسعر اللتر والعداد والمحطة. الإجمالي هو مبلغ السجل وليس دفعة.',
    'hint.service':
        'صف لكل عمل أو رسم: رقم اللوحة والتاريخ، وأي من النوع (زيت، إطارات، إصلاح، مخالفة، رسوم طريق...) والإجمالي والعداد والورشة والموعد القادم.',
    'hint.odometer': 'صف لكل قراءة: رقم اللوحة والتاريخ والكيلومترات.',
    'template': 'حفظ نموذج {kind}',
    'choose': 'اختر ملفًا',
    'file': 'الملف: {name}',
    'another': 'اختر ملفًا آخر',
    'reading': 'جارٍ قراءة الملف...',
    'done':
        'تم الاستيراد: أُضيف {created}، وحُدّث {updated}، وتُخطي {skipped}، و{readings} قراءة عداد.',
    'mapping': 'الأعمدة',
    'mappingIntro':
        'طابق ANUM الأعمدة من أسمائها. غيّر أي عمود خاطئ ثم تحقق مرة أخرى.',
    'mappingNone':
        'لم يتمكن ANUM من معرفة العمود الذي يحتوي كل حقل مطلوب. اخترها ثم تحقق من الملف.',
    'required': 'مطلوب',
    'none': 'غير موجود في الملف',
    'check': 'تحقق مرة أخرى',
    'checking': 'جارٍ التحقق...',
    'summary': 'ما سيفعله الملف: {rows} صف',
    'search': 'ابحث في الصفوف',
    'allRows': 'كل الصفوف',
    'noMatch': 'لا يوجد صف مطابق.',
    'row': 'الصف {row}',
    'repeats': 'يكرر الصف {row}',
    'actionFor': 'الإجراء للصف {row}',
    'totals': '{create} للإضافة، {update} للتحديث، {skip} للتخطي',
    'apply': 'استيراد',
    'applying': 'جارٍ الاستيراد...',
    'nothing': 'لا شيء للاستيراد: كل الصفوف متخطاة.',
    'confirmTitle': 'استيراد هذا الملف؟',
    'confirmText':
        '{kind}: ستُضاف {create} صفوف وتُحدّث {update}، وتُتخطى {skip}. يُحفظ كل شيء معًا، أو لا شيء إذا رُفض صف.',
    'showing': 'عرض {shown} من {total}',
    'more': 'عرض المزيد',
    'failed': 'تعذر فحص الملف: {message}',
    'applyFailed': 'لم يُستورد شيء: {message}',
    'err.type': 'اختر ملف Excel (.xlsx) أو CSV.',
    'err.empty': 'هذا الملف فارغ.',
    'err.workbookSize':
        'ملف Excel أكبر من 5 ميغابايت. قسّمه أو احفظه بصيغة CSV.',
    'err.size': 'الملف كبير جدًا. قسّمه إلى ملفات لا تتجاوز 5,000 صف.',
    'err.read': 'تعذرت قراءة الملف نصًا. احفظه بصيغة CSV (UTF-8).',
    'err.pick': 'تعذر فتح الملف: {message}',
    'kind.vehicles': 'المركبات',
    'kind.fuel': 'تعبئات الوقود',
    'kind.service': 'الخدمة والرسوم',
    'kind.odometer': 'قراءات العداد',
    'status.new': 'جديد',
    'status.exists': 'موجود مسبقًا',
    'status.duplicate_in_file': 'مكرر في الملف',
    'status.imported': 'مستورد سابقًا',
    'status.invalid': 'فيه مشكلة',
    'action.create': 'إضافة',
    'action.update': 'تحديث',
    'action.skip': 'تخطي',
    'issue.required': '{field} فارغ.',
    'issue.number': '{field}: "{value}" ليس رقمًا.',
    'issue.date':
        '{field}: "{value}" ليس تاريخًا (استخدم 2026-03-31 أو 31/03/2026).',
    'issue.future': '{field}: التاريخ في المستقبل.',
    'issue.range': '{field}: القيمة خارج ما يقبله ANUM.',
    'issue.choice': '{field}: "{value}" ليست من القيم التي يعرفها ANUM.',
    'issue.unknown_vehicle':
        'لا توجد مركبة {registration} في مساحة العمل هذه. استورد المركبات أولًا.',
    'issue.ambiguous_vehicle':
        'مركبتان تحملان رقم اللوحة {registration}. أضف عمود الدولة.',
    'issue.archived_vehicle':
        'المركبة {registration} مؤرشفة. استعدها أولًا أو تخطَّ الصف.',
    'issue.odometer':
        'سينخفض العداد هنا: قراءة أخرى لهذه المركبة أعلى. صحح الكيلومترات أو تخطَّ الصف.',
    'issue.registration':
        'رقم اللوحة: استخدم حروفًا أو أرقامًا أو مسافات أو شرطات، حتى 20 حرفًا.',
    'issue.vin': 'رقم الهيكل: من 11 إلى 17 حرفًا ورقمًا، دون I أو O أو Q.',
    'issue.currency': '{field}: استخدم رمز عملة مثل AED أو SAR.',
    'field.registration': 'رقم اللوحة',
    'field.country': 'الدولة',
    'field.nickname': 'رقم الأسطول',
    'field.make': 'الماركة',
    'field.model': 'الطراز',
    'field.year': 'السنة',
    'field.vin': 'رقم الهيكل',
    'field.vehicle_type': 'نوع المركبة',
    'field.fuel_type': 'نوع الوقود',
    'field.tank_litres': 'سعة الخزان باللتر',
    'field.currency': 'العملة',
    'field.odometer_km': 'العداد بالكيلومتر',
    'field.odometer_on': 'تاريخ قراءة العداد',
    'field.acquisition_date': 'تاريخ الاقتناء',
    'field.purchase_price': 'سعر الشراء',
    'field.insurance_expires_on': 'انتهاء التأمين',
    'field.registration_expires_on': 'انتهاء التسجيل',
    'field.inspection_expires_on': 'انتهاء الفحص',
    'field.notes': 'ملاحظات',
    'field.date': 'التاريخ',
    'field.litres': 'اللترات',
    'field.total': 'الإجمالي',
    'field.price_per_litre': 'سعر اللتر',
    'field.station': 'المحطة',
    'field.full_tank': 'خزان ممتلئ',
    'field.kind': 'نوع الخدمة',
    'field.workshop': 'الورشة',
    'field.description': 'الوصف',
    'field.reference': 'المرجع',
    'field.next_due_on': 'تاريخ الاستحقاق القادم',
    'field.next_due_km': 'كيلومترات الاستحقاق القادم',
  };
}

AnumTone _statusTone(String status) => switch (status) {
      'new' => AnumTone.ok,
      'invalid' => AnumTone.stop,
      'exists' || 'duplicate_in_file' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

class FleetImportScreen extends StatefulWidget {
  const FleetImportScreen(
      {required this.api,
      this.picker = pickFleetFile,
      this.saver = const SystemExportSaver(),
      this.onImported,
      super.key});
  final FleetImportApi api;
  final FleetFilePicker picker;

  /// Where a template goes (the system save dialog; a fake in tests).
  final ExportSaver saver;

  /// After an import was saved (the vehicles list reloads).
  final VoidCallback? onImported;

  @override
  State<FleetImportScreen> createState() => _FleetImportScreenState();
}

class _FleetImportScreenState extends State<FleetImportScreen> {
  String _kind = 'vehicles', _country = 'AE', _dateOrder = 'dmy';
  FleetFile? _file;
  FleetImportPreview? _preview;
  Map<String, String> _mapping = {};
  final Map<int, String> _choices = {};
  String? _status;
  String _query = '';
  int _shown = fleetImportPage;
  String? _busy; // reading, checking, importing
  String? _error;
  bool _denied = false;
  FleetImportResult? _result;
  int _generation = 0;

  void _reset() => setState(() {
        _file = null;
        _preview = null;
        _mapping = {};
        _choices.clear();
        _error = null;
        _status = null;
        _query = '';
      });

  Future<void> _check(FleetFile file, Map<String, String>? mapping) async {
    final s = FleetImportStrings.of(context);
    final generation = ++_generation;
    setState(() {
      _busy = mapping == null ? 'reading' : 'checking';
      _error = null;
      _result = null;
    });
    try {
      final next = await widget.api.preview(file,
          kind: _kind,
          country: _country,
          dateOrder: _dateOrder,
          mapping: mapping);
      if (!mounted || generation != _generation) return;
      setState(() {
        _preview = next;
        _mapping = {...next.mapping};
        _choices.clear();
        _shown = fleetImportPage;
        _busy = null;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _busy = null;
        _denied = failure.denied;
        _error = s.t('failed', {'message': failure.message});
      });
    }
  }

  Future<void> _choose() async {
    final s = FleetImportStrings.of(context);
    setState(() {
      _error = null;
      _result = null;
      _preview = null;
    });
    final ({String name, Uint8List bytes})? picked;
    try {
      picked = await widget.picker();
    } on FormatException catch (error) {
      if (mounted) setState(() => _error = s.t('err.${error.message}'));
      return;
    } on Object catch (error) {
      if (mounted) {
        setState(() => _error =
            s.t('err.pick', {'message': RecordsFailure.from(error).message}));
      }
      return;
    }
    if (picked == null || !mounted) return;
    final FleetFile file;
    try {
      file = readFleetFile(picked.name, picked.bytes);
    } on FormatException catch (error) {
      setState(() => _error = s.t('err.${error.message}'));
      return;
    }
    setState(() => _file = file);
    await _check(file, null);
  }

  Future<void> _template() async {
    await exportCsvFile(context,
        load: () async => '${fleetTemplateHeaders[_kind]!.join(',')}\r\n',
        fileName: 'anum-fleet-$_kind-template.csv',
        saver: widget.saver);
  }

  Future<void> _apply(FleetImportPreview preview, FleetFile file,
      Map<String, int> totals) async {
    final s = FleetImportStrings.of(context);
    final sure = await confirmRecords(context,
        title: s.t('confirmTitle'),
        message: s.t('confirmText', {
          'kind': s.t('kind.$_kind'),
          'create': totals['create']!,
          'update': totals['update']!,
          'skip': totals['skip']!,
        }),
        action: s.t('apply'));
    if (!sure || !mounted) return;
    setState(() {
      _busy = 'importing';
      _error = null;
    });
    try {
      final done = await widget.api.apply(file,
          kind: _kind,
          country: _country,
          dateOrder: _dateOrder,
          mapping: _mapping,
          fingerprint: preview.fingerprint,
          decisions: [
            for (final row in preview.rows)
              if (fleetActionFor(row, _choices) != row.action)
                {'row': row.row, 'action': fleetActionFor(row, _choices)}
          ]);
      if (!mounted) return;
      setState(() {
        _busy = null;
        _result = done;
        _preview = null;
        _file = null;
        _choices.clear();
      });
      widget.onImported?.call();
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _busy = null;
        _denied = failure.denied;
        _error = s.t('applyFailed', {'message': failure.message});
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FleetImportStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final locked = _busy != null || _file != null;
    DropdownButtonFormField<String> pick(String key, String label, String value,
            Map<String, String> options, ValueChanged<String> on) =>
        DropdownButtonFormField<String>(
          key: Key(key),
          initialValue: value,
          isExpanded: true,
          decoration: InputDecoration(labelText: label),
          items: [
            for (final MapEntry(:key, :value) in options.entries)
              DropdownMenuItem(
                  value: key,
                  child: Text(value, overflow: TextOverflow.ellipsis)),
          ],
          onChanged: locked ? null : (v) => setState(() => on(v ?? value)),
        );

    final preview = _preview, file = _file;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('title'))),
      body: AnumPage(children: [
        Text(s.t('intro'), style: muted),
        Text(s.t('limits'), style: muted),
        if (_denied)
          AnumEmpty(
              key: const Key('fleet-import-denied'),
              icon: Icons.lock_outline,
              title: s.t('ownersTitle'),
              message: s.t('ownersOnly')),
        RecordsSection(
          key: const Key('fleet-import-settings'),
          title: s.t('settings'),
          children: [
            pick(
                'fleet-import-kind',
                s.t('kind'),
                _kind,
                {for (final k in fleetKinds) k: s.t('kind.$k')},
                (v) => _kind = v),
            pick(
                'fleet-import-country',
                s.t('country'),
                _country,
                {'AE': s.t('country.AE'), 'SA': s.t('country.SA')},
                (v) => _country = v),
            pick('fleet-import-dates', s.t('dates'), _dateOrder,
                {'dmy': s.t('dmy'), 'mdy': s.t('mdy')}, (v) => _dateOrder = v),
            Text(s.t('hint.$_kind'), style: muted),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: const Key('fleet-import-template'),
                onPressed: _template,
                icon: const Icon(Icons.download_outlined),
                label: Text(s.t('template', {'kind': s.t('kind.$_kind')})),
              ),
            ),
          ],
        ),
        if (file == null)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('fleet-import-choose'),
              onPressed: _busy != null ? null : _choose,
              icon: const Icon(Icons.upload_file),
              label: Text(s.t('choose')),
            ),
          )
        else
          Wrap(
              spacing: AnumSpacing.sm,
              runSpacing: AnumSpacing.xs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(s.t('file', {'name': file.name}),
                    key: const Key('fleet-import-file')),
                OutlinedButton(
                  key: const Key('fleet-import-another'),
                  onPressed: _busy != null ? null : _reset,
                  child: Text(s.t('another')),
                ),
              ]),
        if (_busy == 'reading')
          Row(children: [
            const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(child: Text(s.t('reading'))),
          ]),
        if (_error != null)
          Semantics(
            liveRegion: true,
            child: Text(_error!,
                key: const Key('fleet-import-error'),
                style: TextStyle(color: p.stop)),
          ),
        if (_result case final r?)
          Semantics(
            liveRegion: true,
            child: Text(
                s.t('done', {
                  'created': r.created,
                  'updated': r.updated,
                  'skipped': r.skipped,
                  'readings': r.readings,
                }),
                key: const Key('fleet-import-done'),
                style: TextStyle(color: p.ok)),
          ),
        if (preview != null && file != null) ..._previewParts(s, preview, file),
      ]),
    );
  }

  List<Widget> _previewParts(
      FleetImportStrings s, FleetImportPreview preview, FleetFile file) {
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final decided = [
      for (final row in preview.rows) fleetActionFor(row, _choices)
    ];
    final totals = {
      for (final a in const ['create', 'update', 'skip'])
        a: decided.where((d) => d == a).length
    };
    final needle = _query.trim().toLowerCase();
    final rows = [
      for (final row in preview.rows)
        if ((_status == null || row.status == _status) &&
            (needle.isEmpty ||
                row.registration.toLowerCase().contains(needle) ||
                row.summary.values
                    .any((v) => v.toLowerCase().contains(needle))))
          row
    ];
    final busy = _busy != null;
    return [
      RecordsSection(
        key: const Key('fleet-import-mapping'),
        title: s.t('mapping'),
        children: [
          Text(s.t(preview.rows.isEmpty ? 'mappingNone' : 'mappingIntro'),
              style: muted),
          for (final f in preview.fields)
            DropdownButtonFormField<String>(
              key: Key('fleet-map-${f.field}-${_mapping[f.field] ?? ''}'),
              initialValue: _mapping[f.field] ?? '',
              isExpanded: true,
              decoration: InputDecoration(
                  labelText: f.required
                      ? '${s.field(f.field)} (${s.t('required')})'
                      : s.field(f.field)),
              items: [
                DropdownMenuItem(value: '', child: Text(s.t('none'))),
                for (final c in preview.columns)
                  if (c.isNotEmpty)
                    DropdownMenuItem(
                        value: c,
                        child: Text(c, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: busy
                  ? null
                  : (v) => setState(() {
                        if (v == null || v.isEmpty) {
                          _mapping.remove(f.field);
                        } else {
                          _mapping[f.field] = v;
                        }
                      }),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('fleet-import-check'),
              onPressed: busy ? null : () => _check(file, {..._mapping}),
              icon: const Icon(Icons.refresh),
              label: Text(s.t(_busy == 'checking' ? 'checking' : 'check')),
            ),
          ),
        ],
      ),
      if (preview.rows.isNotEmpty) ...[
        Semantics(
          header: true,
          child: Text(
              s.t('summary',
                  {'rows': preview.counts['rows'] ?? preview.rows.length}),
              style: Theme.of(context).textTheme.titleMedium),
        ),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          ChoiceChip(
            key: const Key('fleet-filter-all'),
            label: Text(s.t('allRows')),
            selected: _status == null,
            onSelected: (_) => setState(() {
              _status = null;
              _shown = fleetImportPage;
            }),
          ),
          for (final st in fleetStatuses)
            ChoiceChip(
              key: Key('fleet-filter-$st'),
              label: Text('${s.t('status.$st')}: ${preview.counts[st] ?? 0}'),
              selected: _status == st,
              onSelected: (on) => setState(() {
                _status = on ? st : null;
                _shown = fleetImportPage;
              }),
            ),
        ]),
        TextField(
          key: const Key('fleet-import-search'),
          decoration: InputDecoration(
              labelText: s.t('search'), prefixIcon: const Icon(Icons.search)),
          onChanged: (v) => setState(() {
            _query = v;
            _shown = fleetImportPage;
          }),
        ),
        if (rows.isEmpty)
          Text(s.t('noMatch'), style: muted)
        else
          AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final row in rows.take(_shown)) _row(s, row, busy),
            ]),
          ),
        if (rows.length > _shown)
          Wrap(
              spacing: AnumSpacing.sm,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(s.t('showing', {'shown': _shown, 'total': rows.length}),
                    style: muted),
                OutlinedButton(
                  key: const Key('fleet-import-more'),
                  onPressed: () => setState(() => _shown += fleetImportPage),
                  child: Text(s.t('more')),
                ),
              ]),
        Text(
            s.t('totals', {
              'create': totals['create']!,
              'update': totals['update']!,
              'skip': totals['skip']!,
            }),
            key: const Key('fleet-import-totals')),
        if (totals['create']! + totals['update']! == 0)
          Text(s.t('nothing'), style: muted),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('fleet-import-apply'),
            onPressed: busy || totals['create']! + totals['update']! == 0
                ? null
                : () => _apply(preview, file, totals),
            icon: const Icon(Icons.upload),
            label: Text(s.t(_busy == 'importing' ? 'applying' : 'apply')),
          ),
        ),
      ],
    ];
  }

  Widget _row(FleetImportStrings s, FleetImportRow row, bool busy) {
    final p = context.palette;
    final action = fleetActionFor(row, _choices);
    final summary = [
      for (final MapEntry(:key, :value) in row.summary.entries)
        '${s.field(key)}: $value'
    ].join(' · ');
    return Padding(
      key: Key('fleet-row-${row.row}'),
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(s.t('row', {'row': row.row}),
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
              Text(row.registration.isEmpty ? '-' : row.registration,
                  textDirection: TextDirection.ltr,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              AnumPill(
                  label: s.t('status.${row.status}'),
                  tone: _statusTone(row.status)),
            ]),
        if (summary.isNotEmpty)
          Text(summary,
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final issue in row.issues)
          Text(s.issue(issue, row.registration),
              style: TextStyle(color: p.stop, fontSize: AnumType.sm)),
        if (row.duplicateOf != null)
          Text(s.t('repeats', {'row': row.duplicateOf!}),
              style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
        DropdownButtonFormField<String>(
          key: Key('fleet-action-${row.row}'),
          initialValue: action,
          isExpanded: true,
          decoration:
              InputDecoration(labelText: s.t('actionFor', {'row': row.row})),
          items: [
            for (final a in row.actions.isEmpty ? [row.action] : row.actions)
              DropdownMenuItem(value: a, child: Text(s.t('action.$a'))),
          ],
          onChanged: busy || row.actions.length < 2
              ? null
              : (v) => setState(() => _choices[row.row] = v ?? action),
        ),
        const Divider(height: AnumSpacing.md),
      ]),
    );
  }
}
