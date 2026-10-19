import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'email_controller.dart';
import 'email_models.dart';
import 'email_repository.dart';
import 'inbox_screen.dart' show EmailApprovalCard;

/// A brand-new email from one connected mailbox (docs/email.md): From, To,
/// Cc, Bcc, Subject and the message. Save draft keeps it; Ask to send saves
/// any edits and asks for the send approval, which an owner approves with
/// the payload hash shown. Nothing leaves before that. Leaving with unsaved
/// edits asks first.
class ComposeEmailScreen extends StatefulWidget {
  const ComposeEmailScreen({
    required this.repository,
    required this.accounts,
    super.key,
  });
  final EmailRepository repository;
  final List<EmailAccount> accounts;

  @override
  State<ComposeEmailScreen> createState() => _ComposeEmailScreenState();
}

class _ComposeEmailScreenState extends State<ComposeEmailScreen> {
  late final ComposeController c = ComposeController(widget.repository);
  final _to = TextEditingController();
  final _cc = TextEditingController();
  final _bcc = TextEditingController();
  final _subject = TextEditingController();
  final _body = TextEditingController();
  late String? _accountId = _sendable.firstOrNull?.id;
  String? _toError, _ccError, _bccError, _bodyError;

  List<EmailAccount> get _sendable => [
        for (final a in widget.accounts)
          if (!a.needsReconnect) a
      ];

  List<TextEditingController> get _fieldsControllers =>
      [_to, _cc, _bcc, _subject, _body];

  @override
  void initState() {
    super.initState();
    for (final field in _fieldsControllers) {
      field.addListener(_edited);
    }
  }

  @override
  void dispose() {
    for (final field in _fieldsControllers) {
      field.dispose();
    }
    c.dispose();
    super.dispose();
  }

  void _edited() => setState(() {});

  /// Once asked or sent, the message is exactly what the approval covers.
  bool get _locked => c.sent || c.sendApproval != null;

  EmailDraftFields _fields() => EmailDraftFields(
        accountId: _accountId ?? '',
        to: splitAddresses(_to.text).valid,
        cc: splitAddresses(_cc.text).valid,
        bcc: splitAddresses(_bcc.text).valid,
        subject: _subject.text.trim(),
        body: _body.text,
      );

  bool get _dirty {
    if (_locked) return false;
    if (c.saved == null) {
      return _fieldsControllers.any((f) => f.text.trim().isNotEmpty);
    }
    return c.saved != _fields();
  }

  String? _addressError(AnumLocalizations l, String text) {
    final parts = splitAddresses(text);
    if (parts.invalid.isNotEmpty) {
      return l.f('inbox.compose.invalid', {'list': parts.invalid.join(', ')});
    }
    if (parts.valid.length > EmailDraftFields.maxAddresses) {
      return l
          .f('inbox.compose.tooMany', {'count': EmailDraftFields.maxAddresses});
    }
    return null;
  }

  /// Checks the fields; [sending] also needs a To address and a message.
  bool _validate(AnumLocalizations l, {required bool sending}) {
    setState(() {
      _toError = _addressError(l, _to.text);
      _ccError = _addressError(l, _cc.text);
      _bccError = _addressError(l, _bcc.text);
      _bodyError = null;
      if (sending) {
        if (_toError == null && splitAddresses(_to.text).valid.isEmpty) {
          _toError = l.t('inbox.compose.needTo');
        }
        if (_body.text.trim().isEmpty) {
          _bodyError = l.t('inbox.compose.needBody');
        }
      }
    });
    return _toError == null &&
        _ccError == null &&
        _bccError == null &&
        _bodyError == null;
  }

  Future<void> _save(AnumLocalizations l) async {
    if (!_validate(l, sending: false)) return;
    await c.save(_fields());
  }

  /// Said next to the buttons (a snack bar would cover them) while the
  /// fields still match the saved draft.
  bool get _showSaved => c.saved != null && !_dirty && !_locked;

  Future<void> _ask(AnumLocalizations l) async {
    if (!_validate(l, sending: true)) return;
    await c.askToSend(_fields());
  }

