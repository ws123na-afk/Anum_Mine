import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_depth_controller.dart';
import 'finance_depth_strings.dart';
import 'finance_money_strings.dart';
import 'finance_review_models.dart' show fileableCategories;
import 'finance_strings.dart';

/// Finance settings, Statement keyword rules on the phone
/// (docs/finance.md#finance-settings): a statement row whose description
/// contains the words gets the category. Add, edit and delete; every save
/// sends the whole list with the currency and time zone kept
/// (`PUT /settings`), at most 100 rules of 2 to 80 characters.

const maxKeywordRules = 100;

class KeywordRulesScreen extends StatefulWidget {
  const KeywordRulesScreen({required this.controller, super.key});
  final FinanceDepthController controller;

  @override
  State<KeywordRulesScreen> createState() => _KeywordRulesScreenState();
}

class _KeywordRulesScreenState extends State<KeywordRulesScreen> {
  FinanceDepthController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (!c.settings.ready) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) c.loadSettings();
      });
    }
  }

  List<Map<String, Object?>> get _rules => c.settings.value?.rules ?? const [];

  Future<void> _form({int? index}) async {
    final m = FinanceMoneyStrings.of(context);
    final f = FinanceStrings.of(context);
    final rule = index == null ? null : _rules[index];
    if (index == null && _rules.length >= maxKeywordRules) {
      recordsToast(context, m.t('kw.full'));
      return;
    }
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: index == null ? m.t('kw.add') : m.t('kw.edit'),
        intro: m.t('kw.intro'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.text('match', m.t('kw.match'),
              required: true,
              initial: rule?['match'] as String?,
              help: m.t('kw.matchHelp'),
              maxLength: 80),
          RecordsInput.choice('category', m.t('kw.category'),
              options: {
                for (final cat in fileableCategories) cat: f.t('category.$cat')
              },
              initial: (rule?['category'] as String?) ?? 'other'),
        ],
        onSubmit: (v) async {
          final match = (v['match'] as String).trim();
          if (match.length < 2 || match.length > 80) {
            throw FormatException(m.t('kw.badMatch'));
          }
          final taken = [
            for (final (i, r) in _rules.indexed)
              if (i != index &&
                  '${r['match']}'.toLowerCase() == match.toLowerCase())
                r
          ];
          if (taken.isNotEmpty) throw FormatException(m.t('kw.duplicate'));
          final next = [..._rules];
          final value = {'match': match, 'category': v['category'] ?? 'other'};
          if (index == null) {
            next.add(value);
          } else {
            next[index] = value;
          }
          await c.saveRules(next);
        },
      ),
    );
    if (saved && mounted) recordsToast(context, m.t('kw.saved'));
  }

  Future<void> _delete(int index) async {
    final m = FinanceMoneyStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('kw.delete.title'),
        message: m.t('kw.delete.text'),
        action: m.t('kw.delete'))) {
      return;
    }
    try {
      await c.saveRules([..._rules]..removeAt(index));
      if (mounted) recordsToast(context, m.t('kw.saved'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final f = FinanceStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('kw.title'))),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final part = c.settings;
          return AnumPage(onRefresh: c.loadSettings, children: [
            Text(m.t('kw.intro'),
                style: TextStyle(color: context.palette.muted)),
            if (!part.ready)
              RecordsStateView(
                  phase: part.phase,
                  failure: part.failure,
                  deniedHint: FinanceDepthStrings.of(context).t('set.denied'),
                  onRetry: c.loadSettings)
            else ...[
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: const Key('kw-add'),
                  onPressed: () => _form(),
                  icon: const Icon(Icons.add),
                  label: Text(m.t('kw.add')),
                ),
              ),
              if (_rules.isEmpty)
                AnumEmpty(
                    icon: Icons.rule_folder_outlined,
                    title: m.t('kw.empty'),
                    message: m.t('kw.emptyText'),
                    action: m.t('kw.add'),
                    onAction: () => _form())
              else
                AnumSurface(
                  padding: const EdgeInsets.symmetric(
                      horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
                  child: Column(children: [
                    for (final (i, rule) in _rules.indexed)
                      AnumRow(
                        key: Key('kw-$i'),
                        icon: Icons.label_outline,
                        title: m.t('kw.row', {'match': '${rule['match']}'}),
                        subtitle: f.t('category.${rule['category']}'),
                        onTap: () => _form(index: i),
                        trailing: IconButton(
                          key: Key('kw-delete-$i'),
                          tooltip: m.t('kw.delete'),
                          icon: const Icon(Icons.delete_outline),
                          onPressed: () => _delete(i),
                        ),
                      ),
                  ]),
                ),
            ],
          ]);
        },
      ),
    );
  }
}
