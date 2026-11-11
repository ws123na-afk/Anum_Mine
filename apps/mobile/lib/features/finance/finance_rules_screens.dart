import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_review_models.dart';
import 'finance_review_strings.dart';
import 'finance_rules_data.dart';
import 'finance_strings.dart';
import 'finance_tools_data.dart';
import 'finance_tools_strings.dart';

/// Finance, Rules and learning on the phone
/// (docs/finance.md#rules-and-learning, #automation-policies): the owner's
/// rules with their versions (a change runs only once approved with the
/// payload hash shown), the preview of earlier rows and Apply as one batch
/// that can be undone, merchant spellings, subcategories and the automation
/// switches. Nothing is automated unless the owner turns it on.

String _catLabel(BuildContext context, String category) =>
    FinanceStrings.of(context).maybe('category.$category') ?? category;

String _typeLabel(BuildContext context, String type) =>
    FinanceReviewStrings.of(context).maybe('type.$type') ?? type;

class RulesHubScreen extends StatelessWidget {
  const RulesHubScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.t('rules.title')),
          bottom: TabBar(isScrollable: true, tabs: [
            Tab(
                key: const Key('rules-tab-rules'),
                text: t.t('rules.tab.rules')),
            Tab(
                key: const Key('rules-tab-merchants'),
                text: t.t('rules.tab.merchants')),
            Tab(key: const Key('rules-tab-subs'), text: t.t('rules.tab.subs')),
            Tab(
                key: const Key('rules-tab-automation'),
                text: t.t('rules.tab.automation')),
          ]),
        ),
        body: TabBarView(children: [
          _RulesTab(repository: repository),
          _MerchantsTab(repository: repository),
          _SubcategoriesTab(repository: repository),
          _AutomationTab(repository: repository),
        ]),
      ),
    );
  }
}

/// Asks the owner to approve [version] with the payload hash they are shown;
/// true once approved. Declining leaves the version waiting.
Future<bool> approveRuleVersion(BuildContext context,
    FinanceMoreRepository repository, RuleVersion version) async {
  final t = FinanceToolsStrings.of(context);
  final c = version.content;
  final ok = await showDialog<bool>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(t.t('rules.approveTitle', {'version': version.version})),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(t.t('rules.approveText')),
            const SizedBox(height: AnumSpacing.sm),
            Text(c.summary,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(_ruleFiles(dialog, c)),
            Text(_ruleWhen(dialog, c)),
            const SizedBox(height: AnumSpacing.sm),
            Text(
                t.t('rc.hash', {
                  'hash': ltrValue(dialog, version.payloadHash.substring(0, 12))
                }),
                style: TextStyle(color: dialog.palette.muted)),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(t.t('rules.later'))),
        FilledButton(
            key: const Key('rule-approve-confirm'),
            onPressed: () => Navigator.pop(dialog, true),
            child: Text(t.t('rules.approve'))),
      ],
    ),
  );
  if (ok != true || !context.mounted) return false;
  try {
    await repository.approveRule(version);
    return true;
  } on Object catch (error) {
    if (context.mounted) {
      recordsToast(context, RecordsFailure.from(error).message);
    }
    return false;
  }
}

String _ruleFiles(BuildContext context, RuleContentSpec c) => [
      _catLabel(context, c.category),
      if (c.transactionType != null) _typeLabel(context, c.transactionType!),
    ].join(', ');

String _ruleWhen(BuildContext context, RuleContentSpec c) {
  final t = FinanceToolsStrings.of(context);
  final m = c.match;
  return [
    t.t('rules.dir.${m.direction}'),
    if (m.minAmount != null || m.maxAmount != null)
      t.t('rules.range', {
        'currency': m.currency ?? '',
        'min': m.minAmount ?? '0',
        'max': m.maxAmount ?? '-'
      }),
    if (m.accountIds.isNotEmpty)
      t.t('rules.accounts', {'n': m.accountIds.length}),
  ].join(', ');
}

/// What a rule would change on earlier rows, as a block.
class RulePreviewBlock extends StatelessWidget {
  const RulePreviewBlock(this.preview, {super.key});
  final RulePreview preview;

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Column(
      key: const Key('rule-preview'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(preview.changes == 0
            ? t.t('rules.previewNone')
            : t.t('rules.previewCounts', {
                'changes': preview.changes,
                'unchanged': preview.unchanged,
                'manual': preview.manualKept,
                'sms': preview.smsKept,
              })),
        if (preview.truncated)
          Text(t.t('rules.previewTruncated'), style: muted),
        for (final r in preview.rows)
          AnumRow(
            key: Key('rule-preview-${r.transactionId}'),
            icon: Icons.swap_horiz,
            title: r.description.isEmpty ? '-' : r.description,
            subtitle: [
              ltrValue(context, r.postedOn),
              formatAmount(r.amount, r.currency),
            ].join(' · '),
            detail: t.t('rules.previewChange', {
              'from': r.beforeType == 'uncategorized'
                  ? _typeLabel(context, 'uncategorized')
                  : _catLabel(context, r.before),
              'to': _catLabel(context, r.after),
            }),
          ),
      ],
    );
  }
}

// -- rules ---------------------------------------------------------------------

