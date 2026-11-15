import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_more_data.dart';

/// Finance, iPhone SMS settings on the phone (docs/finance-sms.md#on-the-phone),
/// the web's Settings form (`FinanceSms.tsx` `SettingsForm`): which ledger
/// this is, how long masked message text is kept, the account used when a
/// message names none, where cash withdrawals go, recording confident
/// messages on their own, learning from reviews, the limits for automatic
/// recording, card and account numbers (with card limits), Apple Wallet
/// cards, extra senders and AI help with its consent. The same route as the
/// web (`GET` and `PUT /finance/sms/settings` with the version read), so a
/// change made elsewhere since is refused (409) in the API's own words.

class SmsMapRow {
  SmsMapRow({this.last4 = '', this.accountId = '', this.limit = ''});
  String last4, accountId, limit;
}

class SmsWalletRow {
  SmsWalletRow({this.card = '', this.last4 = '', this.accountId = ''});
  String card, last4, accountId;
}

class SmsLimitRow {
  SmsLimitRow({this.currency = '', this.amount = ''});
  String currency, amount;
}

/// The settings as `GET /sms/settings` sends them (`FinanceSmsSettings`).
class SmsSettings {
  const SmsSettings({
    required this.version,
    required this.ledgerKind,
    required this.autoPost,
    required this.retentionDays,
    required this.defaultAccountId,
    required this.cashAccountId,
    required this.accountMap,
    required this.walletCards,
    required this.extraSenders,
    required this.aiEnabled,
    required this.learning,
    required this.confirmAfter,
    required this.limits,
  });

  factory SmsSettings.fromJson(JsonReader r) => SmsSettings(
        version: r.optInt('version') ?? 0,
        ledgerKind: r.optString('ledger_kind') ?? 'personal',
        autoPost: r.optBool('auto_post') ?? false,
        retentionDays: r.optInt('body_retention_days') ?? 0,
        defaultAccountId: r.optString('default_account_id'),
        cashAccountId: r.optString('cash_account_id'),
        accountMap: r.optObjects(
            'account_map',
            (m) => (
                  last4: m.optString('last4') ?? '',
                  accountId: m.optString('account_id') ?? '',
                  limit: dec(m, 'credit_limit') ?? '',
                )),
        walletCards: r.optObjects(
            'wallet_cards',
            (c) => (
                  card: c.optString('card') ?? '',
                  last4: c.optString('last4') ?? '',
                  accountId: c.optString('account_id') ?? '',
                )),
        extraSenders:
            r.optObjects('extra_senders', (s) => s.optString('sender') ?? ''),
        aiEnabled: r.optObject('ai')?.optBool('enabled') ?? false,
        learning: r.optObject('learning')?.optBool('enabled') ?? true,
        confirmAfter: r.optObject('learning')?.optInt('confirm_after') ?? 2,
        limits: r.optObjects(
            'auto_post_limits',
            (l) => (
                  currency: l.optString('currency') ?? '',
                  amount: dec(l, 'amount') ?? '',
                )),
      );

  final int version, retentionDays, confirmAfter;
  final String ledgerKind;
  final bool autoPost, aiEnabled, learning;
  final String? defaultAccountId, cashAccountId;
  final List<({String last4, String accountId, String limit})> accountMap;
  final List<({String card, String last4, String accountId})> walletCards;
  final List<String> extraSenders;
  final List<({String currency, String amount})> limits;
}

extension FinanceSmsSettingsApi on FinanceMoreRepository {
  Future<SmsSettings> smsSettings() async => SmsSettings.fromJson(JsonReader.of(
      await api.request('GET', '${FinanceMoreRepository.base}/sms/settings')));

  Future<void> saveSmsSettings(Map<String, Object?> body) => api.request(
      'PUT', '${FinanceMoreRepository.base}/sms/settings',
      body: body);
}

