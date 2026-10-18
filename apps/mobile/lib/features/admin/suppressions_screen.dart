import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'org_admin_models.dart';
import 'org_admin_repository.dart';
import 'org_admin_widgets.dart';

enum SuppressionsPhase { loading, ready, denied, error }

/// The contact suppression list (docs/privacy.md#contact-suppression):
/// owners and organization administrators search, add and remove entries,
/// 50 at a time. Everyone else gets the API's 403.
class SuppressionsController extends ChangeNotifier {
  SuppressionsController(this.repository);
  final OrgAdminRepository repository;
  static const pageSize = 50;

  SuppressionsPhase phase = SuppressionsPhase.loading;
  String? error;
  List<SuppressionEntry> items = const [];
  int total = 0;
  bool canExport = false;

  /// The search that produced [items].
  String query = '';
  String? channel;

  bool loadingMore = false;
  String? moreError;
  bool busy = false;

  /// The last add or remove, said once under the search.
  String? flash;

  bool get hasMore => items.length < total;
  bool get filtered => query.trim().isNotEmpty || channel != null;
  int _request = 0;

  /// Shows the entries matching [query] and [channel] from the first page.
  Future<void> search(String query, String? channel) {
    this.query = query;
    this.channel = channel;
    return load();
  }

  Future<void> load() async {
    final id = ++_request;
    phase = SuppressionsPhase.loading;
    error = null;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.suppressions(
          query: query, channel: channel, limit: pageSize);
      if (id != _request) return;
      items = page.items;
      total = page.total;
      canExport = page.canExport;
      phase = SuppressionsPhase.ready;
    } on ApiException catch (e) {
      if (id != _request) return;
      phase = e.isPermissionDenied
          ? SuppressionsPhase.denied
          : SuppressionsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (id != _request) return;
      phase = SuppressionsPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  Future<void> clear() => search('', null);

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.suppressions(
          query: query,
          channel: channel,
          offset: items.length,
          limit: pageSize);
      items = [...items, ...page.items];
      total = page.total;
    } on ApiException catch (e) {
      moreError = e.readable;
    } on Object catch (e) {
      moreError = '$e';
    }
    loadingMore = false;
    notifyListeners();
  }

  /// Sends a checked draft; returns the API's refusal, or null when added.
  Future<String?> add(SuppressionDraft draft,
      {required String Function(String contact, bool created) said}) async {
    busy = true;
    notifyListeners();
    try {
      final result = await repository.addSuppression(draft);
      flash = said(result.entry.masked, result.created);
      busy = false;
      await load();
      return null;
    } on ApiException catch (e) {
      busy = false;
      notifyListeners();
      return e.readable;
    }
  }

  /// Removes [entry]; returns the API's refusal, or null when removed.
  Future<String?> remove(SuppressionEntry entry, String reason,
      {required String said}) async {
    busy = true;
    notifyListeners();
    try {
      await repository.removeSuppression(entry.id, reason.trim());
      flash = said;
      busy = false;
      await load();
      return null;
    } on ApiException catch (e) {
      busy = false;
      notifyListeners();
      return e.readable;
    }
  }
}

String suppressionChannelName(AnumLocalizations l, String channel) => l.t(
    'oa.supp.channel.${suppressionChannels.contains(channel) ? channel : 'all'}');

String suppressionPurposeName(AnumLocalizations l, String purpose) =>
    l.t(purpose == 'marketing'
        ? 'oa.supp.purpose.marketing'
        : 'oa.supp.purpose.all');

String suppressionSourceName(AnumLocalizations l, String source) =>
    const {'manual', 'whatsapp_keyword', 'email_unsubscribe'}.contains(source)
        ? l.t('oa.supp.source.$source')
        : source;

String suppressionProblemText(AnumLocalizations l, SuppressionProblem p) =>
    l.t(switch (p) {
      SuppressionProblem.contact => 'oa.supp.err.contact',
      SuppressionProblem.channel => 'oa.supp.err.channel',
      SuppressionProblem.reason => 'oa.supp.err.reason',
    });

class SuppressionsScreen extends StatefulWidget {
  const SuppressionsScreen({required this.controller, super.key});
  final SuppressionsController controller;
  @override
  State<SuppressionsScreen> createState() => _SuppressionsScreenState();
}

