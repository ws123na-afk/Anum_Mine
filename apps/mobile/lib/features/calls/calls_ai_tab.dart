part of 'calls_screen.dart';

// -- AI calls and the provider (docs/calls.md#ai-calls) ----------------------
//
// The same rules as the web's AI calls tab (apps/web/src/CallsAdmin.tsx):
// only members (call:log) ask, only for contacts with recorded consent who
// did not opt out, only while the server reports AI calls ready, and every
// request waits for a workspace owner's approval of the exact number,
// purpose and script (approved under Approvals, bound to the payload hash).
// The server checks all of it again; the phone only explains it first.

AnumTone _aiTone(String status) => switch (status) {
      'completed' => AnumTone.ok,
      'failed' => AnumTone.stop,
      'pending_approval' || 'calling' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String _aiStatus(AnumLocalizations l, String status) =>
    aiCallStatuses.contains(status) ? l.t('calls.ai.status.$status') : status;

class _AiTab extends StatefulWidget {
  const _AiTab(
      {required this.controller,
      required this.onOpenApprovals,
      required this.onOpenContacts,
      required this.onDial});
  final CallsController controller;
  final VoidCallback onOpenApprovals, onOpenContacts;
  final Future<void> Function(String phone, CallContact? contact) onDial;

  @override
  State<_AiTab> createState() => _AiTabState();
}

class _AiTabState extends State<_AiTab> {
  String? _note, _problem;
  bool _sentNote = false;
  String? _busy;

  CallsController get c => widget.controller;

  Future<void> _cancel(AiCallRequest request) async {
    final l = AnumLocalizations.of(context);
    setState(() {
      _busy = request.id;
      _problem = null;
    });
    try {
      await c.repository.cancelAiCall(request.id);
      if (!mounted) return;
      setState(() {
        _note = l.t('calls.ai.cancelled');
        _sentNote = false;
      });
      await c.aiCalls.load();
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  void _sent() {
    setState(() {
      _note = AnumLocalizations.of(context).t('calls.ai.sent');
      _sentNote = true;
    });
    unawaited(c.aiCalls.load());
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final list = c.aiCalls;
    return ListenableBuilder(
      listenable: Listenable.merge([c, list, c.contacts]),
      builder: (context, _) {
        final setup = c.setup;
        final canLog = setup?.canLog ?? false;
        final consenting = consentingContacts(c.contacts.items);
        return KeyedSubtree(
          key: const Key('ai-tab'),
          child: _ListPage(
            onRefresh: c.loadAi,
            children: [
              _ProviderCard(controller: c),
              _AiSection(
                title: l.t('calls.ai.new'),
                icon: Icons.smart_toy_outlined,
                children: [
                  Text(l.t('calls.ai.rules'),
                      style: TextStyle(color: context.palette.muted)),
                  if (!canLog)
                    _Blocked(
                        icon: Icons.lock_outline,
                        text: l.t('calls.ai.blocked.role'))
                  else if (!(setup?.aiCalls ?? false))
                    _Blocked(
                        key: const Key('ai-off'),
                        icon: Icons.power_off_outlined,
                        text: setup?.aiProblem ?? l.t('calls.ai.blocked.off'))
                  else if (consenting.isEmpty)
                    AnumEmpty(
                      icon: Icons.how_to_reg_outlined,
                      title: l.t('calls.ai.noConsenting'),
                      message: '',
                      action: l.t('calls.tab.contacts'),
                      onAction: widget.onOpenContacts,
                    )
                  else
                    AiCallForm(
                      key: const Key('ai-form'),
                      repository: c.repository,
                      contacts: consenting,
                      centsPerMinute: setup?.costPerMinuteCents,
                      onSent: _sent,
                    ),
                  if (_note != null)
                    Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        _Note(text: _note!),
                        if (_sentNote)
                          OutlinedButton(
                            onPressed: widget.onOpenApprovals,
                            child: Text(l.t('calls.ai.openApprovals')),
                          ),
                      ],
                    ),
                ],
              ),
              Semantics(
                header: true,
                child: Text(l.t('calls.ai.title'),
                    style: Theme.of(context).textTheme.titleMedium),
              ),
              if (_problem != null) _Note(text: _problem!, error: true),
              _ListState(
                list: list,
                emptyTitle: l.t('calls.ai.empty'),
                emptyText: l.t('calls.ai.emptyText'),
                noMatch: l.t('calls.ai.empty'),
                icon: Icons.smart_toy_outlined,
              ),
              if (list.phase == CallsPhase.ready)
                for (final request in list.items)
                  _AiRow(
                    request: request,
                    canLog: canLog,
                    busy: _busy != null,
                    onCancel: () => unawaited(_cancel(request)),
                    onOpenApprovals: widget.onOpenApprovals,
                    onOpenCall: request.callId == null
                        ? null
                        : () => Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => CallDetailScreen(
                                  controller: c,
                                  callId: request.callId!,
                                  onDial: widget.onDial,
                                ),
                              ),
                            ),
                  ),
              if (list.hasMore && list.phase == CallsPhase.ready)
                Center(
                  child: OutlinedButton(
                    onPressed:
                        list.loadingMore ? null : () => unawaited(list.more()),
                    child: Text(l.t('calls.more')),
                  ),
                ),
              _OptOutsSection(controller: c),
            ],
          ),
        );
      },
    );
  }
}

