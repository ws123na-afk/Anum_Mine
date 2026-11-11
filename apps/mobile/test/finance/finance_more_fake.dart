// The fake Finance API behind the parity pages' tests (docs/finance.md):
// reconciliation, bank loans, one payment split across loans, foreign
// balances, trips and projects, the Money advisor, the Plan, payslips,
// transfers home and the keyword rules, answering with the API's JSON
// shapes. Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';

Map<String, Object?> reconRow(int n,
        {String status = 'open',
        String state = 'unresolved',
        bool changed = false,
        String kind = 'bank'}) =>
    {
      'id': 'rec_$n',
      'account_id': 'acc_$n',
      'account_name': kind == 'card' ? 'Example card' : 'Example bank $n',
      'account_kind': kind,
      'currency': 'AED',
      'period_start': '2026-09-01',
      'period_end': '2026-09-30',
      'source': 'typed',
      'import_id': null,
      'status': status,
      'state': state,
      'changed': changed,
      'can_complete': state == 'balanced',
      'blockers': state == 'balanced' ? <String>[] : ['difference'],
      'statement_closing': '-1200.00',
      'book_closing': '-1150.00',
      'unexplained': state == 'balanced' ? '0.00' : '-50.00',
      'exceptions': 2,
      'blocking': 1,
      'completed_at': status == 'completed' ? '2026-10-02T08:00:00Z' : null,
      'completed_by': null,
      'version': 3,
    };

Map<String, Object?> issue(int n, {String severity = 'blocking'}) => {
      'id': 'exc_$n',
      'kind': n.isEven ? 'unmatched_payment' : 'not_in_ledger',
      'severity': severity,
      'actions': ['match'],
      'posted_on': '2026-09-${(n % 28 + 1).toString().padLeft(2, '0')}',
      'amount': '-${100 + n}.00',
      'description': 'Example shop $n',
      'transaction_id': null,
      'other_transaction_id': null,
      'statement_ref': 'ref_$n',
      'reason': 'held_for_review',
      'month': null,
      'age_days': null,
      'details': <String, Object?>{},
    };

Map<String, Object?> loanTotals({int ambiguous = 1}) => {
      'status': 'open',
      'basis': 'reducing',
      'installment': '1500.00',
      'installment_from': 'computed',
      'split_known': true,
      'never_ends': false,
      'effective_rate': '5.0000',
      'total_payable': '36000.00',
      'total_interest': '2000.00',
      'paid': '7500.00',
      'interest_paid': '600.00',
      'principal_paid': '6900.00',
      'balance': '27100.00',
      'payoff_on': '2028-06-01',
      'due_count': 6,
      'paid_count': 5,
      'months_ahead': -1,
      'next_due_on': '2026-11-01',
      'next_amount': '1500.00',
      'from_salary': false,
      'found': 0,
      'missing': 1,
      'ambiguous': ambiguous,
    };

Map<String, Object?> loanRow(int n) => {
      'number': n,
      'due_on': '2026-${(n % 12 + 1).toString().padLeft(2, '0')}-01',
      'payment': '1500.00',
      'principal': '1400.00',
      'interest': '100.00',
      'balance_after': '${30000 - n * 1400}.00',
      'status': n == 2 ? 'ambiguous' : (n < 6 ? 'paid' : 'upcoming'),
      'paid': n < 6 && n != 2 ? '1500.00' : '0',
      'payments': <Object?>[],
      'candidates': n == 2
          ? [
              {
                'canonical_id': 'ctx_a',
                'posted_on': '2026-03-01',
                'amount': '1500.00',
                'currency': 'AED',
                'merchant': 'EXAMPLE BANK LOAN',
                'account_id': 'acc_1'
              },
              {
                'canonical_id': 'ctx_b',
                'posted_on': '2026-03-03',
                'amount': '1500.00',
                'currency': 'AED',
                'merchant': 'EXAMPLE TRANSFER',
                'account_id': 'acc_1'
              },
            ]
          : <Object?>[],
      'found': null,
    };