class _RulesTab extends StatefulWidget {
  const _RulesTab({required this.repository});
  final FinanceMoreRepository repository;

  @override
  State<_RulesTab> createState() => _RulesTabState();
}

class _RulesTabState extends State<_RulesTab> {
  String _state = 'all';
  int _pending = 0, _legacy = 0;
  late final RecordsList<RuleSummary> _rules =
      RecordsList((query, _, token) async {
    final page = await widget.repository
        .ownerRules(state: _state, query: query, token: token);
    if (mounted) {
      setState(() {
        _pending = page.pending;
        _legacy = page.legacy;
      });
    }
    return page.chunk;
  });
  late final RecordsList<RuleBatch> _batches = RecordsList(
      (_, __, token) => widget.repository.ruleBatches(token: token));

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _rules.dispose();
    _batches.dispose();
    super.dispose();
  }

  Future<void> _reload() => Future.wait([_rules.load(), _batches.load()]);

  Future<void> _new() async {
    final saved = await Navigator.of(context).push(MaterialPageRoute<bool>(
        builder: (_) => RuleEditorScreen(repository: widget.repository)));
    if (saved == true && mounted) await _reload();
  }

  Future<void> _open(RuleSummary rule) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => RuleDetailScreen(
            repository: widget.repository, ruleId: rule.ruleId)));
    if (mounted) await _reload();
  }

  Future<void> _undo(RuleBatch b) async {
    final t = FinanceToolsStrings.of(context);
    final ok = await confirmRecords(context,
        title: t.t('rules.undoTitle'),
        message: t.t('rules.undoText', {'n': b.changed}),
        action: t.t('rules.undo'));
    if (!ok || !mounted) return;
    try {
      final done = await widget.repository.undoRuleBatch(b.id);
      if (!mounted) return;
      recordsToast(context,
          t.t('rules.undone', {'restored': done.restored, 'kept': done.kept}));
      await _reload();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return ListenableBuilder(
      listenable: Listenable.merge([_rules, _batches]),
      builder: (context, _) => AnumPage(onRefresh: _reload, children: [
        Text(t.t('rules.intro'), style: muted),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('rules-new'),
            onPressed: _new,
            icon: const Icon(Icons.add),
            label: Text(t.t('rules.new')),
          ),
        ),
        if (_pending > 0)
          Text(t.t('rules.pendingCount', {'n': _pending}),
              style: TextStyle(color: context.palette.warn)),
        if (_legacy > 0)
          Text(t.t('rules.legacy', {'n': _legacy}), style: muted),
        RecordsSearchField(
            initial: '',
            hint: t.t('rules.search'),
            onSearch: (q) => _rules.load(query: q)),
        RecordsFilterChips(
          options: {
            for (final s in const ['all', 'active', 'pending', 'disabled'])
              s: t.t('rules.state.$s')
          },
          selected: _state,
          onSelected: (s) {
            setState(() => _state = s);
            _rules.load(filter: s);
          },
        ),
        ...recordsPagedBody<RuleSummary>(
          _rules,
          deniedHint: t.t('denied'),
          empty: AnumEmpty(
              icon: Icons.rule,
              title: t.t(_rules.narrowed ? 'rules.noMatch' : 'rules.empty'),
              message: t.t('rules.emptyText'),
              action: t.t('rules.new'),
              onAction: _new),
          row: (rule) {
            final c = rule.shown.content;
            return AnumRow(
              key: Key('rule-${rule.ruleId}'),
              icon: Icons.rule,
              tone: rule.pending != null
                  ? AnumTone.warn
                  : rule.current != null && !c.enabled
                      ? AnumTone.neutral
                      : AnumTone.ok,
              title: c.summary.isEmpty ? '-' : c.summary,
              subtitle:
                  [_ruleFiles(context, c), _ruleWhen(context, c)].join(' · '),
              detail: [
                t.t('rules.prov.${rule.shown.provenance}'),
                t.t('rules.version', {'n': rule.shown.version}),
                if (rule.current != null && !c.enabled) t.t('rules.off'),
                if (rule.pending != null)
                  t.t('rules.pending', {'version': rule.pending!.version}),
              ].join(' · '),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(rule),
            );
          },
        ),
        AnumHeader(
            title: t.t('rules.batches'), subtitle: t.t('rules.batchesSub')),
        ...recordsPagedBody<RuleBatch>(
          _batches,
          deniedHint: t.t('denied'),
          empty: Text(t.t('rules.batchesNone'), style: muted),
          row: (b) => RecordsRowAction(
            row: AnumRow(
              key: Key('rule-batch-${b.id}'),
              icon: Icons.history,
              title: t.t('rules.batchRows', {'n': b.changed}),
              subtitle: [
                t.label('rules.origin', b.origin),
                if (b.createdAt.isNotEmpty)
                  recordsDateTime(context, b.createdAt),
              ].join(' · '),
              detail: b.undone ? t.t('rules.batchUndone') : null,
            ),
            action: b.undone
                ? null
                : TextButton.icon(
                    key: Key('rule-batch-undo-${b.id}'),
                    onPressed: () => _undo(b),
                    icon: const Icon(Icons.undo),
                    label: Text(t.t('rules.undo')),
                  ),
          ),
        ),
      ]),
    );
  }
}

