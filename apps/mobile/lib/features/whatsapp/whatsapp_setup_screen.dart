import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_models.dart';
import 'whatsapp_ops_controller.dart';

/// Retention choices offered (days; 0 keeps messages until deleted), and the
/// sentinel for "follow the deployment default".
const _retentionChoices = [0, 30, 90, 180, 365, 730];
const _retentionDefault = -1;

/// Setup (docs/whatsapp.md#templates-and-setup): the connection's status for
/// everyone, and for owners the connect or update form, Check, a test
/// message and Disconnect. Access token, app secret and verify token are
/// typed into obscured fields, sent once in the save request, cleared when
/// it ends (also when it fails) and never logged, shown or read back.
class WhatsAppSetupScreen extends StatefulWidget {
  const WhatsAppSetupScreen(
      {required this.controller, this.apiBase, this.onChanged, super.key});
  final WaSetupController controller;

  /// The API's public address, for the webhook URL to copy.
  final Uri? apiBase;

  /// Called after the account changed, so the inbox reloads.
  final VoidCallback? onChanged;

  @override
  State<WhatsAppSetupScreen> createState() => _WhatsAppSetupScreenState();
}

class _WhatsAppSetupScreenState extends State<WhatsAppSetupScreen> {
  final _phone = TextEditingController();
  final _waba = TextEditingController();
  final _testNumber = TextEditingController();
  final _accessToken = TextEditingController();
  final _appSecret = TextEditingController();
  final _verifyToken = TextEditingController();
  bool _triage = true;
  int _retention = _retentionDefault;
  bool _copied = false;
  bool _seeded = false;

