import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_more_data.dart';

/// Finance, iPhone SMS set-up on the phone (docs/finance-sms.md#on-the-phone),
/// the web's Set-up section: the safety check with its results, forwarding on
/// and off (only once the check passed), the addresses for the Shortcut, the
/// connected iPhones (pause, resume, new token, disconnect after a confirm),
/// connecting a new one with what its token may send, and the Shortcut steps.
/// The same routes and the same rules as the web: reading needs
/// `finance:read`, every change `finance:manage`, and the API's refusal is
/// shown in its own words.
///
/// A device token is shown once, on its own page ([SmsTokenScreen]): it lives
/// only in that page's memory, is hidden until the person asks to see it, is
/// hidden again whenever the app leaves the foreground (so the app switcher
/// never shows it), is never written to storage or a log, and is gone when
/// the page closes. Copy puts it on the clipboard and clears the clipboard a
/// minute later if it still holds the token.
///
/// The settings form (card and account last four digits, Apple Wallet cards,
/// extra senders, AI help, retention) stays on the web.

/// Where a pasted address leads: the API's ingest path on this server.
String smsIngestUrl(Uri base, String path) => base.resolve(path).toString();

/// The Apple Wallet address next to the SMS one (the same device token).
String smsWalletUrl(String smsUrl) =>
    smsUrl.replaceFirst(RegExp(r'/sms/ingest$'), '/wallet/ingest');

class SmsSetupState {
  const SmsSetupState({
    required this.enabled,
    required this.ready,
    required this.version,
    this.passedAt,
    this.ingestPath,
    this.devicesActive = 0,
    this.lastOkAt,
    this.lastError,
    this.lastErrorAt,
  });
  final bool enabled, ready;
  final int version, devicesActive;
  final String? passedAt, ingestPath, lastOkAt, lastError, lastErrorAt;
}

class SmsCheck {
  const SmsCheck(this.name, this.passed);
  final String name;
  final bool passed;
}

class SmsDevice {
  const SmsDevice({
    required this.id,
    required this.name,
    required this.status,
    required this.tokenHint,
    required this.scopes,
    required this.messageCount,
    this.lastOkAt,
    this.lastError,
    this.lastErrorAt,
  });
  factory SmsDevice.fromJson(JsonReader r) => SmsDevice(
        id: r.string('id'),
        name: r.string('name'),
        status: r.string('status'),
        tokenHint: r.optString('token_hint') ?? '',
        scopes: r.optStrings('scopes') ?? const [],
        messageCount: r.optInt('message_count') ?? 0,
        lastOkAt: r.optString('last_ok_at'),
        lastError: r.optString('last_error'),
        lastErrorAt: r.optString('last_error_at'),
      );
  final String id, name, status, tokenHint;
  final List<String> scopes;
  final int messageCount;
  final String? lastOkAt, lastError, lastErrorAt;
}

/// A token as the API returns it once (`POST /sms/devices`, `/rotate`).
class SmsIssuedToken {
  const SmsIssuedToken(
      {required this.device,
      required this.token,
      required this.ingestPath,
      required this.headers,
      required this.jsonFields});
  factory SmsIssuedToken.fromJson(JsonReader r) => SmsIssuedToken(
        device: SmsDevice.fromJson(r.object('device')),
        token: r.string('token'),
        ingestPath: r.string('ingest_path'),
        headers: {
          for (final MapEntry(:key, :value)
              in (r.optMap('headers') ?? const {}).entries)
            key: '$value'
        },
        jsonFields: {
          for (final MapEntry(:key, :value)
              in (r.optMap('json_fields') ?? const {}).entries)
            key: '$value'
        },
      );
  final SmsDevice device;
  final String token, ingestPath;
  final Map<String, String> headers, jsonFields;
}

extension FinanceSmsSetupApi on FinanceMoreRepository {
  Future<JsonReader> _send(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api
          .request(method, '${FinanceMoreRepository.base}$path', body: body));

  Future<SmsSetupState> smsSetup() async {
    final status = await _send('GET', '/sms/status');
    final settings = await _send('GET', '/sms/settings');
    return SmsSetupState(
      enabled: status.optBool('enabled') ?? false,
      ready: status.optBool('ready') ?? false,
      version: settings.optInt('version') ?? 0,
      passedAt: status.optString('self_test_passed_at'),
      ingestPath: status.optString('ingest_path'),
      devicesActive: status.optInt('devices_active') ?? 0,
      lastOkAt: status.optString('last_ok_at'),
      lastError: status.optString('last_error'),
      lastErrorAt: status.optString('last_error_at'),
    );
  }

  Future<({bool passed, List<SmsCheck> checks})> smsSelfTest() async {
    final r = await _send('POST', '/sms/self-test');
    return (
      passed: r.optBool('passed') ?? false,
      checks: r.optObjects('checks',
          (c) => SmsCheck(c.string('name'), c.optBool('passed') ?? false)),
    );
  }