Map<String, Object?> personLoan(String id, String amount,
        {String status = 'open',
        String direction = 'lent',
        String currency = 'AED'}) =>
    {
      'id': id,
      'direction': direction,
      'person': 'Sara Example',
      'amount': amount,
      'currency': currency,
      'lent_on': '2026-07-15',
      'due_on': '2027-01-01',
      'notes': null,
      'remind': true,
      'remind_days_before': 3,
      'closed_on': null,
      'repaid': '0.00',
      'outstanding': amount,
      'handed': amount,
      'status': status,
      'interest': '0',
      'next_due_on': '2026-11-01',
      'next_due_amount': '100.00',
      'overdue_amount': '0',
    };

Map<String, Object?> candidate(String id, String amount, String remaining) => {
      'canonical_id': id,
      'posted_on': '2026-10-05',
      'merchant': 'Transfer from Sara',
      'category': 'transfer',
      'account_id': 'acc_1',
      'amount': amount,
      'currency': 'AED',
      'remaining': remaining,
    };

Map<String, Object?> tagJson(int n, {String kind = 'trip', int links = 1}) => {
      'id': 'tag_$n',
      'kind': kind,
      'name': kind == 'trip' ? 'Example trip $n' : 'Example project $n',
      'starts_on': '2026-10-01',
      'ends_on': '2026-10-10',
      'keywords': 'example city',
      'notes': null,
      'archived': false,
      'totals': links == 0 ? <String, Object?>{} : {'AED': '850.00'},
      'links': links,
    };

Map<String, Object?> link(String id, {String role = 'payment'}) => {
      'link': {
        'id': id,
        'role': role,
        'status': 'active',
        'amount': '850.00',
        'currency': 'AED',
      },
      'posted_on': '2026-10-03',
      'description': 'Example hotel',
      'account_id': 'acc_1',
    };

Map<String, Object?> adviceNote(String id) => {
      'id': id,
      'week_start': '2026-11-02',
      'trigger': 'schedule',
      'generated_at': '2026-11-08T05:00:00Z',
      'delivered_at': null,
      'note': {
        'version': 1,
        'week_end': '2026-11-08',
        'history_days': 120,
        'currencies': [
          {
            'currency': 'AED',
            'runway': {
              'status': 'ok',
              'liquid': '12000.00',
              'scenarios': [
                {
                  'kind': 'bare',
                  'monthly_outflow': '3000.00',
                  'days': 121,
                  'runs_out_on': '2027-03-01'
                },
                {
                  'kind': 'essential',
                  'monthly_outflow': '6000.00',
                  'days': 60,
                  'runs_out_on': null
                },
                {
                  'kind': 'full',
                  'monthly_outflow': '0',
                  'days': null,
                  'runs_out_on': null
                },
              ],
            },
            'spending': {
              'week': {
                'amount': '700.00',
                'previous': '500.00',
                'change_percent': 40
              },
              'month': {
                'amount': '2100.00',
                'previous': '1900.00',
                'change_percent': null
              },
            },
            'coming': {
              'items': [
                {
                  'kind': 'bill',
                  'name': 'Example rent',
                  'amount': '3000.00',
                  'due_on': '2026-11-15',
                  'detected': false
                },
                {
                  'kind': 'card_minimum',
                  'name': 'Example card',
                  'amount': '250.00',
                  'due_on': null,
                  'detected': false
                },
              ],
              'total': '3250.00',
              'within_7_days': '0.00',
            },
          }
        ],
        'cards': [
          {
            'name': 'Example card',
            'currency': 'AED',
            'last4': '1234',
            'owed': '5000.00',
            'available': '15000.00',
            'minimum_payment': '250.00',
            'due_on': '2026-11-20',
            'apr_percent': '36.00',
            'interest_if_minimum': '143.00',
            'months_to_clear_at_minimum': null,
            'never_clears_at_minimum': true,
            'missing': ['limit'],
          }
        ],
        'income': {'salary_found': false},
        'actions': [
          {
            'code': 'pay_card_in_full',
            'severity': 'urgent',
            'currency': 'AED',
            'params': <String, Object?>{},
            'text_en': 'Pay Example card in full before 20 November.',
            'text_ar': 'سدد Example card بالكامل قبل 20 نوفمبر.',
            'tab': null
          }
        ],
        'notice_en': 'ANUM only advises.',
        'notice_ar': 'ANUM يقدّم النصيحة فقط.',
      },
    };