/// The web's `PUT /sms/settings` body from the form's values: empty rows
/// are left out, senders split on commas, currencies upper case, and the AI
/// consent sent only when AI help is being turned on.
Map<String, Object?> smsSettingsBody({
  required SmsSettings settings,
  required String ledgerKind,
  required bool autoPost,
  required int retentionDays,
  required String defaultAccountId,
  required String cashAccountId,
  required List<SmsMapRow> map,
  required List<SmsWalletRow> wallet,
  required String senders,
  required bool ai,
  required bool consent,
  required bool learning,
  required int confirmAfter,
  required List<SmsLimitRow> limits,
}) =>
    {
      'version': settings.version,
      'ledger_kind': ledgerKind,
      'auto_post': autoPost,
      'body_retention_days': retentionDays,
      'default_account_id': defaultAccountId.isEmpty ? null : defaultAccountId,
      'cash_account_id': cashAccountId.isEmpty ? null : cashAccountId,
      'account_map': [
        for (final r in map)
          if (r.last4.trim().isNotEmpty && r.accountId.isNotEmpty)
            {
              'last4': r.last4.trim(),
              'account_id': r.accountId,
              'credit_limit': r.limit.trim().isEmpty ? null : r.limit.trim(),
            }
      ],
      'wallet_cards': [
        for (final c in wallet)
          if (c.card.trim().isNotEmpty && c.accountId.isNotEmpty)
            {
              'card': c.card.trim(),
              'last4': c.last4.trim().isEmpty ? null : c.last4.trim(),
              'account_id': c.accountId,
            }
      ],
      'extra_senders': [
        for (final s in senders.split(','))
          if (s.trim().isNotEmpty)
            {
              'sender': s.trim(),
              'bank': s.trim().toLowerCase().substring(
                  0, s.trim().length < 40 ? s.trim().length : 40),
            }
      ],
      'ai_enabled': ai,
      if (ai && !settings.aiEnabled) 'ai_consent': consent,
      'learning_enabled': learning,
      'learning_confirm_after': confirmAfter,
      'auto_post_limits': [
        for (final l in limits)
          if (l.currency.trim().isNotEmpty && l.amount.trim().isNotEmpty)
            {
              'currency': l.currency.trim().toUpperCase(),
              'amount': l.amount.trim(),
            }
      ],
    };

/// English and Arabic, worded as the web's `fsms.*` settings keys.
class SmsSettingsStrings extends ModuleStrings {
  const SmsSettingsStrings(bool arabic) : super(arabic, _en, _ar);
  static SmsSettingsStrings of(BuildContext context) =>
      SmsSettingsStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  static const _en = <String, String>{
    'title': 'iPhone SMS settings',
    'open': 'Settings',
    'loading': 'Reading the settings...',
    'loadFailed': 'The settings could not be read: {message}',
    'retry': 'Try again',
    'denied':
        'Finance is the person\'s own: only they can see and change it. A viewer of this workspace reads only.',
    'ledgerKind': 'This ledger is',
    'ledgerKind.personal': 'Personal',
    'ledgerKind.business': 'Business',
    'ledgerKindHelp':
        'Each workspace keeps its own ledger, so personal and business money never mix. Keep personal banking in the workspace you use for yourself.',
    'retention': 'Keep message text (days)',
    'retentionHelp':
        'Masked message text is kept this long for review and teaching, then erased. 0 keeps none.',
    'retentionBad': 'Enter a whole number of days from 0 to 365.',
    'defaultAccount': 'Account when a message names none',
    'cashAccount': 'Cash withdrawals go to',
    'cashAsSpending': 'Count as cash spending',
    'none': 'None',
    'pick': 'Choose...',
    'autoPost':
        'Record confident messages from confirmed formats on their own (otherwise every message waits for review)',
    'learning': 'Learning from your reviews',
    'learning.help':
        'When you record messages from a sender without changing what ANUM read, ANUM starts recording that sender on its own. The same choice twice (salary, a transfer from your own account, a category) is applied next time. A card ending you map the same way twice is proposed, never mapped without you.',
    'learning.on': 'Learn from my reviews',
    'learning.after':
        'Trust a sender after this many messages recorded unchanged',
    'limits': 'Limits for automatic recording',
    'limits.help':
        'A message above the limit in its currency always waits for you, even from a trusted sender. No row means no limit.',
    'limits.currency': 'Currency',
    'limits.amount': 'Largest amount',
    'limits.add': 'Add a limit',
    'limits.remove': 'Remove this limit',
    'map': 'Card and account numbers',
    'mapHelp':
        'Messages name cards and accounts by their last four digits. Link each to one of your accounts. A card limit shows how much of it you use.',
    'map.last4': 'Last four digits',
    'map.account': 'Account',
    'map.limit': 'Card limit',
    'map.remove': 'Remove this number',
    'map.add': 'Add a number',
    'last4Bad': 'Last four digits must be exactly 4 digits.',
    'amountBad': 'Enter an amount such as 1500 or 1500.50.',
    'wallet.cards': 'Apple Wallet cards',
    'wallet.cardsHelp':
        'The card or pass name as Wallet shows it (and the card\'s last four digits in Wallet, which differ from the physical card) and the account its payments hit. Without this, Wallet payments wait for review.',
    'wallet.card': 'Card or pass name',
    'wallet.last4': 'Wallet last four',
    'wallet.remove': 'Remove this card',
    'wallet.add': 'Add a Wallet card',
    'senders': 'Other senders you allow',
    'sendersHelp':
        'Sender names, separated by commas, for banks ANUM does not list. Their messages still wait for review until you confirm the format.',
    'ai': 'AI help (off by default)',
    'aiHelp':
        'When on, you can ask your workspace\'s own AI model to suggest the fields of one message in Review. Account and card numbers are masked first, nothing is recorded without you, and there is a daily limit.',
    'aiOn': 'Turn on AI help',
    'aiConsent':
        'I agree that the masked text of a message I choose is sent to my workspace\'s AI model.',
    'aiNeedsConsent':
        'Tick the agreement to turn on AI help, or turn AI help off.',
    'save': 'Save',
    'saving': 'Saving...',
    'saved': 'Saved.',
    'fix': 'Fix the marked fields first.',
  };