  Future<void> setSmsForwarding({required int version, required bool on}) =>
      _send('PUT', '/sms/settings', {'version': version, 'enabled': on});

  Future<List<SmsDevice>> smsDevices() async =>
      (await _send('GET', '/sms/devices'))
          .optObjects('data', SmsDevice.fromJson);

  Future<SmsIssuedToken> connectSmsDevice(
          String name, List<String> scopes) async =>
      SmsIssuedToken.fromJson(await _send(
          'POST', '/sms/devices', {'name': name, 'scopes': scopes}));

  Future<SmsIssuedToken> rotateSmsDevice(String id) async =>
      SmsIssuedToken.fromJson(await _send(
          'POST', '/sms/devices/${Uri.encodeComponent(id)}/rotate'));

  Future<void> changeSmsDevice(String id, String change) =>
      _send('POST', '/sms/devices/${Uri.encodeComponent(id)}/$change');
}

/// English and Arabic, worded as the web's `fsms.*` catalog.
class SmsSetupStrings extends ModuleStrings {
  const SmsSetupStrings(bool arabic) : super(arabic, _en, _ar);
  static SmsSetupStrings of(BuildContext context) =>
      SmsSetupStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  static const _en = <String, String>{
    'title': 'iPhone SMS set-up',
    'open': 'Set up forwarding',
    'intro':
        'Bank and card messages your iPhone forwards are checked, kept from being recorded twice and written to your own ledger. Codes, passwords and adverts are refused and never kept.',
    'loading': 'Reading the set-up...',
    'loadFailed': 'The set-up could not be read: {message}',
    'retry': 'Try again',
    'denied':
        'Finance is the person\'s own: only they can see and change it. A viewer of this workspace reads only.',
    'state': 'Forwarding',
    'forwarding': 'Forwarding',
    'on': 'On',
    'off': 'Off',
    'safetyCheck': 'Safety check',
    'passedAt': 'Passed {date}',
    'notRun': 'Not run yet',
    'devicesActive': 'Connected devices',
    'lastOk': 'Last success',
    'lastError': 'Last problem',
    'run': 'Run the safety check',
    'running': 'Checking...',
    'failed':
        'The safety check failed. Forwarding stays off. Try again, or tell your administrator which check failed.',
    'passedAll': 'The safety check passed.',
    'why':
        'The safety check tests reading, refusing codes, hiding numbers, tokens and the ledger with made-up messages only. It must pass before you connect a phone or forward a real message.',
    'results': 'Safety check results',
    'check.parse_purchase': 'Read a purchase message',
    'check.refuse_one_time_codes': 'Refuse one-time codes',
    'check.mask_numbers': 'Hide account and card numbers',
    'check.token_round_trip': 'Device token check',
    'check.double_entry_balances': 'Ledger entries balance',
    'check.storage_reachable': 'Storage reachable',
    'passed': 'passed',
    'failedOne': 'failed',
    'turnOn': 'Turn forwarding on',
    'turnOff': 'Turn forwarding off',
    'turnedOn': 'Forwarding is on.',
    'turnedOff': 'Forwarding is off.',
    'endpoint': 'Address for the Shortcut',
    'walletEndpoint': 'Address for the Apple Wallet automation',
    'endpointHidden': 'The address appears once the safety check passes.',
    'devices': 'Devices',
    'devicesNone': 'No device connected',
    'devicesNoneText':
        'Name your iPhone and connect it. You get a token to paste into the Shortcut.',
    'needCheck': 'Run the safety check first, then connect your iPhone here.',
    'device.active': 'Active',
    'device.paused': 'Paused',
    'device.revoked': 'Disconnected',
    'device.token': 'token ends {hint}',
    'device.messages': 'Messages: {n}',
    'device.pause': 'Pause',
    'device.resume': 'Resume',
    'device.rotate': 'New token',
    'device.rotateTitle': 'Make a new token for {name}?',
    'device.rotateText':
        'The token in the Shortcut stops working at once. Paste the new one into the Shortcut on the iPhone.',
    'device.revoke': 'Disconnect',
    'device.revokeTitle': 'Disconnect this device?',
    'device.revokeText':
        '{name} will stop sending messages at once. To use it again, connect it as a new device.',
    'device.paused.done': '{name} is paused.',
    'device.resumed.done': '{name} sends again.',
    'device.revoked.done': '{name} is disconnected.',
    'device.name': 'Device name',
    'device.scopes': 'This token may send',
    'scope.finance.sms': 'Bank messages (SMS)',
    'scope.finance.wallet': 'Apple Pay (Wallet)',
    'device.add': 'Connect a device',
    'device.noScope': 'Choose at least one thing this token may send.',
    'token.title': 'Your device token',
    'token.once':
        'Copy this token now. ANUM shows it only once and keeps only a fingerprint of it. If you lose it, make a new token.',
    'token.protected':
        'The token stays hidden until you show it, is hidden again when you leave the app, and is gone when you close this page. It is not saved on this phone.',
    'token.label': 'Token',
    'token.hidden': 'Hidden. Press Show token to see it.',
    'token.show': 'Show token',
    'token.hide': 'Hide token',
    'token.copy': 'Copy token',
    'token.copied':
        'Copied. ANUM clears the clipboard in a minute if the token is still on it.',
    'token.done': 'I saved it',
    'token.for': 'For {name}, token ends {hint}',
    'shortcut.title': 'Set up the Shortcut',
    'shortcut.method': 'Method',
    'shortcut.headers': 'Headers',
    'shortcut.json': 'JSON body',
    'shortcut.limits':
        'Good to know: the token lives inside the Shortcut, so make a new one if the phone is lost. iOS may delay automations when the phone is locked or in Low Power Mode. Apple gives no access to older messages: paste them under iPhone SMS review or import a statement.',
    'step.1': 'Open Shortcuts, Automation, New Automation, Message.',
    'step.2':
        'Pick your bank as the sender if you can, leave Message Contains empty, and choose Run Immediately.',
    'step.3':
        'Add If: Shortcut Input, Sender, contains your bank\'s name. This keeps personal messages on your phone.',
    'step.4':
        'Inside the If, add Get Contents of URL: {url}, Method POST, the headers Authorization = Bearer and the token, Content-Type = application/json, and the JSON body message = Shortcut Input, Content and sender = Shortcut Input, Sender.',
    'step.5':
        'Test it: make a second shortcut with the same action and the JSON message = ANUM TEST and sender = ANUM TEST, then run it. ANUM answers Connected and records nothing.',
    'step.6':
        'When the first real message from a bank arrives, check it under iPhone SMS review and record it with "This reading is right".',
    'wallet.title': 'Apple Wallet automation (Apple Pay)',
    'wstep.1':
        'Open Shortcuts, Automation, New Automation, Wallet (Transaction). Pick your cards, choose Run Immediately.',
    'wstep.2':
        'Add Get Contents of URL: {url}, Method POST, the same two headers, Request Body JSON.',
    'wstep.3':
        'Add the JSON fields: source = apple_wallet (text), amount = Shortcut Input, Amount; merchant = Shortcut Input, Merchant; card = Shortcut Input, Card or Pass; name = Shortcut Input, Name.',
    'wstep.4':
        'Map each Wallet card to its account in Finance, iPhone SMS on the web (Settings). A payment and its bank message then become one transaction, whichever arrives first.',
    'url.placeholder': 'the address above',
    'webSettings':
        'Card and account numbers, Apple Wallet cards, extra sender names, AI help and how long message text is kept are set in Finance, iPhone SMS on the web.',
  };

