import 'dart:async';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/sse.dart';
import '../chat/chat_models.dart';
import '../chat/chat_repository.dart';
import 'finance_personal_models.dart';
import 'finance_review_models.dart';

/// Personal money over the live API (docs/finance.md#lent-and-borrowed,
/// #fixed-commitments, #planning-and-advice). Every record is the caller's
/// own; the server checks `finance:read` and `finance:manage` on each call.
abstract interface class FinancePersonalRepository {
  Future<PersonLoanPage> loans(
      {LoanDirection? direction, required int offset, int limit = 25});
  Future<PersonLoan> createLoan(PersonLoanInput input);

  /// One loan with its repayment schedule.
  Future<PersonLoanDetail> loan(String id);

  /// Replaces the loan with [input] (the schedule, rate and the rest).
  Future<PersonLoan> updateLoan(String id, PersonLoanInput input);

  /// Repayments the Suggestions queue proposes for loans with people
  /// (`kind=loan_repayment`, docs/finance.md#lent-and-borrowed), one page.
  Future<SuggestionQueue> loanSuggestions(
      {required int offset, int limit = 25});

  /// Accepts exactly the proposal shown (its payload hash): the link is made
  /// through the links API with every link check.
  Future<SuggestionDecision> acceptSuggestion(String key, String payloadHash);

  /// Dismisses a proposal; it does not come back.
  Future<void> dismissSuggestion(String key);

  /// Expected one-off income (docs/finance.md#forecast): `open` (still
  /// expected, the default) or `all`.
  Future<ExpectedIncomePage> expectedIncome(
      {String state = 'open', required int offset, int limit = 25});
  Future<ExpectedIncome> createExpectedIncome(ExpectedIncomeInput input);
  Future<ExpectedIncome> updateExpectedIncome(
      String id, ExpectedIncomeInput input);
  Future<void> deleteExpectedIncome(String id);

  /// The person's own recorded transactions a repayment of [loan] could be:
  /// money in when someone repays what I lent, money out when I repay.
  Future<List<LoanCandidate>> repaymentCandidates(PersonLoan loan,
      {String query = ''});

  /// Links the recorded transaction [canonicalId] to [loanId] as a
  /// repayment. One [idempotencyKey] per sheet, so a retry links once.
  Future<void> linkRepayment(String loanId,
      {required String canonicalId, required String idempotencyKey});
  Future<FixedCommitments> fixedCommitments();
  Future<PayoffPage> payoff();

  /// The salary profile and its recent months (docs/finance.md#salary);
  /// no profile reads as an empty view.
  Future<SalaryView> salary({int months = 3});

  /// Confirms [transactionId] as [month]'s salary, or says it is not.
  Future<void> decideSalary(String month,
      {required String transactionId, required bool confirm});
}

class ApiFinancePersonalRepository implements FinancePersonalRepository {
  ApiFinancePersonalRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  @override
  Future<PersonLoanPage> loans(
          {LoanDirection? direction,
          required int offset,
          int limit = 25}) async =>
      PersonLoanPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/people-loans${_q({
                'direction': direction?.name,
                'offset': '$offset',
                'limit': '$limit',
              })}')));

  @override
  Future<PersonLoan> createLoan(PersonLoanInput input) async =>
      PersonLoan.fromJson(JsonReader.of(await api
          .request('POST', '$base/people-loans', body: input.toJson())));

  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<PersonLoanDetail> loan(String id) async => PersonLoanDetail.fromJson(
      JsonReader.of(await api.request('GET', '$base/people-loans/${_id(id)}')));

  @override
  Future<PersonLoan> updateLoan(String id, PersonLoanInput input) async =>
      PersonLoan.fromJson(JsonReader.of(await api.request(
          'PATCH', '$base/people-loans/${_id(id)}',
          body: input.toJson())));

  @override
  Future<SuggestionQueue> loanSuggestions(
          {required int offset, int limit = 25}) async =>
      SuggestionQueue.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/suggestions${_q({
                'kind': 'loan_repayment',
                'limit': '$limit',
                'offset': '$offset',
              })}')));

  @override
  Future<SuggestionDecision> acceptSuggestion(
          String key, String payloadHash) async =>
      SuggestionDecision.fromJson(JsonReader.of(await api.request(
          'POST', '$base/suggestions/accept',
          body: {'key': key, 'payload_hash': payloadHash})));

  @override
  Future<void> dismissSuggestion(String key) async {
    await api.request('POST', '$base/suggestions/reject', body: {'key': key});
  }

  @override
  Future<ExpectedIncomePage> expectedIncome(
          {String state = 'open', required int offset, int limit = 25}) async =>
      ExpectedIncomePage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/expected-income${_q({
                'state': state,
                'limit': '$limit',
                'offset': '$offset',
              })}')));

  @override
  Future<ExpectedIncome> createExpectedIncome(
          ExpectedIncomeInput input) async =>
      ExpectedIncome.fromJson(JsonReader.of(await api
          .request('POST', '$base/expected-income', body: input.toJson())));