class _SuppressionsScreenState extends State<SuppressionsScreen> {
  SuppressionsController get c => widget.controller;
  final _search = TextEditingController();
  String? _channel;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _find() => c.search(_search.text, _channel);

  void _clear() {
    _search.clear();
    setState(() => _channel = null);
    c.clear();
  }

  Future<void> _add() async {
    final added = await Navigator.of(context).push<bool>(
        MaterialPageRoute(builder: (_) => AddSuppressionScreen(controller: c)));
    if (added == true && mounted) setState(() {});
  }

  Future<void> _remove(SuppressionEntry entry) => showDialog<void>(
      context: context,
      builder: (_) => _RemoveDialog(controller: c, entry: entry));

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('oa.supp.title')), actions: [
          IconButton(
              onPressed: c.phase == SuppressionsPhase.loading ? null : c.load,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh)),
        ]),
        floatingActionButton: c.phase == SuppressionsPhase.ready
            ? FloatingActionButton.extended(
                key: const Key('supp-add'),
                onPressed: c.busy ? null : _add,
                icon: const Icon(Icons.person_off_outlined),
                label: Text(l.t('oa.supp.add')))
            : null,
        body: switch (c.phase) {
          SuppressionsPhase.loading when c.items.isEmpty && !c.filtered =>
            AdminLoading(label: l.t('oa.supp.loading')),
          SuppressionsPhase.denied => AdminStateView(
              icon: Icons.lock_outline,
              title: l.t('oa.supp.deniedTitle'),
              message: l.t('oa.supp.deniedText'),
              detail: l.f('oa.apiSaid', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          SuppressionsPhase.error => AdminStateView(
              icon: Icons.error_outline,
              title: l.t('oa.supp.errorTitle'),
              message: l.f('oa.errorHint', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          _ => _list(context, l),
        },
      ),
    );
  }

  Widget _list(BuildContext context, AnumLocalizations l) {
    final text = Theme.of(context).textTheme;
    return AnumPage(onRefresh: c.load, children: [
      Text(l.t('oa.supp.intro'), style: text.bodyMedium),
      Text(l.t('oa.supp.storage'), style: text.bodySmall),
      if (c.flash != null) AdminFlash(text: c.flash!),
      AnumSurface(
        child: Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.sm,
          crossAxisAlignment: WrapCrossAlignment.end,
          children: [
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: TextField(
                key: const Key('supp-search'),
                controller: _search,
                maxLength: 320,
                textInputAction: TextInputAction.search,
                onSubmitted: (_) => _find(),
                decoration: InputDecoration(
                    labelText: l.t('oa.supp.search'),
                    hintText: l.t('oa.supp.searchHint'),
                    counterText: '',
                    prefixIcon: const Icon(Icons.search)),
              ),
            ),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 260),
              child: DropdownButtonFormField<String?>(
                key: const Key('supp-channel-filter'),
                initialValue: _channel,
                isExpanded: true,
                decoration:
                    InputDecoration(labelText: l.t('oa.supp.field.channel')),
                items: [
                  DropdownMenuItem(
                      value: null, child: Text(l.t('oa.supp.anyChannel'))),
                  for (final channel in suppressionChannels)
                    DropdownMenuItem(
                        value: channel,
                        child: Text(suppressionChannelName(l, channel))),
                ],
                onChanged: (value) => setState(() => _channel = value),
              ),
            ),
            FilledButton.tonalIcon(
                onPressed: c.phase == SuppressionsPhase.loading ? null : _find,
                icon: const Icon(Icons.search),
                label: Text(l.t('oa.supp.find'))),
            if (c.filtered)
              TextButton.icon(
                  onPressed: _clear,
                  icon: const Icon(Icons.restart_alt),
                  label: Text(l.t('oa.supp.clear'))),
          ],
        ),
      ),
      if (c.phase == SuppressionsPhase.loading)
        AdminLoading(label: l.t('oa.supp.loading'))
      else ...[
        Text(
            l.f(c.filtered ? 'oa.supp.countFiltered' : 'oa.supp.count',
                {'count': c.total}),
            style: text.titleSmall),
        if (c.items.isEmpty)
          c.filtered
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: l.t('oa.supp.noMatchTitle'),
                  message: l.t('oa.supp.noMatchText'),
                  action: l.t('oa.supp.clear'),
                  onAction: _clear)
              : AnumEmpty(
                  icon: Icons.notifications_off_outlined,
                  title: l.t('oa.supp.emptyTitle'),
                  message: l.t('oa.supp.emptyText'),
                  action: l.t('oa.supp.add'),
                  onAction: _add)
        else
          AnumSurface(
            child: Column(children: [
              for (final entry in c.items) _row(context, l, entry),
            ]),
          ),
        if (c.moreError != null)
          AdminFlash(
              text: l.f('oa.errorHint', {'message': c.moreError!}),
              error: true),
        if (c.hasMore)
          Center(
            child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.more,
                child: Text(c.loadingMore
                    ? l.t('oa.supp.loading')
                    : l.t('oa.supp.more'))),
          ),
        Text(l.t(c.canExport ? 'oa.supp.exportWeb' : 'oa.supp.exportOwners'),
            style: text.bodySmall),
      ],
      // Room for the floating button over the last row.
      const SizedBox(height: AnumSpacing.huge),
    ]);
  }

  Widget _row(BuildContext context, AnumLocalizations l, SuppressionEntry e) {
    final by = e.createdBy == suppressionSystemActor
        ? l.t('oa.supp.bySystem')
        : l.f('oa.supp.by', {'who': e.createdBy});
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            // Numbers and addresses read left to right in Arabic too.
            Text(e.masked,
                textDirection: TextDirection.ltr,
                style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: AnumSpacing.xxs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                children: [
                  AnumPill(label: suppressionChannelName(l, e.channel)),
                  AnumPill(
                      label: suppressionPurposeName(l, e.purpose),
                      tone: e.purpose == 'all'
                          ? AnumTone.warn
                          : AnumTone.neutral),
                  AnumPill(
                      label: suppressionSourceName(l, e.source),
                      tone: AnumTone.info),
                ]),
            const SizedBox(height: AnumSpacing.xxs),
            Text('$by · ${adminDate(context, e.createdAt)}',
                style: Theme.of(context).textTheme.bodySmall),
            if (e.reason.isNotEmpty)
              Text(l.f('oa.supp.reasonLine', {'reason': e.reason}),
                  style: Theme.of(context).textTheme.bodySmall),
          ]),
        ),
        IconButton(
          tooltip: l.f('oa.supp.removeLabel', {'contact': e.masked}),
          onPressed: c.busy ? null : () => _remove(e),
          icon: const Icon(Icons.delete_outline),
        ),
      ]),
    );
  }
}