  static const _ar = <String, String>{
    'title': 'إعداد رسائل SMS من iPhone',
    'open': 'إعداد التحويل',
    'intro':
        'تُفحص رسائل البنك والبطاقة التي يحولها الآيفون، ولا تُسجل مرتين، وتُكتب في سجلك الخاص. تُرفض الرموز وكلمات المرور والإعلانات ولا تُحفظ أبدا.',
    'loading': 'جارٍ قراءة الإعداد...',
    'loadFailed': 'تعذرت قراءة الإعداد: {message}',
    'retry': 'أعد المحاولة',
    'denied':
        'المالية خاصة بصاحبها: هو وحده من يراها ويغيرها. من يطّلع على مساحة العمل هذه يقرأ فقط.',
    'state': 'التحويل',
    'forwarding': 'التحويل',
    'on': 'يعمل',
    'off': 'متوقف',
    'safetyCheck': 'فحص الأمان',
    'passedAt': 'نجح في {date}',
    'notRun': 'لم يُشغّل بعد',
    'devicesActive': 'الأجهزة المربوطة',
    'lastOk': 'آخر نجاح',
    'lastError': 'آخر مشكلة',
    'run': 'شغّل فحص الأمان',
    'running': 'جارٍ الفحص...',
    'failed':
        'فشل فحص الأمان. يبقى التحويل متوقفا. أعد المحاولة، أو أخبر المسؤول بالفحص الذي فشل.',
    'passedAll': 'نجح فحص الأمان.',
    'why':
        'يختبر فحص الأمان القراءة ورفض الرموز وإخفاء الأرقام والرموز المميزة والسجل برسائل مصطنعة فقط. يجب أن ينجح قبل ربط هاتف أو تحويل رسالة حقيقية.',
    'results': 'نتائج فحص الأمان',
    'check.parse_purchase': 'قراءة رسالة شراء',
    'check.refuse_one_time_codes': 'رفض رموز الاستخدام لمرة واحدة',
    'check.mask_numbers': 'إخفاء أرقام الحسابات والبطاقات',
    'check.token_round_trip': 'فحص رمز الجهاز',
    'check.double_entry_balances': 'توازن قيود السجل',
    'check.storage_reachable': 'الوصول إلى التخزين',
    'passed': 'نجح',
    'failedOne': 'فشل',
    'turnOn': 'شغّل التحويل',
    'turnOff': 'أوقف التحويل',
    'turnedOn': 'التحويل يعمل.',
    'turnedOff': 'التحويل متوقف.',
    'endpoint': 'العنوان للاختصار',
    'walletEndpoint': 'العنوان لأتمتة Apple Wallet',
    'endpointHidden': 'يظهر العنوان بعد نجاح فحص الأمان.',
    'devices': 'الأجهزة',
    'devicesNone': 'لا يوجد جهاز مربوط',
    'devicesNoneText':
        'سمّ جهاز الآيفون واربطه. ستحصل على رمز تلصقه في الاختصار.',
    'needCheck': 'شغّل فحص الأمان أولا، ثم اربط الآيفون هنا.',
    'device.active': 'نشط',
    'device.paused': 'متوقف مؤقتا',
    'device.revoked': 'مفصول',
    'device.token': 'الرمز ينتهي بـ {hint}',
    'device.messages': 'الرسائل: {n}',
    'device.pause': 'إيقاف مؤقت',
    'device.resume': 'استئناف',
    'device.rotate': 'رمز جديد',
    'device.rotateTitle': 'إنشاء رمز جديد لـ {name}؟',
    'device.rotateText':
        'يتوقف الرمز الموجود في الاختصار عن العمل فورا. الصق الرمز الجديد في الاختصار على الآيفون.',
    'device.revoke': 'فصل',
    'device.revokeTitle': 'فصل هذا الجهاز؟',
    'device.revokeText':
        'سيتوقف {name} عن إرسال الرسائل فورا. لاستخدامه مرة أخرى، اربطه كجهاز جديد.',
    'device.paused.done': 'أُوقف {name} مؤقتا.',
    'device.resumed.done': 'عاد {name} إلى الإرسال.',
    'device.revoked.done': 'فُصل {name}.',
    'device.name': 'اسم الجهاز',
    'device.scopes': 'يُسمح لهذا الرمز بإرسال',
    'scope.finance.sms': 'رسائل البنك (SMS)',
    'scope.finance.wallet': 'Apple Pay (المحفظة)',
    'device.add': 'ربط جهاز',
    'device.noScope': 'اختر شيئا واحدا على الأقل يُسمح لهذا الرمز بإرساله.',
    'token.title': 'رمز جهازك',
    'token.once':
        'انسخ هذا الرمز الآن. يعرضه ANUM مرة واحدة فقط ويحتفظ ببصمته فقط. إذا فقدته، أنشئ رمزا جديدا.',
    'token.protected':
        'يبقى الرمز مخفيا حتى تعرضه، ويُخفى مرة أخرى عند مغادرة التطبيق، ويختفي عند إغلاق هذه الصفحة. لا يُحفظ على هذا الهاتف.',
    'token.label': 'الرمز',
    'token.hidden': 'مخفي. اضغط عرض الرمز لرؤيته.',
    'token.show': 'عرض الرمز',
    'token.hide': 'إخفاء الرمز',
    'token.copy': 'نسخ الرمز',
    'token.copied':
        'تم النسخ. يمسح ANUM الحافظة بعد دقيقة إذا كان الرمز ما زال فيها.',
    'token.done': 'حفظته',
    'token.for': 'لـ {name}، الرمز ينتهي بـ {hint}',
    'shortcut.title': 'إعداد الاختصار',
    'shortcut.method': 'الطريقة',
    'shortcut.headers': 'الترويسات',
    'shortcut.json': 'محتوى JSON',
    'shortcut.limits':
        'معلومة مفيدة: الرمز محفوظ داخل الاختصار، فأنشئ رمزا جديدا إذا فقدت الهاتف. قد يؤخر iOS الأتمتة والهاتف مقفل أو في وضع توفير الطاقة. لا تتيح Apple الوصول إلى الرسائل القديمة: الصقها في مراجعة رسائل SMS من iPhone أو استورد كشف حساب.',
    'step.1': 'افتح الاختصارات، ثم الأتمتة، ثم أتمتة جديدة، ثم الرسالة.',
    'step.2':
        'اختر بنكك مرسلا إن أمكن، واترك "الرسالة تحتوي على" فارغا، واختر التشغيل فورا.',
    'step.3':
        'أضف "إذا": مدخل الاختصار، المرسل، يحتوي على اسم بنكك. هكذا تبقى الرسائل الشخصية على هاتفك.',
    'step.4':
        'داخل "إذا"، أضف "الحصول على محتويات URL": {url}، والطريقة POST، والترويستين Authorization = Bearer ثم الرمز و Content-Type = application/json، ومحتوى JSON فيه message = مدخل الاختصار، المحتوى و sender = مدخل الاختصار، المرسل.',
    'step.5':
        'اختبره: أنشئ اختصارا ثانيا بالإجراء نفسه ومحتوى JSON فيه message = ANUM TEST و sender = ANUM TEST، ثم شغّله. يرد ANUM بأنه متصل ولا يسجل شيئا.',
    'step.6':
        'عندما تصل أول رسالة حقيقية من بنك، راجعها في مراجعة رسائل SMS من iPhone وسجّلها مع "هذه القراءة صحيحة".',
    'wallet.title': 'أتمتة Apple Wallet (Apple Pay)',
    'wstep.1':
        'افتح الاختصارات، ثم الأتمتة، ثم أتمتة جديدة، ثم المحفظة (معاملة). اختر بطاقاتك، واختر التشغيل فورا.',
    'wstep.2':
        'أضف "الحصول على محتويات URL": {url}، والطريقة POST، والترويستين نفسيهما، ومحتوى الطلب JSON.',
    'wstep.3':
        'أضف حقول JSON: source = apple_wallet (نص)، و amount = مدخل الاختصار، المبلغ؛ و merchant = مدخل الاختصار، التاجر؛ و card = مدخل الاختصار، البطاقة أو البطاقة الرقمية؛ و name = مدخل الاختصار، الاسم.',
    'wstep.4':
        'اربط كل بطاقة في المحفظة بحسابها في المالية، رسائل SMS من iPhone على الويب (الإعدادات). عندها تصبح الدفعة ورسالة البنك عنها معاملة واحدة، أيهما وصل أولا.',
    'url.placeholder': 'العنوان أعلاه',
    'webSettings':
        'أرقام البطاقات والحسابات، وبطاقات Apple Wallet، وأسماء المرسلين الإضافية، والمساعدة بالذكاء الاصطناعي، ومدة الاحتفاظ بنص الرسالة تُضبط في المالية، رسائل SMS من iPhone على الويب.',
  };
}

