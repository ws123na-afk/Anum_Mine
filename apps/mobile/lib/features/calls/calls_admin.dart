part of 'calls_screen.dart';

// -- owner tools: provider, AI call consent and opt-outs ---------------------
//
// The same permission rules as the web (apps/web/src/CallsAdmin.tsx and
// CallsLists.tsx), and the server checks every one again:
// - the provider is connected, checked and disconnected by owners
//   (call:manage); members only see its state;
// - consent to AI calls is recorded by owners, with the owner's confirmation,
//   and withdrawn by anyone who logs calls (call:log: it only stops calls);
// - opt-outs are added by anyone who logs calls and lifted only by owners,
//   with a reason, when the person asked to be called again.
// Provider secrets are typed into obscured fields, sent once to the API over
// HTTPS, cleared after the request and never logged, shown or read back.

const _secretFieldOptions = (
  obscureText: true,
  enableSuggestions: false,
  autocorrect: false,
  enableIMEPersonalizedLearning: false,
);

/// Connect, change, check or disconnect the voice provider (owners).
class ProviderScreen extends StatefulWidget {
  const ProviderScreen({required this.controller, super.key});
  final CallsController controller;
  @override
  State<ProviderScreen> createState() => _ProviderScreenState();
}

class _ProviderScreenState extends State<ProviderScreen> {
  ProviderStatus? _status;
  String? _loadError, _note, _problem, _busy;
  bool _denied = false;
  ProviderForm _form = ProviderForm.of(null);

  final _authToken = TextEditingController();
  final _apiKeySecret = TextEditingController();
  late final _text = <String, TextEditingController>{
    for (final key in const [
      'accountSid',
      'apiKeySid',
      'twimlAppSid',
      'fromNumber',
      'businessName',
      'greeting',
      'retention',
      'rate',
      'aiMax',
      'aiCost',
    ])
      key: TextEditingController(),
  };

  CallsRepository get _repo => widget.controller.repository;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _form.clearSecrets();
    _authToken.dispose();
    _apiKeySecret.dispose();
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _show(ProviderStatus status) {
    _status = status;
    _form = ProviderForm.of(status);
    _text['accountSid']!.text = _form.accountSid;
    _text['apiKeySid']!.text = _form.apiKeySid;
    _text['twimlAppSid']!.text = _form.twimlAppSid;
    _text['fromNumber']!.text = _form.fromNumber;
    _text['businessName']!.text = _form.businessName;
    _text['greeting']!.text = _form.greeting;
    _text['retention']!.text = '${_form.recordingRetentionDays}';
    _text['rate']!.text = '${_form.costPerMinuteCents}';
    _text['aiMax']!.text = '${_form.aiMaxSeconds}';
    _text['aiCost']!.text = '${_form.aiMaxCostCents}';
    _authToken.clear();
    _apiKeySecret.clear();
  }

