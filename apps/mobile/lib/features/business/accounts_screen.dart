import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'accounts_data.dart';
import 'business_controller.dart';
import 'business_screen.dart' show stateTone;
import 'desk_widgets.dart';

/// The accounts section of Business (docs/business.md): companies and the
/// people at them, with the consent basis that decides whether ANUM may ever
/// record a contact. Owners and members add and edit; owners delete.

class AccountsView extends StatefulWidget {
  const AccountsView({
    required this.controller,
    required this.canWrite,
    required this.canManage,
    super.key,
  });
  final AccountsController controller;
  final bool canWrite, canManage;

  @override
  State<AccountsView> createState() => _AccountsViewState();
}

class _AccountsViewState extends State<AccountsView> {
  final search = TextEditingController();
  AccountsController get c => widget.controller;

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  Future<void> openNew() async {
    final created = await Navigator.of(context).push<BusinessAccount>(
      MaterialPageRoute(builder: (_) => AccountFormScreen(controller: c)),
    );
    if (created != null && mounted) await open(created);
  }

  Future<void> open(BusinessAccount account) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AccountScreen(
            controller: c,
            accountId: account.id,
            canWrite: widget.canWrite,
            canManage: widget.canManage,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            key: const Key('acct-search'),
            controller: search,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              labelText: l.t('biz.acct.search'),
              prefixIcon: const Icon(Icons.search),
            ),
            onSubmitted: c.search,
          ),
          if (widget.canWrite)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: const Key('acct-new'),
                onPressed: openNew,
                icon: const Icon(Icons.add),
                label: Text(l.t('biz.acct.new')),
              ),
            ),
          DeskList<BusinessAccount>(
            state: c.state,
            items: c.items,
            total: c.total,
            loadingMore: c.loadingMore,
            error: c.error,
            onRetry: c.load,
            onMore: c.more,
            filtered: c.query != null,
            emptyTitle: l.t('biz.acct.empty'),
            emptyText: widget.canWrite
                ? l.t('biz.acct.emptyWrite')
                : l.t('biz.acct.emptyText'),
            emptyAction: widget.canWrite ? l.t('biz.acct.new') : null,
            onEmptyAction: widget.canWrite ? openNew : null,
            row: (item) => AnumRow(
              key: Key('acct-row-${item.id}'),
              title: item.name,
              subtitle: [
                item.industry,
                item.place,
              ].where((v) => v.isNotEmpty).join(' · '),
              detail: [
                l.f('biz.acct.counts', {
                  'contacts': item.contactCount,
                  'leads': item.openLeads,
                }),
                if (item.doNotContact) l.t('biz.leads.dnc'),
              ].join(' · '),
              icon: Icons.apartment_outlined,
              tone: item.doNotContact ? AnumTone.stop : null,
              trailing: const Icon(Icons.chevron_right),
              onTap: () => open(item),
            ),
          ),
        ],
      ),
    );
  }
}

class AccountScreen extends StatefulWidget {
  const AccountScreen({
    required this.controller,
    required this.accountId,
    required this.canWrite,
    required this.canManage,
    super.key,
  });
  final AccountsController controller;
  final String accountId;
  final bool canWrite, canManage;

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  BusinessAccount? account;
  BusinessLoad state = BusinessLoad.loading;
  String? error, actionError;
  bool busy = false;

  AccountsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      state = BusinessLoad.loading;
      error = null;
    });
    try {
      final found = await c.repository.account(widget.accountId);
      if (!mounted) return;
      setState(() {
        account = found;
        state = BusinessLoad.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        state = e is ApiException && e.isPermissionDenied
            ? BusinessLoad.denied
            : BusinessLoad.error;
        error = e is ApiException ? e.readable : '$e';
      });
    }
  }

  /// Runs one change; the server's refusal is shown, never swallowed.
  Future<void> run(Future<void> Function() change) async {
    setState(() {
      busy = true;
      actionError = null;
    });
    try {
      await change();
    } on ApiException catch (e) {
      if (mounted) setState(() => actionError = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> editAccount() async {
    final next = await Navigator.of(context).push<BusinessAccount>(
      MaterialPageRoute(
        builder: (_) => AccountFormScreen(controller: c, account: account),
      ),
    );
    if (next != null) await load();
  }

  Future<void> removeAccount() async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.acct.deleteTitle', {'name': account!.name}),
      message: l.t('biz.acct.deleteText'),
      action: l.t('biz.acct.delete'),
    );
    if (!ok) return;
    setState(() {
      busy = true;
      actionError = null;
    });
    try {
      await c.delete(account!.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          actionError = e.readable;
          busy = false;
        });
      }
    }
  }

  Future<void> toggleAccountDnc() async {
    final l = context.anum;
    final on = !account!.doNotContact;
    final reason = await askReason(
      context,
      title: on ? l.t('biz.acct.dncOn') : l.t('biz.acct.dncOff'),
      label: l.t('biz.lead.reason'),
    );
    if (reason == null) return;
    await run(() async {
      final next = await c.setDnc(account!.id, on, reason);
      if (mounted) setState(() => account = next);
    });
  }

  Future<void> editContact([AccountContact? contact]) async {
    final next = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => ContactFormScreen(
          controller: c,
          accountId: account!.id,
          contact: contact,
        ),
      ),
    );
    if (next == true) await load();
  }

  Future<void> toggleContactDnc(AccountContact contact) async {
    final l = context.anum;
    final on = !contact.doNotContact;
    final reason = await askReason(
      context,
      title: on ? l.t('biz.acct.dncOn') : l.t('biz.acct.dncOff'),
      label: l.t('biz.lead.reason'),
    );
    if (reason == null) return;
    await run(() async {
      await c.repository.contactDnc(contact.id, on, reason);
      await load();
    });
  }

  Future<void> removeContact(AccountContact contact) async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.acct.contact.deleteTitle', {'name': contact.name}),
      message: l.t('biz.acct.contact.deleteText'),
      action: l.t('biz.acct.contact.delete'),
    );
    if (!ok) return;
    await run(() async {
      await c.repository.deleteContact(contact.id);
      await load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final a = account;
    return Scaffold(
      appBar: AppBar(title: Text(a?.name ?? l.t('biz.tab.accounts'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (state == BusinessLoad.loading && a == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: l.t('biz.loading'),
            ),
          if (state == BusinessLoad.denied)
            AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: l.t('biz.denied'),
            ),
          if (state == BusinessLoad.error)
            AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: l.f('biz.failed', {'message': error ?? ''}),
              onRetry: load,
            ),
          if (a != null) ...[
            if (actionError != null)
              Semantics(
                liveRegion: true,
                child: Text(
                  actionError!,
                  key: const Key('account-error'),
                  style: TextStyle(color: context.palette.stop),
                ),
              ),
            _summary(context, a),
            AnumHeader(title: l.t('biz.acct.contacts')),
            if (widget.canWrite)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: const Key('contact-add'),
                  onPressed: busy ? null : () => editContact(),
                  icon: const Icon(Icons.person_add_alt),
                  label: Text(l.t('biz.acct.contact.add')),
                ),
              ),
            if (a.contacts.isEmpty)
              AnumEmpty(
                title: l.t('biz.acct.contact.empty'),
                message: l.t('biz.acct.contact.emptyText'),
              ),
            for (final contact in a.contacts) _contactCard(context, contact),
            if (a.leads.isNotEmpty) ...[
              AnumHeader(title: l.t('biz.acct.leads')),
              AnumSurface(
                child: Column(
                  children: [
                    for (final lead in a.leads)
                      AnumRow(
                        title: lead.title,
                        icon: Icons.person_search_outlined,
                        tone: stateTone(lead.state),
                        trailing: AnumPill(
                          label: l.t('biz.state.${lead.state}'),
                          tone: stateTone(lead.state),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }

  Widget _summary(BuildContext context, BusinessAccount a) {
    final l = context.anum;
    final muted = TextStyle(color: context.palette.muted);
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnumHeader(
            eyebrow: a.industry.isEmpty ? null : a.industry,
            title: a.name,
            subtitle: a.place.isEmpty ? null : a.place,
          ),
          if (a.website != null)
            Text(l.f('biz.acct.website', {'url': a.website!}), style: muted),
          if (a.notes.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(a.notes),
          ],
          if (a.doNotContact) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(
              l.f('biz.acct.dncNote', {'reason': a.dncReason}),
              key: const Key('account-dnc'),
              style: TextStyle(color: context.palette.stop),
            ),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Wrap(
            spacing: AnumSpacing.xs,
            children: [
              if (widget.canWrite)
                OutlinedButton.icon(
                  key: const Key('account-edit'),
                  onPressed: busy ? null : editAccount,
                  icon: const Icon(Icons.edit_outlined),
                  label: Text(l.t('biz.form.edit')),
                ),
              if (widget.canWrite && (!a.doNotContact || widget.canManage))
                OutlinedButton.icon(
                  key: const Key('account-dnc-toggle'),
                  onPressed: busy ? null : toggleAccountDnc,
                  icon: Icon(
                    a.doNotContact ? Icons.check_circle_outline : Icons.block,
                  ),
                  label: Text(
                    a.doNotContact
                        ? l.t('biz.acct.dncOff')
                        : l.t('biz.acct.dncOn'),
                  ),
                ),
              if (widget.canManage)
                OutlinedButton.icon(
                  key: const Key('account-delete'),
                  onPressed: busy ? null : removeAccount,
                  icon: const Icon(Icons.delete_outline),
                  label: Text(l.t('biz.acct.delete')),
                ),
            ],
          ),
          if (widget.canWrite && !widget.canManage)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Text(l.t('biz.acct.ownersDelete'), style: muted),
            ),
        ],
      ),
    );
  }

  Widget _contactCard(BuildContext context, AccountContact contact) {
    final l = context.anum;
    final details = [
      if (contact.roleTitle.isNotEmpty) contact.roleTitle,
      if (contact.email != null) contact.email!,
      if (contact.phone != null) contact.phone!,
    ].join(' · ');
    return AnumSurface(
      key: Key('contact-${contact.id}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnumRow(
            title: contact.name,
            subtitle: details.isEmpty ? null : details,
            detail:
                '${l.t('biz.acct.consent')}: ${l.t('biz.acct.consent.${contact.consentBasis}')}',
            icon: Icons.person_outline,
          ),
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
            child: AnumPill(
              label: contact.contactable
                  ? l.t('biz.acct.contactable')
                  : l.t('biz.acct.blocked'),
              tone: contact.contactable ? AnumTone.ok : AnumTone.warn,
            ),
          ),
          if (contact.blockReason != null)
            Text(
              contact.blockReason!,
              style: TextStyle(color: context.palette.muted),
            ),
          if (widget.canWrite)
            Wrap(
              spacing: AnumSpacing.xs,
              children: [
                TextButton(
                  key: Key('contact-${contact.id}-edit'),
                  onPressed: busy ? null : () => editContact(contact),
                  child: Text(l.t('biz.form.edit')),
                ),
                if (!contact.doNotContact || widget.canManage)
                  TextButton(
                    key: Key('contact-${contact.id}-dnc'),
                    onPressed: busy ? null : () => toggleContactDnc(contact),
                    child: Text(
                      contact.doNotContact
                          ? l.t('biz.acct.dncOff')
                          : l.t('biz.acct.dncOn'),
                    ),
                  ),
                if (widget.canManage)
                  TextButton(
                    key: Key('contact-${contact.id}-delete'),
                    onPressed: busy ? null : () => removeContact(contact),
                    child: Text(l.t('biz.acct.contact.delete')),
                  ),
              ],
            ),
        ],
      ),
    );
  }
}