  static const _ar = <String, String>{
    'title': 'إعدادات رسائل SMS من iPhone',
    'open': 'الإعدادات',
    'loading': 'جارٍ قراءة الإعدادات...',
    'loadFailed': 'تعذرت قراءة الإعدادات: {message}',
    'retry': 'أعد المحاولة',
    'denied':
        'المالية خاصة بصاحبها: هو وحده من يراها ويغيرها. من يطّلع على مساحة العمل هذه يقرأ فقط.',
    'ledgerKind': 'هذا السجل',
    'ledgerKind.personal': 'شخصي',
    'ledgerKind.business': 'للأعمال',
    'ledgerKindHelp':
        'لكل مساحة عمل سجلها الخاص، فلا تختلط الأموال الشخصية بأموال الأعمال. احتفظ بحساباتك الشخصية في مساحة العمل التي تستخدمها لنفسك.',
    'retention': 'مدة حفظ نص الرسالة (أيام)',
    'retentionHelp':
        'يُحفظ نص الرسالة المخفي هذه المدة للمراجعة والتعليم ثم يُمحى. 0 يعني عدم الحفظ.',
    'retentionBad': 'أدخل عددا صحيحا من الأيام من 0 إلى 365.',
    'defaultAccount': 'الحساب عندما لا تذكر الرسالة حسابا',
    'cashAccount': 'السحب النقدي يذهب إلى',
    'cashAsSpending': 'احسبه إنفاقا نقديا',
    'none': 'لا شيء',
    'pick': 'اختر...',
    'autoPost':
        'سجّل الرسائل الواضحة من الصيغ المؤكدة تلقائيا (وإلا تنتظر كل رسالة المراجعة)',
    'learning': 'التعلم من مراجعاتك',
    'learning.help':
        'عندما تسجل رسائل مرسل دون تغيير ما قرأه ANUM، يبدأ ANUM بتسجيل رسائل هذا المرسل وحده. الاختيار نفسه مرتين (راتب، تحويل من حسابك، فئة) يُطبق في المرة التالية. البطاقة التي تربطها بالحساب نفسه مرتين تُقترح عليك، ولا تُربط دونك أبدا.',
    'learning.on': 'تعلّم من مراجعاتي',
    'learning.after': 'ثق بالمرسل بعد هذا العدد من الرسائل المسجلة دون تغيير',
    'limits': 'حدود التسجيل التلقائي',
    'limits.help':
        'الرسالة الأعلى من الحد في عملتها تنتظرك دائما، حتى من مرسل موثوق. عدم وجود صف يعني عدم وجود حد.',
    'limits.currency': 'العملة',
    'limits.amount': 'أكبر مبلغ',
    'limits.add': 'أضف حدا',
    'limits.remove': 'احذف هذا الحد',
    'map': 'أرقام البطاقات والحسابات',
    'mapHelp':
        'تذكر الرسائل البطاقات والحسابات بآخر أربعة أرقام. اربط كل رقم بأحد حساباتك. حد البطاقة يبين مقدار ما تستخدمه منها.',
    'map.last4': 'آخر أربعة أرقام',
    'map.account': 'الحساب',
    'map.limit': 'حد البطاقة',
    'map.remove': 'إزالة هذا الرقم',
    'map.add': 'إضافة رقم',
    'last4Bad': 'آخر أربعة أرقام يجب أن تكون 4 أرقام بالضبط.',
    'amountBad': 'أدخل مبلغا مثل 1500 أو 1500.50.',
    'wallet.cards': 'بطاقات Apple Wallet',
    'wallet.cardsHelp':
        'اسم البطاقة كما تظهره المحفظة (وآخر أربعة أرقام للبطاقة في المحفظة، وهي تختلف عن البطاقة الفعلية) والحساب الذي تُخصم منه دفعاتها. بدون ذلك تنتظر دفعات المحفظة المراجعة.',
    'wallet.card': 'اسم البطاقة',
    'wallet.last4': 'آخر أربعة أرقام في المحفظة',
    'wallet.remove': 'إزالة هذه البطاقة',
    'wallet.add': 'إضافة بطاقة من المحفظة',
    'senders': 'مرسلون آخرون تسمح بهم',
    'sendersHelp':
        'أسماء المرسلين مفصولة بفواصل، لبنوك لا يذكرها ANUM. تبقى رسائلهم بانتظار المراجعة حتى تؤكد الصيغة.',
    'ai': 'مساعدة الذكاء الاصطناعي (متوقفة افتراضيا)',
    'aiHelp':
        'عند تشغيلها، يمكنك أن تطلب من نموذج الذكاء الاصطناعي الخاص بمساحة عملك اقتراح حقول رسالة واحدة في المراجعة. تُخفى أرقام الحسابات والبطاقات أولا، ولا يُسجل شيء بدونك، وهناك حد يومي.',
    'aiOn': 'شغّل مساعدة الذكاء الاصطناعي',
    'aiConsent':
        'أوافق على إرسال النص المخفي للرسالة التي أختارها إلى نموذج الذكاء الاصطناعي في مساحة عملي.',
    'aiNeedsConsent':
        'ضع علامة على الموافقة لتشغيل مساعدة الذكاء الاصطناعي، أو أوقفها.',
    'save': 'حفظ',
    'saving': 'جارٍ الحفظ...',
    'saved': 'تم الحفظ.',
    'fix': 'صحح الحقول المحددة أولا.',
  };
}