String _when(BuildContext context, String? value) =>
    value == null || value.isEmpty ? '-' : recordsDateTime(context, value);

class SmsSetupScreen extends StatefulWidget {
  const SmsSetupScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<SmsSetupScreen> createState() => _SmsSetupScreenState();
}

class _SmsSetupScreenState extends State<SmsSetupScreen> {
  SmsSetupState? _state;
  List<SmsDevice>? _devices;
  RecordsFailure? _failure;
  String? _devicesError;
  List<SmsCheck>? _checks;
  String? _notice;
  bool _noticeBad = false;
  bool _busy = false;
  final _name = TextEditingController();
  final _scopes = <String>{'finance.sms', 'finance.wallet'};

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _failure = null);
    try {
      final state = await widget.repository.smsSetup();
      if (!mounted) return;
      setState(() => _state = state);
    } on Object catch (error) {
      if (mounted) setState(() => _failure = RecordsFailure.from(error));
      return;
    }
    try {
      final devices = await widget.repository.smsDevices();
      if (mounted) {
        setState(() {
          _devices = devices;
          _devicesError = null;
        });
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() => _devicesError = RecordsFailure.from(error).message);
      }
    }
  }

  Future<void> _act(Future<String?> Function() action) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _notice = null;
    });
    String? notice;
    var bad = false;
    try {
      notice = await action();
    } on Object catch (error) {
      notice = RecordsFailure.from(error).message;
      bad = true;
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _notice = notice;
      _noticeBad = bad;
    });
    await _load();
  }

  Future<void> _selfTest() => _act(() async {
        final s = SmsSetupStrings.of(context);
        final result = await widget.repository.smsSelfTest();
        _checks = result.checks;
        if (!result.passed) throw FormatException(s.t('failed'));
        return s.t('passedAll');
      });

  Future<void> _toggle(SmsSetupState state) => _act(() async {
        final s = SmsSetupStrings.of(context);
        await widget.repository
            .setSmsForwarding(version: state.version, on: !state.enabled);
        return s.t(state.enabled ? 'turnedOff' : 'turnedOn');
      });

  Future<void> _connect() async {
    final s = SmsSetupStrings.of(context);
    if (_scopes.isEmpty) {
      setState(() {
        _notice = s.t('device.noScope');
        _noticeBad = true;
      });
      return;
    }
    SmsIssuedToken? issued;
    await _act(() async {
      final name = _name.text.trim();
      issued = await widget.repository
          .connectSmsDevice(name.isEmpty ? 'iPhone' : name, [
        for (final scope in const ['finance.sms', 'finance.wallet'])
          if (_scopes.contains(scope)) scope
      ]);
      _name.clear();
      return null;
    });
    if (issued != null && mounted) await _showToken(issued!);
  }

  Future<void> _device(SmsDevice device, String change) async {
    final s = SmsSetupStrings.of(context);
    if (change == 'revoke' || change == 'rotate') {
      final ok = await confirmRecords(context,
          title: s.t(
              change == 'revoke' ? 'device.revokeTitle' : 'device.rotateTitle',
              {'name': device.name}),
          message: s.t(
              change == 'revoke' ? 'device.revokeText' : 'device.rotateText',
              {'name': device.name}),
          action: s.t(change == 'revoke' ? 'device.revoke' : 'device.rotate'));
      if (!ok || !mounted) return;
    }
    SmsIssuedToken? issued;
    await _act(() async {
      if (change == 'rotate') {
        issued = await widget.repository.rotateSmsDevice(device.id);
        return null;
      }
      await widget.repository.changeSmsDevice(device.id, change);
      return s.t(
          switch (change) {
            'pause' => 'device.paused.done',
            'resume' => 'device.resumed.done',
            _ => 'device.revoked.done',
          },
          {'name': device.name});
    });
    if (issued != null && mounted) await _showToken(issued!);
  }

  Future<void> _showToken(SmsIssuedToken issued) async {
    final base = widget.repository.api.baseUri;
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => SmsTokenScreen(
            issued: issued, url: smsIngestUrl(base, issued.ingestPath))));
  }

  @override
  Widget build(BuildContext context) {
    final s = SmsSetupStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('title'))),
      body: _body(context, s),
    );
  }

  Widget _body(BuildContext context, SmsSetupStrings s) {
    final failure = _failure;
    if (failure != null) {
      return AnumPage(onRefresh: _load, children: [
        AnumEmpty(
          key: const Key('sms-setup-failed'),
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
    final state = _state;
    if (state == null) {
      return Center(
          child: Semantics(
              label: s.t('loading'), child: const CircularProgressIndicator()));
    }
    final p = context.palette;
    final muted = TextStyle(color: p.muted, fontSize: AnumType.sm);
    final url = state.ingestPath == null
        ? null
        : smsIngestUrl(widget.repository.api.baseUri, state.ingestPath!);
    return AnumPage(onRefresh: _load, children: [
      Text(s.t('intro'), style: TextStyle(color: p.muted)),
      RecordsSection(
        key: const Key('sms-setup-state'),
        title: s.t('state'),
        trailing: AnumPill(
            label: s.t(state.enabled ? 'on' : 'off'),
            tone: state.enabled ? AnumTone.ok : AnumTone.neutral),
        children: [
          RecordsField(
              s.t('safetyCheck'),
              state.ready
                  ? s.t('passedAt', {'date': _when(context, state.passedAt)})
                  : s.t('notRun')),
          RecordsField(s.t('devicesActive'), '${state.devicesActive}'),
          RecordsField(s.t('lastOk'), _when(context, state.lastOkAt)),
          RecordsField(
              s.t('lastError'),
              state.lastError == null
                  ? '-'
                  : '${state.lastError} (${_when(context, state.lastErrorAt)})'),
          if (_checks != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
              header: true,
              child: Text(s.t('results'),
                  style: Theme.of(context).textTheme.titleSmall),
            ),
            for (final check in _checks!)
              Row(key: Key('sms-check-${check.name}'), children: [
                Icon(
                    check.passed
                        ? Icons.check_circle_outline
                        : Icons.highlight_off,
                    size: 18,
                    color: check.passed ? p.ok : p.stop),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: Text(
                        '${s.maybe('check.${check.name}') ?? check.name}: ${s.t(check.passed ? 'passed' : 'failedOne')}')),
              ]),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
              key: const Key('sms-setup-check'),
              onPressed: _busy ? null : _selfTest,
              icon: const Icon(Icons.verified_user_outlined),
              label: Text(s.t(_busy ? 'running' : 'run')),
            ),
            OutlinedButton.icon(
              key: const Key('sms-setup-toggle'),
              onPressed: _busy || !state.ready ? null : () => _toggle(state),
              icon: Icon(state.enabled ? Icons.pause : Icons.play_arrow),
              label: Text(s.t(state.enabled ? 'turnOff' : 'turnOn')),
            ),
          ]),
          if (!state.ready) Text(s.t('why'), style: muted),
        ],
      ),
      if (_notice != null)
        Semantics(
          liveRegion: true,
          child: Text(_notice!,
              key: const Key('sms-setup-notice'),
              style: TextStyle(color: _noticeBad ? p.stop : p.ok)),
        ),
      RecordsSection(
        key: const Key('sms-setup-devices'),
        title: s.t('devices'),
        children: [
          if (url == null)
            Text(s.t('endpointHidden'), style: muted)
          else ...[
            Text(s.t('endpoint'), style: muted),
            SelectableText(url,
                key: const Key('sms-setup-url'),
                textDirection: TextDirection.ltr),
            Text(s.t('walletEndpoint'), style: muted),
            SelectableText(smsWalletUrl(url), textDirection: TextDirection.ltr),
          ],
          const SizedBox(height: AnumSpacing.xs),
          if (_devicesError != null)
            Text(_devicesError!, style: TextStyle(color: p.stop))
          else if (_devices == null)
            const Center(child: CircularProgressIndicator())
          else if (_devices!.isEmpty)
            AnumEmpty(
                icon: Icons.phone_iphone,
                title: s.t('devicesNone'),
                message: s.t(state.ready ? 'devicesNoneText' : 'needCheck'))
          else
            for (final d in _devices!) _deviceRow(context, s, d),
          const Divider(),
          TextField(
            key: const Key('sms-setup-name'),
            controller: _name,
            enabled: state.ready && !_busy,
            maxLength: 60,
            decoration: InputDecoration(
                labelText: s.t('device.name'), hintText: 'iPhone'),
          ),
          Text(s.t('device.scopes'), style: muted),
          for (final scope in const ['finance.sms', 'finance.wallet'])
            CheckboxListTile(
              key: Key('sms-setup-scope-$scope'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _scopes.contains(scope),
              onChanged: state.ready && !_busy
                  ? (on) => setState(() =>
                      on == true ? _scopes.add(scope) : _scopes.remove(scope))
                  : null,
              title: Text(s.t('scope.$scope')),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('sms-setup-connect'),
              onPressed: state.ready && !_busy ? _connect : null,
              icon: const Icon(Icons.add),
              label: Text(s.t('device.add')),
            ),
          ),
        ],
      ),
      SmsShortcutSteps(url: url),
      Text(s.t('webSettings'), style: muted),
    ]);
  }

  Widget _deviceRow(BuildContext context, SmsSetupStrings s, SmsDevice d) {
    final lines = [
      [
        s.t('device.${d.status}'),
        s.t('device.token', {'hint': d.tokenHint}),
        s.t('device.messages', {'n': d.messageCount}),
      ].join(' · '),
      d.scopes.map((scope) => s.maybe('scope.$scope') ?? scope).join(', '),
      '${s.t('lastOk')}: ${_when(context, d.lastOkAt)} · ${s.t('lastError')}: ${d.lastError == null ? '-' : '${d.lastError} (${_when(context, d.lastErrorAt)})'}',
    ];
    return Column(
      key: Key('sms-device-${d.id}'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(
          icon: Icons.phone_iphone,
          tone: d.status == 'active'
              ? AnumTone.ok
              : d.status == 'paused'
                  ? AnumTone.warn
                  : null,
          title: d.name,
          subtitle: lines[0],
          detail: '${lines[1]}\n${lines[2]}',
        ),
        if (d.status != 'revoked')
          Wrap(spacing: AnumSpacing.xs, children: [
            TextButton(
              key: Key(
                  'sms-device-${d.status == 'active' ? 'pause' : 'resume'}-${d.id}'),
              onPressed: _busy
                  ? null
                  : () => _device(d, d.status == 'active' ? 'pause' : 'resume'),
              child: Text(
                  s.t(d.status == 'active' ? 'device.pause' : 'device.resume')),
            ),
            TextButton(
              key: Key('sms-device-rotate-${d.id}'),
              onPressed: _busy ? null : () => _device(d, 'rotate'),
              child: Text(s.t('device.rotate')),
            ),
            TextButton(
              key: Key('sms-device-revoke-${d.id}'),
              onPressed: _busy ? null : () => _device(d, 'revoke'),
              child: Text(s.t('device.revoke')),
            ),
          ]),
      ],
    );
  }
}

