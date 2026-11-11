import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_controller.dart';
import 'finance_depth_controller.dart';
import 'finance_depth_models.dart';
import 'finance_depth_strings.dart';
import 'finance_keyword_rules.dart';
import 'finance_more_menu.dart';
import 'finance_models.dart';
import 'finance_personal_controller.dart';
import 'finance_personal_view.dart' show MoneyLinkOpener;
import 'finance_review_strings.dart';
import 'finance_review_view.dart' show FinanceWebLink, typeLabel;
import 'finance_strings.dart';

/// Finance settings, the emergency reserve form, the exception notices and
/// a transaction's card FX section on the phone (docs/finance.md). Every
/// change goes through the routes the web uses; nothing here moves money.

const _zones = [
  'Asia/Dubai',
  'Asia/Riyadh',
  'Asia/Qatar',
  'Asia/Kuwait',
  'Asia/Bahrain',
  'Asia/Muscat',
  'Asia/Karachi',
  'Asia/Kolkata',
  'Europe/London',
  'UTC',
];

Widget _ltr(String text, {TextStyle? style, Key? key}) =>
    Text(text, key: key, textDirection: TextDirection.ltr, style: style);

/// A card's state while it is not ready: loading, who may use it, or what
/// failed with Try again.
Widget _partState<T>(DepthPart<T> part, VoidCallback retry, String denied) =>
    RecordsStateView(
        phase: part.phase,
        failure: part.failure,
        deniedHint: denied,
        onRetry: retry);

class _Card extends StatelessWidget {
  const _Card(
      {required this.title,
      required this.subtitle,
      required this.icon,
      required this.children,
      super.key});
  final String title, subtitle;
  final IconData icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            AnumIconChip(icon: icon),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(child: AnumHeader(title: title, subtitle: subtitle)),
          ]),
          for (final child in children) ...[
            const SizedBox(height: AnumSpacing.xs),
            child,
          ],
        ]),
      );
}

/// Finance settings (docs/finance.md#finance-settings): currency and time
/// zone, the Safe to spend buffer, the emergency reserve, the salary
/// summary, the exception notices, and links to rules, automation and
/// connection health.
class FinanceSettingsSection extends StatelessWidget {
  const FinanceSettingsSection(
      {required this.controller,
      required this.finance,
      required this.appUrl,
      required this.openLink,
      super.key});
  final FinanceDepthController controller;
  final FinanceController finance;
  final String appUrl;
  final MoneyLinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final personal = finance.personal;
    return ListenableBuilder(
      listenable:
          Listenable.merge([controller, if (personal != null) personal]),
      builder: (context, _) {
        final d = FinanceDepthStrings.of(context);
        final muted = TextStyle(color: context.palette.muted);
        final c = controller;
        final base = c.settings.value?.baseCurrency ?? 'AED';
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(d.t('set.intro'), style: muted),
            const SizedBox(height: AnumSpacing.md),
            _GeneralCard(controller: c),
            const SizedBox(height: AnumSpacing.md),
            _BufferCard(controller: c, base: base),
            const SizedBox(height: AnumSpacing.md),
            ReserveSettings(controller: c, finance: finance, base: base),
            const SizedBox(height: AnumSpacing.md),
            if (personal != null) ...[
              _SalaryCard(
                  personal: personal,
                  appUrl: appUrl,
                  openLink: openLink,
                  onEdit: finance.more == null
                      ? null
                      : () => openFinanceMore(
                          context, finance, FinanceMorePage.salary)),
              const SizedBox(height: AnumSpacing.md),
            ],
            NoticesCard(controller: c),
            const SizedBox(height: AnumSpacing.md),
            _Card(
              title: d.t('set.links.title'),
              subtitle: d.t('set.links.subtitle'),
              icon: Icons.auto_fix_high_outlined,
              children: [
                Text(d.t('set.links.onWeb'), style: muted),
                if (finance.more != null)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: FilledButton.icon(
                      key: const Key('set-open-rules'),
                      onPressed: () => openFinanceMore(
                          context, finance, FinanceMorePage.rules),
                      icon: const Icon(Icons.rule),
                      label: Text(d.t('set.links.open')),
                    ),
                  ),
                for (final view in ['rules', 'merchants', 'automation'])
                  FinanceWebLink(
                      key: Key('set-link-$view'),
                      appUrl: appUrl,
                      openLink: openLink,
                      fragment: 'finance-suggestions?view=$view',
                      label: d.t('set.links.$view')),
              ],
            ),
            const SizedBox(height: AnumSpacing.md),
            _Card(
              title: d.t('set.data.title'),
              subtitle: d.t('set.data.subtitle'),
              icon: Icons.sync_alt,
              children: [
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton.icon(
                    key: const Key('set-health'),
                    onPressed: () => finance.select(FinanceSection.connections),
                    icon: const Icon(Icons.monitor_heart_outlined),
                    label: Text(d.t('set.data.health')),
                  ),
                ),
                FinanceWebLink(
                    appUrl: appUrl,
                    openLink: openLink,
                    fragment: 'finance-import',
                    label: d.t('set.data.import')),
                FinanceWebLink(
                    appUrl: appUrl,
                    openLink: openLink,
                    fragment: 'finance-reconciliation',
                    label: d.t('set.data.reconciliation')),
              ],
            ),
          ],
        );
      },
    );
  }
}

