import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../owner_desk/desk_widgets.dart';
import 'action_browser_data.dart';

/// The action browser's saved logins and cards on a phone
/// (docs/action-browser.md#on-the-phone), for workspace owners: the handle,
/// the site and a masked hint of each, add, change and delete. A password
/// or card number is typed into a hidden field, sent once, and forgotten:
/// it is never stored on the phone and never shown back. The card security
/// code is never saved at all.
class CredentialsScreen extends StatefulWidget {
  const CredentialsScreen({required this.controller, super.key});
  final CredentialsController controller;

  @override
  State<CredentialsScreen> createState() => _CredentialsScreenState();
}

class _CredentialsScreenState extends State<CredentialsScreen> {
  CredentialsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  Future<void> _edit(BuildContext context,
      {String? kind, BrowserCredential? existing}) async {
    await showDialog<void>(
        context: context,
        builder: (_) => _CredentialForm(
            controller: c, kind: existing?.kind ?? kind!, existing: existing));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          final p = context.palette;
          final ready = c.phase == CredentialsPhase.ready;
          return Scaffold(
            appBar: AppBar(title: Text(l.t('ab.vaultTitle')), actions: [
              IconButton(
                  key: const Key('cred-refresh'),
                  tooltip: l.t('od.refresh'),
                  onPressed: c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.ab.title'),
                  title: l.t('ab.vaultTitle'),
                  subtitle: l.t('ab.vaultIntro')),
              if (c.noteRaw != null)
                DeskMessage(text: c.noteRaw!, failed: true)
              else if (c.noteKey != null)
                DeskMessage(
                    key: const Key('cred-note'),
                    text: l.f(c.noteKey!, {'handle': c.noteHandle ?? ''})),
              ...switch (c.phase) {
                CredentialsPhase.loading => [
                    DeskLoading(label: l.t('ab.loading'))
                  ],
                CredentialsPhase.denied => [
                    AnumSurface(
                        child: AnumEmpty(
                            key: const Key('cred-denied'),
                            icon: Icons.lock_outline,
                            title: l.t('ab.vaultTitle'),
                            message: l.t('ab.ownerOnly')))
                  ],
                CredentialsPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                            key: const Key('cred-error'),
                            icon: Icons.error_outline,
                            title: l.t('od.loadFailedTitle'),
                            message:
                                l.f('ab.loadFailed', {'error': c.error ?? '-'}),
                            action: l.t('ab.retry'),
                            onAction: c.load))
                  ],
                CredentialsPhase.ready when c.items.isEmpty => [
                    AnumSurface(
                        child: AnumEmpty(
                            key: const Key('cred-empty'),
                            icon: Icons.key_outlined,
                            title: l.t('ab.emptyTitle'),
                            message: l.t('ab.emptyText')))
                  ],
                CredentialsPhase.ready => [
                    for (final item in c.items) _row(context, item)
                  ],
              },
              if (ready)
                Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      FilledButton.icon(
                          key: const Key('cred-add-login'),
                          onPressed: c.busy != null
                              ? null
                              : () => _edit(context, kind: 'login'),
                          icon: const Icon(Icons.add),
                          label: Text(l.t('ab.addLogin'))),
                      OutlinedButton.icon(
                          key: const Key('cred-add-card'),
                          onPressed: c.busy != null
                              ? null
                              : () => _edit(context, kind: 'card'),
                          icon: const Icon(Icons.add_card),
                          label: Text(l.t('ab.addCard'))),
                    ]),
              if (ready)
                Text(l.t('ab.cardNoCode'),
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            ]),
          );
        },
      );

  Widget _row(BuildContext context, BrowserCredential item) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: Key('cred-${item.id}'),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(item.isLogin ? Icons.key_outlined : Icons.credit_card,
            color: p.muted),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Wrap(
                spacing: AnumSpacing.xs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(item.handle,
                      textDirection: TextDirection.ltr,
                      style: TextStyle(
                          color: p.text, fontWeight: FontWeight.w800)),
                  AnumPill(label: l.t(item.isLogin ? 'ab.login' : 'ab.card')),
                ]),
            Text(item.hint,
                textDirection: TextDirection.ltr,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            Text(deskWhen(context, item.updatedAt),
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  OutlinedButton(
                      key: Key('cred-edit-${item.id}'),
                      onPressed: c.busy != null
                          ? null
                          : () => _edit(context, existing: item),
                      child: Semantics(
                          label: '${l.t('ab.edit')} ${item.handle}',
                          excludeSemantics: true,
                          child: Text(l.t('ab.edit')))),
                  OutlinedButton(
                      key: Key('cred-delete-${item.id}'),
                      style: OutlinedButton.styleFrom(foregroundColor: p.stop),
                      onPressed: c.busy != null
                          ? null
                          : () async {
                              if (await deskConfirm(context,
                                  title: l.f('ab.confirmRemove',
                                      {'handle': item.handle}),
                                  text: item.hint,
                                  confirm: l.t('ab.remove'),
                                  cancel: l.t('ab.cancel'),
                                  danger: true,
                                  confirmKey:
                                      const Key('cred-delete-confirm'))) {
                                await c.delete(item);
                              }
                            },
                      child: Semantics(
                          label: '${l.t('ab.remove')} ${item.handle}',
                          excludeSemantics: true,
                          child: Text(l.t('ab.remove')))),
                ]),
          ]),
        ),
      ]),
    );
  }
}