/// One rule: what runs, what waits, Apply to earlier rows, Edit and the
/// versions with Restore.
class RuleDetailScreen extends StatefulWidget {
  const RuleDetailScreen(
      {required this.repository, required this.ruleId, super.key});
  final FinanceMoreRepository repository;
  final String ruleId;

  @override
  State<RuleDetailScreen> createState() => _RuleDetailScreenState();
}

class _RuleDetailScreenState extends State<RuleDetailScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<RuleVersion> _versions = const [];
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      if (_versions.isEmpty) _phase = RecordsPhase.loading;
    });
    try {
      final versions = await widget.repository.ruleVersions(widget.ruleId);
      if (!mounted) return;
      setState(() {
        _versions = versions;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  RuleVersion? get _current {
    final approved = _versions.where((v) => v.status == 'approved').toList()
      ..sort((a, b) => b.version.compareTo(a.version));
    return approved.isEmpty ? null : approved.first;
  }

  RuleVersion? get _pending {
    if (_versions.isEmpty) return null;
    final newest = _versions.reduce((a, b) => a.version > b.version ? a : b);
    return newest.status == 'proposed' ? newest : null;
  }

  Future<void> _work(Future<bool> Function() work, String done) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      if (await work() && mounted) {
        recordsToast(context, done);
        await _load();
      }
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _apply() async {
    final t = FinanceToolsStrings.of(context);
    RulePreview preview;
    try {
      preview = await widget.repository.previewRule(widget.ruleId);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
      return;
    }
    if (!mounted) return;
    final go = await showRecordsSheet<bool>(
      context,
      (sheet) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(t.t('rules.previewTitle'),
              style: Theme.of(sheet).textTheme.titleLarge),
          RulePreviewBlock(preview),
          const SizedBox(height: AnumSpacing.md),
          if (preview.changes > 0)
            FilledButton(
              key: const Key('rule-apply-confirm'),
              onPressed: () => Navigator.pop(sheet, true),
              child: Text(t.t('rules.applyN', {'n': preview.changes})),
            ),
        ],
      ),
    );
    if (go != true || !mounted) return;
    await _work(() async {
      final batch =
          await widget.repository.applyRule(widget.ruleId, preview.hash);
      if (mounted) {
        recordsToast(context, t.t('rules.applied', {'n': batch.changed}));
      }
      return false;
    }, '');
    if (mounted) await _load();
  }

  Future<void> _edit(RuleVersion from) async {
    final saved = await Navigator.of(context).push(MaterialPageRoute<bool>(
        builder: (_) => RuleEditorScreen(
            repository: widget.repository,
            ruleId: widget.ruleId,
            initial: from.content)));
    if (saved == true && mounted) await _load();
  }

  Future<void> _restore(RuleVersion v) async {
    final t = FinanceToolsStrings.of(context);
    RuleVersion made;
    try {
      made = await widget.repository.revertRule(widget.ruleId, v.version);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
      return;
    }
    if (!mounted) return;
    final approved = await approveRuleVersion(context, widget.repository, made);
    if (!mounted) return;
    recordsToast(
        context, t.t(approved ? 'rules.restored' : 'rules.restoredWaiting'));
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final current = _current, pending = _pending;
    final shown = (current ?? pending)?.content;
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(t.t('rules.rule'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: t.t('denied'),
              onRetry: _load)
        else if (shown != null) ...[
          AnumHeader(
              title: shown.summary.isEmpty ? '-' : shown.summary,
              subtitle: _ruleFiles(context, shown)),
          RecordsSection(title: t.t('rules.runs'), children: [
            if (current == null)
              Text(t.t('rules.notRunning'), style: muted)
            else ...[
              RecordsField(
                  t.t('rules.when'), _ruleWhen(context, current.content)),
              if (current.content.match.reference != null)
                RecordsField(
                    t.t('rules.reference'), current.content.match.reference!),
              RecordsField(
                  t.t('rules.priority'), '${current.content.priority}'),
              RecordsField(t.t('rules.status'),
                  t.t(current.content.enabled ? 'rules.on' : 'rules.off')),
              RecordsField(t.t('rules.autoApply'),
                  t.t(current.content.autoApply ? 'rules.yes' : 'rules.no')),
              if (current.content.note != null)
                RecordsField(t.t('rules.note'), current.content.note!),
            ],
          ]),
          if (pending != null)
            RecordsSection(
                title: t.t('rules.pending', {'version': pending.version}),
                children: [
                  Text(pending.content.summary),
                  Text(_ruleFiles(context, pending.content)),
                  Text(_ruleWhen(context, pending.content), style: muted),
                  Wrap(
                      spacing: AnumSpacing.sm,
                      runSpacing: AnumSpacing.sm,
                      children: [
                        FilledButton.icon(
                          key: const Key('rule-approve'),
                          onPressed: _busy
                              ? null
                              : () => _work(
                                  () => approveRuleVersion(
                                      context, widget.repository, pending),
                                  t.t('rules.approved')),
                          icon: const Icon(Icons.check),
                          label: Text(t.t('rules.approve')),
                        ),
                        OutlinedButton(
                          key: const Key('rule-reject'),
                          onPressed: _busy
                              ? null
                              : () => _work(() async {
                                    await widget.repository.rejectRule(pending);
                                    return true;
                                  }, t.t('rules.rejected')),
                          child: Text(t.t('rules.reject')),
                        ),
                      ]),
                ]),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            if (current != null && current.content.enabled)
              OutlinedButton.icon(
                key: const Key('rule-apply'),
                onPressed: _busy ? null : _apply,
                icon: const Icon(Icons.playlist_add_check),
                label: Text(t.t('rules.applyEarlier')),
              ),
            OutlinedButton.icon(
              key: const Key('rule-edit'),
              onPressed: _busy ? null : () => _edit(pending ?? current!),
              icon: const Icon(Icons.edit_outlined),
              label: Text(t.t('rules.edit')),
            ),
          ]),
          AnumHeader(title: t.t('rules.versions')),
          AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final v in _versions)
                RecordsRowAction(
                  row: AnumRow(
                    key: Key('rule-version-${v.version}'),
                    icon: Icons.layers_outlined,
                    tone: v.status == 'approved'
                        ? AnumTone.ok
                        : v.status == 'proposed'
                            ? AnumTone.warn
                            : AnumTone.neutral,
                    title: t.t('rules.version', {'n': v.version}),
                    subtitle: [
                      t.t('rules.vstatus.${v.status}'),
                      t.t('rules.prov.${v.provenance}'),
                      if (v.createdAt.isNotEmpty)
                        recordsDateTime(context, v.createdAt),
                    ].join(' · '),
                    detail: [
                      '${v.content.summary}: ${_catLabel(context, v.content.category)}',
                      if (v.revertedFrom != null)
                        t.t('rules.restoresFrom', {'n': v.revertedFrom!}),
                    ].join(' · '),
                  ),
                  action:
                      v.status == 'approved' && v.version != current?.version
                          ? TextButton.icon(
                              key: Key('rule-restore-${v.version}'),
                              onPressed: _busy ? null : () => _restore(v),
                              icon: const Icon(Icons.restore),
                              label: Text(t.t('rules.restore')),
                            )
                          : null,
                ),
            ]),
          ),
        ],
      ]),
    );
  }
}