  Future<void> _load() async {
    setState(() {
      _loadError = null;
      _denied = false;
    });
    try {
      final status = await _repo.provider();
      if (mounted) setState(() => _show(status));
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _denied = e.isPermissionDenied;
          _loadError = e.readable;
        });
      }
    } on Object catch (e) {
      if (mounted) setState(() => _loadError = '$e');
    }
  }

  int _int(String key, int fallback, int min, int max) =>
      (int.tryParse(westernDigits(_text[key]!.text.trim())) ?? fallback)
          .clamp(min, max);

  Future<void> _save() async {
    final l = AnumLocalizations.of(context);
    final status = _status!;
    _form
      ..accountSid = _text['accountSid']!.text
      ..apiKeySid = _text['apiKeySid']!.text
      ..twimlAppSid = _text['twimlAppSid']!.text
      ..fromNumber = _text['fromNumber']!.text
      ..businessName = _text['businessName']!.text
      ..greeting = _text['greeting']!.text
      ..authToken = _authToken.text
      ..apiKeySecret = _apiKeySecret.text
      ..recordingRetentionDays = _int('retention', 30, 1, 365)
      ..costPerMinuteCents = _int('rate', 0, 0, 1000)
      ..aiMaxSeconds = _int('aiMax', 30, 30, status.limitSeconds)
      ..aiMaxCostCents = _int('aiCost', 1, 1, status.limitCostCents);
    if (_form.accountSid.trim().isEmpty ||
        _form.fromNumber.trim().isEmpty ||
        _form.businessName.trim().isEmpty) {
      setState(() => _problem = l.t('calls.provider.required'));
      return;
    }
    if (!status.connected && _form.authToken.trim().isEmpty) {
      setState(() => _problem = l.t('calls.provider.tokenRequired'));
      return;
    }
    if ((_form.apiKeySid.trim().isEmpty) !=
        (_form.twimlAppSid.trim().isEmpty)) {
      setState(() => _problem = l.t('calls.provider.keyPair'));
      return;
    }
    setState(() {
      _busy = 'save';
      _problem = null;
      _note = null;
    });
    try {
      final saved = await _repo.saveProvider(_form.body());
      if (!mounted) return;
      setState(() {
        _show(saved);
        _note = l.t('calls.provider.saved');
      });
      unawaited(widget.controller.providerChanged(saved));
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      // The typed secrets never outlive the request.
      _form.clearSecrets();
      _authToken.clear();
      _apiKeySecret.clear();
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _check() async {
    final l = AnumLocalizations.of(context);
    setState(() {
      _busy = 'check';
      _problem = null;
      _note = null;
    });
    try {
      final checked = await _repo.checkProvider();
      if (!mounted) return;
      setState(() {
        _status = checked;
        _note = l.f('calls.provider.checked',
            {'detail': checked.lastCheckDetail ?? ''});
      });
      unawaited(widget.controller.providerChanged(checked));
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _disconnect() async {
    final l = AnumLocalizations.of(context);
    if (!await _confirm(context, l.t('calls.provider.disconnectTitle'),
        l.t('calls.provider.disconnectText'))) {
      return;
    }
    setState(() {
      _busy = 'disconnect';
      _problem = null;
      _note = null;
    });
    try {
      await _repo.deleteProvider();
      final status = await _repo.provider();
      if (!mounted) return;
      setState(() {
        _show(status);
        _note = l.t('calls.provider.disconnected');
      });
      unawaited(widget.controller.providerChanged(status));
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Widget _field(String key, String label,
      {String? helper,
      bool ltr = true,
      bool number = false,
      int? maxLength,
      TextInputType? keyboard}) {
    return TextField(
      key: Key('provider-$key'),
      controller: _text[key],
      maxLength: maxLength,
      textDirection: ltr ? TextDirection.ltr : null,
      keyboardType: number ? TextInputType.number : keyboard,
      inputFormatters:
          number ? [FilteringTextInputFormatter.digitsOnly] : const [],
      autocorrect: false,
      enableSuggestions: false,
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        helperMaxLines: 4,
        border: const OutlineInputBorder(),
      ),
    );
  }

  Widget _secret(TextEditingController controller, String key, String label,
          String? helper) =>
      TextField(
        key: Key('provider-$key'),
        controller: controller,
        maxLength: 200,
        textDirection: TextDirection.ltr,
        obscureText: _secretFieldOptions.obscureText,
        enableSuggestions: _secretFieldOptions.enableSuggestions,
        autocorrect: _secretFieldOptions.autocorrect,
        enableIMEPersonalizedLearning:
            _secretFieldOptions.enableIMEPersonalizedLearning,
        autofillHints: const <String>[],
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
          helperMaxLines: 4,
          border: const OutlineInputBorder(),
        ),
      );

  Widget _switch(String key, String label, bool value,
          ValueChanged<bool>? onChanged) =>
      SwitchListTile(
        key: Key('provider-$key'),
        contentPadding: EdgeInsets.zero,
        title: Text(label),
        value: value,
        onChanged: onChanged,
      );

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final status = _status;
    final small = TextStyle(color: p.muted, fontSize: 13);

    Widget body;
    if (status == null && _loadError == null) {
      body = Semantics(
        liveRegion: true,
        label: l.t('calls.provider.loading'),
        child: const _Centered(child: CircularProgressIndicator()),
      );
    } else if (status == null) {
      body = _Centered(
        child: AnumEmpty(
          icon: _denied ? Icons.lock_outline : Icons.cloud_off_outlined,
          title: _denied
              ? l.t('calls.provider.ownerOnly')
              : l.f('calls.provider.loadFailed', {'message': _loadError!}),
          message: _denied ? (_loadError ?? '') : '',
          action: _denied ? null : l.t('calls.retry'),
          onAction: _denied ? null : () => unawaited(_load()),
        ),
      );
    } else {
      final f = _form;
      body = _ListPage(children: [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(l.t('calls.provider.what')),
            AnumPill(
              key: const Key('provider-state'),
              label: l.t('calls.provider.state.${status.state}'),
              tone: switch (status.state) {
                'on' => AnumTone.ok,
                'failed' => AnumTone.stop,
                _ => AnumTone.neutral,
              },
            ),
          ],
        ),
        if (!status.connected)
          Text(l.t('calls.provider.notConfiguredText'), style: small),
        if (status.connected && status.problem != null)
          _Note(text: status.problem!, error: true),
        if (status.lastCheckedAt != null)
          Text(
              l.f('calls.provider.lastCheck', {
                'when': _when(context, status.lastCheckedAt!),
                'detail': status.lastCheckDetail ?? '',
              }),
              style: small),
        if (_note != null) _Note(key: const Key('provider-note'), text: _note!),
        if (_problem != null)
          _Note(
              key: const Key('provider-problem'), text: _problem!, error: true),
        _AiSection(
          title: l.t('calls.provider.account'),
          icon: Icons.key_outlined,
          children: [
            Text(l.t('calls.provider.secretsNote'), style: small),
            _field('accountSid', l.t('calls.provider.accountSid'),
                maxLength: 64),
            _secret(
                _authToken,
                'authToken',
                l.t('calls.provider.authToken'),
                status.credentialHint == null
                    ? null
                    : l.f('calls.provider.authTokenKept',
                        {'hint': status.credentialHint!})),
            _field('fromNumber', l.t('calls.provider.fromNumber'),
                maxLength: 40, keyboard: TextInputType.phone),
            _field('businessName', l.t('calls.provider.business'),
                ltr: false, maxLength: 120),
            _field('apiKeySid', l.t('calls.provider.apiKeySid'), maxLength: 64),
            _secret(
                _apiKeySecret,
                'apiKeySecret',
                l.t('calls.provider.apiKeySecret'),
                status.hasApiKeySecret
                    ? l.t('calls.provider.secretKept')
                    : null),
            _field('twimlAppSid', l.t('calls.provider.appSid'), maxLength: 64),
            _field('greeting', l.t('calls.provider.greeting'),
                ltr: false, maxLength: 500),
          ],
        ),
        _AiSection(
          title: l.t('calls.provider.recordingTitle'),
          icon: Icons.fiber_manual_record_outlined,
          children: [
            _switch('recording', l.t('calls.provider.recording'),
                f.recordingEnabled, (v) {
              setState(() {
                f.recordingEnabled = v;
                if (!v) f.transcriptionEnabled = false;
              });
            }),
            _switch(
                'transcription',
                l.t('calls.provider.transcription'),
                f.transcriptionEnabled,
                f.recordingEnabled
                    ? (v) => setState(() => f.transcriptionEnabled = v)
                    : null),
            _field('retention', l.t('calls.provider.retention'),
                number: true, helper: l.t('calls.provider.retentionRange')),
          ],
        ),
        _AiSection(
          title: l.t('calls.provider.aiTitle'),
          icon: Icons.smart_toy_outlined,
          children: [
            _switch('aiCalls', l.t('calls.provider.aiCalls'), f.aiCallsEnabled,
                (v) => setState(() => f.aiCallsEnabled = v)),
            _switch(
                'inboundAi',
                l.t('calls.provider.inboundAi'),
                f.inboundAiEnabled,
                (v) => setState(() => f.inboundAiEnabled = v)),
            _field('aiMax',
                l.f('calls.provider.aiMax', {'max': '${status.limitSeconds}'}),
                number: true),
            _field(
                'aiCost',
                l.f('calls.provider.aiCost',
                    {'max': '${status.limitCostCents}'}),
                number: true),
            _field('rate', l.t('calls.provider.rateField'), number: true),
          ],
        ),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton(
            key: const Key('provider-save'),
            onPressed: _busy == null ? () => unawaited(_save()) : null,
            child: Text(_busy == 'save'
                ? l.t('calls.saving')
                : l.t('calls.provider.save')),
          ),
          if (status.connected)
            OutlinedButton.icon(
              key: const Key('provider-check'),
              onPressed: _busy == null ? () => unawaited(_check()) : null,
              icon: const Icon(Icons.refresh),
              label: Text(l.t('calls.provider.check')),
            ),
          if (status.connected)
            TextButton.icon(
              key: const Key('provider-disconnect'),
              onPressed: _busy == null ? () => unawaited(_disconnect()) : null,
              icon: Icon(Icons.link_off,
                  color: Theme.of(context).colorScheme.error),
              label: Text(l.t('calls.provider.disconnect'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
        ]),
        _AiSection(
          title: l.t('calls.provider.steps'),
          icon: Icons.checklist_outlined,
          children: [
            for (final (i, step) in const [
              'account',
              'number',
              'keys',
              'app',
              'urls',
              'save'
            ].indexed)
              Text('${i + 1}. ${l.t('calls.provider.step.$step')}'),
            if (!status.webhooksReady)
              _Note(text: l.t('calls.provider.noPublicUrl')),
            if (status.voiceUrl != null)
              _UrlRow(
                  label: l.t('calls.provider.voiceUrl'),
                  value: status.voiceUrl!),
            if (status.statusUrl != null)
              _UrlRow(
                  label: l.t('calls.provider.statusUrl'),
                  value: status.statusUrl!),
            Text(l.t('calls.provider.costs'), style: small),
          ],
        ),
      ]);
    }
    return Scaffold(
      appBar: AppBar(title: Text(l.t('calls.provider.title'))),
      body: SafeArea(child: body),
    );
  }
}

class _UrlRow extends StatelessWidget {
  const _UrlRow({required this.label, required this.value});
  final String label, value;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style: TextStyle(color: context.palette.muted, fontSize: 13)),
          SelectableText(value, textDirection: TextDirection.ltr),
        ]),
      ),
      IconButton(
        tooltip: l.f('calls.provider.copy', {'what': label}),
        icon: const Icon(Icons.copy_outlined),
        onPressed: () async {
          await Clipboard.setData(ClipboardData(text: value));
          if (context.mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text(l.t('calls.provider.copied'))));
          }
        },
      ),
    ]);
  }
}

