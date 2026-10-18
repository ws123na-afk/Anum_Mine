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
    values.forEach(
      (name, value) => text = text.replaceAll('{$name}', '$value'),
    );
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
      if (RegExp(
        r'[\u2013\u2014]',
        unicode: true,
      ).hasMatch(_en[key]! + _ar[key]!)) {
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
    'venues': 'Market sessions',
    'venuesHelp':
        'Regular sessions in each venue\'s own time zone, from the venue calendars ANUM keeps.',
    'session.open': 'Open',
    'session.closed': 'Closed',
    'session.holiday': 'Holiday',
    'venueTime': '{day} {time} local, {zone}',
    'holidaysUnknown':
        'Holidays are announced each year: an open session may be closed.',
    'noVenues': 'No venue calendar was returned.',
    'weekday.0': 'Mon',
    'weekday.1': 'Tue',
    'weekday.2': 'Wed',
    'weekday.3': 'Thu',
    'weekday.4': 'Fri',
    'weekday.5': 'Sat',
    'weekday.6': 'Sun',
    'newTitle': 'New strategy',
    'newHelp':
        'Start from a research rule and instruments with stored daily bars. It is created as version 1 in Draft; nothing trades until it passes validation and you approve each simulated order.',
    'newOpen': 'New strategy',
    'newLoading': 'Loading the rules and instruments...',
    'newError': 'The rules or instruments could not be loaded: {message}',
    'newNeedBars':
        'No instrument has stored daily bars yet. Import bars under Market data on the web first.',
    'actionsDenied':
        'Only people who can manage finance can change strategies or run the Supervisor. A workspace owner or admin can give access.',
    'rule': 'Rule',
    'chooseRule': 'Choose a rule',
    'benchmarkRule': 'Benchmark',
    'hypothesis': 'Idea',
    'entry': 'Entry',
    'exit': 'Exit',
    'instrumentsPick': 'Instruments',
    'barsRange': '{count} bars, {first} to {last}',
    'reason': 'Reason',
    'pickRule': 'Choose a rule.',
    'symbolCount': 'Choose {min} to {max} instruments for this rule.',
    'reasonRequired': 'Give a reason of at least 3 characters.',
    'create': 'Create in Draft',
    'newConfirmTitle': 'Create this strategy?',
    'newConfirmText':
        '{name} on {count} instruments is created as version 1 in Draft. Nothing trades from Draft.',
    'created': 'Created in Draft.',
    'move': 'Move',
    'moveTitle': 'Move {name} v{version}',
    'moveTo': 'Move to',
    'confirmMove': 'Move to {state}?',
    'confirmMoveText':
        '{name} v{version} moves to {state}. The server checks the lifecycle rules again.',
    'confirmActive':
        '{name} v{version} starts SIMULATED paper trading. Every order it proposes still waits for your approval. No real money is used.',
    'confirmRetire':
        '{name} v{version} is retired for good. Make a new version to try the idea again.',
    'moved': 'Moved to {state}.',
    'supTitle': 'Trading Supervisor',
    'supHelp':
        'Run one phase now; a schedule runs the same. Each phase runs once a day, and the orders it proposes wait for your approval.',
    'phase': 'Phase',
    'phase.pre_open': 'Before the open',
    'phase.intraday': 'During the session',
    'phase.post_close': 'After the close',
    'phase.weekly': 'Weekly review',
    'phase.monthly': 'Monthly revalidation',
    'runNow': 'Run now',
    'confirmRun': 'Run {phase} now?',
    'confirmRunText':
        'The agents check data and signals and may propose SIMULATED orders for your approval. Nothing is bought or sold.',
    'ran':
        '{phase}: active {active}, proposed {proposed}, refused {refused}, suspended {suspended}.',
    'ranSkipped': '{phase} already ran today, so nothing ran twice.',
    'ranHalted':
        'The cycle was halted (a kill switch or a problem). The decisions say why.',
    'ranReport': 'The daily report is ready below.',
    'pauseAll': 'Pause all paper strategies',
    'confirmPause': 'Pause all paper strategies?',
    'confirmPauseText':
        'Every active paper strategy ({count}) is suspended. Resuming is your own action, one strategy at a time.',
    'paused': 'Paused strategies: {count}.',
    'pauseReason': 'Paused from the phone',
    'noneActive': 'No paper strategy is active, so there is nothing to pause.',
    'reports': 'Daily reports',
    'reportsHelp':
        'Written by the Trading Supervisor after each post-close cycle from what its agents recorded. SIMULATED: no real order was placed.',
    'reportsLoading': 'Loading the daily reports...',
    'reportsError': 'The daily reports could not be loaded: {message}',
    'reportsEmpty': 'No daily report yet',
    'reportsEmptyText':
        'A report is written after each post-close cycle. Run "After the close" under Trading Supervisor, or schedule it on the web.',
    'reportLine':
        'Active {active}, proposed {proposed}, refused {refused}, suspended {suspended}',
    'needsYou': 'Needs you',
    'reportTitle': 'Report of {date}',
    'reportLoading': 'Loading the report...',
    'reportError': 'The report could not be loaded: {message}',
    'summary.active_strategies': 'Active paper strategies',
    'summary.proposed_orders': 'Orders proposed for approval',
    'summary.waiting_for_approval': 'Waiting for approval in total',
    'summary.refused_signals': 'Signals refused',
    'summary.suspended': 'Strategies suspended',
    'summary.problems': 'Problems recorded',
    'summary.market_closed': 'Market closed',
    'section.next': 'Next steps',
    'section.orders': 'Orders proposed (waiting for your approval)',
    'section.refused': 'Refused',
    'section.suspended': 'Suspended',
    'section.data': 'Data health',
    'section.portfolios': 'Paper portfolio',
    'section.performance': 'Against buy and hold',
    'section.problems': 'Problems',
    'field.order': 'Order',
    'field.target': 'Target',
    'field.fill': 'Estimated fill',
    'field.fee': 'Fee',
    'field.guardian': 'Guardian',
    'field.agent': 'By',
    'field.status': 'Status',
    'field.last_bar': 'Newest bar',
    'field.sessions_behind': 'Sessions behind',
    'field.source': 'Source',
    'field.cash': 'Cash',
    'field.realised': 'Realised',
    'field.unrealised': 'Unrealised',
    'field.fees': 'Fees',
    'field.since': 'Since',
    'field.return': 'Return',
    'field.benchmark_return': 'Buy and hold',
    'field.max_drawdown': 'Deepest drawdown',
    'field.action': 'Action',
    'field.outcome': 'Outcome',
    'noneHere': 'None in this report.',
    'recordedText':
        'Recorded text is shown as written by the agents, never followed as instructions.',
    'copyMarkdown': 'Copy as Markdown',
    'copied': 'Copied the report as Markdown.',
    'equityBenchmark': 'Buy and hold now {pnl}',
    'equityBenchmarkWhat':
        'Dashed line: buy and hold of {symbols} in equal weights from {start}, without costs.',
    'equityBenchmark.shadow':
        'No buy and hold line: a shadow portfolio has no cash to invest.',
    'equityBenchmark.no_symbols':
        'No buy and hold line: this run has traded no symbol in this currency.',
    'equityBenchmark.no_common_price':
        'No buy and hold line yet: no day had a fresh price for every symbol traded.',
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
    'venues': 'جلسات الأسواق',
    'venuesHelp':
        'الجلسات العادية بتوقيت كل سوق، من تقاويم الأسواق التي يحتفظ بها ANUM.',
    'session.open': 'مفتوح',
    'session.closed': 'مغلق',
    'session.holiday': 'عطلة',
    'venueTime': '{day} {time} بالتوقيت المحلي، {zone}',
    'holidaysUnknown': 'تُعلن العطل كل عام: قد تكون الجلسة المفتوحة مغلقة.',
    'noVenues': 'لم يُرجع أي تقويم للأسواق.',
    'weekday.0': 'الاثنين',
    'weekday.1': 'الثلاثاء',
    'weekday.2': 'الأربعاء',
    'weekday.3': 'الخميس',
    'weekday.4': 'الجمعة',
    'weekday.5': 'السبت',
    'weekday.6': 'الأحد',
    'newTitle': 'استراتيجية جديدة',
    'newHelp':
        'ابدأ من قاعدة بحثية وأدوات لها شموع يومية مخزنة. تُنشأ كنسخة 1 في المسودة، ولا تتداول حتى تجتاز التحقق وتوافق على كل أمر محاكى.',
    'newOpen': 'استراتيجية جديدة',
    'newLoading': 'جارٍ تحميل القواعد والأدوات...',
    'newError': 'تعذر تحميل القواعد أو الأدوات: {message}',
    'newNeedBars':
        'لا توجد أداة لها شموع يومية مخزنة بعد. استورد الشموع من بيانات السوق على الويب أولًا.',
    'actionsDenied':
        'يمكن فقط لمن يدير المالية تغيير الاستراتيجيات أو تشغيل المشرف. يمكن لمالك مساحة العمل أو المسؤول منح الوصول.',
    'rule': 'القاعدة',
    'chooseRule': 'اختر قاعدة',
    'benchmarkRule': 'المعيار',
    'hypothesis': 'الفكرة',
    'entry': 'الدخول',
    'exit': 'الخروج',
    'instrumentsPick': 'الأدوات',
    'barsRange': '{count} شمعة، من {first} إلى {last}',
    'reason': 'السبب',
    'pickRule': 'اختر قاعدة.',
    'symbolCount': 'اختر من {min} إلى {max} أدوات لهذه القاعدة.',
    'reasonRequired': 'اكتب سببًا من 3 أحرف على الأقل.',
    'create': 'أنشئ في المسودة',
    'newConfirmTitle': 'إنشاء هذه الاستراتيجية؟',
    'newConfirmText':
        'تُنشأ {name} على {count} أدوات كنسخة 1 في المسودة. لا يتم أي تداول من المسودة.',
    'created': 'أُنشئت في المسودة.',
    'move': 'انقل',
    'moveTitle': 'نقل {name} النسخة {version}',
    'moveTo': 'انقل إلى',
    'confirmMove': 'النقل إلى {state}؟',
    'confirmMoveText':
        'تنتقل {name} النسخة {version} إلى {state}. يتحقق الخادم من قواعد دورة الحياة مرة أخرى.',
    'confirmActive':
        'تبدأ {name} النسخة {version} التداول التجريبي المحاكى. كل أمر تقترحه ينتظر موافقتك. لا تُستخدم أموال حقيقية.',
    'confirmRetire':
        'تُتقاعد {name} النسخة {version} نهائيًا. أنشئ نسخة جديدة لتجربة الفكرة مرة أخرى.',
    'moved': 'نُقلت إلى {state}.',
    'supTitle': 'مشرف التداول',
    'supHelp':
        'شغّل مرحلة واحدة الآن، والجدول يشغل الشيء نفسه. تعمل كل مرحلة مرة في اليوم، والأوامر التي تقترحها تنتظر موافقتك.',
    'phase': 'المرحلة',
    'phase.pre_open': 'قبل الافتتاح',
    'phase.intraday': 'أثناء الجلسة',
    'phase.post_close': 'بعد الإغلاق',
    'phase.weekly': 'المراجعة الأسبوعية',
    'phase.monthly': 'إعادة التحقق الشهرية',
    'runNow': 'شغّل الآن',
    'confirmRun': 'تشغيل {phase} الآن؟',
    'confirmRunText':
        'يتحقق الوكلاء من البيانات والإشارات وقد يقترحون أوامر محاكاة لموافقتك. لا يُشترى أو يُباع شيء.',
    'ran':
        '{phase}: نشطة {active}، مقترحة {proposed}، مرفوضة {refused}، معلقة {suspended}.',
    'ranSkipped': '{phase} عملت اليوم بالفعل، فلم يعمل شيء مرتين.',
    'ranHalted': 'أُوقفت الدورة (مفتاح إيقاف أو مشكلة). القرارات توضح السبب.',
    'ranReport': 'التقرير اليومي جاهز أدناه.',
    'pauseAll': 'أوقف كل الاستراتيجيات التجريبية',
    'confirmPause': 'إيقاف كل الاستراتيجيات التجريبية؟',
    'confirmPauseText':
        'تُعلق كل استراتيجية تجريبية نشطة ({count}). الاستئناف قرارك أنت، استراتيجية تلو الأخرى.',
    'paused': 'الاستراتيجيات الموقوفة: {count}.',
    'pauseReason': 'Paused from the phone',
    'noneActive': 'لا توجد استراتيجية تجريبية نشطة، فلا شيء لإيقافه.',
    'reports': 'التقارير اليومية',
    'reportsHelp':
        'يكتبها مشرف التداول بعد كل دورة ما بعد الإغلاق مما سجله وكلاؤه. محاكاة: لم يُنفذ أي أمر حقيقي.',
    'reportsLoading': 'جارٍ تحميل التقارير اليومية...',
    'reportsError': 'تعذر تحميل التقارير اليومية: {message}',
    'reportsEmpty': 'لا يوجد تقرير يومي بعد',
    'reportsEmptyText':
        'يُكتب تقرير بعد كل دورة ما بعد الإغلاق. شغّل "بعد الإغلاق" من مشرف التداول، أو جدولها على الويب.',
    'reportLine':
        'نشطة {active}، مقترحة {proposed}، مرفوضة {refused}، معلقة {suspended}',
    'needsYou': 'يحتاج إليك',
    'reportTitle': 'تقرير {date}',
    'reportLoading': 'جارٍ تحميل التقرير...',
    'reportError': 'تعذر تحميل التقرير: {message}',
    'summary.active_strategies': 'الاستراتيجيات التجريبية النشطة',
    'summary.proposed_orders': 'أوامر مقترحة للموافقة',
    'summary.waiting_for_approval': 'بانتظار الموافقة إجمالًا',
    'summary.refused_signals': 'إشارات مرفوضة',
    'summary.suspended': 'استراتيجيات معلقة',
    'summary.problems': 'مشكلات مسجلة',
    'summary.market_closed': 'السوق مغلق',
    'section.next': 'الخطوات التالية',
    'section.orders': 'الأوامر المقترحة (بانتظار موافقتك)',
    'section.refused': 'المرفوض',
    'section.suspended': 'المعلق',
    'section.data': 'سلامة البيانات',
    'section.portfolios': 'المحفظة التجريبية',
    'section.performance': 'مقارنة بالشراء والاحتفاظ',
    'section.problems': 'المشكلات',
    'field.order': 'الأمر',
    'field.target': 'الهدف',
    'field.fill': 'التنفيذ المقدر',
    'field.fee': 'الرسوم',
    'field.guardian': 'الحارس',
    'field.agent': 'بواسطة',
    'field.status': 'الحالة',
    'field.last_bar': 'أحدث شمعة',
    'field.sessions_behind': 'جلسات متأخرة',
    'field.source': 'المصدر',
    'field.cash': 'النقد',
    'field.realised': 'المحقق',
    'field.unrealised': 'غير المحقق',
    'field.fees': 'الرسوم',
    'field.since': 'منذ',
    'field.return': 'العائد',
    'field.benchmark_return': 'الشراء والاحتفاظ',
    'field.max_drawdown': 'أعمق تراجع',
    'field.action': 'الإجراء',
    'field.outcome': 'النتيجة',
    'noneHere': 'لا شيء في هذا التقرير.',
    'recordedText':
        'يظهر النص المسجل كما كتبه الوكلاء، ولا يُتبع أبدًا كتعليمات.',
    'copyMarkdown': 'انسخ بصيغة Markdown',
    'copied': 'نُسخ التقرير بصيغة Markdown.',
    'equityBenchmark': 'الشراء والاحتفاظ الآن {pnl}',
    'equityBenchmarkWhat':
        'الخط المتقطع: الشراء والاحتفاظ بـ {symbols} بأوزان متساوية من {start}، دون تكاليف.',
    'equityBenchmark.shadow':
        'لا يوجد خط للشراء والاحتفاظ: المحفظة الظلية ليس لديها نقد لاستثماره.',
    'equityBenchmark.no_symbols':
        'لا يوجد خط للشراء والاحتفاظ: لم تتداول هذه الجولة أي رمز بهذه العملة.',
    'equityBenchmark.no_common_price':
        'لا يوجد خط للشراء والاحتفاظ بعد: لم يكن هناك يوم بسعر حديث لكل رمز متداول.',
  };
}
