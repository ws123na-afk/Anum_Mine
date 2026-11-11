import 'package:flutter/material.dart';

import '../records_kit/records_export.dart';
import 'finance_advisor_screens.dart';
import 'finance_controller.dart';
import 'finance_income_screens.dart';
import 'finance_keyword_rules.dart';
import 'finance_loans_screens.dart';
import 'finance_more_strings.dart';
import 'finance_recon_screens.dart';
import 'finance_salary_screen.dart';
import 'finance_tags_screens.dart';

/// The Finance app bar menu (docs/finance.md#clients): the pages the phone
/// gained for parity with the web, each its own screen over the same API.

enum FinanceMorePage {
  advisor,
  plan,
  salary,
  recon,
  loans,
  payslips,
  home,
  tags,
  fx,
  keywords,
}

class FinanceMoreMenu extends StatelessWidget {
  const FinanceMoreMenu(
      {required this.controller,
      this.saver = const SystemExportSaver(),
      super.key});
  final FinanceController controller;
  final ExportSaver saver;

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    return PopupMenuButton<FinanceMorePage>(
      key: const Key('finance-more'),
      tooltip: m.t('menu'),
      icon: const Icon(Icons.more_vert),
      onSelected: (page) =>
          openFinanceMore(context, controller, page, saver: saver),
      itemBuilder: (_) => [
        for (final page in FinanceMorePage.values)
          if (page != FinanceMorePage.keywords || controller.depth != null)
            PopupMenuItem(
              key: Key('finance-more-${page.name}'),
              value: page,
              child: Text(m.t('menu.${page.name}')),
            ),
      ],
    );
  }
}

Future<void> openFinanceMore(
    BuildContext context, FinanceController controller, FinanceMorePage page,
    {ExportSaver saver = const SystemExportSaver()}) {
  final more = controller.more;
  if (more == null) return Future.value();
  final depth = controller.depth;
  if (page == FinanceMorePage.keywords && depth == null) return Future.value();
  return Navigator.of(context).push(MaterialPageRoute<void>(
    builder: (_) => switch (page) {
      FinanceMorePage.advisor => AdvisorScreen(repository: more),
      FinanceMorePage.plan => PlanScreen(repository: more),
      FinanceMorePage.salary => SalaryProfileScreen(
          repository: more, onChanged: () => controller.personal?.loadSalary()),
      FinanceMorePage.recon =>
        ReconciliationScreen(repository: more, saver: saver),
      FinanceMorePage.loans => BankLoansScreen(repository: more),
      FinanceMorePage.payslips => PayslipsScreen(repository: more),
      FinanceMorePage.home => SendHomeScreen(repository: more),
      FinanceMorePage.tags => TagsScreen(repository: more),
      FinanceMorePage.fx => FxBalancesScreen(repository: more),
      FinanceMorePage.keywords => KeywordRulesScreen(controller: depth!),
    },
  ));
}
