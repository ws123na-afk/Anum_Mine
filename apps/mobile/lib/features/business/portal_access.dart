import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_models.dart' show configuredWebAppUrl;
import 'business_controller.dart';
import 'invoice_controller.dart';
import 'invoice_document.dart' show PillRow;
import 'invoice_models.dart';
import 'invoices.dart' show friendly;

/// Clients (accounts and their contacts) and the customer portal links of a
/// contact (docs/business-portal.md#giving-access). Members see who has access
/// and when each link was last used; owners create and turn off links. A link
/// is shown once, when it is created, and is a credential.

/// What a customer decided in their portal, on a proposal or a change order.
/// The owner's own approval is still what accepts it (docs/business-portal.md).
class PortalDecisionLine extends StatelessWidget {
  const PortalDecisionLine({required this.decision, super.key});
  final PortalDecisionSummary decision;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = decision;
    return Container(
      key: const Key('portal-decision'),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AnumRadius.md),
        border: Border.all(
            color: d.approved ? context.palette.ok : context.palette.warn),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            l.f('biz.portal.decision.${d.decision}', {
              'name': d.signerName.isEmpty ? '-' : d.signerName,
              'date': dayOfDate(d.decidedAt),
            }),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          Text(d.current
              ? l.t('biz.portal.decision.current')
              : l.t('biz.portal.decision.old')),
          if (d.note.isNotEmpty)
            Text(l.f('biz.portal.decision.note', {'note': d.note})),
          Text(l.t('biz.portal.decision.hint'),
              style: TextStyle(color: context.palette.muted)),
        ],
      ),
    );
  }
}

/// The Clients section of Business: every client account, searchable.
class ClientsView extends StatefulWidget {
  const ClientsView({required this.controller, this.webAppUrl, super.key});
  final InvoicesController controller;
  final String? webAppUrl;

  @override
  State<ClientsView> createState() => _ClientsViewState();
}

class _ClientsViewState extends State<ClientsView> {
  final search = TextEditingController();

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final list = c.clients;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('biz.clients.intro'),
                style: TextStyle(color: context.palette.muted)),
            const SizedBox(height: 12),
            TextField(
              key: const Key('clients-search'),
              controller: search,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                labelText: l.t('biz.clients.search'),
                prefixIcon: const Icon(Icons.search),
              ),
              onSubmitted: c.searchClients,
            ),
            const SizedBox(height: 16),
            ...switch (list.state) {
              BusinessLoad.idle || BusinessLoad.loading => [
                  AnumFeedback(
                      kind: AnumFeedbackKind.loading,
                      message: l.t('biz.loading')),
                ],
              BusinessLoad.denied => [
                  AnumFeedback(
                      kind: AnumFeedbackKind.permission,
                      message: list.error ?? l.t('biz.denied')),
                ],
              BusinessLoad.error => [
                  AnumFeedback(
                      kind: AnumFeedbackKind.error,
                      message: l.f('biz.failed', {'message': list.error ?? ''}),
                      onRetry: c.firstClients),
                ],
              BusinessLoad.ready when list.items.isEmpty => [
                  c.clientQuery == null
                      ? AnumEmpty(
                          title: l.t('biz.clients.empty'),
                          message: l.t('biz.clients.emptyText'),
                          icon: Icons.apartment_outlined)
                      : AnumFeedback(
                          kind: AnumFeedbackKind.empty,
                          message: l.t('biz.clients.noMatch')),
                ],
              BusinessLoad.ready => [
                  for (final a in list.items)
                    AnumSurface(
                      key: Key('client-${a.id}'),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => ClientScreen(
                            controller: c,
                            accountId: a.id,
                            webAppUrl: widget.webAppUrl,
                          ),
                        ),
                      ),
                      child: PillRow(
                        title: a.name,
                        subtitle: [a.city, a.country]
                            .where((e) => e.isNotEmpty)
                            .join(', '),
                        detail: l.f('biz.clients.counts', {
                          'contacts': a.contacts,
                          'leads': a.openLeads,
                        }),
                        icon: Icons.apartment_outlined,
                        pills: a.doNotContact
                            ? [
                                AnumPill(
                                    label: l.t('biz.leads.dnc'),
                                    tone: AnumTone.stop)
                              ]
                            : const [],
                      ),
                    ),
                  if (list.hasMore)
                    OutlinedButton(
                      key: const Key('clients-more'),
                      onPressed: list.loadingMore ? null : c.moreClients,
                      child: Text(l.f('biz.more',
                          {'shown': list.items.length, 'total': list.total})),
                    ),
                  if (list.error != null)
                    Text(list.error!,
                        style: TextStyle(color: context.palette.stop)),
                ],
            },
          ],
        );
      },
    );
  }
}