  WaSetupController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_seed);
    c.load();
  }

  @override
  void dispose() {
    c.removeListener(_seed);
    for (final field in [
      _phone,
      _waba,
      _testNumber,
      _accessToken,
      _appSecret,
      _verifyToken
    ]) {
      field.dispose();
    }
    c.dispose();
    super.dispose();
  }

  /// Fill the non-secret fields from the saved account, once it is known
  /// (and again after a save or a disconnect).
  void _seed() {
    final account = c.account;
    if (account == null || c.phase != WaPhase.ready || _seeded) return;
    _seeded = true;
    _phone.text = account.phoneNumberId ?? '';
    _waba.text = account.businessAccountId ?? '';
    _testNumber.text = account.ownerTestNumber ?? '';
    _triage = account.triageEnabled;
    _retention = account.messageRetentionDays ?? _retentionDefault;
  }

  void _clearSecrets() {
    _accessToken.clear();
    _appSecret.clear();
    _verifyToken.clear();
  }

  Future<void> _save() async {
    final update = WaAccountUpdate(
        phoneNumberId: _phone.text,
        businessAccountId: _waba.text,
        ownerTestNumber: _testNumber.text,
        triageEnabled: _triage,
        autoSendTemplates: c.account?.autoSendTemplates ?? const [],
        retentionDays: _retention == _retentionDefault ? null : _retention,
        sendRetention: true,
        accessToken: _accessToken.text,
        appSecret: _appSecret.text,
        verifyToken: _verifyToken.text);
    final fixable = c.validate(update) != null;
    final ok = await c.save(update);
    // A request was made: the typed secrets never outlive it, even when it
    // failed. A form problem found before sending keeps what was typed.
    if (!fixable) _clearSecrets();
    if (ok && mounted) {
      setState(() {
        _seeded = false;
        _seed();
      });
      widget.onChanged?.call();
    }
  }

  Future<void> _disconnect() async {
    final l = context.anum;
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('waOps.setup.disconnectTitle')),
        content: Text(l.t('waOps.setup.disconnectText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(l.t('waOps.cancel'))),
          FilledButton(
              key: const Key('wa-disconnect-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(l.t('waOps.setup.disconnect'))),
        ],
      ),
    );
    if (yes != true) return;
    if (await c.disconnect() && mounted) {
      _clearSecrets();
      setState(() {
        _seeded = false;
        _seed();
      });
      widget.onChanged?.call();
    }
  }

  Future<void> _copy(String url) async {
    await Clipboard.setData(ClipboardData(text: url));
    if (!mounted) return;
    setState(() => _copied = true);
  }

  Future<void> _generate() async {
    final token = randomVerifyToken();
    _verifyToken.text = token;
    // Meta needs the same value: it goes to the clipboard, not on screen.
    await Clipboard.setData(ClipboardData(text: token));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(context.anum.t('waOps.setup.generated'))));
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('waOps.setup.title')), actions: [
        ListenableBuilder(
            listenable: c,
            builder: (context, _) => IconButton(
                onPressed: c.busy == null ? c.load : null,
                tooltip: l.t('refresh'),
                icon: const Icon(Icons.refresh))),
      ]),
      body: ListenableBuilder(
          listenable: c,
          builder: (context, _) =>
              AnumPage(onRefresh: c.load, children: _body(l))),
    );
  }

  List<Widget> _body(AnumLocalizations l) {
    switch (c.phase) {
      case WaPhase.initial:
      case WaPhase.loading:
      case WaPhase.notConnected:
        return [
          Semantics(
              liveRegion: true,
              label: l.t('waOps.setup.loading'),
              child: const Center(
                  child: Padding(
                      padding: EdgeInsets.all(AnumSpacing.lg),
                      child: CircularProgressIndicator())))
        ];
      case WaPhase.denied:
        return [
          AnumEmpty(
              icon: Icons.lock_outline,
              title: l.t('waOps.setup.deniedTitle'),
              message: c.error ?? l.t('waDeniedText'))
        ];
      case WaPhase.error:
        return [
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('waOps.setup.failed'),
              message: c.error ?? '',
              action: l.t('retry'),
              onAction: c.load)
        ];
      case WaPhase.ready:
        break;
    }
    final account = c.account!;
    return [
      _StatusCard(
          account: account,
          webhook: widget.apiBase == null
              ? account.webhookPath
              : webhookUrl(widget.apiBase!, account.webhookPath),
          copied: _copied,
          onCopy: _copy),
      if (account.configured && c.canManage) _actions(l, account),
      if ((c.notice != null || c.noticeKey != null) && !c.noticeInForm)
        _notice(l),
      if (c.canManage)
        _form(l, account)
      else
        AnumSurface(
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Icon(Icons.lock_outline),
          const SizedBox(width: AnumSpacing.sm),
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(l.t('waOps.setup.ownerOnlyTitle'),
                    style: Theme.of(context).textTheme.titleSmall),
                Text(l.t('waOps.setup.ownerOnly')),
              ])),
        ])),
    ];
  }

  Widget _notice(AnumLocalizations l) {
    final p = context.palette;
    return Semantics(
        liveRegion: true,
        child: Text(
            c.noticeKey != null ? l.f(c.noticeKey!, c.noticeValues) : c.notice!,
            key: const Key('wa-setup-notice'),
            style: Theme.of(context)
                .textTheme
                .bodyMedium
                ?.copyWith(color: c.noticeFailed ? p.stop : p.ok)));
  }

  Widget _actions(AnumLocalizations l, WaAccount account) {
    final idle = c.busy == null;
    final canTest = (account.ownerTestNumber ?? '').isNotEmpty;
    return Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
      OutlinedButton.icon(
          key: const Key('wa-setup-check'),
          onPressed: idle ? c.check : null,
          icon: const Icon(Icons.refresh),
          label: Text(c.busy == 'check'
              ? l.t('waOps.setup.checking')
              : l.t('waOps.setup.check'))),
      Tooltip(
        message: canTest ? '' : l.t('waOps.setup.testNeedsNumber'),
        child: OutlinedButton.icon(
            key: const Key('wa-setup-test'),
            onPressed: idle && canTest ? c.sendTest : null,
            icon: const Icon(Icons.send),
            label: Text(
                c.busy == 'test' ? l.t('waSending') : l.t('waOps.setup.test'))),
      ),
      OutlinedButton.icon(
          key: const Key('wa-setup-disconnect'),
          style:
              OutlinedButton.styleFrom(foregroundColor: context.palette.stop),
          onPressed: idle ? _disconnect : null,
          icon: const Icon(Icons.link_off),
          label: Text(l.t('waOps.setup.disconnect'))),
    ]);
  }

  Widget _secret(String key, TextEditingController controller, String label,
      {String? helper, Widget? suffix}) {
    return TextField(
      key: Key(key),
      controller: controller,
      maxLength: 512,
      textDirection: TextDirection.ltr,
      obscureText: true,
      enableSuggestions: false,
      autocorrect: false,
      enableIMEPersonalizedLearning: false,
      autofillHints: const <String>[],
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        helperMaxLines: 4,
        counterText: '',
        suffixIcon: suffix,
        border: const OutlineInputBorder(),
      ),
    );
  }

  Widget _plain(String key, TextEditingController controller, String label,
      {String? helper, String? hint, TextInputType? keyboard}) {
    return TextField(
      key: Key(key),
      controller: controller,
      textDirection: TextDirection.ltr,
      keyboardType: keyboard,
      autocorrect: false,
      enableSuggestions: false,
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        helperMaxLines: 4,
        hintText: hint,
        border: const OutlineInputBorder(),
      ),
    );
  }

  Widget _form(AnumLocalizations l, WaAccount account) {
    final text = Theme.of(context).textTheme;
    final busy = c.busy != null;
    final saved = account.configured;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Semantics(
            header: true,
            child: Text(
                saved
                    ? l.t('waOps.setup.editTitle')
                    : l.t('waOps.setup.connectTitle'),
                style: text.titleMedium)),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('waOps.setup.intro'), style: text.bodySmall),
        const SizedBox(height: AnumSpacing.sm),
        _plain('wa-setup-phone', _phone, l.t('waOps.setup.phoneId'),
            keyboard: TextInputType.number),
        const SizedBox(height: AnumSpacing.sm),
        _plain('wa-setup-waba', _waba, l.t('waOps.setup.wabaId'),
            keyboard: TextInputType.number),
        const SizedBox(height: AnumSpacing.sm),
        _secret('wa-setup-token', _accessToken, l.t('waOps.setup.token'),
            helper: account.accessTokenHint == null
                ? null
                : l.f('waOps.setup.keep', {'hint': account.accessTokenHint!})),
        const SizedBox(height: AnumSpacing.sm),
        _secret('wa-setup-secret', _appSecret, l.t('waOps.setup.secret'),
            helper: account.appSecretSet ? l.t('waOps.setup.keepSaved') : null),
        const SizedBox(height: AnumSpacing.sm),
        _secret('wa-setup-verify', _verifyToken, l.t('waOps.setup.verify'),
            helper: account.verifyTokenSet
                ? l.t('waOps.setup.keepSaved')
                : l.f('waOps.setup.verifyHelp',
                    {'count': WaSetupController.minVerifyToken}),
            suffix: IconButton(
                key: const Key('wa-setup-generate'),
                tooltip: l.t('waOps.setup.generate'),
                onPressed: busy ? null : _generate,
                icon: const Icon(Icons.auto_fix_high))),
        const SizedBox(height: AnumSpacing.sm),
        _plain(
            'wa-setup-test-number', _testNumber, l.t('waOps.setup.testNumber'),
            hint: '9665XXXXXXXX', keyboard: TextInputType.phone),
        const SizedBox(height: AnumSpacing.sm),
        DropdownButtonFormField<int>(
          key: const Key('wa-setup-retention'),
          initialValue: _retention,
          isExpanded: true,
          decoration: InputDecoration(
              labelText: l.t('waOps.setup.retention'),
              helperText: l.t('waOps.setup.retentionNote'),
              helperMaxLines: 4,
              border: const OutlineInputBorder()),
          items: [
            DropdownMenuItem(
                value: _retentionDefault,
                child: Text(
                    l.f('waOps.setup.retentionDefault', {
                      'value': _retentionLabel(l, account.defaultRetentionDays)
                    }),
                    overflow: TextOverflow.ellipsis)),
            for (final days
                in {..._retentionChoices, if (_retention >= 0) _retention}
                    .toList()
                  ..sort())
              DropdownMenuItem(
                  value: days,
                  child: Text(_retentionLabel(l, days),
                      overflow: TextOverflow.ellipsis)),
          ],
          onChanged: busy
              ? null
              : (value) =>
                  setState(() => _retention = value ?? _retentionDefault),
        ),
        SwitchListTile(
            key: const Key('wa-setup-triage'),
            contentPadding: EdgeInsets.zero,
            title: Text(l.t('waOps.setup.triage')),
            value: _triage,
            onChanged: busy ? null : (v) => setState(() => _triage = v)),
        Text(l.t('waOps.setup.secretsNote'), style: text.bodySmall),
        if ((c.notice != null || c.noticeKey != null) && c.noticeInForm) ...[
          const SizedBox(height: AnumSpacing.sm),
          _notice(l),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Align(
            alignment: AlignmentDirectional.centerEnd,
            child: FilledButton.icon(
                key: const Key('wa-setup-save'),
                onPressed: busy ? null : _save,
                icon: const Icon(Icons.power),
                label: Text(c.busy == 'save'
                    ? l.t('waOps.saving')
                    : saved
                        ? l.t('waOps.setup.save')
                        : l.t('waOps.setup.connect')))),
      ]),
    );
  }

  String _retentionLabel(AnumLocalizations l, int days) => days > 0
      ? l.f('waOps.setup.days', {'count': days})
      : l.t('waOps.setup.retentionKeep');
}