/// The Shortcut and Apple Wallet steps, with the address when it is known.
class SmsShortcutSteps extends StatelessWidget {
  const SmsShortcutSteps({required this.url, super.key});
  final String? url;

  @override
  Widget build(BuildContext context) {
    final s = SmsSetupStrings.of(context);
    final address = url ?? s.t('url.placeholder');
    final wallet = url == null ? s.t('url.placeholder') : smsWalletUrl(url!);
    Widget steps(List<String> items) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < items.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
                child: Text('${i + 1}. ${items[i]}'),
              ),
          ],
        );
    return RecordsSection(
      key: const Key('sms-setup-steps'),
      title: s.t('shortcut.title'),
      children: [
        steps([
          s.t('step.1'),
          s.t('step.2'),
          s.t('step.3'),
          s.t('step.4', {'url': address}),
          s.t('step.5'),
          s.t('step.6'),
        ]),
        const SizedBox(height: AnumSpacing.xs),
        Semantics(
            header: true,
            child: Text(s.t('wallet.title'),
                style: Theme.of(context).textTheme.titleSmall)),
        steps([
          s.t('wstep.1'),
          s.t('wstep.2', {'url': wallet}),
          s.t('wstep.3'),
          s.t('wstep.4'),
        ]),
        Text(s.t('shortcut.limits'),
            style:
                TextStyle(color: context.palette.muted, fontSize: AnumType.sm)),
      ],
    );
  }
}