/// A new rule or a changed version: the conditions, what it files rows as,
/// a preview of earlier rows, then Save (proposed) and the approval.
class RuleEditorScreen extends StatefulWidget {
  const RuleEditorScreen(
      {required this.repository, this.ruleId, this.initial, super.key});
  final FinanceMoreRepository repository;
  final String? ruleId;
  final RuleContentSpec? initial;

  @override
  State<RuleEditorScreen> createState() => _RuleEditorScreenState();
}

class _RuleEditorScreenState extends State<RuleEditorScreen> {
  late final _merchant =
      TextEditingController(text: widget.initial?.match.merchant);
  late final _words =
      TextEditingController(text: widget.initial?.match.words.join(', '));
  late final _reference =
      TextEditingController(text: widget.initial?.match.reference);
  late final _min =
      TextEditingController(text: widget.initial?.match.minAmount);
  late final _max =
      TextEditingController(text: widget.initial?.match.maxAmount);
  late final _currency =
      TextEditingController(text: widget.initial?.match.currency);
  late final _note = TextEditingController(text: widget.initial?.note);
  late final _priority =
      TextEditingController(text: '${widget.initial?.priority ?? 100}');
  late String _direction = widget.initial?.match.direction ?? 'any';
  late String _category = widget.initial?.category ?? 'groceries';
  late String? _sub = widget.initial?.subcategoryId;
  late String? _type = widget.initial?.transactionType;
  late String? _account = (widget.initial?.match.accountIds.isNotEmpty ?? false)
      ? widget.initial!.match.accountIds.first
      : null;
  late bool _enabled = widget.initial?.enabled ?? true;
  late bool _autoApply = widget.initial?.autoApply ?? false;
  List<FinanceAccount> _accounts = const [];
  List<FinanceSubcategory> _subs = const [];
  RulePreview? _preview;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    widget.repository.financeAccounts().then((a) {
      if (mounted) setState(() => _accounts = a);
    }, onError: (_) {});
    widget.repository.subcategories().then((s) {
      if (mounted) setState(() => _subs = s);
    }, onError: (_) {});
  }

  @override
  void dispose() {
    for (final c in [
      _merchant,
      _words,
      _reference,
      _min,
      _max,
      _currency,
      _note,
      _priority
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  /// The content as typed, or null with the problem shown.
  RuleContentSpec? _content() {
    final t = FinanceToolsStrings.of(context);
    String? text(TextEditingController c) =>
        c.text.trim().isEmpty ? null : c.text.trim();
    final words = [
      for (final w in _words.text.split(','))
        if (w.trim().isNotEmpty) w.trim()
    ];
    String? problem;
    final min =
        text(_min) == null ? null : parseAmount(_min.text, signed: false);
    final max =
        text(_max) == null ? null : parseAmount(_max.text, signed: false);
    final currency = text(_currency)?.toUpperCase();
    final priority = int.tryParse(_priority.text.trim());
    if (text(_merchant) == null && words.isEmpty && text(_reference) == null) {
      problem = 'rules.error.needMatch';
    } else if ((text(_min) != null && min == null) ||
        (text(_max) != null && max == null)) {
      problem = 'rules.error.amount';
    } else if ((min != null || max != null) &&
        (currency == null || !RegExp(r'^[A-Z]{3}$').hasMatch(currency))) {
      problem = 'rules.error.needCurrency';
    } else if (min != null &&
        max != null &&
        double.parse(min) > double.parse(max)) {
      problem = 'rules.error.badRange';
    } else if (priority == null || priority < 0 || priority > 1000) {
      problem = 'rules.error.priority';
    }
    if (problem != null) {
      setState(() => _error = t.t(problem!));
      return null;
    }
    setState(() => _error = null);
    return RuleContentSpec(
      match: RuleMatchSpec(
        merchant: text(_merchant),
        words: words,
        reference: text(_reference),
        direction: _direction,
        minAmount: min,
        maxAmount: max,
        currency: min == null && max == null ? null : currency,
        accountIds: _account == null ? const [] : [_account!],
      ),
      category: _category,
      subcategoryId: _sub,
      transactionType: _type,
      priority: priority!,
      enabled: _enabled,
      autoApply: _autoApply,
      note: text(_note),
    );
  }

  Future<void> _previewIt() async {
    final content = _content();
    if (content == null) return;
    setState(() => _busy = true);
    try {
      final preview = await widget.repository.previewDraftRule(content);
      if (mounted) setState(() => _preview = preview);
    } on Object catch (error) {
      if (mounted) setState(() => _error = RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save() async {
    final t = FinanceToolsStrings.of(context);
    final content = _content();
    if (content == null) return;
    setState(() => _busy = true);
    try {
      final made = await widget.repository.proposeRule(widget.ruleId, content);
      if (!mounted) return;
      final approved =
          await approveRuleVersion(context, widget.repository, made);
      if (!mounted) return;
      recordsToast(context,
          t.t(approved ? 'rules.savedApproved' : 'rules.savedWaiting'));
      Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) setState(() => _error = RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _text(String key, TextEditingController c, String label,
          {String? help, TextInputType? keyboard, int? maxLength}) =>
      TextField(
        key: Key('rule-field-$key'),
        controller: c,
        keyboardType: keyboard,
        maxLength: maxLength,
        decoration: InputDecoration(labelText: label, helperText: help),
        onChanged: (_) => setState(() => _preview = null),
      );

  Widget _pick(String key, String label, String? value,
          Map<String, String> options, ValueChanged<String?> onChanged) =>
      DropdownButtonFormField<String>(
        key: Key('rule-field-$key'),
        initialValue: options.containsKey(value ?? '') ? (value ?? '') : null,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final MapEntry(:key, :value) in options.entries)
            DropdownMenuItem(
                value: key,
                child: Text(value, overflow: TextOverflow.ellipsis)),
        ],
        onChanged: (v) => setState(() {
          _preview = null;
          onChanged(v == null || v.isEmpty ? null : v);
        }),
      );

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(
          title: Text(
              t.t(widget.ruleId == null ? 'rules.new' : 'rules.editTitle'))),
      body: AnumPage(children: [
        Text(t.t('rules.editorIntro'), style: TextStyle(color: p.muted)),
        _text('merchant', _merchant, t.t('rules.merchant'),
            help: t.t('rules.merchantHint'), maxLength: 80),
        _text('words', _words, t.t('rules.words'),
            help: t.t('rules.wordsHint')),
        _text('reference', _reference, t.t('rules.reference'), maxLength: 60),
        _pick(
            'direction',
            t.t('rules.direction'),
            _direction,
            {
              for (final d in const ['any', 'in', 'out']) d: t.t('rules.dir.$d')
            },
            (v) => _direction = v ?? 'any'),
        _text('min', _min, t.t('rules.min'),
            keyboard: const TextInputType.numberWithOptions(decimal: true)),
        _text('max', _max, t.t('rules.max'),
            keyboard: const TextInputType.numberWithOptions(decimal: true)),
        _text('currency', _currency, t.t('rules.currency'), maxLength: 3),
        _pick(
            'account',
            t.t('rules.account'),
            _account,
            {
              '': t.t('rules.anyAccount'),
              for (final a in _accounts)
                if (!a.archived) a.id: a.name,
            },
            (v) => _account = v),
        _pick('category', t.t('rules.category'), _category, {
          for (final c in fileableCategories) c: _catLabel(context, c)
        }, (v) {
          _category = v ?? _category;
          _sub = null;
        }),
        _pick(
            'sub',
            t.t('rules.sub'),
            _sub,
            {
              '': t.t('rules.none'),
              for (final s in _subs)
                if (s.parent == _category && !s.archived) s.id: s.name,
            },
            (v) => _sub = v),
        _pick(
            'type',
            t.t('rules.type'),
            _type,
            {
              '': t.t('rules.typeAuto'),
              for (final k in fileableTypes) k: _typeLabel(context, k),
            },
            (v) => _type = v),
        _text('priority', _priority, t.t('rules.priority'),
            help: t.t('rules.priorityHint'), keyboard: TextInputType.number),
        _text('note', _note, t.t('rules.note'), maxLength: 200),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('rule-field-enabled'),
              contentPadding: EdgeInsets.zero,
              value: _enabled,
              onChanged: (v) => setState(() => _enabled = v),
              title: Text(t.t('rules.enabled')),
            )),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('rule-field-auto'),
              contentPadding: EdgeInsets.zero,
              value: _autoApply,
              onChanged: (v) => setState(() => _autoApply = v),
              title: Text(t.t('rules.autoApplyLong')),
            )),
        if (_error != null)
          Text(_error!,
              key: const Key('rule-error'), style: TextStyle(color: p.stop)),
        if (_preview != null) RulePreviewBlock(_preview!),
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
          FilledButton.icon(
            key: const Key('rule-save'),
            onPressed: _busy ? null : _save,
            icon: const Icon(Icons.save_outlined),
            label: Text(t.t('rules.save')),
          ),
          OutlinedButton.icon(
            key: const Key('rule-preview-button'),
            onPressed: _busy ? null : _previewIt,
            icon: const Icon(Icons.visibility_outlined),
            label: Text(t.t('rules.preview')),
          ),
        ]),
      ]),
    );
  }
}