class _GeneralCard extends StatelessWidget {
  const _GeneralCard({required this.controller});
  final FinanceDepthController controller;

  Future<void> _edit(BuildContext context, FinanceSettingsData s) async {
    final d = FinanceDepthStrings.of(context);
    final zones = {
      for (final z in {..._zones, s.timezone}) z: z,
    };
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: d.t('set.general.title'),
        intro: d.t('set.zoneHelp'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.text('currency', d.t('set.currency'),
              required: true, initial: s.baseCurrency, maxLength: 3),
          RecordsInput.choice('timezone', d.t('set.timezone'),
              initial: s.timezone, options: zones),
        ],
        onSubmit: (v) async {
          final currency = (v['currency']! as String).toUpperCase();
          if (!RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
            throw FormatException(d.t('set.currencyError'));
          }
          await controller.saveGeneral(
              currency: currency, timezone: v['timezone']! as String);
        },
      ),
    );
    if (saved && context.mounted) recordsToast(context, d.t('set.saved'));
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final part = controller.settings;
    final s = part.value;
    return _Card(
      key: const Key('set-general'),
      title: d.t('set.general.title'),
      subtitle: d.t('set.general.subtitle'),
      icon: Icons.public,
      children: !part.ready || s == null
          ? [_partState(part, controller.loadSettings, d.t('set.denied'))]
          : [
              RecordsField(d.t('set.currency'), s.baseCurrency),
              RecordsField(d.t('set.timezone'), s.timezone),
              Text(d.t('set.zoneHelp'),
                  style: TextStyle(color: context.palette.muted)),
              Text(d.t('set.rulesKept', {'n': s.rules.length}),
                  style: TextStyle(color: context.palette.muted)),
              Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.sm,
                children: [
                  OutlinedButton.icon(
                    key: const Key('set-general-edit'),
                    onPressed: () => _edit(context, s),
                    icon: const Icon(Icons.edit_outlined),
                    label: Text(d.t('set.edit')),
                  ),
                  OutlinedButton.icon(
                    key: const Key('set-rules-edit'),
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                            builder: (_) =>
                                KeywordRulesScreen(controller: controller))),
                    icon: const Icon(Icons.rule_folder_outlined),
                    label: Text(d.t('set.rulesEdit')),
                  ),
                ],
              ),
            ],
    );
  }
}

class _BufferCard extends StatelessWidget {
  const _BufferCard({required this.controller, required this.base});
  final FinanceDepthController controller;
  final String base;