// -- opt-outs ------------------------------------------------------------------

class _OptOutsSection extends StatefulWidget {
  const _OptOutsSection({required this.controller});
  final CallsController controller;
  @override
  State<_OptOutsSection> createState() => _OptOutsSectionState();
}

class _OptOutsSectionState extends State<_OptOutsSection> {
  final _phone = TextEditingController();
  final _noteText = TextEditingController();
  String? _note, _problem;
  bool _busy = false;

  CallsController get c => widget.controller;

  @override
  void dispose() {
    _phone.dispose();
    _noteText.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final l = AnumLocalizations.of(context);
    if (!isDialable(_phone.text)) {
      setState(() => _problem = l.t('calls.contact.badPhone'));
      return;
    }
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      await c.repository.addOptOut(_phone.text, note: _noteText.text);
      if (!mounted) return;
      _phone.clear();
      _noteText.clear();
      setState(() => _note = l.t('calls.optOuts.added'));
      await Future.wait([c.optOuts.load(), c.contacts.load()]);
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _lift(OptOutEntry entry) async {
    final l = AnumLocalizations.of(context);
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => _LiftDialog(masked: entry.masked),
    );
    if (reason == null || !mounted) return;
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      await c.repository.removeOptOut(entry.id, reason);
      if (!mounted) return;
      setState(() => _note = l.t('calls.optOuts.removed'));
      await Future.wait([c.optOuts.load(), c.contacts.load()]);
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final list = c.optOuts;
    final canLog = c.setup?.canLog ?? false;
    final canManage = c.setup?.canManage ?? false;
    return ListenableBuilder(
      listenable: list,
      builder: (context, _) => _AiSection(
        key: const Key('opt-outs'),
        title: l.t('calls.optOuts.title'),
        icon: Icons.block_outlined,
        children: [
          Text(l.t('calls.optOuts.text'), style: TextStyle(color: p.muted)),
          if (canLog) ...[
            TextField(
              key: const Key('opt-out-phone'),
              controller: _phone,
              maxLength: 40,
              keyboardType: TextInputType.phone,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                  labelText: l.t('calls.optOuts.number'),
                  border: const OutlineInputBorder()),
            ),
            TextField(
              key: const Key('opt-out-note'),
              controller: _noteText,
              maxLength: 500,
              decoration: InputDecoration(
                  labelText: l.t('calls.optOuts.note'),
                  border: const OutlineInputBorder()),
            ),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                key: const Key('opt-out-add'),
                onPressed: _busy ? null : () => unawaited(_add()),
                child: Text(l.t('calls.optOuts.add')),
              ),
            ),
          ],
          if (_note != null) _Note(text: _note!),
          if (_problem != null) _Note(text: _problem!, error: true),
          switch (list.phase) {
            CallsPhase.loading => Semantics(
                liveRegion: true,
                label: l.t('calls.loading'),
                child: const LinearProgressIndicator()),
            CallsPhase.denied =>
              _Blocked(icon: Icons.lock_outline, text: list.error ?? ''),
            CallsPhase.error => Wrap(
                  spacing: AnumSpacing.xs,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    _Note(
                        text: l.f(
                            'calls.loadFailed', {'message': list.error ?? ''}),
                        error: true),
                    OutlinedButton(
                      key: const Key('opt-outs-retry'),
                      onPressed: () => unawaited(list.load()),
                      child: Text(l.t('calls.retry')),
                    ),
                  ]),
            CallsPhase.ready when list.items.isEmpty => Text(
                l.t('calls.optOuts.empty'),
                style: TextStyle(color: p.muted)),
            CallsPhase.ready => Column(children: [
                for (final entry in list.items)
                  // A wrapping row, not a ListTile: at 200 percent text on a
                  // narrow phone the Lift button moves under the number.
                  Padding(
                    key: Key('opt-out-${entry.id}'),
                    padding:
                        const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
                    child: Wrap(
                      spacing: AnumSpacing.sm,
                      runSpacing: AnumSpacing.xxs,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      alignment: WrapAlignment.spaceBetween,
                      children: [
                        Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(entry.masked,
                                  textDirection: TextDirection.ltr,
                                  style: const TextStyle(
                                      fontWeight: FontWeight.w600)),
                              Text(
                                  [
                                    optOutSources.contains(entry.source)
                                        ? l.t(
                                            'calls.optOuts.source.${entry.source}')
                                        : entry.source,
                                    _when(context, entry.createdAt),
                                    if (entry.note != null) entry.note!,
                                  ].join(' · '),
                                  style:
                                      TextStyle(color: p.muted, fontSize: 13)),
                            ]),
                        if (canManage)
                          TextButton(
                            key: Key('opt-out-lift-${entry.id}'),
                            onPressed:
                                _busy ? null : () => unawaited(_lift(entry)),
                            child: Text(l.t('calls.optOuts.remove'),
                                semanticsLabel: l.f('calls.optOuts.removeLabel',
                                    {'number': entry.masked})),
                          ),
                      ],
                    ),
                  ),
                if (list.hasMore)
                  OutlinedButton(
                    onPressed:
                        list.loadingMore ? null : () => unawaited(list.more()),
                    child: Text(l.t('calls.more')),
                  ),
              ]),
          },
          if (!canManage && list.items.isNotEmpty)
            Text(l.t('calls.optOuts.ownerOnly'),
                style: TextStyle(color: p.muted, fontSize: 13)),
        ],
      ),
    );
  }
}

