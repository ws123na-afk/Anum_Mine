import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';
import '../records_kit/records_widgets.dart';

/// English and Arabic strings of the adjustments list, the manual journal
/// entries, the tax settings and the dismissed receipts on the phone
/// (docs/business-books.md, docs/business-tax.md). Terms follow the web
/// catalogs (businessBooks.*.ts, financeLastGaps.*.ts).
class BooksMoreStrings extends ModuleStrings {
  const BooksMoreStrings(bool arabic) : super(arabic, _en, _ar);
  static BooksMoreStrings of(BuildContext context) =>
      BooksMoreStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);

  /// "The Accountant review reminds you ... due {n days} ..." with the
  /// right Arabic plural.
  String ownerDeadline(int count) => t('deadline.owner', {'days': days(count)});

  static const _en = <String, String>{
    'more': 'More of the books',
    'refresh': 'Refresh',
    'menu.adjustments': 'Adjustments',
    'menu.journal': 'Manual journal entries',
    'menu.tax': 'Tax settings',
    'menu.dismissed': 'Dismissed receipts',
    // adjustments
    'adj.title': 'Adjustments',
    'adj.intro':
        'Only what invoices, bills and allocations cannot say: accruals, prepayments, capital items, loans, owner money in and out. An adjustment never changes; a mistake is voided with a reason.',
    'adj.kindFilter': 'Kind',
    'adj.allKinds': 'All kinds',
    'adj.filter.': 'Active and pending',
    'adj.filter.pending': 'Waiting for approval',
    'adj.filter.active': 'Active',
    'adj.filter.with_void': 'All, with voided',
    'adj.state.active': 'Active',
    'adj.state.pending': 'Waits for approval',
    'adj.state.void': 'Voided',
    'adj.empty': 'No adjustments',
    'adj.emptyText':
        'Most businesses need none. Use one for a cost incurred but not yet billed, a loan or an asset bought outside a bill.',
    'adj.emptyMember':
        'Owners and accountants record adjustments. Ask an owner of this business if one is missing.',
    'adj.new': 'New adjustment',
    'adj.spread': 'Over {months} months from {start}',
    'adj.reverses': 'Reverses on {day}',
    'adj.voidedLine': 'Voided: {reason}',
    'adj.date': 'Date',
    'adj.amount': 'Amount',
    'adj.status': 'Status',
    'adj.description': 'What it is for',
    'adj.void': 'Void',
    'adj.voidTitle': 'Void this adjustment',
    'adj.voidIntro':
        'Its posting is reversed. The adjustment and your reason stay in the record with your name.',
    'adj.reject': 'Reject',
    'adj.rejectTitle': 'Reject this adjustment',
    'adj.rejectIntro':
        'It is never posted. The reason stays in the record with your name.',
    'adj.reason': 'Reason',
    'adj.reasonShort': 'Give a reason of at least 3 characters.',
    'adj.voided': 'Adjustment voided.',
    'adj.rejected': 'Adjustment rejected. It will not be posted.',
    'adj.approve': 'Approve',
    'adj.approved': 'Approved the adjustment. It is posted now.',
    'adj.own':
        'You recorded this adjustment: another owner or an accountant approves it.',
    'adj.accountantsOnly':
        'Only owners and accountants void, approve or attach documents to adjustments. Ask an owner of this business.',
    'adj.denied':
        'The adjustments are for owners and members of this business. Ask a workspace owner to make you a member.',
    'adj.missingText':
        'This adjustment is at or above the evidence threshold and has no supporting document.',
    // manual journal entries
    'je.title': 'Manual journal entries',
    'je.intro':
        'Opening balances and corrections an owner or accountant recorded. Attach their supporting documents here.',
    'je.empty': 'No manual journal entries',
    'je.emptyText':
        'An owner or accountant records them on the web in Books, Journal. Refresh once one is there.',
    'je.posted': 'Posted',
    'je.reversed': 'Reversed',
    'je.lines': 'Lines',
    'je.debit': 'Debit',
    'je.credit': 'Credit',
    'je.total': 'Total',
    'je.docs': 'Supporting documents: {n}',
    'je.denied':
        'The journal is for owners and members of this business. Ask a workspace owner to make you a member.',
    // tax settings
    'tax.title': 'Tax settings',
    'tax.intro':
        'The VAT filing period and the return deadline the Accountant review reminds you of. ANUM never files a return.',
    'tax.jurisdiction': 'Jurisdiction',
    'tax.j.SA': 'Saudi Arabia',
    'tax.j.AE': 'United Arab Emirates',
    'tax.j.OTHER': 'Other country',
    'tax.frequency': 'Filing period',
    'tax.f.monthly': 'Monthly',
    'tax.f.quarterly': 'Quarterly',
    'tax.cycle': 'Quarters start in month',
    'tax.cycleHelp':
        'Month 1, 2 or 3 of the year: the first quarter starts in January, February or March.',
    'tax.country': 'Filing country',
    'tax.countryHelp':
        'Two letters, for example BH for Bahrain or OM for Oman. Bahrain and Oman have built-in deadlines; Kuwait and Qatar have no VAT.',
    'tax.dueDays': 'VAT return due day (days after the period ends)',
    'tax.dueDaysHelp':
        'Your own due day always wins over a built-in deadline. Leave it empty to use the built-in one.',
    'tax.deadlineTitle': 'Which deadline applies',
    'tax.kept':
        'The VAT registration and ZATCA e-invoicing details stay as saved; change them on the web in Books, Tax.',
    'tax.save': 'Save tax settings',
    'tax.saved': 'Tax settings saved.',
    'tax.readOnly':
        'Only owners and accountants change the tax settings. Ask an owner of this business.',
    'tax.notSet': 'Tax is not set up yet',
    'tax.notSetText':
        'Choose the jurisdiction and the filing period, then save. The Accountant review then reminds you of each return.',
    'tax.notSetMember':
        'An owner or accountant sets up tax for this business. Ask an owner of this business.',
    'tax.denied':
        'The tax settings are for owners and members of this business. Ask a workspace owner to make you a member.',
    'deadline.owner':
        'The Accountant review reminds you of each return, due {days} after its period ends.',
    'deadline.preset':
        'Without your own due day, the usual deadline applies: {rule}.',
    'deadline.endOfNextMonth': 'the last day of the month after the period',
    'deadline.days28': '28 days after the period ends',
    'deadline.days30': '30 days after the period ends',
    'deadline.noVat':
        'This country has no VAT, so ANUM knows no deadline. Enter a filing period and due day only if you file a VAT return there.',
    'deadline.none':
        'ANUM knows no deadline for this country. Enter the filing period and due day so the Accountant review can remind you.',
    'deadline.portal':
        "A reminder only: the authority's portal shows the binding date. ANUM never files and does not check ZATCA or FTA compliance.",
    'problem.country':
        'The filing country is two letters, for example BH or OM.',
    'problem.countryJurisdiction':
        'For Saudi Arabia or the UAE choose that jurisdiction, not Other country.',
    'problem.dueDays': 'The due day is a whole number of days from 1 to 120.',
    // dismissed receipts
    'dis.title': 'Dismissed receipts',
    'dis.help':
        'A dismissed receipt is never proposed for that bill again. Restore it to see the proposal again.',
    'dis.empty': 'No dismissed receipts',
    'dis.emptyText':
        'A receipt you dismiss for a bill on the web appears here, so you can restore it.',
    'dis.row': '{document} for the bill {reference} from {vendor}',
    'dis.on': 'Dismissed on {date}',
    'dis.documentGone': 'A receipt no longer in the vault',
    'dis.billGone': 'a bill that was removed',
    'dis.restore': 'Restore',
    'dis.restoreLabel': 'Restore {document}',
    'dis.restored':
        'Restored. The receipt can be proposed for this bill again.',
    'dis.denied':
        'Dismissed receipts are for owners and accountants with access to the Document vault. Ask an owner of this business.',
  };

  static const _ar = <String, String>{
    'more': 'المزيد من الدفاتر',
    'refresh': 'تحديث',
    'menu.adjustments': 'قيود التسوية',
    'menu.journal': 'قيود اليومية اليدوية',
    'menu.tax': 'إعدادات الضريبة',
    'menu.dismissed': 'الإيصالات المتجاهلة',
    'adj.title': 'قيود التسوية',
    'adj.intro':
        'فقط ما لا تعبّر عنه الفواتير وفواتير الموردين والمخصصات: المستحقات والمدفوعات المقدمة والأصول والقروض وأموال المالك الداخلة والخارجة. القيد لا يتغير، والخطأ يُلغى مع سبب.',
    'adj.kindFilter': 'النوع',
    'adj.allKinds': 'كل الأنواع',
    'adj.filter.': 'السارية والمنتظرة',
    'adj.filter.pending': 'بانتظار الموافقة',
    'adj.filter.active': 'السارية',
    'adj.filter.with_void': 'الكل، مع الملغاة',
    'adj.state.active': 'سارٍ',
    'adj.state.pending': 'ينتظر الموافقة',
    'adj.state.void': 'ملغى',
    'adj.empty': 'لا قيود تسوية',
    'adj.emptyText':
        'لا تحتاجها معظم الأنشطة. استخدمها لتكلفة تحققت ولم تصل فاتورتها، أو لقرض، أو لأصل اشتُري خارج فاتورة.',
    'adj.emptyMember':
        'يسجل المالكون والمحاسبون قيود التسوية. اسأل مالك هذا النشاط إن كان قيد ناقصًا.',
    'adj.new': 'قيد تسوية جديد',
    'adj.spread': 'على {months} شهر من {start}',
    'adj.reverses': 'يُعكس في {day}',
    'adj.voidedLine': 'أُلغي: {reason}',
    'adj.date': 'التاريخ',
    'adj.amount': 'المبلغ',
    'adj.status': 'الحالة',
    'adj.description': 'الغرض',
    'adj.void': 'إلغاء',
    'adj.voidTitle': 'إلغاء هذا القيد',
    'adj.voidIntro': 'يُعكس ترحيله. يبقى القيد وسببك في السجل مع اسمك.',
    'adj.reject': 'ارفض',
    'adj.rejectTitle': 'رفض هذا القيد',
    'adj.rejectIntro': 'لن يُرحَّل أبدًا. يبقى السبب في السجل مع اسمك.',
    'adj.reason': 'السبب',
    'adj.reasonShort': 'اكتب سببًا من 3 أحرف على الأقل.',
    'adj.voided': 'أُلغي القيد.',
    'adj.rejected': 'رُفض القيد ولن يُرحَّل.',
    'adj.approve': 'اعتماد',
    'adj.approved': 'تمت الموافقة على القيد وتم ترحيله.',
    'adj.own': 'أنت سجلت هذا القيد: يوافق عليه مالك أو محاسب آخر.',
    'adj.accountantsOnly':
        'لا يلغي قيود التسوية أو يعتمدها أو يرفق مستنداتها إلا المالكون والمحاسبون. اسأل مالك هذا النشاط.',
    'adj.denied':
        'قيود التسوية لمالكي هذا النشاط وأعضائه. اطلب من مالك مساحة العمل أن يضيفك عضوًا.',
    'adj.missingText':
        'هذا القيد يبلغ حد المستندات الداعمة أو يتجاوزه ولا يوجد له مستند داعم.',
    'je.title': 'قيود اليومية اليدوية',
    'je.intro':
        'أرصدة افتتاحية وتصحيحات سجلها مالك أو محاسب. أرفق مستنداتها الداعمة هنا.',
    'je.empty': 'لا قيود يومية يدوية',
    'je.emptyText':
        'يسجلها مالك أو محاسب على الويب في الدفاتر، اليومية. حدّث الصفحة بعد تسجيل أحدها.',
    'je.posted': 'مُرحّل',
    'je.reversed': 'معكوس',
    'je.lines': 'السطور',
    'je.debit': 'مدين',
    'je.credit': 'دائن',
    'je.total': 'الإجمالي',
    'je.docs': 'المستندات الداعمة: {n}',
    'je.denied':
        'اليومية لمالكي هذا النشاط وأعضائه. اطلب من مالك مساحة العمل أن يضيفك عضوًا.',
    'tax.title': 'إعدادات الضريبة',
    'tax.intro':
        'فترة إقرار ضريبة القيمة المضافة وموعد الإقرار الذي تذكّرك به مراجعة المحاسب. لا يقدم ANUM أي إقرار.',
    'tax.jurisdiction': 'الجهة الضريبية',
    'tax.j.SA': 'السعودية',
    'tax.j.AE': 'الإمارات العربية المتحدة',
    'tax.j.OTHER': 'دولة أخرى',
    'tax.frequency': 'فترة الإقرار',
    'tax.f.monthly': 'شهري',
    'tax.f.quarterly': 'ربع سنوي',
    'tax.cycle': 'تبدأ الأرباع في الشهر',
    'tax.cycleHelp':
        'الشهر 1 أو 2 أو 3 من السنة: يبدأ الربع الأول في يناير أو فبراير أو مارس.',
    'tax.country': 'دولة الإقرار',
    'tax.countryHelp':
        'حرفان، مثل BH للبحرين أو OM لعُمان. للبحرين وعُمان مواعيد مدمجة، ولا توجد ضريبة قيمة مضافة في الكويت وقطر.',
    'tax.dueDays': 'موعد استحقاق الإقرار (أيام بعد نهاية الفترة)',
    'tax.dueDaysHelp':
        'موعدك أنت يتقدم دائمًا على الموعد المدمج. اتركه فارغًا لاستخدام الموعد المدمج.',
    'tax.deadlineTitle': 'الموعد المطبق',
    'tax.kept':
        'تبقى بيانات التسجيل في ضريبة القيمة المضافة والفوترة الإلكترونية كما حُفظت؛ غيّرها على الويب في الدفاتر، الضريبة.',
    'tax.save': 'حفظ إعدادات الضريبة',
    'tax.saved': 'حُفظت إعدادات الضريبة.',
    'tax.readOnly':
        'لا يغير إعدادات الضريبة إلا المالكون والمحاسبون. اسأل مالك هذا النشاط.',
    'tax.notSet': 'لم تُضبط الضريبة بعد',
    'tax.notSetText':
        'اختر الجهة الضريبية وفترة الإقرار ثم احفظ. بعدها تذكّرك مراجعة المحاسب بكل إقرار.',
    'tax.notSetMember':
        'يضبط مالك أو محاسب الضريبة لهذا النشاط. اسأل مالك هذا النشاط.',
    'tax.denied':
        'إعدادات الضريبة لمالكي هذا النشاط وأعضائه. اطلب من مالك مساحة العمل أن يضيفك عضوًا.',
    'deadline.owner':
        'تذكّرك مراجعة المحاسب بكل إقرار، ويستحق بعد {days} من نهاية فترته.',
    'deadline.preset': 'من دون موعدك أنت، يُطبق الموعد المعتاد: {rule}.',
    'deadline.endOfNextMonth': 'آخر يوم من الشهر التالي للفترة',
    'deadline.days28': '28 يومًا بعد نهاية الفترة',
    'deadline.days30': '30 يومًا بعد نهاية الفترة',
    'deadline.noVat':
        'لا توجد ضريبة قيمة مضافة في هذه الدولة، لذا لا يعرف ANUM موعدًا. أدخل الفترة والموعد فقط إن كنت تقدم إقرارًا هناك.',
    'deadline.none':
        'لا يعرف ANUM موعدًا لهذه الدولة. أدخل فترة الإقرار وموعد استحقاقه لتذكّرك مراجعة المحاسب.',
    'deadline.portal':
        'تذكير فقط: بوابة الجهة المختصة تبين الموعد الملزم. لا يقدم ANUM أي إقرار ولا يتحقق من الالتزام بقواعد هيئة الزكاة والضريبة والجمارك أو الهيئة الاتحادية للضرائب.',
    'problem.country': 'دولة الإقرار حرفان، مثل BH أو OM.',
    'problem.countryJurisdiction':
        'للسعودية أو الإمارات اختر تلك الجهة، لا "دولة أخرى".',
    'problem.dueDays': 'موعد الاستحقاق عدد صحيح من الأيام بين 1 و120.',
    'dis.title': 'الإيصالات المتجاهلة',
    'dis.help':
        'لا يُقترح الإيصال المتجاهل لتلك الفاتورة مرة أخرى. استعده ليظهر الاقتراح من جديد.',
    'dis.empty': 'لا إيصالات متجاهلة',
    'dis.emptyText':
        'يظهر هنا كل إيصال تتجاهله لفاتورة على الويب، لتتمكن من استعادته.',
    'dis.row': '{document} للفاتورة {reference} من {vendor}',
    'dis.on': 'تم التجاهل في {date}',
    'dis.documentGone': 'إيصال لم يعد في الخزنة',
    'dis.billGone': 'فاتورة حُذفت',
    'dis.restore': 'استعادة',
    'dis.restoreLabel': 'استعادة {document}',
    'dis.restored':
        'تمت الاستعادة. يمكن اقتراح الإيصال لهذه الفاتورة مرة أخرى.',
    'dis.denied':
        'الإيصالات المتجاهلة للمالكين والمحاسبين الذين يصلون إلى خزنة المستندات. اسأل مالك هذا النشاط.',
  };
}
