import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/sse.dart' show EventStreamTimeoutException;
import 'finance_personal_models.dart';
import 'finance_personal_repository.dart';

/// Where one part of personal money stands. Offline is told apart from
/// other failures so the screen can say to check the connection.
enum MoneyLoad { idle, loading, ready, denied, offline, error }

/// State of the phone's personal money screens (docs/finance.md): the Money
/// planner, Lent and borrowed, Fixed costs and the debt payoff card. Each
/// part loads and fails on its own. Nothing here pays or moves money.
class FinancePersonalController extends ChangeNotifier {
  FinancePersonalController(this.repository, {this.planner});

  final FinancePersonalRepository repository;

  /// Null when this build has no Assistant to ask; the planner then says so.
  final MoneyPlanner? planner;
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
        _ => '$error',
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
    await loadLoans();
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