/// One client account: its contacts, each with a "Customer portal" block.
class ClientScreen extends StatefulWidget {
  const ClientScreen({
    required this.controller,
    required this.accountId,
    this.webAppUrl,
    super.key,
  });
  final InvoicesController controller;
  final String accountId;

  /// The web app's address, to build the full link; defaults to the build's
  /// `ANUM_WEB_APP_URL`.
  final String? webAppUrl;

  @override
  State<ClientScreen> createState() => _ClientScreenState();
}

class _ClientScreenState extends State<ClientScreen> {
  ClientDetail? detail;
  List<PortalAccess> access = const [];
  String? error, notice, accessError;
  bool busy = false;

  InvoicesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final next = await c.repository.account(widget.accountId);
      if (mounted) {
        setState(() {
          detail = next;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
      return;
    }
    await _loadAccess();
  }

  Future<void> _loadAccess() async {
    try {
      final next = await c.repository.portalAccess(widget.accountId);
      if (mounted) {
        setState(() {
          access = next;
          accessError = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => accessError = friendly(e));
    }
  }

  Future<void> _create(ClientContact contact) async {
    final l = context.anum;
    final choice = await showDialog<({int days, bool decide})>(
      context: context,
      builder: (_) => CreatePortalLinkDialog(contactName: contact.name),
    );
    if (choice == null) return;
    setState(() {
      busy = true;
      notice = null;
    });
    try {
      final created = await c.repository.createPortalAccess(
        accountId: widget.accountId,
        contactId: contact.id,
        expiresInDays: choice.days,
        canDecide: choice.decide,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (_) => PortalLinkDialog(
          created: created,
          webAppUrl: widget.webAppUrl ?? configuredWebAppUrl,
        ),
      );
      await _loadAccess();
    } catch (e) {
      if (mounted) {
        setState(
            () => notice = '${l.t('biz.portal.createFailed')} ${friendly(e)}');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _revoke(PortalAccess item) async {
    final l = context.anum;
    final ok = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: Text(l.t('biz.portal.revokeTitle')),
            content:
                Text(l.f('biz.portal.revokeBody', {'name': item.contactName})),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: Text(l.t('biz.cancel'))),
              FilledButton(
                  key: const Key('portal-revoke-confirm'),
                  onPressed: () => Navigator.pop(context, true),
                  child: Text(l.t('biz.portal.revoke'))),
            ],
          ),
        ) ??
        false;
    if (!ok) return;
    setState(() {
      busy = true;
      notice = null;
    });
    try {
      await c.repository.revokePortalAccess(item.id);
      await _loadAccess();
      if (mounted) setState(() => notice = l.t('biz.portal.revoked'));
    } catch (e) {
      if (mounted) setState(() => notice = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = detail;
    return Scaffold(
      appBar: AppBar(title: Text(d?.account.name ?? l.t('biz.clients.title'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (d == null && error == null)
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
          if (error != null)
            AnumFeedback(
                kind: AnumFeedbackKind.error, message: error!, onRetry: load),
          if (d != null) ...[
            AnumHeader(
              title: d.account.name,
              subtitle: [d.account.city, d.account.country]
                  .where((e) => e.isNotEmpty)
                  .join(', '),
            ),
            if (notice != null)
              Text(notice!,
                  key: const Key('portal-notice'),
                  style: TextStyle(color: context.palette.muted)),
            if (d.contacts.isEmpty)
              AnumEmpty(
                title: l.t('biz.clients.noContacts'),
                message: l.t('biz.clients.noContactsText'),
                icon: Icons.person_outline,
              ),
            for (final contact in d.contacts) _contact(context, d, contact),
          ],
        ],
      ),
    );
  }

  Widget _contact(BuildContext context, ClientDetail d, ClientContact contact) {
    final l = context.anum;
    final mine = [
      for (final a in access)
        if (a.contactId == contact.id) a
    ];
    final blocked = d.account.doNotContact || contact.doNotContact;
    return AnumSurface(
      key: Key('contact-${contact.id}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          PillRow(
            title: contact.name,
            subtitle: [
              if (contact.roleTitle.isNotEmpty) contact.roleTitle,
              if (contact.email != null) contact.email!,
            ].join(' · '),
            icon: Icons.person_outline,
            pills: contact.doNotContact
                ? [AnumPill(label: l.t('biz.leads.dnc'), tone: AnumTone.stop)]
                : const [],
          ),
          Text(l.t('biz.portal.title'),
              style: Theme.of(context).textTheme.titleSmall),
          if (accessError != null)
            Text(accessError!, style: TextStyle(color: context.palette.stop))
          else if (mine.isEmpty)
            Text(l.t('biz.portal.none'),
                style: TextStyle(color: context.palette.muted)),
          for (final a in mine)
            Padding(
              key: Key('portal-access-${a.id}'),
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Wrap(spacing: 8, runSpacing: 4, children: [
                    AnumPill(
                      label: a.working
                          ? l.t('biz.portal.working')
                          : a.active
                              ? l.t('biz.portal.expired')
                              : l.t('biz.portal.off'),
                      tone: a.working ? AnumTone.ok : AnumTone.neutral,
                    ),
                    AnumPill(
                        label: a.canDecide
                            ? l.t('biz.portal.canDecide')
                            : l.t('biz.portal.readOnly')),
                  ]),
                  Text(
                    l.f('biz.portal.dates', {
                      'expires': dayOfDate(a.expiresAt),
                      'used': a.lastUsedAt == null
                          ? l.t('biz.portal.neverUsed')
                          : dayOfDate(a.lastUsedAt),
                    }),
                    style: TextStyle(color: context.palette.muted),
                  ),
                  if (a.working && c.canManage)
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: TextButton(
                        key: Key('portal-revoke-${a.id}'),
                        onPressed: busy ? null : () => _revoke(a),
                        child: Text(l.t('biz.portal.revoke')),
                      ),
                    ),
                ],
              ),
            ),
          if (c.canManage)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                key: Key('portal-create-${contact.id}'),
                onPressed: busy || blocked ? null : () => _create(contact),
                icon: const Icon(Icons.link),
                label: Text(l.t('biz.portal.create')),
              ),
            )
          else
            Text(l.t('biz.portal.ownerOnly'),
                style: TextStyle(color: context.palette.muted)),
          if (blocked && c.canManage)
            Text(l.t('biz.portal.blocked'),
                style: TextStyle(color: context.palette.warn)),
        ],
      ),
    );
  }
}