class _AiSection extends StatelessWidget {
  const _AiSection(
      {required this.title,
      required this.icon,
      required this.children,
      super.key});
  final String title;
  final IconData icon;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => AnumSurface(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Semantics(
            header: true,
            child: Row(children: [
              Icon(icon, color: context.palette.violet),
              const SizedBox(width: AnumSpacing.xs),
              Expanded(
                child:
                    Text(title, style: Theme.of(context).textTheme.titleMedium),
              ),
            ]),
          ),
          for (final child in children) ...[
            const SizedBox(height: AnumSpacing.sm),
            child,
          ],
        ]),
      );
}

class _Blocked extends StatelessWidget {
  const _Blocked({required this.icon, required this.text, super.key});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      liveRegion: true,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(icon, size: 18, color: p.warn),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(child: Text(text)),
      ]),
    );
  }
}

/// The voice provider's status. Owners see the account (GET /provider);
/// members see what GET /setup reports. Owners open ProviderScreen to connect,
/// change, check or disconnect it (calls_admin.dart).
class _ProviderCard extends StatelessWidget {
  const _ProviderCard({required this.controller});
  final CallsController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final setup = controller.setup;
    final owner = setup?.canManage ?? false;
    final account = controller.provider;
    final connected = owner
        ? (account?.connected ?? false)
        : (setup?.providerConnected ?? false);
    final problem = owner ? account?.problem : setup?.providerProblem;
    final small = TextStyle(color: p.muted, fontSize: AnumType.sm);

    final List<Widget> body;
    if (owner && controller.providerLoading && account == null) {
      body = [
        Semantics(
          liveRegion: true,
          label: l.t('calls.loading'),
          child: const Center(child: CircularProgressIndicator()),
        ),
      ];
    } else if (owner && controller.providerError != null && account == null) {
      body = [
        _Note(
            text: l.f('calls.provider.loadFailed',
                {'message': controller.providerError!}),
            error: true),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton(
            onPressed: () => unawaited(controller.loadAi()),
            child: Text(l.t('calls.retry')),
          ),
        ),
      ];
    } else if (!connected) {
      body = [
        Text(l.t('calls.provider.notConfiguredText'),
            key: const Key('provider-off')),
        if (!owner) Text(l.t('calls.provider.askOwner'), style: small),
      ];
    } else {
      final checked = account?.lastCheckedAt;
      body = [
        if (account != null) ...[
          if (account.fromNumber != null)
            Text(l.f('calls.provider.number', {'number': account.fromNumber!}),
                style: small),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
            AnumPill(
                label: l.t(account.aiCallsEnabled
                    ? 'calls.provider.aiOn'
                    : 'calls.provider.aiOff'),
                tone: account.aiCallsEnabled ? AnumTone.ok : AnumTone.neutral),
            AnumPill(
                label: l.t(account.recordingEnabled
                    ? 'calls.provider.recordingOn'
                    : 'calls.provider.recordingOff')),
            AnumPill(
                label: l.f('calls.provider.rate',
                    {'cost': aiCostText(60, account.costPerMinuteCents)})),
          ]),
          Text(
            checked == null
                ? l.t('calls.provider.neverChecked')
                : account.lastCheckStatus == 'failed'
                    ? l.f('calls.provider.checkFailed', {
                        'when': _when(context, checked),
                        'detail': account.lastCheckDetail ?? '',
                      })
                    : l.f('calls.provider.checkOk',
                        {'when': _when(context, checked)}),
            style: small,
          ),
        ],
        if (problem != null) _Note(text: problem, error: true),
      ];
    }

    return AnumSurface(
      key: const Key('provider-card'),
      padding: const EdgeInsets.all(AnumSpacing.md),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Semantics(
              header: true,
              child: Text(l.t('calls.provider.title'),
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            AnumPill(
              label: l.t(connected
                  ? 'calls.provider.connected'
                  : 'calls.provider.notConfigured'),
              tone: connected
                  ? (problem == null ? AnumTone.ok : AnumTone.warn)
                  : AnumTone.neutral,
            ),
          ],
        ),
        for (final child in body) ...[
          const SizedBox(height: AnumSpacing.xs),
          child,
        ],
        if (owner) ...[
          const SizedBox(height: AnumSpacing.sm),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.tonalIcon(
              key: const Key('provider-manage'),
              onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                      builder: (_) => ProviderScreen(controller: controller))),
              icon: const Icon(Icons.settings_outlined),
              label: Text(l.t(connected
                  ? 'calls.provider.manage'
                  : 'calls.provider.connect')),
            ),
          ),
        ],
      ]),
    );
  }
}