final _last4 = RegExp(r'^\d{4}$');

class SmsSettingsScreen extends StatefulWidget {
  const SmsSettingsScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<SmsSettingsScreen> createState() => _SmsSettingsScreenState();
}

class _SmsSettingsScreenState extends State<SmsSettingsScreen> {
  SmsSettings? _settings;
  List<AccountChoice> _accounts = const [];
  RecordsFailure? _failure;
  final _form = GlobalKey<FormState>();
  bool _busy = false;
  String? _notice;
  bool _noticeBad = false;

  // The form's values, set from the settings each time they load.
  String _kind = 'personal', _default = '', _cash = '', _senders = '';
  String _retention = '0';
  bool _autoPost = false, _ai = false, _consent = false, _learn = true;
  int _after = 2;
  List<SmsMapRow> _map = [];
  List<SmsWalletRow> _wallet = [];
  List<SmsLimitRow> _limits = [];
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    setState(() => _failure = null);
    try {
      final settings = await widget.repository.smsSettings();
      var accounts = const <AccountChoice>[];
      try {
        accounts = await widget.repository.accounts();
      } on Object {
        // The form still works with the accounts the settings name.
      }
      if (!mounted) return;
      setState(() {
        _settings = settings;
        _accounts = accounts;
        _kind = settings.ledgerKind;
        _autoPost = settings.autoPost;
        _retention = '${settings.retentionDays}';
        _default = _known(settings.defaultAccountId);
        _cash = _known(settings.cashAccountId);
        _map = [
          for (final m in settings.accountMap)
            SmsMapRow(
                last4: m.last4, accountId: _known(m.accountId), limit: m.limit)
        ];
        _wallet = [
          for (final c in settings.walletCards)
            SmsWalletRow(
                card: c.card, last4: c.last4, accountId: _known(c.accountId))
        ];
        _senders = settings.extraSenders.join(', ');
        _ai = settings.aiEnabled;
        _consent = false;
        _learn = settings.learning;
        _after = settings.confirmAfter.clamp(1, 5);
        _limits = [
          for (final l in settings.limits)
            SmsLimitRow(currency: l.currency, amount: l.amount)
        ];
        _generation++;
      });
    } on Object catch (error) {
      if (mounted) setState(() => _failure = RecordsFailure.from(error));
    }
  }

  /// An account id the dropdowns can show; one no longer listed is kept
  /// only when the accounts could not be read.
  String _known(String? id) {
    if (id == null || id.isEmpty) return '';
    return _accounts.isEmpty || _accounts.any((a) => a.id == id) ? id : '';
  }

  Future<void> _save() async {
    final s = SmsSettingsStrings.of(context);
    final settings = _settings;
    if (settings == null || _busy) return;
    if (!(_form.currentState?.validate() ?? false)) {
      setState(() {
        _notice = s.t('fix');
        _noticeBad = true;
      });
      return;
    }
    if (_ai && !settings.aiEnabled && !_consent) {
      setState(() {
        _notice = s.t('aiNeedsConsent');
        _noticeBad = true;
      });
      return;
    }
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await widget.repository.saveSmsSettings(smsSettingsBody(
        settings: settings,
        ledgerKind: _kind,
        autoPost: _autoPost,
        retentionDays: int.parse(_retention.trim()),
        defaultAccountId: _default,
        cashAccountId: _cash,
        map: _map,
        wallet: _wallet,
        senders: _senders,
        ai: _ai,
        consent: _consent,
        learning: _learn,
        confirmAfter: _after,
        limits: _limits,
      ));
      if (!mounted) return;
      await _load();
      if (!mounted) return;
      setState(() {
        _busy = false;
        _notice = s.t('saved');
        _noticeBad = false;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _notice = RecordsFailure.from(error).message;
        _noticeBad = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = SmsSettingsStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('title'))),
      body: _body(context, s),
    );
  }

  Widget _body(BuildContext context, SmsSettingsStrings s) {
    final failure = _failure;
    if (failure != null) {
      return AnumPage(onRefresh: _load, children: [
        AnumEmpty(
          key: const Key('sms-settings-failed'),
          icon: failure.denied ? Icons.lock_outline : Icons.cloud_off_outlined,
          title: failure.denied
              ? s.t('title')
              : s.t('loadFailed', {'message': failure.message}),
          message: failure.denied ? '${failure.message} ${s.t('denied')}' : '',
          action: failure.denied ? null : s.t('retry'),
          onAction: failure.denied ? null : () => unawaited(_load()),
        ),
      ]);
    }
    final settings = _settings;
    if (settings == null) {
      return Center(
          child: Semantics(
              label: s.t('loading'), child: const CircularProgressIndicator()));
    }
    final p = context.palette;
    final muted = TextStyle(color: p.muted, fontSize: AnumType.sm);
    final g = _generation;
    final cash = _accounts.where((a) => a.kind == 'cash').toList();
    List<DropdownMenuItem<String>> accountItems(String emptyLabel,
            [List<AccountChoice>? only]) =>
        [
          DropdownMenuItem(value: '', child: Text(emptyLabel)),
          for (final a in only ?? _accounts)
            DropdownMenuItem(
                value: a.id,
                child: Text('${a.name} (${a.currency})',
                    overflow: TextOverflow.ellipsis)),
        ];
    String? amountCheck(String? v) {
      final text = (v ?? '').trim();
      if (text.isEmpty) return null;
      return decimalText(text) == null ? s.t('amountBad') : null;
    }

    Widget removeButton(String key, String label, VoidCallback onTap) =>
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: TextButton.icon(
            key: Key(key),
            onPressed: _busy ? null : onTap,
            icon: const Icon(Icons.delete_outline),
            label: Text(label),
          ),
        );
    Widget addButton(String key, String label, VoidCallback onTap) => Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            key: Key(key),
            onPressed: _busy ? null : onTap,
            icon: const Icon(Icons.add),
            label: Text(label),
          ),
        );
    const gap = SizedBox(height: AnumSpacing.xs);

    return Form(
      key: _form,
      child: AnumPage(onRefresh: _load, children: [
        RecordsSection(
          key: const Key('sms-settings-ledger'),
          title: s.t('open'),
          children: [
            DropdownButtonFormField<String>(
              key: ValueKey('sms-settings-kind-$g'),
              initialValue: _kind,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('ledgerKind')),
              items: [
                for (final k in const ['personal', 'business'])
                  DropdownMenuItem(
                      value: k, child: Text(s.t('ledgerKind.$k'))),
              ],
              onChanged: _busy ? null : (v) => _kind = v ?? _kind,
            ),
            Text(s.t('ledgerKindHelp'), style: muted),
            gap,
            TextFormField(
              key: ValueKey('sms-settings-retention-$g'),
              initialValue: _retention,
              keyboardType: TextInputType.number,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(labelText: s.t('retention')),
              onChanged: (v) => _retention = v,
              validator: (v) {
                final n = int.tryParse((v ?? '').trim());
                return n == null || n < 0 || n > 365
                    ? s.t('retentionBad')
                    : null;
              },
            ),
            Text(s.t('retentionHelp'), style: muted),
            gap,
            DropdownButtonFormField<String>(
              key: ValueKey('sms-settings-default-$g'),
              initialValue: _default,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('defaultAccount')),
              items: accountItems(s.t('none')),
              onChanged: _busy ? null : (v) => _default = v ?? '',
            ),
            gap,
            DropdownButtonFormField<String>(
              key: ValueKey('sms-settings-cash-$g'),
              initialValue: cash.any((a) => a.id == _cash) ? _cash : '',
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('cashAccount')),
              items: accountItems(s.t('cashAsSpending'), cash),
              onChanged: _busy ? null : (v) => _cash = v ?? '',
            ),
            CheckboxListTile(
              key: const Key('sms-settings-autopost'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _autoPost,
              onChanged:
                  _busy ? null : (v) => setState(() => _autoPost = v ?? false),
              title: Text(s.t('autoPost')),
            ),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-learning'),
          title: s.t('learning'),
          children: [
            Text(s.t('learning.help'), style: muted),
            CheckboxListTile(
              key: const Key('sms-settings-learn'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _learn,
              onChanged:
                  _busy ? null : (v) => setState(() => _learn = v ?? false),
              title: Text(s.t('learning.on')),
            ),
            DropdownButtonFormField<int>(
              key: ValueKey('sms-settings-after-$g-$_learn'),
              initialValue: _after,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('learning.after')),
              items: [
                for (var n = 1; n <= 5; n++)
                  DropdownMenuItem(value: n, child: Text('$n')),
              ],
              onChanged: _busy || !_learn ? null : (v) => _after = v ?? _after,
            ),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-limits'),
          title: s.t('limits'),
          children: [
            Text(s.t('limits.help'), style: muted),
            for (var i = 0; i < _limits.length; i++)
              _rowBox(
                key: ObjectKey(_limits[i]),
                children: [
                  TextFormField(
                    key: Key('sms-limit-currency-$i'),
                    initialValue: _limits[i].currency,
                    maxLength: 3,
                    textCapitalization: TextCapitalization.characters,
                    textDirection: TextDirection.ltr,
                    decoration:
                        InputDecoration(labelText: s.t('limits.currency')),
                    onChanged: (v) => _limits[i].currency = v,
                  ),
                  TextFormField(
                    key: Key('sms-limit-amount-$i'),
                    initialValue: _limits[i].amount,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    textDirection: TextDirection.ltr,
                    decoration:
                        InputDecoration(labelText: s.t('limits.amount')),
                    onChanged: (v) => _limits[i].amount = v,
                    validator: amountCheck,
                  ),
                  removeButton('sms-limit-remove-$i', s.t('limits.remove'),
                      () => setState(() => _limits.removeAt(i))),
                ],
              ),
            addButton('sms-limit-add', s.t('limits.add'),
                () => setState(() => _limits.add(SmsLimitRow()))),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-map'),
          title: s.t('map'),
          children: [
            Text(s.t('mapHelp'), style: muted),
            for (var i = 0; i < _map.length; i++)
              _rowBox(
                key: ObjectKey(_map[i]),
                children: [
                  TextFormField(
                    key: Key('sms-map-last4-$i'),
                    initialValue: _map[i].last4,
                    maxLength: 4,
                    keyboardType: TextInputType.number,
                    textDirection: TextDirection.ltr,
                    decoration: InputDecoration(labelText: s.t('map.last4')),
                    onChanged: (v) => _map[i].last4 = v,
                    validator: (v) {
                      final text = (v ?? '').trim();
                      return text.isEmpty || _last4.hasMatch(text)
                          ? null
                          : s.t('last4Bad');
                    },
                  ),
                  DropdownButtonFormField<String>(
                    key: Key('sms-map-account-$i'),
                    initialValue: _map[i].accountId,
                    isExpanded: true,
                    decoration: InputDecoration(labelText: s.t('map.account')),
                    items: accountItems(s.t('pick')),
                    onChanged:
                        _busy ? null : (v) => _map[i].accountId = v ?? '',
                  ),
                  TextFormField(
                    key: Key('sms-map-limit-$i'),
                    initialValue: _map[i].limit,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    textDirection: TextDirection.ltr,
                    decoration: InputDecoration(labelText: s.t('map.limit')),
                    onChanged: (v) => _map[i].limit = v,
                    validator: amountCheck,
                  ),
                  removeButton('sms-map-remove-$i', s.t('map.remove'),
                      () => setState(() => _map.removeAt(i))),
                ],
              ),
            addButton('sms-map-add', s.t('map.add'),
                () => setState(() => _map.add(SmsMapRow()))),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-wallet'),
          title: s.t('wallet.cards'),
          children: [
            Text(s.t('wallet.cardsHelp'), style: muted),
            for (var i = 0; i < _wallet.length; i++)
              _rowBox(
                key: ObjectKey(_wallet[i]),
                children: [
                  TextFormField(
                    key: Key('sms-wallet-card-$i'),
                    initialValue: _wallet[i].card,
                    maxLength: 80,
                    decoration:
                        InputDecoration(labelText: s.t('wallet.card')),
                    onChanged: (v) => _wallet[i].card = v,
                  ),
                  TextFormField(
                    key: Key('sms-wallet-last4-$i'),
                    initialValue: _wallet[i].last4,
                    maxLength: 4,
                    keyboardType: TextInputType.number,
                    textDirection: TextDirection.ltr,
                    decoration:
                        InputDecoration(labelText: s.t('wallet.last4')),
                    onChanged: (v) => _wallet[i].last4 = v,
                    validator: (v) {
                      final text = (v ?? '').trim();
                      return text.isEmpty || _last4.hasMatch(text)
                          ? null
                          : s.t('last4Bad');
                    },
                  ),
                  DropdownButtonFormField<String>(
                    key: Key('sms-wallet-account-$i'),
                    initialValue: _wallet[i].accountId,
                    isExpanded: true,
                    decoration: InputDecoration(labelText: s.t('map.account')),
                    items: accountItems(s.t('pick')),
                    onChanged:
                        _busy ? null : (v) => _wallet[i].accountId = v ?? '',
                  ),
                  removeButton('sms-wallet-remove-$i', s.t('wallet.remove'),
                      () => setState(() => _wallet.removeAt(i))),
                ],
              ),
            addButton('sms-wallet-add', s.t('wallet.add'),
                () => setState(() => _wallet.add(SmsWalletRow()))),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-senders'),
          title: s.t('senders'),
          children: [
            TextFormField(
              key: ValueKey('sms-settings-senders-$g'),
              initialValue: _senders,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                  labelText: s.t('senders'), hintText: 'MyBank, MyCard'),
              onChanged: (v) => _senders = v,
            ),
            Text(s.t('sendersHelp'), style: muted),
          ],
        ),
        RecordsSection(
          key: const Key('sms-settings-ai'),
          title: s.t('ai'),
          children: [
            Text(s.t('aiHelp'), style: muted),
            CheckboxListTile(
              key: const Key('sms-settings-ai-on'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _ai,
              onChanged: _busy ? null : (v) => setState(() => _ai = v ?? false),
              title: Text(s.t('aiOn')),
            ),
            if (_ai && !settings.aiEnabled)
              CheckboxListTile(
                key: const Key('sms-settings-ai-consent'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: _consent,
                onChanged: _busy
                    ? null
                    : (v) => setState(() => _consent = v ?? false),
                title: Text(s.t('aiConsent')),
              ),
          ],
        ),
        if (_notice != null)
          Semantics(
            liveRegion: true,
            child: Text(_notice!,
                key: const Key('sms-settings-notice'),
                style: TextStyle(color: _noticeBad ? p.stop : p.ok)),
          ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('sms-settings-save'),
            onPressed: _busy ? null : _save,
            icon: const Icon(Icons.save_outlined),
            label: Text(s.t(_busy ? 'saving' : 'save')),
          ),
        ),
      ]),
    );
  }

  Widget _rowBox({required Key key, required List<Widget> children}) =>
      Container(
        key: key,
        margin: const EdgeInsets.only(top: AnumSpacing.xs),
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: context.palette.line)),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: children),
      );
}