/// Asks how long the link works and whether it may approve.
class CreatePortalLinkDialog extends StatefulWidget {
  const CreatePortalLinkDialog({required this.contactName, super.key});
  final String contactName;

  @override
  State<CreatePortalLinkDialog> createState() => _CreateLinkState();
}

class _CreateLinkState extends State<CreatePortalLinkDialog> {
  final days = TextEditingController(text: '90');
  bool decide = true;
  String? problem;

  @override
  void dispose() {
    days.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.f('biz.portal.createTitle', {'name': widget.contactName})),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('biz.portal.createBody')),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-days'),
              controller: days,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                  labelText: l.t('biz.portal.days'), errorText: problem),
            ),
            SwitchListTile(
              key: const Key('portal-decide'),
              contentPadding: EdgeInsets.zero,
              value: decide,
              onChanged: (v) => setState(() => decide = v),
              title: Text(l.t('biz.portal.canDecideSwitch')),
              subtitle: Text(l.t('biz.portal.canDecideHint')),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('portal-create-confirm'),
          onPressed: () {
            final n = int.tryParse(days.text.trim());
            if (n == null || n < 1 || n > 365) {
              setState(() => problem = l.t('biz.portal.daysInvalid'));
              return;
            }
            Navigator.pop(context, (days: n, decide: decide));
          },
          child: Text(l.t('biz.portal.create')),
        ),
      ],
    );
  }
}

/// The link, shown once. It is a credential: anyone who has it sees the
/// customer's offers and invoices until it expires or is turned off.
class PortalLinkDialog extends StatelessWidget {
  const PortalLinkDialog({required this.created, this.webAppUrl, super.key});
  final PortalAccessCreated created;
  final String? webAppUrl;

  /// The full link when the web address is known, else the path the owner
  /// adds the address to.
  String get link {
    final base = (webAppUrl ?? '').trim().replaceAll(RegExp(r'/+$'), '');
    return base.isEmpty ? created.portalPath : '$base${created.portalPath}';
  }

  Future<void> _copy(BuildContext context, String text, String done) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(done)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.portal.linkTitle')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
                l.f('biz.portal.linkFor', {'name': created.access.contactName}),
                style: const TextStyle(fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text(l.t('biz.portal.linkWarning'),
                key: const Key('portal-link-warning'),
                style: TextStyle(color: context.palette.warn)),
            const SizedBox(height: 12),
            SelectableText(link,
                key: const Key('portal-link'),
                style: const TextStyle(fontFamily: 'monospace')),
            if ((webAppUrl ?? '').isEmpty)
              Text(l.t('biz.portal.noWebAddress'),
                  style: TextStyle(color: context.palette.muted)),
          ],
        ),
      ),
      actions: [
        TextButton(
          key: const Key('portal-copy-code'),
          onPressed: () =>
              _copy(context, created.token, l.t('biz.portal.copiedCode')),
          child: Text(l.t('biz.portal.copyCode')),
        ),
        FilledButton.icon(
          key: const Key('portal-copy'),
          onPressed: () => _copy(context, link, l.t('biz.portal.copied')),
          icon: const Icon(Icons.copy_outlined),
          label: Text(l.t('biz.portal.copy')),
        ),
        TextButton(
          key: const Key('portal-link-done'),
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.portal.done')),
        ),
      ],
    );
  }
}