class _LiftDialog extends StatefulWidget {
  const _LiftDialog({required this.masked});
  final String masked;
  @override
  State<_LiftDialog> createState() => _LiftDialogState();
}

class _LiftDialogState extends State<_LiftDialog> {
  final _reason = TextEditingController();
  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final ok = _reason.text.trim().length >= optOutReasonMin;
    return AlertDialog(
      title: Text(l.t('calls.optOuts.removeTitle')),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(l.f('calls.optOuts.removeText', {'number': widget.masked})),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('opt-out-reason'),
            controller: _reason,
            maxLength: 500,
            onChanged: (_) => setState(() {}),
            decoration: InputDecoration(
                labelText: l.t('calls.optOuts.reason'),
                helperText: l.t('calls.optOuts.reasonHint'),
                helperMaxLines: 3,
                border: const OutlineInputBorder()),
          ),
        ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: Text(l.t('calls.cancel'))),
        FilledButton(
          key: const Key('opt-out-lift-confirm'),
          onPressed:
              ok ? () => Navigator.of(context).pop(_reason.text.trim()) : null,
          child: Text(l.t('calls.optOuts.remove')),
        ),
      ],
    );
  }
}

// -- consent to AI calls ---------------------------------------------------------

class _ConsentSection extends StatefulWidget {
  const _ConsentSection(
      {required this.controller,
      required this.contact,
      required this.onChanged});
  final CallsController controller;
  final CallContact contact;
  final ValueChanged<CallContact> onChanged;
  @override
  State<_ConsentSection> createState() => _ConsentSectionState();
}