class _RemoveDialog extends StatefulWidget {
  const _RemoveDialog({required this.controller, required this.entry});
  final SuppressionsController controller;
  final SuppressionEntry entry;
  @override
  State<_RemoveDialog> createState() => _RemoveDialogState();
}

class _RemoveDialogState extends State<_RemoveDialog> {
  final _reason = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  Future<void> _confirm() async {
    final l = context.anum;
    final problem = reasonProblem(_reason.text);
    if (problem != null) {
      setState(() => _error = suppressionProblemText(l, problem));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final refused = await widget.controller.remove(widget.entry, _reason.text,
        said: l.f('oa.supp.removed', {'contact': widget.entry.masked}));
    if (!mounted) return;
    if (refused == null) {
      Navigator.of(context).pop();
    } else {
      setState(() {
        _busy = false;
        _error = refused;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.f('oa.supp.removeTitle', {'contact': widget.entry.masked})),
      scrollable: true,
      content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l.f('oa.supp.removeBody',
                {'channel': suppressionChannelName(l, widget.entry.channel)})),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('supp-remove-reason'),
              controller: _reason,
              maxLength: 300,
              autofocus: true,
              decoration: InputDecoration(
                  labelText: l.t('oa.supp.removeReason'),
                  errorText: _error,
                  errorMaxLines: 4),
            ),
          ]),
      actions: [
        TextButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(),
            child: Text(l.t('oa.cancel'))),
        FilledButton(
            key: const Key('supp-remove-confirm'),
            style: FilledButton.styleFrom(
                backgroundColor: Theme.of(context).colorScheme.error,
                foregroundColor: Theme.of(context).colorScheme.onError),
            onPressed: _busy ? null : _confirm,
            child: Text(l.t('oa.supp.remove'))),
      ],
    );
  }
}