  Future<void> _edit(BuildContext context, {CurrencyAmount? current}) async {
    final d = FinanceDepthStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: d.t('set.safe.formTitle'),
        intro: d.t('set.safe.formIntro'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.text('currency', d.t('set.safe.currency'),
              required: true, initial: current?.currency ?? base, maxLength: 3),
          RecordsInput.number('amount', d.t('set.safe.amount'),
              required: true, initial: current?.amount),
        ],
        onSubmit: (v) async {
          final currency = (v['currency']! as String).toUpperCase();
          if (!RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
            throw FormatException(d.t('set.currencyError'));
          }
          final amount = v['amount'] as String?;
          if (amount == null) {
            throw FormatException(d.t('set.safe.amountError'));
          }
          await controller.saveBuffer(currency, amount);
        },
      ),
    );
    if (saved && context.mounted) recordsToast(context, d.t('set.safe.saved'));
  }

  Future<void> _remove(BuildContext context, CurrencyAmount buffer) async {
    final d = FinanceDepthStrings.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(d.t('set.safe.removeTitle')),
        content:
            Text(d.t('set.safe.removeText', {'currency': buffer.currency})),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialog).pop(false),
              child: Text(d.t('fx.cancel'))),
          FilledButton(
              key: const Key('set-buffer-remove-confirm'),
              onPressed: () => Navigator.of(dialog).pop(true),
              child: Text(KitStrings.of(dialog).t('delete'))),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    try {
      await controller.saveBuffer(buffer.currency, null);
      if (context.mounted) recordsToast(context, d.t('set.safe.removed'));
    } on Object catch (error) {
      if (context.mounted) {
        recordsToast(context, RecordsFailure.from(error).message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final part = controller.plan;
    final p = part.value;
    final muted = TextStyle(color: context.palette.muted);
    return _Card(
      key: const Key('set-buffer'),
      title: d.t('set.safe.title'),
      subtitle: d.t('set.safe.subtitle'),
      icon: Icons.savings_outlined,
      children: !part.ready || p == null
          ? [_partState(part, controller.loadPlan, d.t('set.denied'))]
          : [
              if (p.buffers.isEmpty)
                Text(d.t('set.safe.noBuffer'), style: muted)
              else
                for (final b in p.buffers)
                  AnumRow(
                    key: Key('set-buffer-${b.currency}'),
                    title: formatAmount(b.amount, b.currency),
                    icon: Icons.shield_outlined,
                    onTap: () => _edit(context, current: b),
                    trailing: IconButton(
                      key: Key('set-buffer-remove-${b.currency}'),
                      tooltip: d.t('set.safe.remove', {'currency': b.currency}),
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _remove(context, b),
                    ),
                  ),
              Text(
                  p.combine
                      ? d.t('set.safe.combineOn', {'days': p.fxMaxAgeDays})
                      : d.t('set.safe.combineOff'),
                  style: muted),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  key: const Key('set-buffer-add'),
                  onPressed: () => _edit(context),
                  icon: const Icon(Icons.add),
                  label: Text(d.t('set.safe.add')),
                ),
              ),
            ],
    );
  }
}

/// The emergency reserve per currency (docs/finance.md#emergency-reserve):
/// saved of target, the progress, months covered, where it is kept and the
/// form that creates or updates it through the goal routes.
class ReserveSettings extends StatelessWidget {
  const ReserveSettings(
      {required this.controller,
      required this.finance,
      required this.base,
      super.key});
  final FinanceDepthController controller;
  final FinanceController finance;
  final String base;

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final part = controller.reserves;
    final book = part.value;
    if (!part.ready || book == null) {
      return _Card(
        key: const Key('set-reserve'),
        title: d.t('res.title'),
        subtitle: d.t('res.subtitle'),
        icon: Icons.health_and_safety_outlined,
        children: [
          _partState(part, controller.loadReserves, d.t('set.denied'))
        ],
      );
    }
    final currencies = {
      base,
      ...book.reserves.map((r) => r.currency),
      ...book.suggestions.map((s) => s.currency),
    }.toList()
      ..sort();
    return Column(
      key: const Key('set-reserve'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < currencies.length; i++) ...[
          if (i > 0) const SizedBox(height: AnumSpacing.md),
          _ReserveCard(
              controller: controller,
              finance: finance,
              book: book,
              currency: currencies[i]),
        ],
      ],
    );
  }
}

class _ReserveCard extends StatelessWidget {
  const _ReserveCard(
      {required this.controller,
      required this.finance,
      required this.book,
      required this.currency});
  final FinanceDepthController controller;
  final FinanceController finance;
  final ReserveBook book;
  final String currency;