class _ConsentSectionState extends State<_ConsentSection> {
  String? _note, _problem;
  bool _busy = false;

  Future<void> _record() async {
    final l = AnumLocalizations.of(context);
    final saved = await showModalBottomSheet<CallContact>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => ConsentSheet(
          repository: widget.controller.repository, contact: widget.contact),
    );
    if (saved == null || !mounted) return;
    setState(() {
      _note = l.t('calls.consent.recorded');
      _problem = null;
    });
    widget.onChanged(saved);
  }

  Future<void> _withdraw() async {
    final l = AnumLocalizations.of(context);
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      final saved =
          await widget.controller.repository.withdrawConsent(widget.contact.id);
      if (!mounted) return;
      setState(() => _note = l.t('calls.consent.withdrawn'));
      widget.onChanged(saved);
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final contact = widget.contact;
    final setup = widget.controller.setup;
    final canManage = setup?.canManage ?? false;
    final canLog = setup?.canLog ?? false;
    final consent = contact.consent;
    return _AiSection(
      key: const Key('consent'),
      title: l.t('calls.consent.title'),
      icon: Icons.smart_toy_outlined,
      children: [
        if (contact.optedOut)
          _Blocked(
              icon: Icons.block_outlined, text: l.t('calls.consent.optedOut'))
        else if (consent != null)
          Text(l.f('calls.consent.given', {
            'method': consentMethods.contains(consent.method)
                ? l.t('calls.consent.method.${consent.method}')
                : consent.method,
            'date': MaterialLocalizations.of(context)
                .formatMediumDate(consent.grantedAt.toLocal()),
            'who': consent.recordedBy,
          }))
        else
          Text(l.t('calls.consent.none'), style: TextStyle(color: p.muted)),
        if (!canManage && consent == null && !contact.optedOut)
          Text(l.t('calls.consent.ownerOnly'),
              style: TextStyle(color: p.muted, fontSize: 13)),
        if (_note != null) _Note(text: _note!),
        if (_problem != null) _Note(text: _problem!, error: true),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          if (canManage && consent == null && !contact.optedOut)
            OutlinedButton.icon(
              key: const Key('consent-record'),
              onPressed: _busy ? null : () => unawaited(_record()),
              icon: const Icon(Icons.verified_user_outlined),
              label: Text(l.t('calls.consent.record')),
            ),
          if (canLog && consent != null)
            OutlinedButton(
              key: const Key('consent-withdraw'),
              onPressed: _busy ? null : () => unawaited(_withdraw()),
              child: Text(l.t('calls.consent.withdraw')),
            ),
        ]),
      ],
    );
  }
}