class AccountFormScreen extends StatefulWidget {
  const AccountFormScreen({required this.controller, this.account, super.key});
  final AccountsController controller;

  /// Null creates an account; otherwise edits it.
  final BusinessAccount? account;

  @override
  State<AccountFormScreen> createState() => _AccountFormScreenState();
}

class _AccountFormScreenState extends State<AccountFormScreen> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.account?.name);
  late final website = TextEditingController(text: widget.account?.website);
  late final industry = TextEditingController(text: widget.account?.industry);
  late final country = TextEditingController(text: widget.account?.country);
  late final city = TextEditingController(text: widget.account?.city);
  late final notes = TextEditingController(text: widget.account?.notes);
  bool busy = false;
  String? error;

  @override
  void dispose() {
    for (final field in [name, website, industry, country, city, notes]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    final fields = <String, Object?>{
      'name': name.text.trim(),
      'website': website.text.trim().isEmpty ? null : website.text.trim(),
      'industry': industry.text.trim(),
      'country': country.text.trim(),
      'city': city.text.trim(),
      'notes': notes.text.trim(),
    };
    try {
      final saved = widget.account == null
          ? await widget.controller.create(fields)
          : await widget.controller.update(widget.account!.id, fields);
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return DeskForm(
      formKey: form,
      title:
          widget.account == null ? l.t('biz.acct.new') : l.t('biz.acct.edit'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        TextFormField(
          key: const Key('account-name'),
          controller: name,
          maxLength: 200,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.name')),
          validator: (v) => (v ?? '').trim().length < 2
              ? l.t('biz.acct.form.nameNeeded')
              : null,
        ),
        TextFormField(
          key: const Key('account-website'),
          controller: website,
          keyboardType: TextInputType.url,
          decoration: InputDecoration(
            labelText: l.t('biz.acct.form.website'),
            helperText: l.t('biz.acct.form.websiteHint'),
          ),
        ),
        TextFormField(
          key: const Key('account-industry'),
          controller: industry,
          maxLength: 120,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.industry')),
        ),
        TextFormField(
          key: const Key('account-country'),
          controller: country,
          maxLength: 80,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.country')),
        ),
        TextFormField(
          key: const Key('account-city'),
          controller: city,
          maxLength: 80,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.city')),
        ),
        TextFormField(
          key: const Key('account-notes'),
          controller: notes,
          minLines: 2,
          maxLines: 6,
          maxLength: 4000,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.notes')),
        ),
      ],
    );
  }
}

class ContactFormScreen extends StatefulWidget {
  const ContactFormScreen({
    required this.controller,
    required this.accountId,
    this.contact,
    super.key,
  });
  final AccountsController controller;
  final String accountId;

  /// Null adds a contact to the account; otherwise edits it.
  final AccountContact? contact;

  @override
  State<ContactFormScreen> createState() => _ContactFormScreenState();
}

class _ContactFormScreenState extends State<ContactFormScreen> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.contact?.name);
  late final role = TextEditingController(text: widget.contact?.roleTitle);
  late final email = TextEditingController(text: widget.contact?.email);
  late final phone = TextEditingController(text: widget.contact?.phone);
  late final profile = TextEditingController(text: widget.contact?.profileUrl);
  late final consentNote = TextEditingController(
    text: widget.contact?.consentNote,
  );
  late String consent = widget.contact?.consentBasis ?? 'unknown';
  bool busy = false;
  String? error;

  @override
  void dispose() {
    for (final field in [name, role, email, phone, profile, consentNote]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    String? text(TextEditingController field) =>
        field.text.trim().isEmpty ? null : field.text.trim();
    final fields = <String, Object?>{
      'name': name.text.trim(),
      'role_title': role.text.trim(),
      'email': text(email),
      'phone': text(phone),
      'profile_url': text(profile),
      'consent_basis': consent,
      'consent_note': consentNote.text.trim(),
    };
    try {
      if (widget.contact == null) {
        await widget.controller.addContact(widget.accountId, fields);
      } else {
        await widget.controller.repository.updateContact(
          widget.contact!.id,
          fields,
        );
      }
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return DeskForm(
      formKey: form,
      title: widget.contact == null
          ? l.t('biz.acct.contact.add')
          : l.t('biz.acct.contact.edit'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        TextFormField(
          key: const Key('contact-name'),
          controller: name,
          maxLength: 160,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.name')),
          validator: (v) =>
              (v ?? '').trim().isEmpty ? l.t('biz.acct.form.nameNeeded') : null,
        ),
        TextFormField(
          key: const Key('contact-role'),
          controller: role,
          maxLength: 160,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.role')),
        ),
        TextFormField(
          key: const Key('contact-email'),
          controller: email,
          keyboardType: TextInputType.emailAddress,
          decoration: InputDecoration(labelText: l.t('biz.support.form.email')),
        ),
        TextFormField(
          key: const Key('contact-phone'),
          controller: phone,
          keyboardType: TextInputType.phone,
          decoration: InputDecoration(labelText: l.t('biz.support.form.phone')),
        ),
        TextFormField(
          key: const Key('contact-profile'),
          controller: profile,
          keyboardType: TextInputType.url,
          decoration: InputDecoration(labelText: l.t('biz.acct.form.profile')),
        ),
        deskChoice(
          key: const Key('contact-consent'),
          label: l.t('biz.acct.consent'),
          value: consent,
          values: consentBases,
          text: (v) => l.t('biz.acct.consent.$v'),
          onChanged: (v) => setState(() => consent = v),
        ),
        Padding(
          padding: const EdgeInsets.only(top: AnumSpacing.xs),
          child: Text(
            l.t('biz.acct.consentHint'),
            style: TextStyle(color: context.palette.muted),
          ),
        ),
        TextFormField(
          key: const Key('contact-consent-note'),
          controller: consentNote,
          maxLength: 500,
          decoration: InputDecoration(
            labelText: l.t('biz.acct.form.consentNote'),
          ),
        ),
      ],
    );
  }
}