Map<String, Object?> payslipJson(int n, {List<Object?> movements = const []}) =>
    {
      'id': 'slip_$n',
      'employer': 'Example Co',
      'month': '2026-${(n % 12 + 1).toString().padLeft(2, '0')}',
      'paid_on': '2026-10-27',
      'currency': 'AED',
      'gross': '10000.00',
      'deductions': [
        {'kind': 'social_insurance', 'amount': '900.00', 'label': null}
      ],
      'notes': null,
      'total_deductions': '900.00',
      'net': '9100.00',
      'received': movements.isEmpty ? '0' : '9100.00',
      'difference': movements.isEmpty ? '-9100.00' : '0.00',
      'status': movements.isEmpty ? 'not_linked' : 'matched',
      'by_kind': {'social_insurance': '900.00'},
      'movements': movements,
    };

Map<String, Object?> remitJson(int n, {List<Object?> movements = const []}) => {
      'id': 'rem_$n',
      'sent_on': '2026-10-0${n % 9 + 1}',
      'amount': '1000.00',
      'currency': 'AED',
      'fee': '15.00',
      'fx_rate': '22.50',
      'received_amount': '22500.00',
      'received_currency': 'INR',
      'recipient': 'Example family',
      'provider': 'Example exchange',
      'regular': n == 1,
      'notes': null,
      'total_paid': '1015.00',
      'fee_percent': '1.50',
      'effective_rate': '22.17',
      'linked': '0',
      'movements': movements,
    };

/// The Finance parity pages' API. Flags turn on refusals and empty lists.
class MoreFinanceApi {
  MoreFinanceApi() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  bool many = false, empty = false, deny = false;
  int? fail;
  bool deleteRefused = false;
  bool noteMissing = false;
  final rules = <Map<String, Object?>>[
    {'match': 'example gym', 'category': 'health'}
  ];
  final unlinked = <String>{};
  bool batchUndone = false;
  final tags = [tagJson(1), tagJson(2, kind: 'project', links: 0)];
  final loanSplit = <String>[];