class _AiRow extends StatelessWidget {
  const _AiRow({
    required this.request,
    required this.canLog,
    required this.busy,
    required this.onCancel,
    required this.onOpenApprovals,
    required this.onOpenCall,
  });
  final AiCallRequest request;
  final bool canLog, busy;
  final VoidCallback onCancel, onOpenApprovals;
  final VoidCallback? onOpenCall;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final small = TextStyle(color: p.muted, fontSize: AnumType.sm);
    return AnumSurface(
      key: Key('ai-${request.id}'),
      padding: const EdgeInsets.all(AnumSpacing.sm),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(request.contactName,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            AnumPill(
                label: _aiStatus(l, request.status),
                tone: _aiTone(request.status)),
          ],
        ),
        Wrap(spacing: AnumSpacing.xs, children: [
          Text(request.phoneMasked,
              textDirection: TextDirection.ltr, style: small),
          Text(_when(context, request.createdAt), style: small),
          Text(l.f('calls.ai.requestedBy', {'who': request.requestedBy}),
              style: small),
        ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.f('calls.ai.purposeShort', {'purpose': request.purpose})),
        if (request.error != null) Text(request.error!, style: small),
        if (request.pending || onOpenCall != null)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                if (request.pending)
                  OutlinedButton(
                    onPressed: onOpenApprovals,
                    child: Text(l.t('calls.ai.openApprovals')),
                  ),
                if (request.pending && canLog)
                  OutlinedButton(
                    key: Key('ai-cancel-${request.id}'),
                    onPressed: busy ? null : onCancel,
                    child: Text(l.t('calls.ai.cancel')),
                  ),
                if (onOpenCall != null)
                  OutlinedButton.icon(
                    onPressed: onOpenCall,
                    icon: const Icon(Icons.phone_in_talk_outlined),
                    label: Text(l.t('calls.ai.openCall')),
                  ),
              ],
            ),
          ),
      ]),
    );
  }
}

/// Asks for an AI call: contact (only consenting ones), language, purpose,
/// script and the longest call, with its cost at the provider's rate.
class AiCallForm extends StatefulWidget {
  const AiCallForm({
    required this.repository,
    required this.contacts,
    required this.centsPerMinute,
    required this.onSent,
    super.key,
  });
  final CallsRepository repository;
  final List<CallContact> contacts;
  final int? centsPerMinute;
  final VoidCallback onSent;

  @override
  State<AiCallForm> createState() => _AiCallFormState();
}

class _AiCallFormState extends State<AiCallForm> {
  final _purpose = TextEditingController();
  final _script = TextEditingController();
  final _seconds = TextEditingController(text: '$aiSecondsDefault');
  late String _contactId = widget.contacts.first.id;
  late String _language = _languageOf(widget.contacts.first);
  bool _busy = false;
  String? _problem;

