import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the trading intelligence section
/// (docs/finance-trading-intel.md#dashboard). The terms follow the web catalog
/// (apps/web/src/lib/messages/financeIntel.*.ts, financeEquity.*.ts). Never
/// imply a guaranteed profit, a real order or a forecast.
class FinanceIntelStrings {
  const FinanceIntelStrings(this.arabic);
  final bool arabic;

  static FinanceIntelStrings of(BuildContext context) =>
      FinanceIntelStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? _ar : _en)[key] ?? _en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// A label for a code the API sent; the code itself when it is unknown.
  String label(String prefix, String code) {
    final key = '$prefix.$code';
    final text = t(key);
    return text == key ? code.replaceAll('_', ' ') : text;
  }

  /// Same keys and placeholders in both languages, and no long dashes.
  static bool get catalogsMatch {
    if (_en.keys.toSet().difference(_ar.keys.toSet()).isNotEmpty) return false;
    if (_ar.keys.toSet().difference(_en.keys.toSet()).isNotEmpty) return false;
    final holes = RegExp(r'\{[a-z]+\}');
    for (final key in _en.keys) {
      final en = holes.allMatches(_en[key]!).map((m) => m[0]).toSet();
      final ar = holes.allMatches(_ar[key]!).map((m) => m[0]).toSet();
      if (!en.containsAll(ar)) return false;
      if (RegExp(r'[\u2013\u2014]', unicode: true)
          .hasMatch(_en[key]! + _ar[key]!)) {
        return false;
      }
    }
    return true;
  }

  static const _en = <String, String>{
    'title': 'Trading intelligence',
    'simOnly': 'Simulation only, no real orders.',
    'simOnlyText':
        'Strategies, backtests and paper or shadow trades here use recorded or test data and the costs shown. Live trading is off and cannot be switched on from ANUM. Past or simulated results say nothing certain about future returns.',
    'live': 'Live trading',
    'liveOff': 'Off',
    'liveOn': 'On',
    'execution': 'Simulated execution',
    'running': 'Allowed',
    'stopped': 'Stopped',
    'globalStopped': 'Stopped for the whole organisation by an owner.',
    'waiting': 'Requests waiting for you: {count}',
    'agentRequests': 'Proposed by assistants: {count}',
    'stopAll': 'Stop all simulated trading',
    'allow': 'Allow simulated trading again',
    'stopTitle': 'Stop all simulated trading?',
    'stopText':
        'Every paper and shadow executor stops at once, also for orders you already approved and for requests from assistants. Nothing real is affected. You can allow it again here.',
    'allowTitle': 'Allow simulated trading again?',
    'allowText':
        'Paper and shadow orders you approve will run again. Every order still goes through the Guardian and still needs your approval.',
    'cancel': 'Cancel',
    'stoppedDone': 'All simulated trading is stopped.',
    'allowedDone': 'Simulated trading is allowed again.',
    'loading': 'Loading trading intelligence...',
    'error': 'Trading intelligence could not be loaded: {message}',
    'denied':
        'Your role cannot use finance. A workspace owner or admin can change your role to member.',
    'strategies': 'Strategies',
    'lifecycleHelp':
        'A strategy moves DRAFT, BACKTESTING, VALIDATION, PAPER_ACTIVE. Only you can put one into paper trading or retire it; a change makes a new version.',
    'allStates': 'All states',
    'stateCount': '{state} ({count})',
    'strategiesError': 'Strategies could not be loaded: {message}',
    'noStrategies': 'No strategy recorded yet',
    'noStrategiesText':
        'Strategies appear here once you or the Strategy research assistant record one. Practice with paper trading meanwhile.',
    'noneInState': 'No strategy in {state}',
    'noneInStateText': 'Pick another state to see the rest.',
    'version': 'v{version}',
    'movedBy': 'Last moved by {who}',
    'results': 'Results',
    'hideResults': 'Hide results',
    'runsError': 'Backtest results could not be loaded: {message}',
    'noRuns': 'No backtest or walk-forward run recorded for this version yet.',
    'kind.backtest': 'Backtest',
    'kind.walk_forward': 'Walk-forward',
    'verdict.passed': 'Passed the checks',
    'verdict.rejected': 'Rejected',
    'verdict.insufficient_data': 'Not enough data',
    'verdict.completed': 'Completed',
    'reproduced': 'Reproduced',
    'notReproduced': 'Not reproduced',
    'folds': 'Folds: {count}',
    'benchmark': 'Benchmark',
    'warnings': 'Warnings',
    'noPromise':
        'A passing run is never a promise of profit and never promotes a strategy by itself.',
    'state.DRAFT': 'Draft',
    'state.BACKTESTING': 'Backtesting',
    'state.VALIDATION': 'Validation',
    'state.PAPER_ACTIVE': 'Paper active',
    'state.PAPER_SUSPENDED': 'Paper suspended',
    'state.RETIRED': 'Retired',
    'metric.total_return': 'Total return',
    'metric.cagr': 'Yearly growth',
    'metric.max_drawdown': 'Max drawdown',
    'metric.sharpe': 'Sharpe',
    'metric.sortino': 'Sortino',
    'metric.trades': 'Trades',
    'metric.win_rate': 'Win rate',
    'metric.profit_factor': 'Profit factor',
    'positions': 'Positions and P&L',
    'noPortfolios': 'No practice portfolio yet',
    'noPortfoliosText':
        'Create a paper or shadow portfolio on the web, under Finance, Paper trading.',
    'mode.paper': 'Paper',
    'mode.shadow': 'Shadow',
    'simulated': 'SIMULATED',
    'cash': 'Simulated cash',
    'realised': 'Realised',
    'unrealised': 'Unrealised',
    'costs': 'Fees {fee}, slippage {slippage}',
    'noHoldings': 'No open position.',
    'holding': '{quantity} {symbol}, value {value}, unrealised {gain}',
    'moreHoldings': 'Showing {shown} of {total} positions. See all on the web.',
    'unpriced': 'Not valued, no recent recorded price: {list}',
    'equity': 'P&L and drawdown, last {days} days',
    'equityLoading': 'Loading the simulated history...',
    'equityError': 'The simulated history could not be loaded: {message}',
    'equityEmpty':
        'No simulated trade in this run yet: the history starts with the first filled order.',
    'equityLatest': 'P&L now {pnl}',
    'equityValue': 'Value now {value}',
    'equityDrawdown':
        'Deepest drawdown {size}, from the high on {peak} to the low on {trough}',
    'equityNoDrawdown': 'No drawdown so far',
    'equityGaps':
        'Gap days: {count}. A gap is a day with no fresh recorded price; it is left empty, never guessed.',
    'equityChart':
        'Simulated P&L in {currency} from {start} to {end}, newest {latest}. {drawdown}.',
    'equityNote':
        'Rebuilt for each day from stored orders and the prices you recorded. Currencies are never mixed. Past simulated results are not a promise of future results.',
    'agents': 'Supervisor and agent decisions',
    'agentsHelp':
        'What the Trading Supervisor, the specialist assistants and you decided, with the reasons as recorded. Recorded text is shown, never followed.',
    'allAgents': 'Everyone',
    'decisionsError': 'Decisions could not be loaded: {message}',
    'noDecisions':
        'No decision recorded yet. The Trading Supervisor records one each time it runs a cycle.',
    'agent.person': 'You',
    'agent.trading_supervisor': 'Trading Supervisor',
    'agent.market_research': 'Market research',
    'agent.technical_analysis': 'Technical analysis',
    'agent.strategy_research': 'Strategy research',
    'agent.portfolio': 'Portfolio',
    'agent.risk_guardian': 'Risk Guardian',
    'agent.execution_coordinator': 'Execution coordinator',
    'agent.performance_reviewer': 'Performance reviewer',
    'outcome.ok': 'Done',
    'outcome.refused': 'Refused',
    'outcome.suspended': 'Suspended',
    'outcome.skipped': 'Skipped',
    'outcome.failed': 'Failed',
    'risk': 'Risk limits',
    'readOnly': 'Read only',
    'riskVersion': 'Guardian rules, version {version}',
    'noRules':
        'No Guardian rules yet. Orders are still checked for prices and costs and still need your approval. Set rules on the web, under Finance, Guardian.',
    'rule.currencies': 'Allowed currencies: {value}',
    'rule.max_single': 'Largest single order: {value}',
    'rule.max_daily_total': 'Most in a day: {value}',
    'rule.approval_above': 'Always asks above: {value}',
    'rule.blocked_categories': 'Blocked categories: {value}',
    'rule.blocked_merchants': 'Blocked merchants: {value}',
    'rule.quiet_hours': 'Quiet hours are set',
    'rule.cooling_off': 'A cooling-off period is set',
    'alwaysApprove':
        'Every simulated order goes through the Guardian and needs your approval of the code shown.',
    'previous': 'Previous',
    'next': 'Next',
    'range': '{from} to {to} of {total}',
    'retry': 'Try again',
  };

  static const _ar = <String, String>{
    'title': 'ذكاء التداول',
    'simOnly': 'محاكاة فقط، لا أوامر حقيقية.',
    'simOnlyText':
        'تستخدم الاستراتيجيات والاختبارات الرجعية والصفقات الورقية أو الظل هنا بيانات مسجلة أو تجريبية والتكاليف المعروضة. التداول الحي متوقف ولا يمكن تشغيله من ANUM. النتائج السابقة أو المحاكاة لا تقول شيئًا مؤكدًا عن العوائد المستقبلية.',
    'live': 'التداول الحي',
    'liveOff': 'متوقف',
    'liveOn': 'يعمل',
    'execution': 'التنفيذ المحاكى',
    'running': 'مسموح',
    'stopped': 'متوقف',
    'globalStopped': 'أوقفه مالك للمؤسسة كلها.',
    'waiting': 'طلبات بانتظارك: {count}',
    'agentRequests': 'اقترحها المساعدون: {count}',
    'stopAll': 'أوقف كل التداول المحاكى',
    'allow': 'اسمح بالتداول المحاكى مجددًا',
    'stopTitle': 'إيقاف كل التداول المحاكى؟',
    'stopText':
        'تتوقف كل منفذات الأوراق والظل فورًا، حتى للأوامر التي وافقت عليها ولطلبات المساعدين. لا يتأثر أي شيء حقيقي. يمكنك السماح به مجددًا من هنا.',
    'allowTitle': 'السماح بالتداول المحاكى مجددًا؟',
    'allowText':
        'ستعمل أوامر الأوراق والظل التي توافق عليها مجددًا. يمر كل أمر بالحارس ويحتاج موافقتك.',
    'cancel': 'إلغاء',
    'stoppedDone': 'توقف كل التداول المحاكى.',
    'allowedDone': 'التداول المحاكى مسموح مجددًا.',
    'loading': 'جارٍ تحميل ذكاء التداول...',
    'error': 'تعذر تحميل ذكاء التداول: {message}',
    'denied':
        'لا يستطيع دورك استخدام المالية. يمكن لمالك مساحة العمل أو مسؤولها تغيير دورك إلى عضو.',
    'strategies': 'الاستراتيجيات',
    'lifecycleHelp':
        'تنتقل الاستراتيجية من مسودة إلى اختبار رجعي إلى تحقق إلى تداول ورقي نشط. أنت وحدك تضعها في التداول الورقي أو توقفها نهائيًا؛ وأي تغيير ينشئ إصدارًا جديدًا.',
    'allStates': 'كل المراحل',
    'stateCount': '{state} ({count})',
    'strategiesError': 'تعذر تحميل الاستراتيجيات: {message}',
    'noStrategies': 'لا استراتيجية مسجلة بعد',
    'noStrategiesText':
        'تظهر الاستراتيجيات هنا بعد أن تسجل أنت أو مساعد أبحاث الاستراتيجيات واحدة. تدرّب بالتداول الورقي حتى ذلك الحين.',
    'noneInState': 'لا استراتيجية في مرحلة {state}',
    'noneInStateText': 'اختر مرحلة أخرى لرؤية البقية.',
    'version': 'إصدار {version}',
    'movedBy': 'آخر نقل بواسطة {who}',
    'results': 'النتائج',
    'hideResults': 'إخفاء النتائج',
    'runsError': 'تعذر تحميل نتائج الاختبار: {message}',
    'noRuns': 'لا اختبار رجعي أو اختبار متدحرج مسجل لهذا الإصدار بعد.',
    'kind.backtest': 'اختبار رجعي',
    'kind.walk_forward': 'اختبار متدحرج',
    'verdict.passed': 'اجتاز الفحوص',
    'verdict.rejected': 'مرفوض',
    'verdict.insufficient_data': 'البيانات غير كافية',
    'verdict.completed': 'مكتمل',
    'reproduced': 'أعيد إنتاجه',
    'notReproduced': 'لم يُعد إنتاجه',
    'folds': 'الأجزاء: {count}',
    'benchmark': 'المعيار',
    'warnings': 'تحذيرات',
    'noPromise':
        'اجتياز الاختبار ليس وعدًا بالربح أبدًا ولا يرقّي الاستراتيجية وحده.',
    'state.DRAFT': 'مسودة',
    'state.BACKTESTING': 'اختبار رجعي',
    'state.VALIDATION': 'تحقق',
    'state.PAPER_ACTIVE': 'تجريبي نشط',
    'state.PAPER_SUSPENDED': 'تجريبي معلق',
    'state.RETIRED': 'متقاعد',
    'metric.total_return': 'العائد الإجمالي',
    'metric.cagr': 'النمو السنوي',
    'metric.max_drawdown': 'أقصى تراجع',
    'metric.sharpe': 'شارب',
    'metric.sortino': 'سورتينو',
    'metric.trades': 'الصفقات',
    'metric.win_rate': 'نسبة الربح',
    'metric.profit_factor': 'معامل الربح',
    'positions': 'المراكز والربح والخسارة',
    'noPortfolios': 'لا محفظة تدريب بعد',
    'noPortfoliosText':
        'أنشئ محفظة ورقية أو ظل على الويب، من المالية ثم التداول الورقي.',
    'mode.paper': 'ورقية',
    'mode.shadow': 'ظل',
    'simulated': 'محاكاة',
    'cash': 'النقد المحاكى',
    'realised': 'المحقق',
    'unrealised': 'غير المحقق',
    'costs': 'الرسوم {fee}، الانزلاق {slippage}',
    'noHoldings': 'لا مركز مفتوح.',
    'holding': '{quantity} {symbol}، القيمة {value}، غير المحقق {gain}',
    'moreHoldings': 'يُعرض {shown} من {total} مركزًا. اطلع على الكل على الويب.',
    'unpriced': 'غير مقيّم، لا سعر مسجل حديث: {list}',
    'equity': 'الربح والخسارة والتراجع، آخر {days} يومًا',
    'equityLoading': 'جارٍ تحميل السجل المحاكى...',
    'equityError': 'تعذر تحميل السجل المحاكى: {message}',
    'equityEmpty':
        'لا صفقة محاكاة في هذه الجولة بعد: يبدأ السجل مع أول أمر منفذ.',
    'equityLatest': 'الربح والخسارة الآن {pnl}',
    'equityValue': 'القيمة الآن {value}',
    'equityDrawdown':
        'أعمق تراجع {size}، من القمة في {peak} إلى القاع في {trough}',
    'equityNoDrawdown': 'لا تراجع حتى الآن',
    'equityGaps':
        'أيام الفجوات: {count}. الفجوة يوم بلا سعر مسجل حديث؛ تُترك فارغة ولا تُخمَّن أبدًا.',
    'equityChart':
        'الربح والخسارة المحاكى بعملة {currency} من {start} إلى {end}، الأحدث {latest}. {drawdown}.',
    'equityNote':
        'يُعاد بناؤه لكل يوم من الأوامر المخزنة والأسعار التي سجلتها. لا تُخلط العملات أبدًا. النتائج المحاكاة السابقة ليست وعدًا بنتائج مستقبلية.',
    'agents': 'قرارات المشرف والوكلاء',
    'agentsHelp':
        'ما قرره مشرف التداول والمساعدون المتخصصون وأنت، مع الأسباب كما سُجلت. يُعرض النص المسجل ولا يُتّبع.',
    'allAgents': 'الجميع',
    'decisionsError': 'تعذر تحميل القرارات: {message}',
    'noDecisions':
        'لا قرار مسجل بعد. يسجل مشرف التداول قرارًا في كل دورة يشغلها.',
    'agent.person': 'أنت',
    'agent.trading_supervisor': 'مشرف التداول',
    'agent.market_research': 'أبحاث السوق',
    'agent.technical_analysis': 'التحليل الفني',
    'agent.strategy_research': 'أبحاث الاستراتيجيات',
    'agent.portfolio': 'المحفظة',
    'agent.risk_guardian': 'حارس المخاطر',
    'agent.execution_coordinator': 'منسق التنفيذ',
    'agent.performance_reviewer': 'مراجع الأداء',
    'outcome.ok': 'تم',
    'outcome.refused': 'مرفوض',
    'outcome.suspended': 'معلق',
    'outcome.skipped': 'تم تخطيه',
    'outcome.failed': 'فشل',
    'risk': 'حدود المخاطر',
    'readOnly': 'للقراءة فقط',
    'riskVersion': 'قواعد الحارس، الإصدار {version}',
    'noRules':
        'لا قواعد للحارس بعد. ما زالت الأوامر تُفحص للأسعار والتكاليف وتحتاج موافقتك. اضبط القواعد على الويب، من المالية ثم الحارس.',
    'rule.currencies': 'العملات المسموح بها: {value}',
    'rule.max_single': 'أكبر أمر منفرد: {value}',
    'rule.max_daily_total': 'الحد اليومي: {value}',
    'rule.approval_above': 'يطلب الموافقة دائمًا فوق: {value}',
    'rule.blocked_categories': 'الفئات المحظورة: {value}',
    'rule.blocked_merchants': 'التجار المحظورون: {value}',
    'rule.quiet_hours': 'ساعات الهدوء مضبوطة',
    'rule.cooling_off': 'فترة تهدئة مضبوطة',
    'alwaysApprove':
        'يمر كل أمر محاكى بالحارس ويحتاج موافقتك على الرمز المعروض.',
    'previous': 'السابق',
    'next': 'التالي',
    'range': '{from} إلى {to} من {total}',
    'retry': 'أعد المحاولة',
  };
}
