import '../../data/readable_error.dart';
import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/sse.dart' show EventStreamTimeoutException;
import 'finance_personal_models.dart';
import 'finance_more_data.dart';
import 'finance_personal_repository.dart';
import 'finance_review_models.dart';

/// Where one part of personal money stands. Offline is told apart from
/// other failures so the screen can say to check the connection.
enum MoneyLoad { idle, loading, ready, denied, offline, error }

/// State of the phone's personal money screens (docs/finance.md): the Money
/// planner, Lent and borrowed, Fixed costs and the debt payoff card. Each
/// part loads and fails on its own. Nothing here pays or moves money.
class FinancePersonalController extends ChangeNotifier {
  FinancePersonalController(this.repository,
      {this.planner, DateTime Function()? today})
      : _today = today ?? DateTime.now;

  final FinancePersonalRepository repository;
  final DateTime Function() _today;

  /// Today as `YYYY-MM-DD` in the phone's time zone.
  String get todayIso {
    final d = _today();
    return '${d.year.toString().padLeft(4, '0')}-'
        '${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  }

  /// Null when this build has no Assistant to ask; the planner then says so.
  final MoneyPlanner? planner;

  /// The routes of the parity pages (one payment split across loans); set
  /// by `FinanceController`, null in builds without them.
  FinanceMoreRepository? more;
  static const pageSize = 25;

  // -- lent and borrowed -----------------------------------------------------
  MoneyLoad loansState = MoneyLoad.idle;
  final List<PersonLoan> loans = [];
  int loansTotal = 0;
  List<OwedTotal> owed = const [];
  int overdue = 0;
  LoanDirection? direction;
  bool loadingMore = false;
  String? loansError;

  bool get hasMoreLoans => loans.length < loansTotal;

  // -- repayment suggestions for loans ---------------------------------------
  MoneyLoad suggestionsState = MoneyLoad.idle;
  final List<Suggestion> loanSuggestions = [];
  int loanSuggestionsTotal = 0;
  String? suggestionsError;
  bool loadingMoreSuggestions = false;

  /// The proposal being accepted or dismissed, so its buttons wait.
  String? decidingSuggestion;

  bool get hasMoreSuggestions => loanSuggestions.length < loanSuggestionsTotal;

  // -- expected one-off income -----------------------------------------------
  MoneyLoad incomeState = MoneyLoad.idle;
  final List<ExpectedIncome> income = [];
  int incomeTotal = 0;
  String? incomeError;
  bool loadingMoreIncome = false;

  /// Received ones are listed too (`state=all`), else only open ones.
  bool showReceived = false;

  /// The income being marked received or deleted, so its buttons wait.
  String? changingIncome;

  bool get hasMoreIncome => income.length < incomeTotal;

  // -- fixed costs and payoff ------------------------------------------------
  MoneyLoad commitmentsState = MoneyLoad.idle;
  FixedCommitments? commitments;
  String? commitmentsError;

  MoneyLoad payoffState = MoneyLoad.idle;
  PayoffPage? payoff;
  String? payoffError;

  // -- salary ----------------------------------------------------------------
  MoneyLoad salaryState = MoneyLoad.idle;
  SalaryView? salary;
  String? salaryError;

  /// The month being confirmed or rejected, so its buttons wait.
  String? decidingSalary;

  // -- the planner -----------------------------------------------------------
  MoneyLoad plannerState = MoneyLoad.idle;
  String question = '';
  PlannerAnswer? answer;
  String? plannerError;

  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  static MoneyLoad failedState(Object error) => switch (error) {
        ApiException(statusCode: 0) => MoneyLoad.offline,
        ApiException(isPermissionDenied: true) => MoneyLoad.denied,
        _ => MoneyLoad.error,
      };

  static String reason(Object error) => switch (error) {
        final ApiException e => e.readable,
        final EventStreamTimeoutException e => e.message,
        final PlannerFailure e => e.message,
        _ => readableError(error),
      };

  Future<void> loadLoans() async {
    loansState = MoneyLoad.loading;
    loansError = null;
    _notify();
    try {
      final page = await repository.loans(
          direction: direction, offset: 0, limit: pageSize);
      loans
        ..clear()
        ..addAll(page.items);
      loansTotal = page.total;
      owed = page.owed;
      overdue = page.overdue;
      loansState = MoneyLoad.ready;
    } catch (error) {
      loansState = failedState(error);
      loansError = reason(error);
    }
    _notify();
  }

  Future<void> showDirection(LoanDirection? next) async {
    direction = next;
    await loadLoans();
  }

  /// The next page; a failure keeps the list and says why.
  Future<void> loadMoreLoans() async {
    if (loadingMore || !hasMoreLoans) return;
    loadingMore = true;
    loansError = null;
    _notify();
    try {
      final page = await repository.loans(
          direction: direction, offset: loans.length, limit: pageSize);
      loans.addAll(page.items);
      loansTotal = page.total;
    } catch (error) {
      loansError = reason(error);
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  /// Saves a new loan, then reloads the list and the totals. Throws the
  /// server's refusal so the form shows it under the fields.
  Future<void> addLoan(PersonLoanInput input) async {
    await repository.createLoan(input);
    await loadLoans();
  }

  /// Links a recorded transaction as a repayment, then reloads.
  Future<void> recordRepayment(PersonLoan loan,
      {required String canonicalId, required String idempotencyKey}) async {
    await repository.linkRepayment(loan.id,
        canonicalId: canonicalId, idempotencyKey: idempotencyKey);
    await Future.wait([loadLoans(), loadLoanSuggestions()]);
  }

  /// Links the money handed over, then reloads.
  Future<void> recordHanded(PersonLoan loan,
      {required String canonicalId, required String idempotencyKey}) async {
    await repository.linkHanded(loan.id,
        canonicalId: canonicalId, idempotencyKey: idempotencyKey);
    await loadLoans();
  }

  /// Removes a linked transaction from [loan], then reloads. Throws the
  /// refusal.
  Future<void> unlinkLoan(PersonLoan loan, String linkId) async {
    await repository.unlinkLoan(loan.id, linkId);
    await Future.wait([loadLoans(), loadLoanSuggestions()]);
  }

  /// Deletes [loan] and its links (the transactions stay), then reloads.
  /// Throws the refusal.
  Future<void> deleteLoan(PersonLoan loan) async {
    await repository.deleteLoan(loan.id);
    await Future.wait([loadLoans(), loadLoanSuggestions()]);
  }

  /// Replaces a loan (schedule, rate and the rest), then reloads. Throws
  /// the server's refusal so the form shows it under the fields.
  Future<void> updateLoan(String id, PersonLoanInput input) async {
    await repository.updateLoan(id, input);
    await Future.wait([loadLoans(), loadLoanSuggestions()]);
  }

  Future<void> loadLoanSuggestions() async {
    suggestionsState = MoneyLoad.loading;
    suggestionsError = null;
    _notify();
    try {
      final page = await repository.loanSuggestions(offset: 0, limit: pageSize);
      loanSuggestions
        ..clear()
        ..addAll(page.items);
      loanSuggestionsTotal = page.total;
      suggestionsState = MoneyLoad.ready;
    } catch (error) {
      suggestionsState = failedState(error);
      suggestionsError = reason(error);
    }
    _notify();
  }

  /// The next page of proposals; a failure keeps the list and says why.
  Future<void> loadMoreLoanSuggestions() async {
    if (loadingMoreSuggestions || !hasMoreSuggestions) return;
    loadingMoreSuggestions = true;
    suggestionsError = null;
    _notify();
    try {
      final page = await repository.loanSuggestions(
          offset: loanSuggestions.length, limit: pageSize);
      loanSuggestions.addAll(page.items);
      loanSuggestionsTotal = page.total;
    } catch (error) {
      suggestionsError = reason(error);
    } finally {
      loadingMoreSuggestions = false;
      _notify();
    }
  }

  /// Accepts (links the repayment through the links API) or dismisses a
  /// proposal, then reloads the proposals and the loans. Returns null on
  /// success, else the server's refusal.
  Future<String?> decideLoanSuggestion(Suggestion item,
      {required bool accept}) async {
    if (decidingSuggestion != null) return null;
    decidingSuggestion = item.key;
    _notify();
    try {
      if (accept) {
        await repository.acceptSuggestion(item.key, item.payloadHash);
      } else {
        await repository.dismissSuggestion(item.key);
      }
      decidingSuggestion = null;
      await Future.wait([loadLoanSuggestions(), if (accept) loadLoans()]);
      return null;
    } catch (error) {
      decidingSuggestion = null;
      // The proposal may have changed or been decided elsewhere: reload.
      await loadLoanSuggestions();
      return reason(error);
    }
  }

  Future<void> loadIncome() async {
    incomeState = MoneyLoad.loading;
    incomeError = null;
    _notify();
    try {
      final page = await repository.expectedIncome(
          state: showReceived ? 'all' : 'open', offset: 0, limit: pageSize);
      income
        ..clear()
        ..addAll(page.items);
      incomeTotal = page.total;
      incomeState = MoneyLoad.ready;
    } catch (error) {
      incomeState = failedState(error);
      incomeError = reason(error);
    }
    _notify();
  }

  Future<void> showReceivedIncome(bool value) async {
    showReceived = value;
    await loadIncome();
  }

  /// The next page; a failure keeps the list and says why.
  Future<void> loadMoreIncome() async {
    if (loadingMoreIncome || !hasMoreIncome) return;
    loadingMoreIncome = true;
    incomeError = null;
    _notify();
    try {
      final page = await repository.expectedIncome(
          state: showReceived ? 'all' : 'open',
          offset: income.length,
          limit: pageSize);
      income.addAll(page.items);
      incomeTotal = page.total;
    } catch (error) {
      incomeError = reason(error);
    } finally {
      loadingMoreIncome = false;
      _notify();
    }
  }

  /// Adds or replaces expected income, then reloads. Throws the server's
  /// refusal so the form shows it under the fields.
  Future<void> saveIncome(ExpectedIncomeInput input, {String? id}) async {
    if (id == null) {
      await repository.createExpectedIncome(input);
    } else {
      await repository.updateExpectedIncome(id, input);
    }
    await loadIncome();
  }

  /// Marks [item] received today, or deletes it. Returns null on success,
  /// else the server's refusal.
  Future<String?> changeIncome(ExpectedIncome item,
      {required bool delete}) async {
    if (changingIncome != null) return null;
    changingIncome = item.id;
    _notify();
    try {
      if (delete) {
        await repository.deleteExpectedIncome(item.id);
      } else {
        await repository.updateExpectedIncome(
            item.id, item.toInput(receivedOn: todayIso));
      }
      changingIncome = null;
      await loadIncome();
      return null;
    } catch (error) {
      changingIncome = null;
      _notify();
      return reason(error);
    }
  }

  Future<void> loadCommitments() async {
    commitmentsState = MoneyLoad.loading;
    commitmentsError = null;
    _notify();
    try {
      commitments = await repository.fixedCommitments();
      commitmentsState = MoneyLoad.ready;
    } catch (error) {
      commitmentsState = failedState(error);
      commitmentsError = reason(error);
    }
    _notify();
  }

  Future<void> loadPayoff() async {
    payoffState = MoneyLoad.loading;
    payoffError = null;
    _notify();
    try {
      payoff = await repository.payoff();
      payoffState = MoneyLoad.ready;
    } catch (error) {
      payoffState = failedState(error);
      payoffError = reason(error);
    }
    _notify();
  }

  Future<void> loadSalary() async {
    salaryState = MoneyLoad.loading;
    salaryError = null;
    _notify();
    try {
      salary = await repository.salary();
      salaryState = MoneyLoad.ready;
    } catch (error) {
      salaryState = failedState(error);
      salaryError = reason(error);
    }
    _notify();
  }

  /// Confirms or rejects the salary found for [month], then reloads.
  /// Returns null on success, else the server's refusal.
  Future<String?> decideSalary(SalaryMonth month,
      {required bool confirm}) async {
    final found = month.found;
    if (found == null || decidingSalary != null) return null;
    decidingSalary = month.month;
    _notify();
    try {
      await repository.decideSalary(month.month,
          transactionId: found.transactionId, confirm: confirm);
      decidingSalary = null;
      await loadSalary();
      return null;
    } catch (error) {
      decidingSalary = null;
      _notify();
      return reason(error);
    }
  }

  bool get asking => plannerState == MoneyLoad.loading;

  /// Asks the planner [text]; the answer or what failed replaces the last.
  Future<void> ask(String text) async {
    final q = text.trim();
    final current = planner;
    if (q.isEmpty || asking || current == null) return;
    question = q;
    answer = null;
    plannerError = null;
    plannerState = MoneyLoad.loading;
    _notify();
    try {
      answer = await current.ask(q);
      plannerState = MoneyLoad.ready;
    } catch (error) {
      if (error is PlannerFailure && error.code == 'stopped') {
        plannerState = MoneyLoad.idle;
      } else {
        plannerState = failedState(error);
        plannerError = reason(error);
      }
    }
    _notify();
  }

  void stopAsking() {
    if (asking) planner?.stop();
  }

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    stopAsking();
    loans.clear();
    loansTotal = 0;
    owed = const [];
    overdue = 0;
    direction = null;
    loanSuggestions.clear();
    loanSuggestionsTotal = 0;
    decidingSuggestion = null;
    income.clear();
    incomeTotal = 0;
    showReceived = false;
    changingIncome = null;
    suggestionsError = incomeError = null;
    suggestionsState = incomeState = MoneyLoad.idle;
    commitments = null;
    payoff = null;
    salary = null;
    decidingSalary = null;
    salaryError = null;
    salaryState = MoneyLoad.idle;
    answer = null;
    question = '';
    loansError = commitmentsError = payoffError = plannerError = null;
    loansState = commitmentsState = payoffState = plannerState = MoneyLoad.idle;
    _notify();
  }

  @override
  void dispose() {
    stopAsking();
    _disposed = true;
    super.dispose();
  }
}