  static String _languageOf(CallContact contact) =>
      contact.language == 'ar' ? 'ar' : 'en';

  int get _secondsValue =>
      clampAiSeconds(int.tryParse(westernDigits(_seconds.text.trim())));

  @override
  void didUpdateWidget(AiCallForm oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!widget.contacts.any((c) => c.id == _contactId)) {
      _contactId = widget.contacts.first.id;
    }
  }

  @override
  void dispose() {
    _purpose.dispose();
    _script.dispose();
    _seconds.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final l = AnumLocalizations.of(context);
    final purpose = _purpose.text.trim(), script = _script.text.trim();
    if (purpose.length < aiPurposeMin || script.length < aiScriptMin) {
      setState(() => _problem = l.t('calls.ai.tooShort'));
      return;
    }
    final seconds = _secondsValue;
    _seconds.text = '$seconds';
    setState(() {
      _busy = true;
      _problem = null;
    });
    try {
      await widget.repository.requestAiCall(
        contactId: _contactId,
        purpose: purpose,
        script: script,
        language: _language,
        maxSeconds: seconds,
      );
      if (!mounted) return;
      _purpose.clear();
      _script.clear();
      widget.onSent();
    } on Object catch (e) {
      if (mounted) setState(() => _problem = _message(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    const gap = SizedBox(height: AnumSpacing.sm);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      DropdownButtonFormField<String>(
        key: const Key('ai-contact'),
        initialValue: _contactId,
        isExpanded: true,
        decoration: InputDecoration(
            labelText: l.t('calls.ai.contact'),
            border: const OutlineInputBorder()),
        items: [
          for (final contact in widget.contacts)
            DropdownMenuItem(
                value: contact.id,
                child: Text(contact.name, overflow: TextOverflow.ellipsis)),
        ],
        onChanged: (id) {
          if (id == null) return;
          final next = widget.contacts.firstWhere((c) => c.id == id);
          setState(() {
            _contactId = id;
            if (next.language == 'ar' || next.language == 'en') {
              _language = next.language;
            }
          });
        },
      ),
      gap,
      DropdownButtonFormField<String>(
        key: ValueKey('ai-language-$_language'),
        initialValue: _language,
        isExpanded: true,
        decoration: InputDecoration(
            labelText: l.t('calls.ai.language'),
            border: const OutlineInputBorder()),
        items: [
          for (final code in const ['en', 'ar'])
            DropdownMenuItem(
                value: code, child: Text(l.t('calls.ai.lang.$code'))),
        ],
        onChanged: (code) {
          if (code != null) setState(() => _language = code);
        },
      ),
      gap,
      TextField(
        key: const Key('ai-purpose'),
        controller: _purpose,
        maxLength: aiPurposeMax,
        decoration: InputDecoration(
          labelText: l.t('calls.ai.purpose'),
          helperText: l.t('calls.ai.purposeHint'),
          helperMaxLines: 4,
          border: const OutlineInputBorder(),
        ),
      ),
      gap,
      TextField(
        key: const Key('ai-script'),
        controller: _script,
        maxLength: aiScriptMax,
        minLines: 3,
        maxLines: 8,
        decoration: InputDecoration(
          labelText: l.t('calls.ai.script'),
          helperText: l.t('calls.ai.scriptHint'),
          helperMaxLines: 4,
          border: const OutlineInputBorder(),
        ),
      ),
      gap,
      TextField(
        key: const Key('ai-seconds'),
        controller: _seconds,
        keyboardType: TextInputType.number,
        inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(
          labelText: l.t('calls.ai.maxSeconds'),
          helperText: '${l.t('calls.ai.secondsRange')} ${l.f('calls.ai.cost', {
                'cost': aiCostText(_secondsValue, widget.centsPerMinute)
              })}',
          helperMaxLines: 4,
          border: const OutlineInputBorder(),
        ),
      ),
      if (_problem != null) ...[
        gap,
        _Note(text: _problem!, error: true),
      ],
      gap,
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: FilledButton.icon(
          key: const Key('ai-submit'),
          onPressed: _busy ? null : () => unawaited(_send()),
          icon: const Icon(Icons.smart_toy_outlined),
          label: Text(_busy ? l.t('calls.saving') : l.t('calls.ai.submit')),
        ),
      ),
    ]);
  }
}