class _StatusCard extends StatelessWidget {
  const _StatusCard(
      {required this.account,
      required this.webhook,
      required this.copied,
      required this.onCopy});
  final WaAccount account;
  final String webhook;
  final bool copied;
  final Future<void> Function(String) onCopy;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final status = account.status;
    final state = !account.configured
        ? l.t('waOps.setup.state.none')
        : status == 'connected'
            ? l.t('waOps.setup.state.connected')
            : status == 'error'
                ? l.t('waOps.setup.state.error')
                : l.t('waOps.setup.state.unverified');
    final tone = !account.configured
        ? AnumTone.neutral
        : status == 'connected'
            ? AnumTone.ok
            : status == 'error'
                ? AnumTone.stop
                : AnumTone.warn;
    Widget fact(String label, String value, {bool ltr = false}) => Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
        child: Wrap(spacing: AnumSpacing.xs, children: [
          Text(label, style: text.bodySmall),
          Text(value,
              textDirection: ltr ? TextDirection.ltr : null,
              style: text.bodyMedium),
        ]));
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Semantics(
            header: true,
            child:
                Text(l.t('waOps.setup.statusTitle'), style: text.titleMedium)),
        const SizedBox(height: AnumSpacing.xs),
        if (account.deploymentProblems.isNotEmpty)
          Semantics(
              liveRegion: true,
              child: Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                  child: Text(
                      l.f('waOps.setup.deployment',
                          {'names': account.deploymentProblems.join(', ')}),
                      style: text.bodyMedium?.copyWith(color: p.stop)))),
        Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: AnumPill(label: state, tone: tone)),
        if ((account.verifiedName ?? '').isNotEmpty)
          fact(l.t('waOps.setup.name'), account.verifiedName!),
        if ((account.displayPhoneNumber ?? '').isNotEmpty)
          fact(l.t('waOps.setup.number'), account.displayPhoneNumber!,
              ltr: true),
        if ((account.qualityRating ?? '').isNotEmpty)
          fact(
              l.t('waOps.setup.quality'), account.qualityRating!.toLowerCase()),
        fact(
            l.t('waOps.setup.lastWebhook'),
            account.lastWebhookAt == null
                ? l.t('waOps.setup.never')
                : exactTime(account.lastWebhookAt!)),
        if (account.lastCheckedAt != null)
          fact(l.t('waOps.setup.lastCheck'), exactTime(account.lastCheckedAt!)),
        if ((account.lastError ?? '').isNotEmpty)
          Text(account.lastError!,
              style: text.bodyMedium?.copyWith(color: p.stop)),
        const SizedBox(height: AnumSpacing.xs),
        Text(l.t('waOps.setup.webhookUrl'), style: text.bodySmall),
        SelectableText(webhook,
            key: const Key('wa-setup-webhook'),
            textDirection: TextDirection.ltr,
            style: text.bodyMedium),
        Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
                key: const Key('wa-setup-copy'),
                onPressed: () => onCopy(webhook),
                icon: Icon(copied ? Icons.check : Icons.copy),
                label: Text(copied
                    ? l.t('waOps.setup.copied')
                    : l.t('waOps.setup.copy')))),
        Text(l.t('waOps.setup.webhookHelp'), style: text.bodySmall),
      ]),
    );
  }
}