/// The add form on its own page, so it fits a 320 px phone at 200% text.
class AddSuppressionScreen extends StatefulWidget {
  const AddSuppressionScreen({required this.controller, super.key});
  final SuppressionsController controller;
  @override
  State<AddSuppressionScreen> createState() => _AddSuppressionScreenState();
}

class _AddSuppressionScreenState extends State<AddSuppressionScreen> {
  final _contact = TextEditingController();
  final _reason = TextEditingController();
  String _channel = 'all';
  String _purpose = 'all';
  String? _error;

  @override
  void dispose() {
    _contact.dispose();
    _reason.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final l = context.anum;
    final draft = SuppressionDraft(
        recipient: _contact.text.trim(),
        channel: _channel,
        purpose: _purpose,
        reason: _reason.text.trim());
    final problem = suppressionProblem(draft);
    if (problem != null) {
      setState(() => _error = suppressionProblemText(l, problem));
      return;
    }
    setState(() => _error = null);
    final refused = await widget.controller.add(draft,
        said: (contact, created) => l.f(
            created ? 'oa.supp.added' : 'oa.supp.already',
            {'contact': contact}));
    if (!mounted) return;
    if (refused == null) {
      Navigator.of(context).pop(true);
    } else {
      setState(() => _error = refused);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final busy = widget.controller.busy;
        return Scaffold(
          appBar: AppBar(title: Text(l.t('oa.supp.addTitle'))),
          body: AnumPage(children: [
            AnumSurface(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    TextField(
                      key: const Key('supp-contact'),
                      controller: _contact,
                      maxLength: 320,
                      keyboardType: TextInputType.emailAddress,
                      textDirection: TextDirection.ltr,
                      autocorrect: false,
                      decoration: InputDecoration(
                          labelText: l.t('oa.supp.field.contact'),
                          helperText: l.t('oa.supp.field.contactHint'),
                          helperMaxLines: 4),
                    ),
                    const SizedBox(height: AnumSpacing.sm),
                    DropdownButtonFormField<String>(
                      key: const Key('supp-channel'),
                      initialValue: _channel,
                      isExpanded: true,
                      decoration: InputDecoration(
                          labelText: l.t('oa.supp.field.channel')),
                      items: [
                        for (final channel in suppressionChannels)
                          DropdownMenuItem(
                              value: channel,
                              child: Text(suppressionChannelName(l, channel))),
                      ],
                      onChanged: busy
                          ? null
                          : (value) =>
                              setState(() => _channel = value ?? 'all'),
                    ),
                    const SizedBox(height: AnumSpacing.sm),
                    DropdownButtonFormField<String>(
                      key: const Key('supp-purpose'),
                      initialValue: _purpose,
                      isExpanded: true,
                      decoration: InputDecoration(
                          labelText: l.t('oa.supp.field.purpose')),
                      items: [
                        for (final purpose in suppressionPurposes)
                          DropdownMenuItem(
                              value: purpose,
                              child: Text(suppressionPurposeName(l, purpose))),
                      ],
                      onChanged: busy
                          ? null
                          : (value) =>
                              setState(() => _purpose = value ?? 'all'),
                    ),
                    const SizedBox(height: AnumSpacing.sm),
                    TextField(
                      key: const Key('supp-reason'),
                      controller: _reason,
                      maxLength: 300,
                      decoration: InputDecoration(
                          labelText: l.t('oa.supp.field.reason'),
                          helperText: l.t('oa.supp.field.reasonHint'),
                          helperMaxLines: 3),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      AdminFlash(text: _error!, error: true),
                    ],
                    const SizedBox(height: AnumSpacing.md),
                    FilledButton.icon(
                        key: const Key('supp-save'),
                        onPressed: busy ? null : _submit,
                        icon: busy
                            ? const SizedBox.square(
                                dimension: 18,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.add),
                        label: Text(l.t('oa.supp.save'))),
                  ]),
            ),
          ]),
        );
      },
    );
  }
}
