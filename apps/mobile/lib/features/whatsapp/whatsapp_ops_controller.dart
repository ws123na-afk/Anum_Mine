import 'dart:math';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_models.dart';
import 'whatsapp_repository.dart';

String _message(Object e) => e is ApiException ? e.readable : '$e';

/// The Templates screen (docs/whatsapp.md#templates-and-setup): Meta's
/// message templates with search, refresh from Meta and, for owners, the
/// auto-send switch (the account's `auto_send_templates`, saved through
/// `PUT /account` like the web app). The server checks every permission.
class WaTemplatesController extends ChangeNotifier {
  WaTemplatesController(this.repository);
  final WhatsAppRepository repository;

  /// Templates shown at a time; "Show more" adds this many.
  static const pageSize = 20;

  WaPhase phase = WaPhase.initial;
  WaAccount? account;
  List<WaTemplate> templates = const [];
  DateTime? fetchedAt;
  String? error;
  String query = '';
  int shown = pageSize;

  /// `refresh` while asking Meta again, or the key of the template whose
  /// auto-send switch is being saved.
  String? busy;

  /// A catalog key, or [notice] as the API wrote it when the key is null.
  String? noticeKey;
  String? notice;
  Map<String, Object> noticeValues = const {};
  bool noticeFailed = false;
  bool _disposed = false;
  int _token = 0;

  bool get canManage => account?.canManage ?? false;

  /// Templates matching the search (name, body or language).
  List<WaTemplate> get matches {
    final needle = query.trim().toLowerCase();
    if (needle.isEmpty) return templates;
    return [
      for (final t in templates)
        if (t.name.toLowerCase().contains(needle) ||
            t.body.toLowerCase().contains(needle) ||
            t.language.toLowerCase().contains(needle))
          t
    ];
  }

  List<WaTemplate> get visible => matches.take(shown).toList();
  bool get hasMoreToShow => matches.length > shown;

  Future<void> load({bool refresh = false}) async {
    final token = ++_token;
    if (phase != WaPhase.ready) phase = WaPhase.loading;
    if (refresh) busy = 'refresh';
    error = null;
    _notify();
    try {
      final found = await repository.account();
      if (token != _token || _disposed) return;
      account = found;
      if (!found.configured) {
        phase = WaPhase.notConnected;
        templates = const [];
        busy = null;
        _notify();
        return;
      }
      final list = await repository.templates(refresh: refresh);
      if (token != _token || _disposed) return;
      templates = list.templates;
      fetchedAt = list.fetchedAt;
      phase = WaPhase.ready;
      if (refresh) {
        noticeKey = 'waOps.templates.refreshed';
        noticeFailed = false;
        notice = null;
      }
    } on ApiException catch (e) {
      if (token != _token || _disposed) return;
      phase = e.isPermissionDenied ? WaPhase.denied : WaPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (token != _token || _disposed) return;
      phase = WaPhase.error;
      error = '$e';
    }
    busy = null;
    _notify();
  }

  void search(String value) {
    query = value;
    shown = pageSize;
    _notify();
  }

  void showMore() {
    shown += pageSize;
    _notify();
  }