  Future<void> _edit(BuildContext context) async {
    final d = FinanceDepthStrings.of(context);
    final reserve = book.reserveIn(currency);
    final suggestion = book.suggestionIn(currency);
    final essential = reserve?.essentialMonthly ?? suggestion?.essentialMonthly;
    final accounts = [
      for (final a in finance.accounts)
        if (!a.archived &&
            a.currency == currency &&
            const {'bank', 'cash', 'investment'}.contains(a.kind))
          a
    ];
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: '${d.t('res.title')} ($currency)',
        intro: essential == null
            ? d.t('res.noEssentials')
            : d.t('res.targetHelp',
                {'essential': formatAmount(essential, currency)}),
        submitLabel: d.t(reserve == null ? 'res.create' : 'res.update'),
        fields: [
          RecordsInput.choice('mode', d.t('res.mode'),
              initial: reserve?.mode ??
                  (suggestion != null || essential != null
                      ? 'months'
                      : 'amount'),
              options: {
                'months': d.t('res.modeMonths'),
                'amount': d.t('res.modeAmount'),
              }),
          RecordsInput.number('months', d.t('res.months'),
              initial:
                  '${reserve?.months ?? suggestion?.months ?? book.defaultMonths}'),
          RecordsInput.number('amount', d.t('res.amount'),
              initial: reserve?.mode == 'amount' ? reserve?.target : null),
          RecordsInput.choice('holder', d.t('res.holder'),
              initial: reserve?.accountId != null &&
                      accounts.any((a) => a.id == reserve!.accountId)
                  ? reserve!.accountId
                  : 'goal',
              options: {
                'goal': d.t('res.inGoal'),
                for (final a in accounts) a.id: a.name,
              }),
          RecordsInput.number('saved', d.t('res.savedField'),
              initial: reserve != null && reserve.heldIn == 'goal'
                  ? reserve.saved
                  : '0'),
          RecordsInput.number('monthly', d.t('res.monthly'),
              initial: reserve?.monthlyContribution),
        ],
        onSubmit: (v) async {
          final mode = v['mode']! as String;
          int? months;
          String target;
          if (mode == 'months') {
            months = int.tryParse((v['months'] as String?) ?? '');
            if (months == null || months < 1 || months > 24) {
              throw FormatException(d.t('res.monthsError'));
            }
            final worked = monthsTarget(essential, months);
            if (worked == null) throw FormatException(d.t('res.noEssentials'));
            target = worked;
          } else {
            final amount = v['amount'] as String?;
            final units = amount == null ? null : minorUnits(amount);
            if (units == null || units <= BigInt.zero) {
              throw FormatException(d.t('res.amountError'));
            }
            target = amount!;
          }
          final holder = v['holder']! as String;
          await controller.saveReserve(
            currency: currency,
            months: months,
            target: target,
            accountId: holder == 'goal' ? null : holder,
            saved: (v['saved'] as String?) ?? '0',
            monthly: v['monthly'] as String?,
            defaultName: d.t('res.defaultName'),
          );
        },
      ),
    );
    if (saved && context.mounted) recordsToast(context, d.t('res.saved'));
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final reserve = book.reserveIn(currency);
    final suggestion = book.suggestionIn(currency);
    final button = Align(
      alignment: AlignmentDirectional.centerStart,
      child: FilledButton.icon(
        key: Key('res-edit-$currency'),
        onPressed: () => _edit(context),
        icon: Icon(reserve == null ? Icons.add : Icons.edit_outlined),
        label: Text(d.t(reserve == null ? 'res.create' : 'res.update')),
      ),
    );
    if (reserve == null) {
      return _Card(
        key: Key('res-$currency'),
        title: d.t('res.none', {'currency': currency}),
        subtitle: d.t('res.subtitle'),
        icon: Icons.health_and_safety_outlined,
        children: [
          Text(d.t('res.noneText'), style: muted),
          if (suggestion != null)
            Text(d.t('res.suggest', {
              'months': suggestion.months,
              'amount': formatAmount(suggestion.target, currency),
            })),
          button,
        ],
      );
    }
    return _Card(
      key: Key('res-$currency'),
      title: '${d.t('res.title')} ($currency)',
      subtitle: reserve.mode == 'months'
          ? d.t('res.mode.months', {'months': reserve.months ?? 0})
          : d.t('res.mode.amount'),
      icon: Icons.health_and_safety_outlined,
      children: [
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
          AnumPill(
              label: d.t('res.status.${reserve.status}'),
              tone: reserve.status == 'reached'
                  ? AnumTone.ok
                  : reserve.status == 'empty'
                      ? AnumTone.warn
                      : AnumTone.neutral),
          if (reserve.paused) AnumPill(label: d.t('res.paused')),
        ]),
        _ltr(
            d.t('res.of', {
              'saved': formatAmount(reserve.saved, currency),
              'target': formatAmount(reserve.target, currency),
            }),
            key: Key('res-of-$currency'),
            style: Theme.of(context).textTheme.titleMedium),
        LinearProgressIndicator(
            value: reserve.fraction,
            minHeight: 6,
            semanticsLabel:
                d.t('res.progress', {'percent': reserve.progressPercent ?? 0}),
            semanticsValue: '${reserve.progressPercent ?? 0}%'),
        if (reserve.monthsCovered != null)
          Text(d.t('res.covers', {'months': reserve.monthsCovered!}),
              key: Key('res-covers-$currency')),
        Text(
            reserve.heldIn == 'account' && reserve.accountName != null
                ? d.t('res.heldAccount', {'account': reserve.accountName!})
                : d.t('res.heldGoal'),
            style: muted),
        if (reserve.monthlyContribution != null)
          Text(
              d.t('res.monthlyLine', {
                'amount': formatAmount(reserve.monthlyContribution!, currency)
              }),
              style: muted),
        if (reserve.drifted)
          Text(
              d.t('res.drift', {
                'amount':
                    formatAmount(reserve.recalculatedTarget ?? '0', currency)
              }),
              key: Key('res-drift-$currency'),
              style: TextStyle(color: context.palette.warn)),
        button,
      ],
    );
  }
}

