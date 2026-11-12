import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'vault_controller.dart';
import 'vault_data.dart';
import 'vault_strings.dart';
import 'vault_transfer.dart';

String categoryLabel(BuildContext context, VaultMeta? meta, String id) {
  final s = VaultStrings.of(context);
  final custom = meta?.categories.where((c) => c.id == id).firstOrNull;
  if (custom != null && !custom.builtIn && custom.label != null) {
    return custom.label!;
  }
  final key = 'cat.$id';
  final text = s.t(key);
  return text == key ? s.t('cat.unknown') : text;
}

/// "passport (version 1).pdf": an older version's file name, so a saved
/// copy never overwrites the current one.
String versionName(String name, int version) {
  final dot = name.lastIndexOf('.');
  return dot > 0
      ? '${name.substring(0, dot)} (version $version)${name.substring(dot)}'
      : '$name (version $version)';
}

/// The expiry badge: never by colour alone, the words say it.
({String text, AnumTone tone}) expiryBadge(
    BuildContext context, VaultDocument d) {
  final s = VaultStrings.of(context);
  final days = d.daysLeft ?? 0;
  return switch (d.expiryState) {
    'expired' => (
        text: s.t('state.expired', {'days': s.days(days.abs())}),
        tone: AnumTone.stop
      ),
    'soon' => (
        text: days == 0
            ? s.t('state.today')
            : s.t('state.soon', {'days': s.days(days)}),
        tone: AnumTone.warn
      ),
    'valid' => (text: s.t('state.valid'), tone: AnumTone.ok),
    _ => (text: s.t('state.none'), tone: AnumTone.neutral),
  };
}

/// The Document vault on the phone (docs/documents.md#mobile): search and
/// filters, documents with masked numbers and expiry badges, adding from
/// the phone's files, versions, links and the trash. From 840 px the list
/// and the selected document sit side by side.
class VaultScreen extends StatefulWidget {
  const VaultScreen(
      {required this.controller, this.picker = pickVaultFile, super.key});
  final VaultController controller;
  final VaultFilePicker picker;

  @override
  State<VaultScreen> createState() => _VaultScreenState();
}

class _VaultScreenState extends State<VaultScreen> {
  String? _selected;
  VaultController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.phase == RecordsPhase.loading) c.load();
  }

  Future<void> _add() async {
    final meta = c.meta;
    if (meta == null) return;
    final added = await showRecordsSheet<VaultDocument>(
      context,
      (_) =>
          _AddDocumentSheet(controller: c, meta: meta, picker: widget.picker),
    );
    if (added == null || !mounted) return;
    await c.changed(added);
    if (!mounted) return;
    recordsToast(context, VaultStrings.of(context).t('added'));
    _open(added);
  }

  void _open(VaultDocument document) {
    if (recordsWide(context)) {
      setState(() => _selected = document.id);
      return;
    }
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => Scaffold(
        appBar: AppBar(title: Text(document.title)),
        body: VaultDocumentHost(
            controller: c, documentId: document.id, picker: widget.picker),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final s = VaultStrings.of(context);
    return ListenableBuilder(
      listenable: Listenable.merge([c, c.documents]),
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(s.t('title'))),
        floatingActionButton: c.meta?.canWrite ?? false
            ? FloatingActionButton.extended(
                key: const Key('vault-add'),
                onPressed: _add,
                icon: const Icon(Icons.upload_file),
                label: Text(s.t('add')),
              )
            : null,
        body: LayoutBuilder(builder: (context, box) {
          final list = _VaultList(
              controller: c, selected: _selected, onOpen: _open, onAdd: _add);
          if (box.maxWidth < AnumBreakpoints.expanded) return list;
          final selected = _selected;
          return Row(children: [
            SizedBox(width: 420, child: list),
            const VerticalDivider(width: 1),
            Expanded(
              child: selected == null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(AnumSpacing.lg),
                        child:
                            Text(s.t('subtitle'), textAlign: TextAlign.center),
                      ),
                    )
                  : VaultDocumentHost(
                      key: ValueKey(selected),
                      controller: c,
                      documentId: selected,
                      picker: widget.picker),
            ),
          ]);
        }),
      ),
    );
  }
}