  /// Turn automatic sending on or off for [template]'s name (every language).
  /// Owners only; a 403 shows the server's sentence.
  Future<void> setAutoSend(WaTemplate template, bool on) async {
    final current = account;
    if (current == null || busy != null) return;
    final names = {...current.autoSendTemplates};
    on ? names.add(template.name) : names.remove(template.name);
    busy = template.key;
    noticeKey = notice = null;
    noticeValues = const {};
    _notify();
    final update = WaAccountUpdate.of(current, autoSendTemplates: [...names]);
    try {
      final saved = await repository.saveAccount(update);
      account = saved;
      final auto = saved.autoSendTemplates.toSet();
      templates = [
        for (final t in templates) t.withAutoSend(auto.contains(t.name))
      ];
      noticeKey = on ? 'waOps.templates.autoOn' : 'waOps.templates.autoOff';
      noticeValues = {'name': template.name};
      noticeFailed = false;
    } on Object catch (e) {
      notice = _message(e);
      noticeFailed = true;
    } finally {
      update.clearSecrets();
    }
    busy = null;
    _notify();
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

/// A problem found before anything is sent, as a catalog key.
typedef WaProblem = String;

/// The Setup screen: connection status for everyone, and for owners the
/// connect or update form, Check, the test message and Disconnect. Secrets
/// travel once in the `PUT` body and are cleared when the request ends,
/// success or not; they are never logged or read back.
class WaSetupController extends ChangeNotifier {
  WaSetupController(this.repository);
  final WhatsAppRepository repository;

  static const minVerifyToken = 16;

  WaPhase phase = WaPhase.initial;
  WaAccount? account;
  String? error;

  /// `save`, `check`, `test` or `disconnect` while a request runs.
  String? busy;
  String? noticeKey;
  String? notice;
  Map<String, Object> noticeValues = const {};
  bool noticeFailed = false;

  /// True when the notice answers the form (a problem to fix or the result
  /// of Save), so the screen shows it next to the Save button.
  bool noticeInForm = false;
  bool _disposed = false;
  int _token = 0;

  bool get canManage => account?.canManage ?? false;

  Future<void> load() async {
    final token = ++_token;
    if (phase != WaPhase.ready) phase = WaPhase.loading;
    error = null;
    _notify();
    try {
      final found = await repository.account();
      if (token != _token || _disposed) return;
      account = found;
      phase = WaPhase.ready;
    } on ApiException catch (e) {
      if (token != _token || _disposed) return;
      phase = e.isPermissionDenied ? WaPhase.denied : WaPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (token != _token || _disposed) return;
      phase = WaPhase.error;
      error = '$e';
    }
    _notify();
  }

  /// The first thing wrong with [update], as a catalog key, or null.
  WaProblem? validate(WaAccountUpdate update) {
    final digits = RegExp(r'^[0-9]{5,20}$');
    if (!digits.hasMatch(update.phoneNumberId.trim())) {
      return 'waOps.err.phoneId';
    }
    if (!digits.hasMatch(update.businessAccountId.trim())) {
      return 'waOps.err.wabaId';
    }
    final connected = account?.configured ?? false;
    if (!connected &&
        (update.accessToken.trim().isEmpty ||
            update.appSecret.trim().isEmpty ||
            update.verifyToken.trim().isEmpty)) {
      return 'waOps.err.secrets';
    }
    final verify = update.verifyToken.trim();
    if (verify.isNotEmpty && verify.length < minVerifyToken) {
      return 'waOps.err.verifyToken';
    }
    final test = update.ownerTestNumber.replaceAll(RegExp(r'[\s+()\-]'), '');
    if (test.isNotEmpty && !RegExp(r'^[0-9]{6,20}$').hasMatch(test)) {
      return 'waOps.err.testNumber';
    }
    return null;
  }

  /// Save [update]. True when the server accepted it. The secrets in
  /// [update] are cleared before this returns.
  Future<bool> save(WaAccountUpdate update) async {
    final problem = validate(update);
    if (problem != null) {
      noticeKey = problem;
      notice = null;
      noticeValues = problem == 'waOps.err.verifyToken'
          ? {'count': minVerifyToken}
          : const {};
      noticeFailed = true;
      noticeInForm = true;
      _notify();
      return false;
    }
    busy = 'save';
    _clearNotice(inForm: true);
    _notify();
    var ok = false;
    try {
      final saved = await repository.saveAccount(update);
      account = saved;
      ok = true;
      if (saved.status == 'connected') {
        noticeKey = 'waOps.setup.saved';
        noticeValues = {
          'name': saved.verifiedName ??
              saved.displayPhoneNumber ??
              saved.phoneNumberId ??
              ''
        };
        noticeFailed = false;
      } else {
        noticeKey = 'waOps.setup.savedProblem';
        noticeValues = {'message': saved.lastError ?? ''};
        noticeFailed = true;
      }
    } on Object catch (e) {
      notice = _message(e);
      noticeFailed = true;
    } finally {
      // The typed secrets never outlive the request, even when it failed.
      update.clearSecrets();
    }
    busy = null;
    _notify();
    return ok;
  }

  Future<void> check() async {
    busy = 'check';
    _clearNotice();
    _notify();
    try {
      final checked = await repository.checkAccount();
      account = checked;
      if (checked.status == 'connected') {
        noticeKey = 'waOps.setup.checkOk';
        noticeFailed = false;
      } else {
        noticeKey = 'waOps.setup.savedProblem';
        noticeValues = {'message': checked.lastError ?? ''};
        noticeFailed = true;
      }
    } on Object catch (e) {
      notice = _message(e);
      noticeFailed = true;
    }
    busy = null;
    _notify();
  }

  Future<void> sendTest() async {
    busy = 'test';
    _clearNotice();
    _notify();
    try {
      final to = await repository.sendTest();
      noticeKey = 'waOps.setup.testSent';
      noticeValues = {'number': to};
      noticeFailed = false;
    } on Object catch (e) {
      notice = _message(e);
      noticeFailed = true;
    }
    busy = null;
    _notify();
  }

  /// Forget the number and its credentials (the screen asks first).
  Future<bool> disconnect() async {
    busy = 'disconnect';
    _clearNotice();
    _notify();
    var ok = false;
    try {
      await repository.disconnect();
      account = await repository.account();
      noticeKey = 'waOps.setup.disconnected';
      noticeFailed = false;
      ok = true;
    } on Object catch (e) {
      notice = _message(e);
      noticeFailed = true;
    }
    busy = null;
    _notify();
    return ok;
  }

  void _clearNotice({bool inForm = false}) {
    noticeKey = notice = null;
    noticeValues = const {};
    noticeInForm = inForm;
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

/// A random verify token to paste into Meta's webhook settings (32 URL-safe
/// characters), from the platform's secure generator.
String randomVerifyToken([Random? random]) {
  const alphabet =
      'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_';
  final source = random ?? Random.secure();
  return String.fromCharCodes(
      [for (var i = 0; i < 32; i++) alphabet.codeUnitAt(source.nextInt(64))]);
}

/// The webhook address to enter in Meta: the API's public address plus the
/// account's webhook path.
String webhookUrl(Uri baseUri, String path) {
  final base = baseUri.toString().replaceAll(RegExp(r'/+$'), '');
  return '$base${path.startsWith('/') ? path : '/$path'}';
}