class _SalaryCard extends StatelessWidget {
  const _SalaryCard(
      {required this.personal,
      required this.appUrl,
      required this.openLink,
      this.onEdit});
  final FinancePersonalController personal;
  final String appUrl;
  final MoneyLinkOpener openLink;

  /// Opens the salary profile on the phone; null shows the web link.
  final VoidCallback? onEdit;

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final profile = personal.salary?.profile;
    final List<Widget> body = switch (personal.salaryState) {
      MoneyLoad.idle || MoneyLoad.loading when personal.salary == null => [
          AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: KitStrings.of(context).t('loading'))
        ],
      MoneyLoad.denied => [
          Text(d.t('set.denied'), style: muted),
        ],
      MoneyLoad.error || MoneyLoad.offline => [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: d.t(
                  'set.salary.failed', {'message': personal.salaryError ?? ''}),
              onRetry: personal.loadSalary),
        ],
      _ when profile == null => [Text(d.t('set.salary.none'), style: muted)],
      _ => [
          Text(profile.employer),
          Text(d.t('set.salary.net',
              {'amount': formatAmount(profile.expectedNet, profile.currency)})),
          Text(d.t('set.salary.next', {'date': profile.nextPayOn})),
        ],
    };
    return _Card(
      key: const Key('set-salary'),
      title: d.t('set.salary.title'),
      subtitle: d.t('set.salary.subtitle'),
      icon: Icons.event_available_outlined,
      children: [
        ...body,
        if (onEdit != null)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('set-salary-edit'),
              onPressed: onEdit,
              icon: const Icon(Icons.edit_outlined),
              label: Text(d.t('set.salary.edit')),
            ),
          )
        else
          FinanceWebLink(
              appUrl: appUrl,
              openLink: openLink,
              fragment: 'finance-advisor-salary',
              label: d.t('set.salary.web')),
      ],
    );
  }
}

/// The exception notices (docs/finance.md#exception-notices): which kinds
/// are on, the thresholds, and the form that turns them on or off.
class NoticesCard extends StatelessWidget {
  const NoticesCard({required this.controller, super.key});
  final FinanceDepthController controller;

  Future<void> _edit(BuildContext context, AutomationPolicy policy) async {
    final d = FinanceDepthStrings.of(context);
    final on = policy.notices;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: d.t('not.title'),
        intro: d.t('not.formIntro'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          for (final kind in noticeKinds)
            RecordsInput.toggle(kind, d.t('not.kind.$kind'),
                initial: on.contains(kind)),
          RecordsInput.number('days', d.t('not.days'),
              required: true, initial: '${policy.exceptionDays}'),
          RecordsInput.number('min', d.t('not.min'),
              required: true, initial: policy.uncategorizedMin),
        ],
        onSubmit: (v) async {
          final days = int.tryParse((v['days'] as String?) ?? '');
          if (days == null || days < 1 || days > 90) {
            throw FormatException(d.t('not.daysError'));
          }
          final min = v['min'] as String?;
          if (min == null) throw FormatException(d.t('not.minError'));
          await controller.saveNotices([
            for (final kind in noticeKinds)
              if (v[kind] == true) kind
          ], days: days, uncategorizedMin: min);
        },
      ),
    );
    if (saved && context.mounted) recordsToast(context, d.t('not.saved'));
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final part = controller.automation;
    final policy = part.value;
    return _Card(
      key: const Key('set-notices'),
      title: d.t('not.title'),
      subtitle: d.t('not.subtitle'),
      icon: Icons.notifications_active_outlined,
      children: !part.ready || policy == null
          ? [_partState(part, controller.loadAutomation, d.t('set.denied'))]
          : [
              for (final kind in noticeKinds)
                AnumRow(
                  key: Key('notice-$kind'),
                  title: d.t('not.kind.$kind'),
                  icon: policy.notices.contains(kind)
                      ? Icons.notifications_active_outlined
                      : Icons.notifications_off_outlined,
                  tone: policy.notices.contains(kind) ? AnumTone.ok : null,
                  detail:
                      d.t(policy.notices.contains(kind) ? 'not.on' : 'not.off'),
                ),
              Text(
                  d.t('not.thresholds', {
                    'days': policy.exceptionDays,
                    'amount': policy.uncategorizedMin,
                  }),
                  style: TextStyle(color: context.palette.muted)),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  key: const Key('notices-edit'),
                  onPressed: () => _edit(context, policy),
                  icon: const Icon(Icons.tune),
                  label: Text(d.t('not.edit')),
                ),
              ),
            ],
    );
  }
}