class _VaultList extends StatelessWidget {
  const _VaultList({
    required this.controller,
    required this.selected,
    required this.onOpen,
    required this.onAdd,
  });
  final VaultController controller;
  final String? selected;
  final ValueChanged<VaultDocument> onOpen;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final s = VaultStrings.of(context);
    final k = KitStrings.of(context);
    final c = controller;
    final meta = c.meta;
    final list = c.documents;
    final children = <Widget>[
      AnumHeader(title: s.t('title'), subtitle: s.t('subtitle')),
    ];
    if (c.phase != RecordsPhase.ready || meta == null) {
      children.add(RecordsStateView(
          phase: c.phase,
          failure: c.failure,
          deniedHint: s.t('denied'),
          onRetry: c.load));
      return AnumPage(onRefresh: c.load, children: children);
    }
    children.addAll([
      Text(
          s.t('summary', {
            'active': meta.active,
            'expiring': meta.expiring,
            'expired': meta.expired
          }),
          key: const Key('vault-summary'),
          style: TextStyle(color: context.palette.muted)),
      RecordsSearchField(
          initial: list.query,
          hint: s.t('search'),
          onSearch: (q) => list.load(query: q)),
      // Keyed by the filter, so clearing the filters resets the field too.
      KeyedSubtree(
        key: ValueKey('category-${c.category}'),
        child: DropdownButtonFormField<String>(
          key: const Key('vault-category'),
          initialValue: c.category,
          isExpanded: true,
          decoration: InputDecoration(labelText: s.t('category')),
          items: [
            DropdownMenuItem(value: '', child: Text(s.t('allCategories'))),
            for (final category in meta.categories)
              DropdownMenuItem(
                  value: category.id,
                  child: Text(categoryLabel(context, meta, category.id),
                      overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (value) => c.filterBy(category: value ?? ''),
        ),
      ),
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        for (final e in ['', 'soon', 'expired'])
          ChoiceChip(
            key: Key('expiry-${e.isEmpty ? 'all' : e}'),
            label: Text(s.t('expiry.${e.isEmpty ? 'all' : e}')),
            selected: !c.trash && c.expiry == e,
            onSelected: (_) => c.filterBy(expiry: e, trash: false),
          ),
        FilterChip(
          key: const Key('vault-trash'),
          label: Text('${s.t('trash')} (${meta.trash})'),
          selected: c.trash,
          onSelected: (on) => c.filterBy(trash: on, expiry: ''),
        ),
      ]),
    ]);
    if (list.phase != RecordsPhase.ready) {
      children.add(RecordsStateView(
          phase: list.phase,
          failure: list.failure,
          deniedHint: s.t('denied'),
          onRetry: list.load));
    } else if (list.items.isEmpty) {
      if (c.trash) {
        children.add(AnumEmpty(
            title: s.t('trashEmpty'),
            message: s.t('trashEmptyText'),
            icon: Icons.delete_outline,
            action: k.t('clearFilters'),
            onAction: c.clearFilters));
      } else if (list.narrowed) {
        children.add(AnumEmpty(
            title: k.t('noMatch'),
            message: k.t('noMatchText'),
            icon: Icons.search_off,
            action: k.t('clearFilters'),
            onAction: c.clearFilters));
      } else {
        children.add(AnumEmpty(
            key: const Key('vault-empty'),
            title: s.t('empty'),
            message: s.t('emptyText'),
            icon: Icons.folder_special_outlined,
            action: meta.canWrite ? s.t('add') : null,
            onAction: meta.canWrite ? onAdd : null));
      }
    } else {
      children.add(AnumSurface(
        child: Column(children: [
          for (final d in list.items)
            Semantics(
              selected: d.id == selected,
              button: true,
              child: AnumRow(
                key: Key('doc-${d.id}'),
                icon: d.accessClass == 'private'
                    ? Icons.lock_outline
                    : Icons.folder_shared_outlined,
                tone: expiryBadge(context, d).tone == AnumTone.neutral
                    ? null
                    : expiryBadge(context, d).tone,
                title: d.title,
                subtitle: [
                  categoryLabel(context, meta, d.category),
                  s.t(d.accessClass == 'private' ? 'private' : 'workspace'),
                  if (d.numberMasked != null) d.numberMasked!,
                ].join(' · '),
                detail: d.inTrash
                    ? s.t(
                        'purgeOn', {'date': recordsDate(context, d.purgeAfter)})
                    : expiryBadge(context, d).text,
                trailing: const Icon(Icons.chevron_right),
                onTap: () => onOpen(d),
              ),
            ),
        ]),
      ));
      children.add(RecordsMore(list: list));
    }
    children.add(const RecordsFabSpace());
    return AnumPage(onRefresh: c.load, children: children);
  }
}