// -- merchant spellings ---------------------------------------------------------

class _MerchantsTab extends StatefulWidget {
  const _MerchantsTab({required this.repository});
  final FinanceMoreRepository repository;

  @override
  State<_MerchantsTab> createState() => _MerchantsTabState();
}

class _MerchantsTabState extends State<_MerchantsTab> {
  late final RecordsList<MerchantSpelling> _list =
      RecordsList((query, _, __) async {
    final all = await widget.repository.merchantSpellings();
    final q = query.toLowerCase();
    final items = q.isEmpty
        ? all
        : all
            .where((a) =>
                a.alias.toLowerCase().contains(q) ||
                a.merchant.toLowerCase().contains(q))
            .toList();
    return RecordsChunk(items, total: items.length);
  });

  @override
  void initState() {
    super.initState();
    _list.load();
  }

  @override
  void dispose() {
    _list.dispose();
    super.dispose();
  }

  Future<void> _edit([MerchantSpelling? a]) async {
    final t = FinanceToolsStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: t.t(a == null ? 'alias.add' : 'alias.edit'),
        intro: t.t('alias.intro'),
        submitLabel: t.t('save'),
        fields: [
          RecordsInput.text('alias', t.t('alias.alias'),
              required: true, initial: a?.alias, maxLength: 80),
          RecordsInput.text('merchant', t.t('alias.merchant'),
              required: true, initial: a?.merchant, maxLength: 80),
        ],
        onSubmit: (v) async {
          final alias = (v['alias'] as String?) ?? '';
          final merchant = (v['merchant'] as String?) ?? '';
          if (alias.length < 2 || merchant.length < 2) {
            throw FormatException(t.t('alias.tooShort'));
          }
          await widget.repository.saveMerchantSpelling(a?.id, alias, merchant);
        },
      ),
    );
    if (saved && mounted) {
      recordsToast(context, t.t('alias.saved'));
      await _list.load();
    }
  }

  Future<void> _remove(MerchantSpelling a) async {
    final t = FinanceToolsStrings.of(context);
    final ok = await confirmRecords(context,
        title: t.t('alias.remove'),
        message: '${a.alias}: ${a.merchant}',
        action: t.t('alias.remove'));
    if (!ok || !mounted) return;
    try {
      await widget.repository.deleteMerchantSpelling(a.id);
      if (!mounted) return;
      recordsToast(context, t.t('alias.removed'));
      await _list.load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    return ListenableBuilder(
      listenable: _list,
      builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
        Text(t.t('alias.subtitle'),
            style: TextStyle(color: context.palette.muted)),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('alias-add'),
            onPressed: () => _edit(),
            icon: const Icon(Icons.add),
            label: Text(t.t('alias.add')),
          ),
        ),
        RecordsSearchField(
            initial: '',
            hint: t.t('alias.search'),
            onSearch: (q) => _list.load(query: q)),
        ...recordsPagedBody<MerchantSpelling>(
          _list,
          deniedHint: t.t('denied'),
          empty: AnumEmpty(
              icon: Icons.storefront_outlined,
              title: t.t(_list.narrowed ? 'alias.noMatch' : 'alias.empty'),
              message: t.t('alias.emptyText'),
              action: t.t('alias.add'),
              onAction: () => _edit()),
          row: (a) => RecordsRowAction(
            row: AnumRow(
              key: Key('alias-${a.id}'),
              icon: Icons.storefront_outlined,
              title: a.merchant,
              subtitle: a.alias,
              trailing: const Icon(Icons.edit_outlined),
              onTap: () => _edit(a),
            ),
            action: TextButton.icon(
              key: Key('alias-remove-${a.id}'),
              onPressed: () => _remove(a),
              icon: const Icon(Icons.delete_outline),
              label: Text(t.t('alias.remove')),
            ),
          ),
        ),
      ]),
    );
  }
}