// -- a transaction and its FX section ------------------------------------------

/// One transaction with its card FX section (docs/finance.md#card-fx-and-fees).
/// [row] is shown when the list had it; a suggestion opens it by id only.
class TransactionScreen extends StatelessWidget {
  const TransactionScreen(
      {required this.repository,
      required this.transactionId,
      this.row,
      this.onChanged,
      super.key});
  final FinanceDepthRepository repository;
  final String transactionId;
  final FinanceTransaction? row;
  final Future<void> Function()? onChanged;

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final f = FinanceStrings.of(context);
    final rs = FinanceReviewStrings.of(context);
    final t = row;
    return Scaffold(
      appBar: AppBar(title: Text(t?.description ?? d.t('tx.title'))),
      body: AnumPage(children: [
        if (t != null)
          AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: t.description),
                  RecordsField(d.t('tx.date'), t.postedOn),
                  RecordsField(
                      d.t('tx.amount'), formatAmount(t.amount, t.currency)),
                  RecordsField(
                      d.t('tx.category'), f.t('category.${t.category}')),
                  if (typeLabel(rs, t.transactionType) case final type?)
                    RecordsField(d.t('tx.type'), type),
                  if ((t.notes ?? '').isNotEmpty)
                    RecordsField(d.t('tx.notes'), t.notes!),
                ]),
          ),
        FxSection(
            repository: repository,
            transactionId: transactionId,
            onChanged: onChanged),
        TransferPairSection(
            repository: repository,
            transactionId: transactionId,
            onChanged: onChanged),
      ]),
    );
  }
}

/// The original and billed amounts, the rate with its date and source
/// ("rate not stated" when only the billed amount exists: a rate is never
/// guessed), the bank's FX fee, and Separate the fee or Link the fee row
/// through the audited correction route, with the figures shown.
class FxSection extends StatefulWidget {
  const FxSection(
      {required this.repository,
      required this.transactionId,
      this.onChanged,
      super.key});
  final FinanceDepthRepository repository;
  final String transactionId;
  final Future<void> Function()? onChanged;

  @override
  State<FxSection> createState() => _FxSectionState();
}