class _AddDocumentSheet extends StatefulWidget {
  const _AddDocumentSheet(
      {required this.controller, required this.meta, required this.picker});
  final VaultController controller;
  final VaultMeta meta;
  final VaultFilePicker picker;

  @override
  State<_AddDocumentSheet> createState() => _AddDocumentSheetState();
}

class _AddDocumentSheetState extends State<_AddDocumentSheet> {
  final _title = TextEditingController();
  String _category = 'other';
  String _access = 'private';
  (String, String, int?)? _file;
  double? _progress;
  String? _error;
  bool _duplicate = false;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final s = VaultStrings.of(context);
    final picked = await widget.picker(widget.meta.acceptedExtensions);
    if (picked == null || !mounted) return;
    final size = picked.$3;
    final max = widget.meta.maxUploadBytes;
    setState(() {
      _duplicate = false;
      _error = size != null && max > 0 && size > max
          ? s.t('tooBig', {'size': s.size(size), 'max': s.size(max)})
          : null;
      _file = _error == null ? picked : null;
      if (_file != null && _title.text.trim().isEmpty) {
        final name = picked.$2;
        final dot = name.lastIndexOf('.');
        _title.text = dot > 0 ? name.substring(0, dot) : name;
      }
    });
  }

  Future<void> _upload({bool allowDuplicate = false}) async {
    final file = _file;
    if (file == null) return;
    setState(() {
      _progress = 0;
      _error = null;
    });
    try {
      final document = await widget.controller.repository.upload(
        file.$1,
        name: file.$2,
        title: _title.text.trim().isEmpty ? file.$2 : _title.text.trim(),
        category: _category,
        accessClass: _access,
        allowDuplicate: allowDuplicate,
        onProgress: (sent, total) {
          if (mounted && total > 0) setState(() => _progress = sent / total);
        },
      );
      if (mounted) Navigator.of(context).pop(document);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _progress = null;
        _duplicate = !allowDuplicate && isDuplicate(error);
        _error = _duplicate
            ? VaultStrings.of(context).t('duplicate')
            : RecordsFailure.from(error).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = VaultStrings.of(context);
    final k = KitStrings.of(context);
    final busy = _progress != null;
    final types = widget.meta.acceptedExtensions.take(8).join(', ');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Semantics(
            header: true,
            child: Text(s.t('addTitle'),
                style: Theme.of(context).textTheme.titleLarge)),
        const SizedBox(height: AnumSpacing.xs),
        Text(
            s.t('addHelp', {
              'types': types.isEmpty ? '-' : types,
              'size': s.size(widget.meta.maxUploadBytes),
            }),
            style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.sm),
        OutlinedButton.icon(
          key: const Key('vault-pick'),
          onPressed: busy ? null : _pick,
          icon: const Icon(Icons.attach_file),
          label: Text(_file == null
              ? s.t('chooseFile')
              : s.t('chosen', {'name': _file!.$2})),
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('vault-title'),
          controller: _title,
          maxLength: 200,
          decoration: InputDecoration(labelText: s.t('docTitle')),
        ),
        DropdownButtonFormField<String>(
          key: const Key('vault-add-category'),
          initialValue: _category,
          isExpanded: true,
          decoration: InputDecoration(labelText: s.t('category')),
          items: [
            for (final category in widget.meta.categories)
              DropdownMenuItem(
                  value: category.id,
                  child: Text(categoryLabel(context, widget.meta, category.id),
                      overflow: TextOverflow.ellipsis)),
          ],
          onChanged:
              busy ? null : (v) => setState(() => _category = v ?? 'other'),
        ),
        const SizedBox(height: AnumSpacing.sm),
        SegmentedButton<String>(
          segments: [
            ButtonSegment(value: 'private', label: Text(s.t('access.private'))),
            ButtonSegment(
                value: 'workspace', label: Text(s.t('access.workspace'))),
          ],
          selected: {_access},
          onSelectionChanged:
              busy ? null : (v) => setState(() => _access = v.first),
        ),
        if (busy) ...[
          const SizedBox(height: AnumSpacing.sm),
          LinearProgressIndicator(value: _progress),
          Text(s.t('uploading', {'percent': ((_progress ?? 0) * 100).round()})),
        ],
        if (_error != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(_error!,
              key: const Key('vault-add-error'),
              style: TextStyle(color: context.palette.stop)),
        ],
        const SizedBox(height: AnumSpacing.md),
        Wrap(
          alignment: WrapAlignment.end,
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            TextButton(
                onPressed: busy ? null : () => Navigator.of(context).pop(),
                child: Text(k.t('cancel'))),
            if (_duplicate)
              OutlinedButton(
                key: const Key('vault-add-anyway'),
                onPressed: busy ? null : () => _upload(allowDuplicate: true),
                child: Text(s.t('addAnyway')),
              ),
            FilledButton(
              key: const Key('vault-upload'),
              onPressed: busy || _file == null ? null : _upload,
              child: Text(s.t('add')),
            ),
          ],
        ),
      ],
    );
  }
}