// -- subcategories --------------------------------------------------------------

class _SubcategoriesTab extends StatefulWidget {
  const _SubcategoriesTab({required this.repository});
  final FinanceMoreRepository repository;

  @override
  State<_SubcategoriesTab> createState() => _SubcategoriesTabState();
}

class _SubcategoriesTabState extends State<_SubcategoriesTab> {
  bool _archived = false;
  late final RecordsList<FinanceSubcategory> _list =
      RecordsList((query, _, __) async {
    final all = await widget.repository.subcategories();
    final q = query.toLowerCase();
    final items = [
      for (final s in all)
        if ((_archived || !s.archived) &&
            (q.isEmpty || s.name.toLowerCase().contains(q)))
          s
    ]..sort(
        (a, b) => '${a.parent} ${a.name}'.compareTo('${b.parent} ${b.name}'));
    return RecordsChunk(items, total: items.length);
  });

  @override
  void initState() {
    super.initState();
    _list.load();
  }

  @override
  void dispose() {
    _list.dispose();
    super.dispose();
  }

  Future<void> _edit([FinanceSubcategory? sub]) async {
    final t = FinanceToolsStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: t.t(sub == null ? 'sub.add' : 'sub.rename'),
        intro: t.t('sub.subtitle'),
        submitLabel: t.t('save'),
        fields: [
          if (sub == null)
            RecordsInput.choice('parent', t.t('sub.parent'),
                options: {
                  for (final c in fileableCategories) c: _catLabel(context, c)
                },
                initial: 'groceries'),
          RecordsInput.text('name', t.t('sub.name'),
              required: true, initial: sub?.name, maxLength: 60),
        ],
        onSubmit: (v) => widget.repository.saveSubcategory(sub?.id,
            parent: sub?.parent ?? v['parent']! as String,
            name: v['name']! as String,
            archived: sub?.archived ?? false),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, t.t('sub.saved'));
      await _list.load();
    }
  }

  Future<void> _toggle(FinanceSubcategory sub) async {
    final t = FinanceToolsStrings.of(context);
    try {
      await widget.repository.saveSubcategory(sub.id,
          parent: sub.parent, name: sub.name, archived: !sub.archived);
      if (!mounted) return;
      recordsToast(
          context, t.t(sub.archived ? 'sub.restored' : 'sub.archivedDone'));
      await _list.load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    return ListenableBuilder(
      listenable: _list,
      builder: (context, _) => AnumPage(onRefresh: _list.load, children: [
        Text(t.t('sub.subtitle'),
            style: TextStyle(color: context.palette.muted)),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('sub-add'),
            onPressed: () => _edit(),
            icon: const Icon(Icons.add),
            label: Text(t.t('sub.add')),
          ),
        ),
        RecordsSearchField(
            initial: '',
            hint: t.t('sub.search'),
            onSearch: (q) => _list.load(query: q)),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('sub-show-archived'),
              contentPadding: EdgeInsets.zero,
              value: _archived,
              onChanged: (v) {
                setState(() => _archived = v);
                _list.load();
              },
              title: Text(t.t('sub.showArchived')),
            )),
        ...recordsPagedBody<FinanceSubcategory>(
          _list,
          deniedHint: t.t('denied'),
          empty: AnumEmpty(
              icon: Icons.category_outlined,
              title: t.t(_list.narrowed ? 'sub.noMatch' : 'sub.empty'),
              message: t.t('sub.emptyText'),
              action: t.t('sub.add'),
              onAction: () => _edit()),
          row: (s) => RecordsRowAction(
            row: AnumRow(
              key: Key('sub-${s.id}'),
              icon: Icons.category_outlined,
              tone: s.archived ? AnumTone.neutral : null,
              title: s.name,
              subtitle: _catLabel(context, s.parent),
              detail: s.archived ? t.t('sub.archived') : null,
              trailing: const Icon(Icons.edit_outlined),
              onTap: () => _edit(s),
            ),
            action: TextButton(
              key: Key('sub-toggle-${s.id}'),
              onPressed: () => _toggle(s),
              child: Text(t.t(s.archived ? 'sub.unarchive' : 'sub.archive')),
            ),
          ),
        ),
      ]),
    );
  }
}