  Future<void> _confirmLeave() async {
    final l = AnumLocalizations.maybeOf(context);
    final discard = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('inbox.compose.discardTitle')),
        content: Text(l.t('inbox.compose.discardText')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(l.t('inbox.compose.keep')),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(l.t('inbox.compose.discard')),
          ),
        ],
      ),
    );
    // Navigator.pop leaves without asking the PopScope again.
    if (discard == true && mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => PopScope(
        canPop: !_dirty && c.busy == null,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop && c.busy == null) _confirmLeave();
        },
        child: Scaffold(
          appBar: AppBar(title: Text(l.t('inbox.compose.title'))),
          body: SafeArea(
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 720),
                child: _sendable.isEmpty
                    ? ListView(
                        padding: const EdgeInsets.all(AnumSpacing.md),
                        children: [
                          AnumEmpty(
                            icon: Icons.link_off,
                            title: l.t('inboxNoAccountsTitle'),
                            message: l.t('inbox.compose.noMailbox'),
                          ),
                        ],
                      )
                    : _form(context, l),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _form(BuildContext context, AnumLocalizations l) {
    final theme = Theme.of(context);
    final busy = c.busy != null;
    final editable = !_locked && !busy;
    const gap = SizedBox(height: AnumSpacing.sm);
    final hidden = widget.accounts.length - _sendable.length;
    return ListView(
      padding: const EdgeInsets.all(AnumSpacing.md),
      children: [
        DropdownButtonFormField<String>(
          key: const Key('compose-from'),
          initialValue: _accountId,
          isExpanded: true,
          decoration: InputDecoration(labelText: l.t('inbox.compose.from')),
          items: [
            for (final account in _sendable)
              DropdownMenuItem(
                value: account.id,
                child: Text(account.address, overflow: TextOverflow.ellipsis),
              ),
          ],
          // A saved draft stays in its mailbox.
          onChanged: editable && c.draft == null
              ? (value) => setState(() => _accountId = value)
              : null,
        ),
        if (hidden > 0)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xxs),
            child: Text(l.t('inbox.compose.reconnectNote'),
                style: theme.textTheme.bodySmall),
          ),
        gap,
        _address(_to, 'compose-to', l.t('inbox.compose.to'), _toError, editable,
            l.t('inbox.compose.addressHint')),
        gap,
        _address(_cc, 'compose-cc', l.t('inbox.compose.cc'), _ccError, editable,
            null),
        gap,
        _address(_bcc, 'compose-bcc', l.t('inbox.compose.bcc'), _bccError,
            editable, null),
        gap,
        TextField(
          key: const Key('compose-subject'),
          controller: _subject,
          enabled: editable,
          maxLength: EmailDraftFields.maxSubject,
          textInputAction: TextInputAction.next,
          decoration: InputDecoration(labelText: l.t('inbox.compose.subject')),
        ),
        TextField(
          key: const Key('compose-body'),
          controller: _body,
          enabled: editable,
          minLines: 6,
          maxLines: 20,
          maxLength: EmailDraftFields.maxBody,
          keyboardType: TextInputType.multiline,
          decoration: InputDecoration(
            labelText: l.t('inbox.compose.body'),
            alignLabelWithHint: true,
            errorText: _bodyError,
            errorMaxLines: 3,
          ),
        ),
        Text(l.t('inbox.compose.approvalNote'),
            style: theme.textTheme.bodySmall),
        if (c.error case final error?)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Semantics(
              liveRegion: true,
              child: Text(error,
                  key: const Key('compose-error'),
                  style: TextStyle(color: theme.colorScheme.error)),
            ),
          ),
        if (c.draftApproval case final approval?)
          EmailApprovalCard(
            key: const Key('compose-draft-approval'),
            approval: approval,
            enabled: !busy,
            onApprove: () => c.approve(approval),
            title: l.t('inboxDraftApprovalTitle'),
            approveLabel: l.t('inboxApproveDraft'),
          ),
        if (c.draftingResult case final result?)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Semantics(
              liveRegion: true,
              child: Text(
                  l.f('inbox.compose.draftingApproved', {'result': result})),
            ),
          ),
        const SizedBox(height: AnumSpacing.sm),
        if (!_locked)
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              OutlinedButton.icon(
                key: const Key('compose-save'),
                onPressed: busy ? null : () => _save(l),
                icon: const Icon(Icons.save_outlined),
                label: Text(c.busy == 'save'
                    ? l.t('inbox.compose.saving')
                    : l.t('inbox.compose.save')),
              ),
              FilledButton.icon(
                key: const Key('compose-ask'),
                onPressed: busy ? null : () => _ask(l),
                icon: const Icon(Icons.send),
                label: Text(c.busy == 'ask'
                    ? l.t('inbox.compose.asking')
                    : l.t('inbox.compose.ask')),
              ),
            ],
          ),
        if (_showSaved)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Semantics(
              liveRegion: true,
              child: Text(l.t('inbox.compose.saved'),
                  key: const Key('compose-saved')),
            ),
          ),
        if (c.sendApproval case final approval?) ...[
          Text(l.t('inbox.compose.locked'), style: theme.textTheme.bodySmall),
          EmailApprovalCard(
            key: const Key('compose-send-approval'),
            approval: approval,
            enabled: !busy,
            onApprove: () => c.approve(approval),
          ),
        ],
        if (c.sent)
          Semantics(
            liveRegion: true,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l.t('inboxSent'),
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                if (c.result case final result? when result.isNotEmpty)
                  Text(result),
                Text(l.t('inbox.compose.sentText')),
              ],
            ),
          ),
      ],
    );
  }

  Widget _address(TextEditingController controller, String key, String label,
          String? error, bool enabled, String? helper) =>
      TextField(
        key: Key(key),
        controller: controller,
        enabled: enabled,
        keyboardType: TextInputType.emailAddress,
        autocorrect: false,
        textInputAction: TextInputAction.next,
        textDirection: TextDirection.ltr,
        decoration: InputDecoration(
          labelText: label,
          helperText: helper,
          helperMaxLines: 3,
          errorText: error,
          errorMaxLines: 4,
        ),
      );
}