/// Add or change one login or card. Secret fields are hidden, start empty
/// (empty keeps the saved value when changing) and are cleared after send.
class _CredentialForm extends StatefulWidget {
  const _CredentialForm(
      {required this.controller, required this.kind, this.existing});
  final CredentialsController controller;
  final String kind;
  final BrowserCredential? existing;

  @override
  State<_CredentialForm> createState() => _CredentialFormState();
}

class _CredentialFormState extends State<_CredentialForm> {
  late final _draft = CredentialDraft(
      kind: widget.kind,
      handle: widget.existing?.handle ?? '',
      site: widget.existing?.site ?? '');
  final _secret = TextEditingController();
  final _number = TextEditingController();
  String? _error;
  bool _saving = false;

  @override
  void dispose() {
    _secret.dispose();
    _number.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    _draft
      ..password = _secret.text
      ..number = _number.text;
    setState(() {
      _saving = true;
      _error = null;
    });
    final problem =
        await widget.controller.save(_draft, existing: widget.existing);
    // Whatever happened, the typed secrets leave the screen.
    _secret.clear();
    _number.clear();
    if (!mounted) return;
    if (problem == null) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      _saving = false;
      _error = problem;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final editing = widget.existing != null;
    final login = widget.kind == 'login';
    final keep = editing ? '••••' : null;
    Widget field(String key, String label,
            {String? initial,
            ValueChanged<String>? onChanged,
            TextEditingController? controller,
            bool hidden = false,
            String? help,
            String? hint,
            TextInputType? keyboard,
            int? maxLength}) =>
        Padding(
          padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
          child: TextFormField(
            key: Key('cred-field-$key'),
            initialValue: controller == null ? initial : null,
            controller: controller,
            onChanged: onChanged,
            obscureText: hidden,
            autocorrect: false,
            enableSuggestions: false,
            keyboardType: keyboard,
            maxLength: maxLength,
            textDirection: TextDirection.ltr,
            decoration: InputDecoration(
                labelText: label, helperText: help, hintText: hint),
          ),
        );
    return AlertDialog(
      title: Text(editing
          ? '${l.t('ab.edit')} ${widget.existing!.handle}'
          : l.t(login ? 'ab.addLogin' : 'ab.addCard')),
      content: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              field('handle', l.t('ab.handle'),
                  initial: _draft.handle,
                  maxLength: 40,
                  help: l.t('ab.handleHelp'),
                  onChanged: (v) => _draft.handle = v),
              if (login) ...[
                field('site', l.t('ab.site'),
                    initial: _draft.site,
                    maxLength: 300,
                    hint: 'example.com',
                    help: l.t('ab.siteHelp'),
                    onChanged: (v) => _draft.site = v),
                field('username', l.t('ab.username'),
                    hint: keep,
                    maxLength: 200,
                    onChanged: (v) => _draft.username = v),
                field('password', l.t('ab.password'),
                    controller: _secret,
                    hidden: true,
                    hint: keep,
                    maxLength: 200),
              ] else ...[
                field('name', l.t('ab.nameOnCard'),
                    hint: keep,
                    maxLength: 100,
                    onChanged: (v) => _draft.name = v),
                field('number', l.t('ab.cardNumber'),
                    controller: _number,
                    hidden: true,
                    hint: keep,
                    keyboard: TextInputType.number,
                    maxLength: 30),
                field('expiry', l.t('ab.expiry'),
                    hint: 'MM/YY',
                    maxLength: 5,
                    onChanged: (v) => _draft.expiry = v),
                Text(l.t('ab.cardNoCode'),
                    style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
              ],
              if (editing)
                Text(l.t('ab.keepHint'),
                    style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
              if (_error != null) ...[
                const SizedBox(height: AnumSpacing.sm),
                DeskMessage(
                    key: const Key('cred-form-error'),
                    text: _error!.startsWith('ab.') ? l.t(_error!) : _error!,
                    failed: true),
              ],
            ]),
      ),
      actions: [
        TextButton(
            onPressed: _saving ? null : () => Navigator.of(context).pop(),
            child: Text(l.t('ab.cancel'))),
        FilledButton(
            key: const Key('cred-save'),
            onPressed: _saving ? null : _save,
            child: Text(_saving ? l.t('ab.saving') : l.t('ab.save'))),
      ],
    );
  }
}