class _FxSectionState extends State<FxSection> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  FxDetails? _view;
  bool _busy = false;
  String? _notice;
  bool _noticeError = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = _view == null ? RecordsPhase.loading : _phase);
    try {
      _view = await widget.repository.fx(widget.transactionId);
      _failure = null;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _run(FxDetails v, String action) async {
    final d = FinanceDepthStrings.of(context);
    String money(String? amount, String? currency) =>
        amount == null || currency == null
            ? '-'
            : formatAmount(amount, currency);
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(d.t('fx.$action.title')),
        scrollable: true,
        content: Text(d.t('fx.$action.text', {
          'fee': money(v.fee?.amount, v.fee?.currency),
          'purchase': money(v.purchaseAmount, v.billedCurrency),
          'billed': money(v.billedAmount, v.billedCurrency),
        })),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialog).pop(false),
              child: Text(d.t('fx.cancel'))),
          FilledButton(
              key: const Key('fx-confirm'),
              onPressed: () => Navigator.of(dialog).pop(true),
              child: Text(d.t('fx.$action.button'))),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      final mode =
          await widget.repository.separateFee(v.transactionId, v.planHash);
      _notice = d.t(mode == 'link' ? 'fx.link.done' : 'fx.split.done');
      _noticeError = false;
      await widget.onChanged?.call();
    } on Object catch (error) {
      _notice = RecordsFailure.from(error).message;
      _noticeError = true;
    }
    if (!mounted) return;
    setState(() => _busy = false);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final v = _view;
    if (_phase == RecordsPhase.loading && v == null) {
      return AnumFeedback(
          kind: AnumFeedbackKind.loading, message: d.t('fx.loading'));
    }
    if (_phase == RecordsPhase.denied) {
      return AnumFeedback(
          kind: AnumFeedbackKind.permission, message: d.t('fx.denied'));
    }
    if (_phase == RecordsPhase.error || v == null) {
      return AnumFeedback(
          key: const Key('fx-error'),
          kind: AnumFeedbackKind.error,
          message: d.t('fx.error', {'message': _failure?.message ?? ''}),
          onRetry: _load);
    }
    String money(String? amount, String? currency) =>
        amount == null || currency == null
            ? '-'
            : formatAmount(amount, currency);
    final action = v.action;
    final fee = v.fee;
    final transfer = v.transfer;
    final children = <Widget>[
      AnumHeader(
          title: d.t(transfer != null ? 'fx.transferTitle' : 'fx.title')),
    ];
    if (!v.shows) {
      children
          .add(Text(d.t('fx.none'), key: const Key('fx-none'), style: muted));
    }
    if (v.foreign) {
      children.addAll([
        RecordsField(
            d.t('fx.original'), money(v.originalAmount, v.originalCurrency)),
        RecordsField(d.t('fx.billed'), money(v.billedAmount, v.billedCurrency)),
        RecordsField(
            d.t('fx.rate'),
            v.rateNotStated
                ? d.t('fx.rate.notStated')
                : '${plainRate(v.rate)} (${d.t('fx.rate.${v.rateKind}')})'),
        if (!v.rateNotStated)
          RecordsField(
              d.t('fx.rateDate'),
              [
                v.rateDate ?? '-',
                if (v.rateSource != null) d.label('fx.source', v.rateSource!),
              ].join(' · ')),
        if (fee != null)
          RecordsField(
              d.t('fx.fee'),
              [
                money(fee.amount, fee.currency) +
                    (fee.markupPercent == null
                        ? ''
                        : ' (${fee.markupPercent}%)'),
                d.label('fx.feeSource', fee.source),
              ].join(' · ')),
        if (fee != null && fee.includedInBilled)
          RecordsField(
              d.t('fx.purchase'), money(v.purchaseAmount, v.billedCurrency)),
      ]);
    }
    if (!v.foreign && fee != null) {
      children.add(Text(d.t('fx.feeOnly', {
        'amount': money(fee.amount, fee.currency),
      })));
    }
    if (v.notes.contains('fee_not_provably_included')) {
      children.add(Text(d.t('fx.notIncluded'), style: muted));
    }
    if (v.split == 'done') {
      children.add(Align(
          alignment: AlignmentDirectional.centerStart,
          child: AnumPill(label: d.t('fx.separated'), tone: AnumTone.ok)));
    }
    if (transfer != null) {
      children.addAll([
        RecordsField(d.t('fx.transfer.sent'),
            money(transfer.outAmount, transfer.outCurrency)),
        RecordsField(d.t('fx.transfer.received'),
            money(transfer.inAmount, transfer.inCurrency)),
        RecordsField(
            d.t('fx.transfer.realised'),
            '1 ${transfer.outCurrency} = ${plainRate(transfer.realisedRate)} '
            '${transfer.inCurrency}'),
        if (transfer.valuationRate != null) ...[
          RecordsField(
              d.t('fx.transfer.valuation'),
              [
                plainRate(transfer.valuationRate),
                transfer.valuationDate ?? '',
                transfer.valuationSource ?? '',
              ].where((p) => p.isNotEmpty).join(' · ')),
          RecordsField(d.t('fx.transfer.difference'),
              '${money(transfer.difference, transfer.inCurrency)} (${d.t('fx.transfer.${transfer.tone}')})'),
        ] else
          RecordsField(
              d.t('fx.transfer.difference'), d.t('fx.transfer.noValuation')),
        Text(d.t('fx.transfer.policy'), style: muted),
      ]);
    }
    if (_notice != null) {
      children.add(Semantics(
        liveRegion: true,
        child: Text(_notice!,
            key: const Key('fx-notice'),
            style: TextStyle(
                color:
                    _noticeError ? context.palette.stop : context.palette.ok)),
      ));
    }
    if (action != null) {
      children.add(Align(
        alignment: AlignmentDirectional.centerStart,
        child: FilledButton.icon(
          key: Key('fx-$action'),
          onPressed: _busy ? null : () => _run(v, action),
          icon: const Icon(Icons.content_cut),
          label: Text(d.t('fx.$action.button')),
        ),
      ));
    }
    if (fee != null && !v.canCorrect && v.split != 'done') {
      children.add(Text(d.t('fx.smsCorrect'), style: muted));
    }
    return AnumSurface(
      key: const Key('fx-section'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (var i = 0; i < children.length; i++) ...[
          if (i > 0) const SizedBox(height: AnumSpacing.xs),
          children[i],
        ],
      ]),
    );
  }
}