/// Owns one document's controller while it is shown.
class VaultDocumentHost extends StatefulWidget {
  const VaultDocumentHost({
    required this.controller,
    required this.documentId,
    this.picker = pickVaultFile,
    super.key,
  });
  final VaultController controller;
  final String documentId;
  final VaultFilePicker picker;

  @override
  State<VaultDocumentHost> createState() => _VaultDocumentHostState();
}

class _VaultDocumentHostState extends State<VaultDocumentHost> {
  late final VaultDocumentController _c = VaultDocumentController(
      widget.controller.repository, widget.documentId,
      onChanged: widget.controller.changed)
    ..load();
  double? _progress;

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action, String done) async {
    try {
      await action();
      if (mounted) recordsToast(context, done);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _reveal() async {
    final s = VaultStrings.of(context);
    try {
      final number = await _c.repository.revealNumber(widget.documentId);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(s.t('numberTitle')),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            SelectableText(number,
                key: const Key('vault-number'),
                textDirection: TextDirection.ltr,
                style: Theme.of(dialog).textTheme.headlineSmall),
            const SizedBox(height: AnumSpacing.xs),
            Text(s.t('numberHelp')),
          ]),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(dialog),
                child: Text(KitStrings.of(dialog).t('close'))),
          ],
        ),
      );
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _newVersion() async {
    final s = VaultStrings.of(context);
    final meta = widget.controller.meta;
    final picked = await widget.picker(meta?.acceptedExtensions ?? const []);
    if (picked == null || !mounted) return;
    setState(() => _progress = 0);
    await _run(
        () => _c.uploadVersion(picked.$1, onProgress: (sent, total) {
              if (mounted && total > 0) {
                setState(() => _progress = sent / total);
              }
            }),
        s.t('versionAdded'));
    if (mounted) setState(() => _progress = null);
  }

  Future<void> _addLink(List<String> types) async {
    final s = VaultStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('addLink'),
        intro: s.t('linkHelp'),
        submitLabel: s.t('addLink'),
        fields: [
          RecordsInput.choice('target_type', s.t('linkType'),
              initial: types.first,
              options: {for (final t in types) t: s.label('link', t)}),
          RecordsInput.text('target_id', s.t('linkTarget'),
              required: true, maxLength: 160),
          RecordsInput.text('label', s.t('linkLabel'), maxLength: 200),
        ],
        onSubmit: (v) => _c.addLink(v['target_type']! as String,
            v['target_id']! as String, (v['label'] as String?) ?? ''),
      ),
    );
    if (saved && mounted) recordsToast(context, s.t('linkAdded'));
  }

  Future<void> _trash() async {
    final s = VaultStrings.of(context);
    final k = KitStrings.of(context);
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        content: Text(s.t('deleteConfirm')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(k.t('cancel'))),
          FilledButton(
              key: const Key('confirm-delete'),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(s.t('delete'))),
        ],
      ),
    );
    if (sure == true && mounted) await _run(_c.trash, s.t('deleted'));
  }

  /// Payments and purchases a confirmed receipt may belong to
  /// (docs/documents.md#receipt-matches). The person picks one; the server
  /// checks it is still proposed.
  Widget _matches(BuildContext context, VaultStrings s) {
    final m = _c.matches;
    final muted = TextStyle(color: context.palette.muted);
    Widget row(VaultMatch item) {
      final percent = item.percent;
      final payment = item.kind == 'payment';
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumRow(
            key: Key('match-${item.targetId}'),
            icon:
                payment ? Icons.payments_outlined : Icons.shopping_bag_outlined,
            title: item.label.isEmpty
                ? s.t(payment ? 'matchPayment' : 'matchPurchase')
                : item.label,
            subtitle: [
              s.t('matchLine', {
                'currency': item.currency,
                'amount': item.amount,
                'date': recordsDate(context, item.on),
              }),
              if (percent != null)
                s.t(item.merchantAgrees == true ? 'matchAgrees' : 'matchSure',
                    {'percent': percent}),
            ].join(' · '),
          ),
          // The actions sit under the row so a phone never cuts them off.
          if (m!.canEdit)
            Wrap(spacing: 4, children: [
              TextButton(
                  key: Key('match-take-${item.targetId}'),
                  onPressed: () => _run(() => _c.acceptMatch(item),
                      s.t(payment ? 'matchLinked' : 'matchAttached')),
                  child: Text(s.t(
                      payment ? 'matchLinkPayment' : 'matchAttachPurchase'))),
              TextButton(
                  key: Key('match-dismiss-${item.targetId}'),
                  onPressed: () => _run(
                      () => _c.dismissMatch(item.kind, item.targetId),
                      s.t('matchDismissed')),
                  child: Text(s.t('matchDismiss'))),
            ]),
        ],
      );
    }

    return RecordsSection(
        key: const Key('vault-matches'),
        title: s.t('matches'),
        children: [
          if (_c.matchesError != null)
            Text(_c.matchesError!,
                style: TextStyle(color: context.palette.stop))
          else if (m == null)
            const LinearProgressIndicator()
          else if (m.reason == 'not_confirmed')
            Text(s.t('matchesNotConfirmed'), style: muted)
          else if (m.reason == null) ...[
            Text(s.t('matchesIntro'), style: muted),
            if (m.linkedPayments.isNotEmpty) Text(s.t('matchesLinkedPayment')),
            if (m.linkedPurchases.isNotEmpty)
              Text(s.t('matchesLinkedPurchase')),
            for (final item in [...m.payments, ...m.purchases]) row(item),
            if (!m.hasProposals && !m.linked)
              Text(s.t('matchesNone'), style: muted),
            if (m.paymentsUnavailable != null)
              Text(s.t('matchesNoFinance'), style: muted),
            if (m.purchasesUnavailable != null)
              Text(s.t('matchesNoPurchases'), style: muted),
            if (m.hasProposals && !m.canEdit)
              Text(s.t('readOnly'), style: muted),
            if (m.dismissed.isNotEmpty) ...[
              Text(s.t('matchesDismissed'),
                  style: Theme.of(context).textTheme.titleSmall),
              for (final d in m.dismissed)
                AnumRow(
                  key: Key('match-dismissed-${d.targetId}'),
                  icon: Icons.block_outlined,
                  title: d.label,
                  subtitle: s.t('matchDismissedBy', {'by': d.dismissedBy}),
                  trailing: m.canEdit
                      ? TextButton(
                          key: Key('match-restore-${d.targetId}'),
                          onPressed: () => _run(
                              () => _c.dismissMatch(d.kind, d.targetId,
                                  restore: true),
                              s.t('matchRestored')),
                          child: Text(s.t('matchRestore')))
                      : null,
                ),
            ],
          ],
        ]);
  }

  /// An insurance or registration document and its vehicle
  /// (docs/documents.md#vehicle-papers): the person links one, and the
  /// vehicle's renewal reminder follows the document's expiry.
  Widget _papers(BuildContext context, VaultStrings s) {
    final p = _c.papers;
    final muted = TextStyle(color: context.palette.muted);
    return RecordsSection(
        key: const Key('vault-papers'),
        title: s.t('papers'),
        children: [
          if (_c.papersError != null)
            Text(_c.papersError!, style: TextStyle(color: context.palette.stop))
          else if (p == null)
            const LinearProgressIndicator()
          else if (p.reason == 'no_expiry')
            Text(s.t('papersNoExpiry'), style: muted)
          else if (p.vehiclesUnavailable != null)
            Text(s.t('papersNoAccess'), style: muted)
          else if (p.reason == null) ...[
            Text(
                s.t('papersIntro',
                    {'date': recordsDate(context, p.expiryDate ?? '')}),
                style: muted),
            if (p.reminderFrom != null && p.reminderDays != null)
              Text(
                  s.t('papersReminder', {
                    'days': p.reminderDays!,
                    'date': recordsDate(context, p.reminderFrom!)
                  }),
                  style: muted),
            if (p.vehicles.isEmpty) Text(s.t('papersNone'), style: muted),
            for (final v in p.vehicles) ...[
              AnumRow(
                key: Key('papers-${v.vehicleId}'),
                icon: Icons.directions_car_outlined,
                title: v.label,
                subtitle: [
                  v.currentExpiry == null
                      ? s.t('papersCurrentNone')
                      : s.t('papersCurrent',
                          {'date': recordsDate(context, v.currentExpiry!)}),
                  if (v.matchesRegistration) s.t('papersMatches'),
                ].join(' · '),
              ),
              if (v.linked && !v.wouldChange)
                Text(s.t('papersLinked'),
                    style: TextStyle(color: context.palette.ok))
              else if (p.canEdit)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton(
                      key: Key('papers-link-${v.vehicleId}'),
                      onPressed: () => _run(
                          () => _c.confirmVehicle(v.vehicleId),
                          s.t('papersSaved')),
                      child: Text(s.t('papersLink'))),
                ),
            ],
            if (!p.canEdit) Text(s.t('readOnly'), style: muted),
          ],
        ]);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: _c,
        builder: (context, _) {
          final s = VaultStrings.of(context);
          final d = _c.document;
          if (_c.phase != RecordsPhase.ready || d == null) {
            return AnumPage(children: [
              RecordsStateView(
                  phase: _c.phase,
                  failure: _c.failure,
                  deniedHint: s.t('denied'),
                  onRetry: _c.load),
            ]);
          }
          final meta = widget.controller.meta;
          final badge = expiryBadge(context, d);
          final file = d.file;
          final infected = file?.scanStatus == 'infected';
          final editable = d.canEdit && !d.inTrash;
          final linkTypes = meta?.linkTypes.isNotEmpty ?? false
              ? meta!.linkTypes
              : const [
                  'vehicle',
                  'person',
                  'business',
                  'purchase',
                  'finance_transaction'
                ];
          return AnumPage(onRefresh: _c.load, children: [
            RecordsSection(title: d.title, children: [
              Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xs,
                  children: [
                    AnumPill(
                        key: const Key('vault-expiry'),
                        label: badge.text,
                        tone: badge.tone,
                        icon: Icons.event_outlined),
                    AnumPill(
                        label: s.t(d.accessClass == 'private'
                            ? 'private'
                            : 'workspace'),
                        icon: d.accessClass == 'private'
                            ? Icons.lock_outline
                            : Icons.groups_outlined),
                    AnumPill(label: categoryLabel(context, meta, d.category)),
                  ]),
              const SizedBox(height: AnumSpacing.xs),
              RecordsField(s.t('number'), d.numberMasked),
              if (d.numberMasked != null && d.isOwner && !d.inTrash)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton.icon(
                    key: const Key('vault-reveal'),
                    onPressed: _reveal,
                    icon: const Icon(Icons.visibility_outlined),
                    label: Text(s.t('showNumber')),
                  ),
                ),
              RecordsField(s.t('issueDate'), recordsDate(context, d.issueDate)),
              RecordsField(
                  s.t('expiryDate'), recordsDate(context, d.expiryDate)),
              RecordsField(s.t('issuer'), d.issuer),
              RecordsField(s.t('country'), d.country),
              if (d.tags.isNotEmpty)
                RecordsField(s.t('tags'), d.tags.join(', ')),
              if (d.description.isNotEmpty) Text(d.description),
              if (d.inTrash)
                Text(
                    s.t('purgeOn',
                        {'date': recordsDate(context, d.purgeAfter)}),
                    style: TextStyle(color: context.palette.warn)),
              if (!d.canEdit)
                Text(s.t('readOnly'),
                    style: TextStyle(color: context.palette.muted)),
            ]),
            if (file != null)
              RecordsSection(title: s.t('file'), children: [
                Text(s.t('fileLine', {
                  'name': file.name,
                  'size': s.size(file.sizeBytes),
                  'version': file.version,
                })),
                if (infected)
                  Text(s.t('infected'),
                      style: TextStyle(color: context.palette.stop)),
                const SizedBox(height: AnumSpacing.xs),
                Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      if (!infected && !d.inTrash)
                        OutlinedButton.icon(
                          key: const Key('vault-download'),
                          onPressed: () => _run(
                              () => _c.repository.save(d.id, file.name),
                              s.t('saved')),
                          icon: const Icon(Icons.download_outlined),
                          label: Text(s.t('download')),
                        ),
                      if (editable)
                        OutlinedButton.icon(
                          key: const Key('vault-new-version'),
                          onPressed: _progress == null ? _newVersion : null,
                          icon: const Icon(Icons.upload_outlined),
                          label: Text(s.t('newVersion')),
                        ),
                    ]),
                if (_progress != null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  LinearProgressIndicator(value: _progress),
                ],
              ]),
            if (!d.inTrash)
              RecordsSection(title: s.t('versions'), children: [
                if (_c.versionsError != null)
                  Text(_c.versionsError!,
                      style: TextStyle(color: context.palette.stop))
                else if (_c.versions == null || _c.versions!.items.isEmpty)
                  Text(s.t('versionsEmpty'),
                      style: TextStyle(color: context.palette.muted))
                else
                  for (final v in _c.versions!.items)
                    AnumRow(
                      key: Key('version-${v.id}'),
                      icon: Icons.history,
                      title: s.t('versionLine',
                          {'version': v.version, 'size': s.size(v.sizeBytes)}),
                      subtitle: recordsDateTime(context, v.createdAt),
                      trailing: PopupMenuButton<String>(
                        key: Key('version-menu-${v.id}'),
                        tooltip: s.t('versions'),
                        onSelected: (action) => action == 'restore'
                            ? _run(() => _c.restoreVersion(v.id),
                                s.t('versionRestored'))
                            : _run(
                                () => _c.repository.save(
                                    d.id,
                                    versionName(
                                        file?.name ?? d.title, v.version),
                                    versionId: v.id),
                                s.t('saved')),
                        itemBuilder: (_) => [
                          if (v.downloadable)
                            PopupMenuItem(
                                value: 'save', child: Text(s.t('saveVersion'))),
                          if (editable)
                            PopupMenuItem(
                                key: const Key('menu-restore-version'),
                                value: 'restore',
                                child: Text(s.t('restoreVersion'))),
                        ],
                      ),
                    ),
              ]),
            if (d.hasReceipt && !d.inTrash) _matches(context, s),
            if (vehiclePaperCategories.contains(d.category) && !d.inTrash)
              _papers(context, s),
            RecordsSection(
              title: s.t('links'),
              trailing: editable
                  ? IconButton(
                      key: const Key('vault-add-link'),
                      tooltip: s.t('addLink'),
                      onPressed: () => _addLink(linkTypes),
                      icon: const Icon(Icons.add_link))
                  : null,
              children: [
                if (d.links.isEmpty)
                  Text(s.t('linksEmpty'),
                      style: TextStyle(color: context.palette.muted))
                else
                  for (final link in d.links)
                    AnumRow(
                      key: Key('link-${link.id}'),
                      icon: Icons.link,
                      title: link.label.isEmpty ? link.targetId : link.label,
                      subtitle: s.label('link', link.targetType),
                      trailing: editable
                          ? IconButton(
                              key: Key('link-remove-${link.id}'),
                              tooltip: s.t('removeLink'),
                              onPressed: () => _run(
                                  () => _c.removeLink(link.id),
                                  s.t('linkRemoved')),
                              icon: const Icon(Icons.link_off))
                          : null,
                    ),
              ],
            ),
            if (d.canEdit)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: d.inTrash
                    ? FilledButton.icon(
                        key: const Key('vault-restore'),
                        onPressed: () => _run(_c.restore, s.t('restored')),
                        icon: const Icon(Icons.restore_from_trash),
                        label: Text(s.t('restore')),
                      )
                    : OutlinedButton.icon(
                        key: const Key('vault-delete'),
                        onPressed: _trash,
                        icon: const Icon(Icons.delete_outline),
                        label: Text(s.t('delete')),
                      ),
              ),
          ]);
        },
      );
}