  FinanceMoreRepository repo() => FinanceMoreRepository(fake.client());
  AnumApiClient client() => fake.client();

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    if (deny) return refuse(403, 'Finance is the person\'s own.');
    if (fail != null) return refuse(fail!, 'The finance service is down.');
    final offset = int.tryParse(q['offset'] ?? '0') ?? 0;
    // -- reconciliation
    if (r.method == 'GET' && path == '$fin/reconciliations/exceptions') {
      final count = empty ? 0 : (many ? 45 : 3);
      final all = [
        for (var n = 1; n <= count; n++)
          {
            'source': 'reconciliation',
            'reconciliation_id': n == 3 ? null : 'rec_1',
            'account_id': 'acc_1',
            'account_name': 'Example bank 1',
            'currency': 'AED',
            'period_start': '2026-09-01',
            'period_end': '2026-09-30',
            'exception': n == 3
                ? {
                    ...issue(n, severity: 'warning'),
                    'kind': 'missing_period',
                    'posted_on': null,
                    'amount': null,
                    'month': '2026-08',
                    'reason': null,
                  }
                : issue(n),
          }
      ];
      return ok({
        'items': all.skip(offset).take(20).toList(),
        'total': all.length,
        'limit': 20,
        'offset': offset,
      });
    }
    if (r.method == 'GET' && path == '$fin/reconciliations/exceptions/export') {
      return ok({'csv': 'account,kind\nExample bank 1,not_in_ledger\n'});
    }
    if (r.method == 'GET' && path == '$fin/reconciliations') {
      final items = empty
          ? <Object?>[]
          : [
              reconRow(1, kind: 'card'),
              reconRow(2, status: 'completed', state: 'balanced'),
              reconRow(3, changed: true, status: 'completed'),
            ];
      return ok(
          {'items': items, 'total': items.length, 'limit': 20, 'offset': 0});
    }
    final recon = RegExp('^$fin/reconciliations/(rec_\\d+)\$').firstMatch(path);
    if (recon != null) {
      final id = recon.group(1)!;
      final n = int.parse(id.split('_')[1]);
      if (r.method == 'DELETE') {
        if (deleteRefused) {
          return refuse(
              409,
              'A reconciliation that was completed keeps its history. Reopen it to work on it again.',
              'has_history');
        }
        return const ApiResponse(statusCode: 204);
      }
      final body = r.method == 'PATCH' ? r.body! : null;
      final row = reconRow(n,
          kind: n == 1 ? 'card' : 'bank',
          status: n == 1 ? 'open' : 'completed',
          state: n == 2 ? 'balanced' : 'unresolved');
      final total = many ? 60 : 2;
      return ok({
        ...row,
        'version': body == null ? 3 : 4,
        'figures': {
          'currency': 'AED',
          'book_opening': '-900.00',
          'deposits': '500.00',
          'payments': '-750.00',
          'book_closing': '-1150.00',
          'statement_opening': '-900.00',
          'statement_closing': body?['statement_closing'] ?? '-1200.00',
          'difference': '-50.00',
          'pending_total': '0.00',
          'adjusted_book_closing': '-1150.00',
          'unexplained': '-50.00',
          'opening_difference': null,
        },
        'card': n == 1
            ? {
                'amount_due': '1200.00',
                'minimum_due': '60.00',
                'due_on': '2026-10-25',
                'owed_in_ledger': '1150.00',
                'due_difference': '50.00',
                'paid_since': '0.00',
                'minimum_covered': false,
              }
            : null,
        'counts': <String, Object?>{},
        'items': [
          for (var i = offset + 1; i <= offset + 50 && i <= total; i++) issue(i)
        ],
        'total': total,
        'limit': 50,
        'offset': offset,
        'resolutions': <Object?>[],
        'history': n == 1
            ? <Object?>[]
            : [
                {
                  'action': 'completed',
                  'at': '2026-10-02T08:00:00Z',
                  'by': 'user_me'
                }
              ],
        'snapshot': <String, Object?>{},
      });
    }
    // -- bank loans
    if (r.method == 'GET' && path == '$fin/loans') {
      return ok({
        'items': empty
            ? <Object?>[]
            : [
                {
                  'debt_id': 'debt_1',
                  'name': 'Example car loan',
                  'lender': 'Example Bank',
                  'currency': 'AED',
                  'repayment': 'bank_debit',
                  'totals': loanTotals(),
                }
              ]
      });
    }
    if (r.method == 'GET' && path == '$fin/debts') {
      return okList([
        {
          'id': 'debt_1',
          'name': 'Example car loan',
          'currency': 'AED',
          'kind': 'loan'
        },
        {
          'id': 'debt_2',
          'name': 'Example personal loan',
          'currency': 'AED',
          'kind': 'loan'
        },
      ]);
    }
    if (r.method == 'GET' && path == '$fin/accounts') {
      return okList([
        {
          'id': 'acc_1',
          'name': 'Example current',
          'kind': 'bank',
          'currency': 'AED',
          'balance': '100.00'
        },
        {
          'id': 'acc_2',
          'name': 'Example USD',
          'kind': 'bank',
          'currency': 'USD',
          'balance': '5.00'
        },
      ]);
    }
    final loan = RegExp('^$fin/debts/(debt_\\d+)/loan\$').firstMatch(path);
    if (loan != null) {
      if (r.method == 'DELETE') return const ApiResponse(statusCode: 204);
      return ok({
        'debt_id': loan.group(1),
        'name': 'Example car loan',
        'kind': 'loan',
        'currency': 'AED',
        'terms': {
          'lender': 'Example Bank',
          'start_on': '2026-01-01',
          'amount': '30000.00',
          'rate_type': 'reducing',
          'rate': '5.0000',
          'term_months': 24,
          'installment': null,
          'payment_day': 1,
          'repayment': 'bank_debit',
          'account_id': 'acc_1',
          'status': 'open',
          'closed_on': null,
        },
        'totals': loanTotals(),
        'rows': [for (var n = 1; n <= 24; n++) loanRow(n)],
        'batches': [
          {
            'id': 'lbatch_1',
            'debt_id': loan.group(1),
            'status': batchUndone ? 'undone' : 'active',
            'created_at': '2026-10-05T08:00:00Z',
            'undone_at': batchUndone ? '2026-10-06T08:00:00Z' : null,
            'count': 4,
            'amount': '6000.00',
            'currency': 'AED',
          }
        ],
      });
    }
    if (r.method == 'POST' && path == '$fin/loan-batches/lbatch_1/undo') {
      batchUndone = true;
      return ok({'id': 'lbatch_1', 'status': 'undone'});
    }
    if (r.method == 'POST' && path == '$fin/debts/debt_1/payments') {
      return ok({
        'link': {'id': 'link_1'},
        'replayed': false
      }, 201);
    }
    // -- people loans and the split
    if (r.method == 'GET' && path == '$fin/people-loans') {
      return ok({
        'items': [
          personLoan('fin_ploan_1', '1000.00'),
          personLoan('fin_ploan_2', '400.00'),
          personLoan('fin_ploan_3', '200.00', currency: 'USD'),
          personLoan('fin_ploan_4', '300.00', status: 'repaid'),
        ],
        'total': 4,
        'owed': <Object?>[],
        'overdue': 0,
      });
    }
    if (r.method == 'GET' && path == '$fin/payment-candidates') {
      final kind = q['for'];
      if (kind == 'person_loan') {
        return ok({
          'items': [candidate('ctx_split', '300.00', '300.00')]
        });
      }
      return ok({
        'items': [candidate('ctx_${kind}_1', '850.00', '850.00')]
      });
    }
    if (r.method == 'POST' && path == '$fin/people-loans/split') {
      return ok({'links': <Object?>[], 'replayed': false}, 201);
    }
    // -- foreign balances
    if (r.method == 'GET' && path == '$fin/fx-revaluation') {
      return ok({
        'base_currency': 'AED',
        'as_of': '2026-11-11',
        'rate_max_age_days': 7,
        'items': empty
            ? <Object?>[]
            : [
                {
                  'account_id': 'acc_2',
                  'name': 'Example USD',
                  'currency': 'USD',
                  'base_currency': 'AED',
                  'balance': '1000.00',
                  'status': 'ok',
                  'value': '3672.50',
                  'rate': '3.6725',
                  'rate_as_of': '2026-11-10',
                  'rate_source': 'ECB',
                  'cost': '3650.00',
                  'unrealised': '22.50',
                  'missing_rate_dates': <String>[],
                },
                {
                  'account_id': 'acc_3',
                  'name': 'Example GBP',
                  'currency': 'GBP',
                  'base_currency': 'AED',
                  'balance': '200.00',
                  'status': 'rate_not_available',
                  'value': null,
                  'rate': null,
                  'rate_as_of': null,
                  'rate_source': null,
                  'cost': null,
                  'unrealised': null,
                  'missing_rate_dates': <String>[],
                },
              ],
        'unrealised_total': empty ? null : '22.50',
        'incomplete': empty ? 0 : 1,
        'truncated': false,
        'note': 'Display only.',
      });
    }
    // -- trips and projects
    if (r.method == 'GET' && path == '$fin/tags') {
      final shown = empty
          ? <Map<String, Object?>>[]
          : tags
              .where((t) => q['kind'] == null || t['kind'] == q['kind'])
              .toList();
      return ok(
          {'items': shown, 'total': shown.length, 'limit': 20, 'offset': 0});
    }
    if (r.method == 'POST' && path == '$fin/tags') {
      final made = {...tagJson(9, links: 0), ...r.body!, 'id': 'tag_9'};
      tags.insert(0, made);
      return ok(made, 201);
    }
    final tag =
        RegExp('^$fin/tags/(tag_\\d+)(/links(/[^/]+)?)?\$').firstMatch(path);
    if (tag != null) {
      final id = tag.group(1)!;
      if (tag.group(2) != null) {
        if (r.method == 'DELETE') {
          unlinked.add(tag.group(3)!.substring(1));
          return const ApiResponse(statusCode: 204);
        }
        return ok({
          'link': {'id': 'link_new'},
          'replayed': false
        }, 201);
      }
      if (r.method == 'DELETE') return const ApiResponse(statusCode: 204);
      final found =
          tags.firstWhere((t) => t['id'] == id, orElse: () => tagJson(1));
      if (r.method == 'PATCH') return ok({...found, ...r.body!});
      return ok({
        ...found,
        'movements': [
          if (!unlinked.contains('link_t1')) link('link_t1'),
          link('link_t2', role: 'refund'),
        ],
      });
    }
    // -- the Money advisor
    if (r.method == 'GET' && path == '$fin/advisor') {
      return ok({
        'settings': {'enabled': true, 'weekday': 6, 'updated_at': null},
        'timezone': 'Asia/Dubai',
        'today': '2026-11-11',
        'week_start': '2026-11-09',
        'next_note_on': '2026-11-15',
        'latest': noteMissing ? null : adviceNote('adv_1'),
      });
    }
    if (r.method == 'GET' && path == '$fin/advisor/notes') {
      return ok({
        'items': noteMissing
            ? <Object?>[]
            : [
                {
                  'id': 'adv_0',
                  'week_start': '2026-10-26',
                  'trigger': 'manual',
                  'generated_at': '2026-11-01T05:00:00Z',
                  'delivered_at': null,
                  'survival': [
                    {'currency': 'AED', 'status': 'ok', 'days': 1}
                  ],
                  'coming': <Object?>[],
                  'actions': 3,
                  'urgent': 1,
                }
              ],
        'total': noteMissing ? 0 : 1,
        'limit': 20,
        'offset': 0,
      });
    }
    if (r.method == 'GET' && path == '$fin/advisor/notes/adv_0') {
      return ok({...adviceNote('adv_0'), 'week_start': '2026-10-26'});
    }
    if (r.method == 'POST' && path == '$fin/advisor/generate') {
      noteMissing = false;
      return ok(adviceNote('adv_2'), 201);
    }
    if (r.method == 'PUT' && path == '$fin/advisor/settings') {
      return ok({...r.body!, 'updated_at': '2026-11-11T08:00:00Z'});
    }
    // -- the Plan
    if (r.method == 'POST' && path == '$fin/planner/afford') {
      return ok({
        'currency': r.body!['currency'] ?? 'AED',
        'item': r.body!['item'],
        'price': r.body!['price'],
        'monthly_cost': null,
        'safe_to_spend': '2000.00',
        'liquid': '12000.00',
        'income_monthly': '9000.00',
        'commitments_monthly': '3000.00',
        'essential_monthly': '4000.00',
        'ratio_now': '33',
        'ratio_after': null,
        'months_left_after_cash': '2.5',
        'verdict': 'cash_leaves_little',
        'reasons': ['reserve_below_three_months'],
        'pay_with': [
          {
            'account_id': 'acc_1',
            'name': 'Example current',
            'kind': 'bank',
            'currency': 'AED',
            'balance': '12000.00',
            'note': 'covers'
          }
        ],
        'avoid': [
          {
            'code': 'late_fees',
            'currency': 'AED',
            'params': {'amount': '50.00', 'count': '2'}
          }
        ],
        'note': 'Advice only.',
      });
    }
    if (r.method == 'POST' && path == '$fin/planner/compare') {
      return ok({
        'currency': 'AED',
        'price': r.body!['price'],
        'options': [
          {
            'kind': 'cash',
            'label': '',
            'upfront': '80000.00',
            'monthly': null,
            'months': 0,
            'total_cost': '80000.00',
            'interest': '0',
            'extra_over_cash': '0',
            'rate_percent': null,
            'basis': 'reducing',
            'rate_source': 'own',
            'rate_read_on': null,
            'status': 'ok'
          },
          {
            'kind': 'bank_loan',
            'label': '',
            'upfront': '0',
            'monthly': '1450.00',
            'months': 60,
            'total_cost': '87000.00',
            'interest': '7000.00',
            'extra_over_cash': '7000.00',
            'rate_percent': '3.5',
            'basis': 'flat',
            'rate_source': 'own',
            'rate_read_on': null,
            'status': 'ok'
          },
        ],
        'note': 'An estimate.',
      });
    }
    // -- payslips and transfers home
    for (final kind in const ['payslips', 'remittances']) {
      final json = kind == 'payslips' ? payslipJson : remitJson;
      if (r.method == 'GET' && path == '$fin/$kind') {
        final count = empty ? 0 : (many ? 30 : 2);
        return ok({
          'items': [
            for (var n = offset + 1; n <= count && n <= offset + 20; n++)
              json(n)
          ],
          'total': count,
          'limit': 20,
          'offset': offset,
          'months': <Object?>[],
          if (kind == 'remittances')
            'providers': empty
                ? <Object?>[]
                : [
                    {
                      'provider': 'Example exchange',
                      'corridor': 'AED>INR',
                      'transfers': 2,
                      'average_fee_percent': '1.50',
                      'best_effective_rate': '22.17',
                      'latest_effective_rate': '22.10',
                      'latest_on': '2026-10-02'
                    }
                  ],
        });
      }
      if (r.method == 'POST' && path == '$fin/$kind') {
        return ok({
          ...json(7),
          for (final e in r.body!.entries)
            if (e.value != null) e.key: e.value,
          'id': kind == 'payslips' ? 'slip_7' : 'rem_7'
        }, 201);
      }
      final one = RegExp('^$fin/$kind/((slip|rem)_\\d+)(/links(/[^/]+)?)?\$')
          .firstMatch(path);
      if (one != null) {
        if (one.group(3) != null) {
          if (r.method == 'DELETE') {
            unlinked.add(one.group(4)!.substring(1));
            return const ApiResponse(statusCode: 204);
          }
          return ok({
            'link': {'id': 'link_new'},
            'replayed': false
          }, 201);
        }
        if (r.method == 'DELETE') return const ApiResponse(statusCode: 204);
        final n = int.parse(one.group(1)!.split('_')[1]);
        return ok(json(n, movements: [
          if (!unlinked.contains('link_r1')) link('link_r1'),
        ]));
      }
    }
    // -- Finance settings (the keyword rules)
    if (r.method == 'GET' && path == '$fin/settings') {
      return ok({
        'base_currency': 'AED',
        'timezone': 'Asia/Dubai',
        'category_rules': rules,
      });
    }
    if (r.method == 'PUT' && path == '$fin/settings') {
      rules
        ..clear()
        ..addAll([
          for (final rule in r.body!['category_rules']! as List<Object?>)
            Map<String, Object?>.from(rule! as Map)
        ]);
      return ok({...r.body!});
    }
    return null;
  }
}