/// The own transfer this transaction belongs to and an Unpair button
/// (docs/finance.md#own-transfers). Unpairing voids the pair's link; both rows
/// keep their categories and the pair is offered again under Find transfers.
class TransferPairSection extends StatefulWidget {
  const TransferPairSection(
      {required this.repository,
      required this.transactionId,
      this.onChanged,
      super.key});
  final FinanceDepthRepository repository;
  final String transactionId;
  final Future<void> Function()? onChanged;

  @override
  State<TransferPairSection> createState() => _TransferPairSectionState();
}

class _TransferPairSectionState extends State<TransferPairSection> {
  TransferPairOf? _pair;
  bool _failed = false, _busy = false;
  String? _notice;
  bool _noticeError = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final next = await widget.repository.transferPair(widget.transactionId);
      if (mounted) {
        setState(() {
          _pair = next;
          _failed = false;
        });
      }
    } on Object {
      if (mounted) setState(() => _failed = true);
    }
  }

  Future<void> _unpair(TransferPairOf pair) async {
    final d = FinanceDepthStrings.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(d.t('tp.confirmTitle')),
        scrollable: true,
        content: Text(d.t('tp.confirmText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialog).pop(false),
              child: Text(d.t('fx.cancel'))),
          FilledButton(
              key: const Key('transfer-unpair-confirm'),
              onPressed: () => Navigator.of(dialog).pop(true),
              child: Text(d.t('tp.unpair'))),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await widget.repository.unpair(pair.linkId!, pair.linkVersion);
      _notice = d.t('tp.done');
      _noticeError = false;
      await widget.onChanged?.call();
    } on Object catch (error) {
      _notice = RecordsFailure.from(error).message;
      _noticeError = true;
    }
    await _load();
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final d = FinanceDepthStrings.of(context);
    final p = context.palette;
    final pair = _pair;
    if (_failed) {
      return Row(children: [
        Expanded(
            child: Text(d.t('tp.error'), style: TextStyle(color: p.muted))),
        TextButton(onPressed: _load, child: Text(d.t('tp.retry'))),
      ]);
    }
    if (pair == null || !pair.paired || pair.linkId == null) {
      return _notice == null
          ? const SizedBox.shrink()
          : Text(_notice!,
              style: TextStyle(color: _noticeError ? p.stop : p.ok));
    }
    final amount = pair.otherAmount != null && pair.otherCurrency != null
        ? formatAmount(pair.otherAmount!, pair.otherCurrency!)
        : '-';
    return AnumSurface(
      key: const Key('transfer-pair'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
            title: d.t('tp.title'),
            subtitle: d.t(pair.role == 'out' ? 'tp.sentTo' : 'tp.receivedFrom',
                {'account': pair.otherAccount ?? d.t('tp.unknownAccount')}),
          ),
          RecordsField(d.t('tp.otherDay'), pair.otherDay ?? '-'),
          RecordsField(d.t('tp.otherAmount'), amount),
          if ((pair.otherText ?? '').isNotEmpty)
            RecordsField(d.t('tp.otherText'), pair.otherText!),
          if (_notice != null)
            Text(_notice!,
                style: TextStyle(color: _noticeError ? p.stop : p.ok)),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('transfer-unpair'),
              onPressed: _busy ? null : () => _unpair(pair),
              icon: const Icon(Icons.link_off),
              label: Text(d.t('tp.unpair')),
            ),
          ),
        ],
      ),
    );
  }
}
