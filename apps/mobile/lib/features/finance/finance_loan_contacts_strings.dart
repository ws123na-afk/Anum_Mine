import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';
import '../records_kit/records_widgets.dart';

/// English and Arabic strings of a loan's person from Contacts and of the
/// loans by person (docs/finance.md#people-from-contacts). The terms follow
/// the web catalog (apps/web/src/lib/messages/financePersonal.*.ts, the
/// `fpm.contact.*` and `fpm.byPerson.*` keys).
class FinanceLoanContactsStrings extends ModuleStrings {
  const FinanceLoanContactsStrings(bool arabic) : super(arabic, _en, _ar);
  static FinanceLoanContactsStrings of(BuildContext context) =>
      FinanceLoanContactsStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);

  static const _en = <String, String>{
    'contact.help':
        'Type at least two letters of the name to find the person in Contacts, or keep the name as typed.',
    'contact.searching': 'Looking in Contacts...',
    'contact.results': 'People in Contacts',
    'contact.pick': 'Link {name} from Contacts',
    'contact.none':
        'Nobody called {name} in Contacts. The loan keeps the name as typed.',
    'contact.add': 'Add {name} to Contacts',
    'contact.cannotAdd':
        'Only workspace owners and members can add people to Contacts. The loan keeps the name as typed.',
    'contact.denied':
        'You cannot read Contacts in this workspace, so the loan keeps the name as typed. Ask a workspace owner to make you a member.',
    'contact.error': 'Contacts could not be searched: {message}',
    'contact.retry': 'Try again',
    'contact.linked': 'Linked to {name} in Contacts',
    'contact.unlink': 'Unlink',
    'contact.unlinkLabel': 'Unlink {name} from this loan',
    'contact.typedAs': 'Saved on the loan as {name}',
    'contact.deleted':
        'No longer in Contacts: the name saved on the loan is shown',
    'contact.hidden': 'Linked to a person in Contacts you cannot see',
    'people.open': 'By person',
    'people.title': 'People you lent to or borrowed from',
    'people.intro':
        'Each person\'s loans together: by the person in Contacts when a loan is linked, else by the name typed.',
    'people.search': 'Search people',
    'people.loading': 'Loading people...',
    'people.failed': 'People could not be loaded: {message}',
    'people.denied':
        'Finance is for workspace owners and members. Ask a workspace owner to change your role.',
    'people.retry': 'Try again',
    'people.empty': 'No loans with people yet',
    'people.emptyText':
        'Add money you lent or borrowed. Loans with the same person are shown together here.',
    'people.noMatch': 'Nobody matches the search',
    'people.more': 'Show more',
    'people.loadingMore': 'Loading more...',
    'people.count': '{open} open of {count}',
    'people.overdue': '{count} overdue',
    'people.theyOwe': 'They owe you {amount}',
    'people.iOwe': 'You owe {amount}',
    'people.fromContacts': 'From Contacts',
    'people.detail': 'Loans with {name}',
    'people.names': 'Names saved on these loans: {names}',
    'people.loans': 'Loans',
    'people.history': 'Money handed over and repaid',
    'people.noHistory':
        'No transactions are linked to these loans yet. Open a loan to link the transfer and each repayment.',
    'people.review': 'needs review',
    'people.paidIn': 'Money in',
    'people.paidOut': 'Money out',
    'people.lent': 'They owe you',
    'people.borrowed': 'You owe',
    'people.left': '{left} left of {amount}',
  };

  static const _ar = <String, String>{
    'contact.help':
        'اكتب حرفين على الأقل من الاسم للبحث عن الشخص في جهات الاتصال، أو أبقِ الاسم كما كتبته.',
    'contact.searching': 'جارٍ البحث في جهات الاتصال...',
    'contact.results': 'أشخاص في جهات الاتصال',
    'contact.pick': 'ربط {name} من جهات الاتصال',
    'contact.none':
        'لا يوجد أحد باسم {name} في جهات الاتصال. يحتفظ القرض بالاسم كما كتبته.',
    'contact.add': 'إضافة {name} إلى جهات الاتصال',
    'contact.cannotAdd':
        'لا يضيف الأشخاص إلى جهات الاتصال إلا مالكو مساحة العمل وأعضاؤها. يحتفظ القرض بالاسم كما كتبته.',
    'contact.denied':
        'لا يمكنك قراءة جهات الاتصال في مساحة العمل هذه، لذلك يحتفظ القرض بالاسم كما كتبته. اطلب من مالك مساحة العمل أن يجعلك عضوًا.',
    'contact.error': 'تعذّر البحث في جهات الاتصال: {message}',
    'contact.retry': 'إعادة المحاولة',
    'contact.linked': 'مربوط بـ {name} في جهات الاتصال',
    'contact.unlink': 'إلغاء الربط',
    'contact.unlinkLabel': 'إلغاء ربط {name} بهذا القرض',
    'contact.typedAs': 'محفوظ في القرض باسم {name}',
    'contact.deleted': 'لم يعد في جهات الاتصال: يظهر الاسم المحفوظ في القرض',
    'contact.hidden': 'مربوط بشخص في جهات الاتصال لا يمكنك رؤيته',
    'people.open': 'حسب الشخص',
    'people.title': 'أشخاص أقرضتهم أو اقترضت منهم',
    'people.intro':
        'قروض كل شخص معًا: حسب الشخص في جهات الاتصال عندما يكون القرض مربوطًا به، وإلا حسب الاسم المكتوب.',
    'people.search': 'البحث عن الأشخاص',
    'people.loading': 'جارٍ تحميل الأشخاص...',
    'people.failed': 'تعذّر تحميل الأشخاص: {message}',
    'people.denied':
        'المالية لمالكي مساحة العمل وأعضائها. اطلب من مالك مساحة العمل تغيير دورك.',
    'people.retry': 'إعادة المحاولة',
    'people.empty': 'لا توجد قروض مع أشخاص بعد',
    'people.emptyText':
        'أضف مالًا أقرضته أو اقترضته. تظهر القروض مع الشخص نفسه معًا هنا.',
    'people.noMatch': 'لا أحد يطابق البحث',
    'people.more': 'عرض المزيد',
    'people.loadingMore': 'جارٍ تحميل المزيد...',
    'people.count': '{open} مفتوحة من {count}',
    'people.overdue': '{count} متأخرة',
    'people.theyOwe': 'مستحق لك {amount}',
    'people.iOwe': 'عليك {amount}',
    'people.fromContacts': 'من جهات الاتصال',
    'people.detail': 'القروض مع {name}',
    'people.names': 'الأسماء المحفوظة في هذه القروض: {names}',
    'people.loans': 'القروض',
    'people.history': 'المال المسلَّم والمسدَّد',
    'people.noHistory':
        'لم تُربط أي معاملات بهذه القروض بعد. افتح قرضًا لربط التحويل وكل سداد.',
    'people.review': 'يحتاج إلى مراجعة',
    'people.paidIn': 'مال وارد',
    'people.paidOut': 'مال صادر',
    'people.lent': 'مستحق لك',
    'people.borrowed': 'عليك',
    'people.left': 'المتبقي {left} من {amount}',
  };
}