/// Records consent to AI calls: how the person agreed, a note, and the
/// owner's confirmation (the API refuses the request without it).
class ConsentSheet extends StatefulWidget {
  const ConsentSheet(
      {required this.repository, required this.contact, super.key});
  final CallsRepository repository;
  final CallContact contact;
  @override
  State<ConsentSheet> createState() => _ConsentSheetState();
}

class _ConsentSheetState extends State<ConsentSheet> {
  final _noteText = TextEditingController();
  String _method = 'verbal';
  bool _confirmed = false, _busy = false;
  String? _problem;

  @override
  void dispose() {
    _noteText.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _problem = null;
    });
    try {
      final saved = await widget.repository.recordConsent(widget.contact.id,
          method: _method, note: _noteText.text);
      if (mounted) Navigator.of(context).pop(saved);
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l.t('calls.consent.record'),
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: AnumSpacing.sm),
          DropdownButtonFormField<String>(
            key: const Key('consent-method'),
            initialValue: _method,
            isExpanded: true,
            decoration: InputDecoration(
                labelText: l.t('calls.consent.method'),
                border: const OutlineInputBorder()),
            items: [
              for (final m in consentMethods)
                DropdownMenuItem(
                    value: m, child: Text(l.t('calls.consent.method.$m'))),
            ],
            onChanged: (m) {
              if (m != null) setState(() => _method = m);
            },
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('consent-note'),
            controller: _noteText,
            maxLength: 500,
            decoration: InputDecoration(
                labelText: l.t('calls.consent.note'),
                border: const OutlineInputBorder()),
          ),
          CheckboxListTile(
            key: const Key('consent-confirm'),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _confirmed,
            onChanged: (v) => setState(() => _confirmed = v ?? false),
            title: Text(
                l.f('calls.consent.confirm', {'name': widget.contact.name})),
          ),
          if (_problem != null) _Note(text: _problem!, error: true),
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: AnumSpacing.xs, children: [
            FilledButton(
              key: const Key('consent-save'),
              onPressed: _confirmed && !_busy ? () => unawaited(_save()) : null,
              child: Text(
                  _busy ? l.t('calls.saving') : l.t('calls.consent.record')),
            ),
            TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: Text(l.t('calls.cancel'))),
          ]),
        ]),
      ),
    );
  }
}