// -- automation -----------------------------------------------------------------

class _AutomationTab extends StatefulWidget {
  const _AutomationTab({required this.repository});
  final FinanceMoreRepository repository;

  @override
  State<_AutomationTab> createState() => _AutomationTabState();
}

class _AutomationTabState extends State<_AutomationTab> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  AutomationState? _state;
  bool _classify = false, _notify = false, _busy = false;
  final _sources = <String>{};
  final _notices = <String>{};
  final _confidence = TextEditingController();
  final _notifyMin = TextEditingController();
  final _days = TextEditingController();
  final _uncategorized = TextEditingController();
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final c in [_confidence, _notifyMin, _days, _uncategorized]) {
      c.dispose();
    }
    super.dispose();
  }

  void _fill(AutomationState s) {
    final p = s.policy;
    _state = s;
    _classify = p.autoClassify;
    _notify = p.notifyUnresolved;
    _sources
      ..clear()
      ..addAll(p.sources);
    _notices
      ..clear()
      ..addAll(p.notices);
    _confidence.text =
        ((double.tryParse(p.minConfidence) ?? 0.9) * 100).round().toString();
    _notifyMin.text = '${p.notifyMinItems}';
    _days.text = '${p.exceptionDays}';
    _uncategorized.text = p.uncategorizedMin;
  }

  Future<void> _load() async {
    try {
      final s = await widget.repository.automation();
      if (!mounted) return;
      setState(() {
        _fill(s);
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _save() async {
    final t = FinanceToolsStrings.of(context);
    final confidence = int.tryParse(_confidence.text.trim());
    final notifyMin = int.tryParse(_notifyMin.text.trim());
    final days = int.tryParse(_days.text.trim());
    final uncategorized =
        parseAmount(_uncategorized.text.trim(), signed: false);
    String? problem;
    if (confidence == null || confidence < 80 || confidence > 100) {
      problem = 'auto.error.confidence';
    } else if (notifyMin == null || notifyMin < 1 || notifyMin > 500) {
      problem = 'auto.error.notifyMin';
    } else if (days == null || days < 1 || days > 90) {
      problem = 'auto.error.days';
    } else if (uncategorized == null) {
      problem = 'auto.error.amount';
    } else if (_classify && _sources.isEmpty) {
      problem = 'auto.error.sources';
    }
    if (problem != null) {
      setState(() => _error = t.t(problem!));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final saved = await widget.repository.saveAutomation(AutomationPolicySpec(
        autoClassify: _classify,
        minConfidence: (confidence! / 100).toStringAsFixed(2),
        sources: [
          for (final s in const ['rule', 'history'])
            if (_sources.contains(s)) s
        ],
        notifyUnresolved: _notify,
        notifyMinItems: notifyMin!,
        notices: [
          for (final k in noticeKinds)
            if (_notices.contains(k)) k
        ],
        exceptionDays: days!,
        uncategorizedMin: uncategorized!,
      ));
      if (!mounted) return;
      setState(() => _fill(saved));
      recordsToast(context, t.t('auto.saved'));
    } on Object catch (error) {
      if (mounted) setState(() => _error = RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _number(String key, TextEditingController c, String label,
          {bool enabled = true, bool decimal = false}) =>
      TextField(
        key: Key('auto-$key'),
        controller: c,
        enabled: enabled,
        keyboardType: TextInputType.numberWithOptions(decimal: decimal),
        decoration: InputDecoration(labelText: label),
      );

  @override
  Widget build(BuildContext context) {
    final t = FinanceToolsStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final s = _state;
    return AnumPage(onRefresh: _load, children: [
      if (_phase != RecordsPhase.ready || s == null)
        RecordsStateView(
            phase: _phase,
            failure: _failure,
            deniedHint: t.t('denied'),
            onRetry: _load)
      else ...[
        Text(t.t('auto.subtitle'), style: muted),
        if (s.updatedAt != null)
          Text(
              t.t('auto.updated',
                  {'when': recordsDateTime(context, s.updatedAt)}),
              style: muted),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('auto-classify'),
              contentPadding: EdgeInsets.zero,
              value: _classify,
              onChanged: (v) => setState(() => _classify = v),
              title: Text(t.t('auto.classify')),
            )),
        _number('confidence', _confidence, t.t('auto.minConfidence'),
            enabled: _classify),
        for (final source in const ['history', 'rule'])
          Material(
              type: MaterialType.transparency,
              child: CheckboxListTile(
                key: Key('auto-source-$source'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: _sources.contains(source),
                onChanged: _classify
                    ? (v) => setState(() => v == true
                        ? _sources.add(source)
                        : _sources.remove(source))
                    : null,
                title: Text(t.t('auto.source.$source')),
              )),
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              key: const Key('auto-notify'),
              contentPadding: EdgeInsets.zero,
              value: _notify,
              onChanged: (v) => setState(() => _notify = v),
              title: Text(t.t('auto.notify')),
            )),
        _number('notify-min', _notifyMin, t.t('auto.notifyMin'),
            enabled: _notify),
        RecordsSection(title: t.t('auto.notices'), children: [
          Text(t.t('auto.noticesText'), style: muted),
          for (final kind in noticeKinds)
            Material(
                type: MaterialType.transparency,
                child: CheckboxListTile(
                  key: Key('auto-notice-$kind'),
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: _notices.contains(kind),
                  onChanged: (v) => setState(() =>
                      v == true ? _notices.add(kind) : _notices.remove(kind)),
                  title: Text(t.t('auto.notice.$kind')),
                )),
          _number('days', _days, t.t('auto.noticeDays'),
              enabled: _notices.contains('reconciliation_overdue')),
          _number(
              'uncategorized', _uncategorized, t.t('auto.noticeUncategorized'),
              enabled: _notices.contains('uncategorized_money'), decimal: true),
        ]),
        if (_error != null)
          Text(_error!,
              key: const Key('auto-error'), style: TextStyle(color: p.stop)),
        FilledButton.icon(
          key: const Key('auto-save'),
          onPressed: _busy ? null : _save,
          icon: const Icon(Icons.save_outlined),
          label: Text(t.t('auto.save')),
        ),
        RecordsSection(title: t.t('auto.never'), children: [
          Text(t.t('auto.neverText'), style: muted),
          for (final k in s.never) Text('• ${t.label('auto.never', k)}'),
        ]),
      ],
    ]);
  }
}