  @override
  Future<ExpectedIncome> updateExpectedIncome(
          String id, ExpectedIncomeInput input) async =>
      ExpectedIncome.fromJson(JsonReader.of(await api.request(
          'PATCH', '$base/expected-income/${_id(id)}',
          body: input.toJson())));

  @override
  Future<void> deleteExpectedIncome(String id) async {
    await api.request('DELETE', '$base/expected-income/${_id(id)}');
  }

  @override
  Future<List<LoanCandidate>> repaymentCandidates(PersonLoan loan,
      {String query = ''}) async {
    final body = await api.request(
        'GET',
        '$base/payment-candidates${_q({
              'for': 'person_loan',
              'direction': loan.direction == LoanDirection.lent ? 'in' : 'out',
              'currency': loan.currency,
              'amount': loan.outstanding,
              'q': query.trim(),
            })}');
    return JsonReader.of(body).optObjects('items', LoanCandidate.fromJson);
  }

  @override
  Future<void> linkRepayment(String loanId,
      {required String canonicalId, required String idempotencyKey}) async {
    await api.request(
        'POST', '$base/people-loans/${Uri.encodeComponent(loanId)}/links',
        body: {'canonical_id': canonicalId, 'kind': 'repayment'},
        headers: {'Idempotency-Key': idempotencyKey});
  }

  @override
  Future<FixedCommitments> fixedCommitments() async =>
      FixedCommitments.fromJson(
          JsonReader.of(await api.request('GET', '$base/fixed-commitments')));

  @override
  Future<PayoffPage> payoff() async => PayoffPage.fromJson(
      JsonReader.of(await api.request('GET', '$base/planner/payoff')));

  @override
  Future<SalaryView> salary({int months = 3}) async {
    final found =
        JsonReader.of(await api.request('GET', '$base/salary/profile'))
            .optObject('profile');
    if (found == null) return const SalaryView();
    return SalaryView(
      profile: SalaryProfile.fromJson(found),
      months: SalaryMonths.fromJson(JsonReader.of(
          await api.request('GET', '$base/salary/months?limit=$months'))),
    );
  }

  @override
  Future<void> decideSalary(String month,
      {required String transactionId, required bool confirm}) async {
    await api.request(
        'POST', '$base/salary/months/${Uri.encodeComponent(month)}/decision',
        body: {
          'transaction_id': transactionId,
          'decision': confirm ? 'confirm' : 'reject',
        });
  }
}

/// Answers a money question in the person's own words. The phone does not
/// plan anything itself: the question goes to the Assistant, whose finance
/// skill runs the planner (`finance.plan.*`, docs/finance.md#assistant).
abstract interface class MoneyPlanner {
  Future<PlannerAnswer> ask(String question);

  /// Stops the answer being written; [ask] then fails with a
  /// [PlannerFailure] coded `stopped`.
  void stop();
}

/// [MoneyPlanner] through the Assistant's conversations
/// (docs/conversations.md): a new conversation per question, the reply read
/// from the same stream the Assistant shows, so the conversation stays in
/// the Assistant's list.
class ChatMoneyPlanner implements MoneyPlanner {
  ChatMoneyPlanner(this.chat);
  final ChatRepository chat;

  StreamSubscription<SseEvent>? _running;
  Completer<void>? _done;

  @override
  Future<PlannerAnswer> ask(String question) async {
    final conversation = await chat.create();
    var turns = startAsk(const [], question);
    final done = _done = Completer<void>();
    _running = chat.ask(conversation.id, content: question).listen(
      (event) => turns = applyAskEvent(turns, event),
      onError: (Object error, StackTrace stack) {
        if (!done.isCompleted) done.completeError(error, stack);
      },
      onDone: () {
        if (!done.isCompleted) done.complete();
      },
      cancelOnError: true,
    );
    try {
      await done.future;
    } finally {
      _running = null;
      _done = null;
    }
    UiTurn? reply;
    for (final turn in turns) {
      if (turn.role == ChatRole.assistant) reply = turn;
    }
    if (reply == null) {
      throw const PlannerFailure('The planner did not answer.');
    }
    if (reply.status == ChatTurnStatus.failed) {
      throw PlannerFailure(reply.error ?? 'The planner did not answer.',
          code: reply.errorCode);
    }
    final text =
        reply.content.trim().isNotEmpty ? reply.content : reply.card?.summary;
    if (text == null || text.trim().isEmpty) {
      throw const PlannerFailure('The planner did not answer.');
    }
    return PlannerAnswer(text: text.trim(), conversationId: conversation.id);
  }

  @override
  void stop() {
    final running = _running;
    _running = null;
    if (running != null) unawaited(running.cancel());
    final done = _done;
    if (done != null && !done.isCompleted) {
      done.completeError(const PlannerFailure('Stopped.', code: 'stopped'));
    }
  }
}