/// The token, shown once. It is held only by this page's state, hidden until
/// asked for, hidden again when the app leaves the foreground, and dropped
/// when the page closes; nothing writes it to storage or a log.
class SmsTokenScreen extends StatefulWidget {
  const SmsTokenScreen({required this.issued, required this.url, super.key});
  final SmsIssuedToken issued;
  final String url;

  @override
  State<SmsTokenScreen> createState() => _SmsTokenScreenState();
}

class _SmsTokenScreenState extends State<SmsTokenScreen>
    with WidgetsBindingObserver {
  bool _shown = false;
  bool _copied = false;
  Timer? _clear;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // The app switcher takes its picture as the app becomes inactive.
    if (state != AppLifecycleState.resumed && _shown) {
      setState(() => _shown = false);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    // A pending clipboard clear still runs: it is a top-level task, not this page's.
    super.dispose();
  }

  Future<void> _copy() async {
    final token = widget.issued.token;
    await Clipboard.setData(ClipboardData(text: token));
    if (!mounted) return;
    setState(() => _copied = true);
    _clear?.cancel();
    _clear = Timer(const Duration(minutes: 1),
        () => unawaited(clearTokenFromClipboard(token)));
  }

  @override
  Widget build(BuildContext context) {
    final s = SmsSetupStrings.of(context);
    final p = context.palette;
    final issued = widget.issued;
    final muted = TextStyle(color: p.muted, fontSize: AnumType.sm);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('token.title'))),
      body: AnumPage(children: [
        AnumSurface(
          accent: p.warn,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(s.t('token.once'), style: TextStyle(color: p.warn)),
            const SizedBox(height: AnumSpacing.xs),
            Text(
                s.t('token.for', {
                  'name': issued.device.name,
                  'hint': issued.device.tokenHint
                }),
                style: muted),
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
              label: s.t('token.label'),
              child: Container(
                key: const Key('sms-token-box'),
                padding: const EdgeInsets.all(AnumSpacing.sm),
                decoration: BoxDecoration(
                    color: p.background,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: p.line)),
                child: _shown
                    ? SelectableText(issued.token,
                        key: const Key('sms-token-value'),
                        textDirection: TextDirection.ltr,
                        style: TextStyle(
                            color: p.text,
                            fontFamily: 'monospace',
                            fontSize: AnumType.sm))
                    : Text(s.t('token.hidden'),
                        key: const Key('sms-token-hidden'), style: muted),
              ),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  OutlinedButton.icon(
                    key: const Key('sms-token-show'),
                    onPressed: () => setState(() => _shown = !_shown),
                    icon: Icon(_shown
                        ? Icons.visibility_off_outlined
                        : Icons.visibility_outlined),
                    label: Text(s.t(_shown ? 'token.hide' : 'token.show')),
                  ),
                  FilledButton.icon(
                    key: const Key('sms-token-copy'),
                    onPressed: () => unawaited(_copy()),
                    icon: const Icon(Icons.copy),
                    label: Text(s.t('token.copy')),
                  ),
                ]),
            if (_copied)
              Semantics(
                liveRegion: true,
                child: Text(s.t('token.copied'),
                    key: const Key('sms-token-copied'),
                    style: TextStyle(color: p.ok)),
              ),
            const SizedBox(height: AnumSpacing.xs),
            Text(s.t('token.protected'), style: muted),
          ]),
        ),
        RecordsSection(title: s.t('shortcut.title'), children: [
          RecordsField(s.t('endpoint'), widget.url),
          RecordsField(s.t('shortcut.method'), 'POST'),
          for (final MapEntry(:key, :value) in issued.headers.entries)
            RecordsField(s.t('shortcut.headers'), '$key: $value'),
          for (final MapEntry(:key, :value) in issued.jsonFields.entries)
            RecordsField(s.t('shortcut.json'), '$key = $value'),
        ]),
        SmsShortcutSteps(url: widget.url),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton(
            key: const Key('sms-token-done'),
            onPressed: () => Navigator.of(context).pop(),
            child: Text(s.t('token.done')),
          ),
        ),
      ]),
    );
  }
}

/// Clears the clipboard if it still holds [token] (the person may have copied
/// something else since). Best effort: a platform that refuses is left as is.
Future<void> clearTokenFromClipboard(String token) async {
  try {
    final current = await Clipboard.getData(Clipboard.kTextPlain);
    if (current?.text == token) {
      await Clipboard.setData(const ClipboardData(text: ''));
    }
  } on Object {
    // Nothing else to do: the clipboard belongs to the system.
  }
}
