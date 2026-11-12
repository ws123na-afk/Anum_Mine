// The fake Finance API behind the tools' tests (docs/finance.md,
// docs/finance-sms.md, docs/finance-statements.md): reconciliation decisions,
// iPhone SMS review, the ledger, statement import (CSV and PDF) and owner
// rules, answering with the API's JSON shapes. It checks what the API
// checks where the phone could get it wrong: the payload hash of a
// correcting entry and of a rule version, and the preview hash of Apply.
// Fixtures are plainly fake.
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/finance/finance_more_data.dart';
import 'package:anum_mobile/features/finance/finance_tools_data.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';
final correctionHash = 'c0' * 32;
final ruleHash = 'a1' * 32;
final previewHash = 'b2' * 32;

Map<String, Object?> account(String id, String name,
        {String kind = 'bank', String currency = 'AED', String? last4}) =>
    {
      'id': id,
      'name': name,
      'kind': kind,
      'currency': currency,
      'balance': '1000.00',
      'last4': last4,
      'archived': false,
    };

Map<String, Object?> exception(String id, String kind,
        {String severity = 'blocking',
        List<String> actions = const [],
        String? amount = '-120.00',
        String? statementRef,
        String? transactionId,
        String? otherTransactionId,
        String description = 'Example shop'}) =>
    {
      'id': id,
      'kind': kind,
      'severity': severity,
      'actions': actions,
      'posted_on': '2026-09-12',
      'amount': amount,
      'description': description,
      'transaction_id': transactionId,
      'other_transaction_id': otherTransactionId,
      'statement_ref': statementRef,
      'reason': null,
      'month': null,
      'age_days': null,
      'details': <String, Object?>{},
    };

class FakePdfTransport implements StatementPdfTransport {
  FakePdfTransport(this.answer);
  JsonMap Function(Map<String, String> query, String? password) answer;
  final calls = <(Map<String, String>, int, String?)>[];

  @override
  Future<JsonMap> readStatementPdf(Map<String, String> query, Uint8List bytes,
      {String? password}) async {
    calls.add((query, bytes.length, password));
    return answer(query, password);
  }
}

class ToolsApi {
  ToolsApi() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;

  // reconciliation
  String status = 'open';
  bool balanced = false;
  final resolutions = <Map<String, Object?>>[];
  final history = <Map<String, Object?>>[];
  bool correctionChanged = false;

  // sms
  final messages = <Map<String, Object?>>[
    {
      'id': 'sms_1',
      'device_id': 'dev_1',
      'source': 'device',
      'received_at': '2026-11-02T08:30:00Z',
      'sender': 'EXAMPLEBANK',
      'bank_key': null,
      'body_masked': 'Purchase of AED 25.00 at Example cafe with card ****',
      'body_kept_until': null,
      'status': 'review',
      'reason': 'format_unverified',
      'parsed': {
        'kind': 'card_purchase',
        'amount': '25.00',
        'currency': 'AED',
        'merchant': 'Example cafe'
      },
      'fix': {'action': 'trust_sender', 'sender': 'examplebank'},
      'rule': 'builtin',
      'entry_id': null,
      'duplicate_of': null,
      'observed_balance': null,
      'observed_currency': null,
      'attempts': 1,
    },
    {
      'id': 'sms_2',
      'device_id': 'dev_1',
      'source': 'device',
      'received_at': '2026-11-01T08:30:00Z',
      'sender': 'EXAMPLEBANK',
      'bank_key': null,
      'body_masked': 'Salary credited AED 9000.00',
      'body_kept_until': null,
      'status': 'posted',
      'reason': null,
      'parsed': {'kind': 'salary', 'amount': '9000.00', 'currency': 'AED'},
      'rule': 'builtin',
      'entry_id': 'ent_1',
      'duplicate_of': null,
      'observed_balance': null,
      'observed_currency': null,
      'attempts': 1,
    },
  ];
  bool aiOn = true;
  bool smsEmpty = false;
  // What ANUM learned (docs/finance-sms.md#learning).
  final smsNotices = <Map<String, Object?>>[
    {
      'id': 'note_1',
      'kind': 'sender_trusted',
      'sender': 'examplebank',
      'created_at': '2026-11-02T09:00:00Z'
    },
    {
      'id': 'note_2',
      'kind': 'card_proposed',
      'last4': '4321',
      'account_id': 'acc_2',
      'account_name': 'Card',
      'created_at': '2026-11-02T09:00:00Z'
    },
  ];
  final smsRules = <Map<String, Object?>>[
    {
      'id': 'smsrule_1',
      'sender': 'EXAMPLEBANK',
      'bank_key': null,
      'kind': 'card_purchase',
      'currency': 'AED',
      'category': null,
      'account_id': null,
      'status': 'proposed',
      'from_message_id': 'sms_1',
      'hits': 0,
      'created_at': '2026-11-02T08:30:00Z',
      'pattern_preview': 'Purchase of {currency} {amount} at {merchant}',
    }
  ];

  // imports
  final imports = <Map<String, Object?>>[];

  // rules
  final ruleVersions = <Map<String, Object?>>[];
  final batches = <Map<String, Object?>>[];
  final aliases = <Map<String, Object?>>[
    {
      'id': 'alias_1',
      'alias': 'EXMPL MART',
      'merchant': 'Example mart',
      'created_at': '2026-10-01T08:00:00Z',
      'updated_at': '2026-10-01T08:00:00Z'
    }
  ];
  final subs = <Map<String, Object?>>[
    {'id': 'sub_1', 'parent': 'groceries', 'name': 'Bakery', 'archived': false}
  ];
  Map<String, Object?> policy = {
    'auto_classify': false,
    'min_confidence': '0.90',
    'sources': ['rule'],
    'notify_unresolved': false,
    'notify_min_items': 1,
    'notices': <String>[],
    'notice_exception_days': 7,
    'notice_uncategorized_min': '500',
  };

  bool deny = false;

  FinanceMoreRepository repo({StatementPdfTransport? pdf}) =>
      FinanceMoreRepository(fake.client(), pdf: pdf);

  Iterable<ApiRequest> sent(String method, String path) =>
      fake.sent(method, '$fin$path');

  Map<String, Object?> get session => {
        'id': 'rec_1',
        'account_id': 'acc_1',
        'account_name': 'Example bank',
        'account_kind': 'bank',
        'currency': 'AED',
        'period_start': '2026-09-01',
        'period_end': '2026-09-30',
        'source': 'typed',
        'import_id': null,
        'status': status,
        'state': balanced ? 'balanced' : 'unresolved',
        'changed': false,
        'can_complete': balanced && status == 'open',
        'blockers': balanced ? <String>[] : ['difference'],
        'statement_closing': '880.00',
        'book_closing': '1000.00',
        'unexplained': balanced ? '0.00' : '-120.00',
        'exceptions': balanced ? 0 : 3,
        'blocking': balanced ? 0 : 1,
        'completed_at': status == 'completed' ? '2026-10-02T08:00:00Z' : null,
        'completed_by': null,
        'version': 4,
        'figures': {
          'currency': 'AED',
          'book_opening': '1000.00',
          'deposits': '0.00',
          'payments': '0.00',
          'book_closing': '1000.00',
          'statement_opening': '1000.00',
          'statement_closing': '880.00',
          'difference': '-120.00',
          'pending_total': '0.00',
          'adjusted_book_closing': '1000.00',
          'unexplained': balanced ? '0.00' : '-120.00',
          'opening_difference': null,
        },
        'card': null,
        'counts': <String, int>{},
        'items': balanced
            ? <Object?>[]
            : [
                exception('nil_1', 'not_in_ledger',
                    actions: ['match', 'correct'], statementRef: 'imp_1#3'),
                exception('pay_1', 'unmatched_payment',
                    severity: 'warning',
                    actions: ['attach', 'pending', 'match'],
                    transactionId: 'txn_9'),
                exception('rev_1', 'reversal_pair',
                    severity: 'info',
                    actions: ['accept_reversal'],
                    transactionId: 'txn_4',
                    otherTransactionId: 'txn_5'),
                exception('dup_1', 'possible_duplicate',
                    severity: 'warning',
                    actions: ['open'],
                    transactionId: 'txn_6'),
              ],
        'total': balanced ? 0 : 4,
        'limit': 50,
        'offset': 0,
        'resolutions': resolutions,
        'history': history,
        'snapshot': <String, Object?>{},
      };

  Map<String, Object?> get csvPreview => {
        'account_id': 'acc_1',
        'currency': 'AED',
        'filename': 'statement.csv',
        'preset': 'auto',
        'headers': ['Date', 'Details', 'Amount', 'Balance'],
        'mapping': {
          'date': 'Date',
          'description': 'Details',
          'amount': 'Amount',
          'balance': 'Balance'
        },
        'date_format': 'DD/MM/YYYY',
        'date_formats': ['DD/MM/YYYY', 'MM/DD/YYYY'],
        'date_format_ambiguous': true,
        'row_count': 3,
        'valid_count': 2,
        'new_count': 1,
        'duplicate_count': 1,
        'error_count': 1,
        'rows': [
          {
            'line': 2,
            'posted_on': '2026-10-03',
            'amount': '-45.00',
            'currency': 'AED',
            'description': 'Example grocer',
            'category': 'groceries',
            'duplicate': false,
            'match': {
              'status': 'new',
              'code': 'new',
              'reason': 'Not recorded yet',
              'record': null,
              'candidates': <Object?>[],
              'lag_days': null,
              'amount_difference': null,
              'bill': null,
              'transfer': null,
              'default_import': true,
            },
          },
          {
            'line': 3,
            'posted_on': '2026-10-04',
            'amount': '-12.00',
            'currency': 'AED',
            'description': 'Example taxi',
            'category': 'transport',
            'duplicate': true,
            'match': {
              'status': 'exact_duplicate',
              'code': 'imported_before',
              'reason': 'Imported before',
              'record': null,
              'candidates': <Object?>[],
              'lag_days': null,
              'amount_difference': null,
              'bill': null,
              'transfer': null,
              'default_import': false,
            },
          },
        ],
        'errors': [
          {'line': 4, 'message': 'The date is not readable.'}
        ],
        'counts': {'new': 1, 'exact_duplicate': 1},
        'reconciliation': {
          'status': 'unknown',
          'opening': null,
          'closing': null,
          'rows_total': '-57.00',
          'expected_closing': null,
          'difference': null,
          'opening_source': 'none',
          'closing_source': 'none',
          'breaks': <int>[],
        },
      };

  static Map<String, Object?> pdfView({List<String>? roles}) => {
        'account_id': 'acc_1',
        'currency': 'AED',
        'filename': 'statement.pdf',
        'file_sha256': 'd' * 64,
        'source': 'text',
        'page_count': 2,
        'layout': 'table',
        'columns': [
          {'index': 0, 'label': 'Date', 'role': roles?[0] ?? 'date'},
          {'index': 1, 'label': 'Details', 'role': roles?[1] ?? 'description'},
          {'index': 2, 'label': 'Amount', 'role': roles?[2] ?? 'amount'},
        ],
        'roles': roles ?? ['date', 'description', 'amount'],
        'fingerprint': 'f' * 32,
        'template_id': null,
        'confidence': 0.9,
        'kind': 'account',
        'statement_date': null,
        'due_date': null,
        'skipped_pages': <Object?>[],
        'date_format': 'DD/MM/YYYY',
        'date_formats': ['DD/MM/YYYY', 'MM/DD/YYYY'],
        'date_format_ambiguous': false,
        'period_start': '2026-10-01',
        'period_end': '2026-10-31',
        'account_last4': '9876',
        'account_last4_matches': false,
        'statement_currency': 'AED',
        'opening_balance': '100.00',
        'closing_balance': '50.00',
        'check': {
          'status': 'matched',
          'opening': '100.00',
          'closing': '50.00',
          'rows_total': '-50.00',
          'expected_closing': '50.00',
          'difference': '0.00',
          'opening_source': 'printed',
          'closing_source': 'printed',
          'breaks': <int>[],
          'page_breaks': <int>[],
          'summary': [
            {
              'name': 'withdrawals_count',
              'stated': '2',
              'found': '2',
              'ok': true
            }
          ],
        },
        'row_count': 2,
        'duplicate_count': 0,
        'skipped_lines': 0,
        'warnings': <String>[],
        'rows': [
          for (final (n, amount, text) in [
            (1, '-30.00', 'Example pharmacy'),
            (2, '-20.00', 'Example bakery')
          ])
            {
              'line': n,
              'page': 1,
              'posted_on': '2026-10-0$n',
              'description': text,
              'amount': amount,
              'debit': null,
              'credit': null,
              'balance': null,
              'reference': null,
              'currency': null,
              'original_amount': null,
              'original_currency': null,
              'fx_rate': null,
              'fee': null,
              'vat': null,
              'kind': null,
              'time': null,
              'counterparty': null,
              'parent_line': null,
              'category': 'health',
              'duplicate': false,
              'confidence': n == 2 ? 0.4 : 0.95,
              'flags': n == 2 ? ['sign_unsure'] : <String>[],
              'match': null,
              'fx': null,
            }
        ],
        'counts': {'new': 2},
        'dropped_rows': [
          {
            'page': 2,
            'reason': 'outside_period',
            'text': '30/09 Example older row 5.00',
            'posted_on': '2026-09-30',
            'amount': '-5.00'
          }
        ],
      };

  Map<String, Object?> importView(String id, String filename, int imported) => {
        'id': id,
        'account_id': 'acc_1',
        'filename': filename,
        'preset': 'auto',
        'date_format': 'DD/MM/YYYY',
        'row_count': imported + 1,
        'imported_count': imported,
        'duplicate_count': 1,
        'error_count': 0,
        'created_at': '2026-11-03T09:00:00Z',
        'reconciled': 1,
        'held_for_review': 0,
        'skipped': 0,
        'transfers': 0,
        'bills_paid': 0,
        'dropped': 0,
        'not_in_ledger': 0,
        'reconciliation': {'status': 'tied'},
      };

  Map<String, Object?> ruleVersion(int version, String status,
          {String merchant = 'Example gym', String category = 'health'}) =>
      {
        'rule_id': 'rule_1',
        'version': version,
        'status': status,
        'content': {
          'match': {
            'merchant': merchant,
            'words': <String>[],
            'reference': null,
            'direction': 'out',
            'min_amount': null,
            'max_amount': null,
            'currency': null,
            'account_ids': <String>[],
            'senders': <String>[],
          },
          'category': category,
          'subcategory_id': null,
          'transaction_type': null,
          'priority': 100,
          'enabled': true,
          'auto_apply': false,
          'note': null,
        },
        'provenance': 'owner',
        'source_transaction_id': null,
        'confidence': '0.9',
        'payload_hash': ruleHash,
        'created_by': 'user_me',
        'created_at': '2026-11-01T08:00:00Z',
        'decided_by': null,
        'decided_at': null,
        'reverted_from': null,
      };

  Map<String, Object?> get rulePage {
    final current = ruleVersions
        .where((v) => v['status'] == 'approved')
        .fold<Map<String, Object?>?>(
            null,
            (a, b) => a == null || (b['version'] as int) > (a['version'] as int)
                ? b
                : a);
    final newest = ruleVersions.isEmpty
        ? null
        : ruleVersions.reduce(
            (a, b) => (a['version'] as int) > (b['version'] as int) ? a : b);
    return {
      'items': ruleVersions.isEmpty
          ? <Object?>[]
          : [
              {
                'rule_id': 'rule_1',
                'current': current,
                'pending': newest?['status'] == 'proposed' ? newest : null,
                'versions': ruleVersions.length,
              }
            ],
      'total': ruleVersions.isEmpty ? 0 : 1,
      'limit': 20,
      'offset': 0,
      'legacy_rules': ruleVersions.isEmpty ? 1 : 0,
      'pending': newest?['status'] == 'proposed' ? 1 : 0,
    };
  }

  ApiResponse? route(ApiRequest r) {
    if (deny) return refuse(403, 'Only the owner of this Finance can do that.');
    final path = r.uri.path.replaceFirst(fin, '');
    final body = r.body ?? const {};
    switch ((r.method, path)) {
      // accounts and imports
      case ('GET', '/accounts'):
        return okList([
          account('acc_1', 'Example bank', last4: '1234'),
          account('acc_2', 'Example card', kind: 'card'),
          {...account('acc_3', 'Old account'), 'archived': true},
        ]);
      case ('GET', '/imports'):
        return ok({
          'items': imports,
          'total': imports.length,
          'limit': 20,
          'offset': 0
        });
      case ('GET', '/formats'):
        return ok({
          'presets': [
            {'id': 'emirates_nbd', 'label': 'Emirates NBD'}
          ],
          'date_formats': ['DD/MM/YYYY', 'MM/DD/YYYY'],
          'categories': <String>[],
          'account_kinds': <String>[],
          'limits': <String, int>{},
        });
      case ('POST', '/imports/preview'):
        return ok(csvPreview);
      case ('POST', '/imports'):
        final made = importView('imp_new', 'statement.csv', 1);
        imports.insert(0, made);
        return ok(made, 201);
      case ('POST', '/imports/pdf'):
        final made = importView('imp_pdf', 'statement.pdf', 2);
        imports.insert(0, made);
        return ok(made, 201);
      // reconciliation
      case ('GET', '/reconciliations'):
        return ok({
          'items': [session],
          'total': 1,
          'limit': 20,
          'offset': 0
        });
      case ('GET', '/reconciliations/exceptions'):
        return ok({'items': <Object?>[], 'total': 0, 'limit': 20, 'offset': 0});
      case ('POST', '/reconciliations'):
        return ok(session, 201);
      case ('GET', '/reconciliations/rec_1'):
        return ok(session);
      case ('POST', '/reconciliations/rec_1/resolutions'):
        final made = {
          'id': 'res_${resolutions.length + 1}',
          'kind': body['kind'],
          'statement_ref': body['statement_ref'],
          'transaction_id': body['transaction_id'],
          'other_transaction_id': body['other_transaction_id'],
          'note': null,
          'created_at': '2026-11-03T09:00:00Z',
        };
        resolutions.add(made);
        return ok(made, 201);
      case ('POST', '/reconciliations/rec_1/corrections/preview'):
        return ok({
          'statement_ref': body['statement_ref'],
          'account_id': 'acc_1',
          'posted_on': '2026-09-12',
          'amount': '-120.00',
          'currency': 'AED',
          'description': 'Example shop',
          'category': 'shopping',
          'legs': [
            {
              'ledger_account': 'asset:acc_1',
              'amount': '-120.00',
              'currency': 'AED'
            },
            {
              'ledger_account': 'expense:shopping',
              'amount': '120.00',
              'currency': 'AED'
            },
          ],
          'payload_hash': correctionHash,
        });
      case ('POST', '/reconciliations/rec_1/corrections'):
        if (correctionChanged || body['payload_hash'] != correctionHash) {
          return refuse(
              409,
              'The correcting entry changed since you saw it. Review it again before you post it.',
              'payload_changed');
        }
        resolutions.add({
          'id': 'res_c',
          'kind': 'correction',
          'statement_ref': body['statement_ref'],
          'transaction_id': 'txn_rc',
          'other_transaction_id': null,
          'note': null,
          'created_at': '2026-11-03T09:00:00Z',
        });
        balanced = true;
        return ok({'transaction_id': 'txn_rc', 'resolution_id': 'res_c'}, 201);
      case ('POST', '/reconciliations/rec_1/complete'):
        if (!balanced) {
          return refuse(
              409, 'This reconciliation does not balance yet.', 'unresolved');
        }
        status = 'completed';
        history.add({
          'action': 'completed',
          'at': '2026-11-03T10:00:00Z',
          'by': 'user_me'
        });
        return ok(session);
      case ('POST', '/reconciliations/rec_1/reopen'):
        status = 'open';
        history.add({
          'action': 'reopened',
          'at': '2026-11-03T11:00:00Z',
          'by': 'user_me',
          'reason': body['reason'],
        });
        return ok(session);
      // sms
      case ('GET', '/sms/status'):
        return ok({
          'enabled': true,
          'ledger_kind': 'personal',
          'self_test_passed_at': '2026-10-01T08:00:00Z',
          'ready': true,
          'ingest_path': '/api/v1/finance/sms/ingest',
          'devices_active': 1,
          'last_received_at': '2026-11-02T08:30:00Z',
          'last_ok_at': '2026-11-02T08:30:00Z',
          'last_error': null,
          'last_error_at': null,
          'counts': {'review': 1},
          'review_count': 1,
        });
      case ('GET', '/sms/settings'):
        return ok({
          'ai': {
            'enabled': aiOn,
            'opted_in_at': aiOn ? '2026-10-01T08:00:00Z' : null
          }
        });
      case ('GET', '/sms/messages'):
        final wanted = r.uri.queryParameters['status'];
        final items = smsEmpty
            ? <Map<String, Object?>>[]
            : [
                for (final m in messages)
                  if (wanted == null || m['status'] == wanted) m
              ];
        return ok(
            {'items': items, 'total': items.length, 'limit': 20, 'offset': 0});
      case ('GET', '/sms/rules'):
        return okList(smsRules);
      case ('GET', '/sms/learning'):
        return ok({'notices': smsNotices});
      case ('GET', '/sms/review/summary'):
        final waiting = smsEmpty ? 0 : 1;
        return ok({
          'total': waiting,
          'scanned': waiting,
          'complete': true,
          'groups': [
            if (waiting > 0)
              {
                'reason': 'format_unverified',
                'count': 1,
                'items': [
                  {
                    'key': 'examplebank',
                    'count': 1,
                    'fix': {'action': 'trust_sender', 'sender': 'examplebank'}
                  }
                ]
              }
          ]
        });
      case ('POST', '/sms/learning/senders/trust'):
      case ('POST', '/sms/learning/cards/map'):
        return ok({'notices': smsNotices});
      case ('POST', '/sms/learning/senders/untrust'):
        smsNotices.removeWhere((n) => n['kind'] == 'sender_trusted');
        return ok({'notices': smsNotices});
      case ('POST', '/sms/paste'):
        return ok({
          'status': 'review',
          'reason': 'unknown_sender',
          'message_id': 'sms_3',
          'text': 'Saved for your review in ANUM.'
        });
      case ('GET', '/ledger/dashboard'):
        return ok(ledgerBoard(r.uri.queryParameters['month'] ?? '2026-11'));
      case ('GET', '/ledger/entries'):
        return ok({
          'items': [ledgerEntry()],
          'total': 1,
          'limit': 20,
          'offset': 0
        });
      case ('GET', '/ledger/transactions/can_1'):
        return ok({
          'canonical_id': 'can_1',
          'current': ledgerEntry(),
          'chain': [ledgerEntry()],
          'transaction_ids': <String>[]
        });
      // rules
      case ('GET', '/rules'):
        return ok(rulePage);
      case ('GET', '/rules/rule_1'):
        return ok({
          'rule_id': 'rule_1',
          'versions': [...ruleVersions]..sort(
              (a, b) => (b['version'] as int).compareTo(a['version'] as int)),
        });
      case ('POST', '/rules'):
        final made = ruleVersion(1, 'proposed',
            merchant: ((body['content'] as Map)['match'] as Map)['merchant']
                as String);
        ruleVersions.add(made);
        return ok(made, 201);
      case ('POST', '/rules/preview'):
        return ok(rulePreview);
      case ('POST', '/rules/rule_1/approve'):
        if (body['payload_hash'] != ruleHash) {
          return refuse(409, 'The rule changed since you saw it.');
        }
        final v =
            ruleVersions.firstWhere((v) => v['version'] == body['version']);
        v['status'] = 'approved';
        return ok(v);
      case ('POST', '/rules/rule_1/reject'):
        final v =
            ruleVersions.firstWhere((v) => v['version'] == body['version']);
        v['status'] = 'rejected';
        return ok(v);
      case ('POST', '/rules/rule_1/versions'):
        final made = ruleVersion(ruleVersions.length + 1, 'proposed',
            merchant: ((body['content'] as Map)['match'] as Map)['merchant']
                as String);
        ruleVersions.add(made);
        return ok(made, 201);
      case ('POST', '/rules/rule_1/revert'):
        final made = {
          ...ruleVersion(ruleVersions.length + 1, 'proposed'),
          'reverted_from': body['to_version'],
        };
        ruleVersions.add(made);
        return ok(made, 201);
      case ('POST', '/rules/rule_1/preview'):
        return ok(rulePreview);
      case ('POST', '/rules/rule_1/apply'):
        if (body['preview_hash'] != previewHash) {
          return refuse(409, 'Your rows changed since the preview.');
        }
        final batch = {
          'id': 'batch_1',
          'origin': 'owner',
          'rule_id': 'rule_1',
          'rule_version': 1,
          'suggestion_key': null,
          'changed': 2,
          'status': 'applied',
          'created_by': 'user_me',
          'created_at': '2026-11-03T09:00:00Z',
          'undone_at': null,
        };
        batches.insert(0, batch);
        return ok(batch, 201);
      case ('GET', '/rule-batches'):
        return ok({
          'items': batches,
          'total': batches.length,
          'limit': 20,
          'offset': 0
        });
      case ('POST', '/rule-batches/batch_1/undo'):
        batches.first['status'] = 'undone';
        return ok({'batch': batches.first, 'restored': 2, 'kept': 0});
      case ('GET', '/merchants/aliases'):
        return okList(aliases);
      case ('POST', '/merchants/aliases'):
        final made = {
          'id': 'alias_${aliases.length + 1}',
          'alias': body['alias'],
          'merchant': body['merchant'],
          'created_at': '2026-11-03T09:00:00Z',
          'updated_at': '2026-11-03T09:00:00Z',
        };
        aliases.add(made);
        return ok(made, 201);
      case ('GET', '/subcategories'):
        return okList(subs);
      case ('POST', '/subcategories'):
        final made = {
          'id': 'sub_${subs.length + 1}',
          'parent': body['parent'],
          'name': body['name'],
          'archived': false
        };
        subs.add(made);
        return ok(made, 201);
      case ('GET', '/automation'):
        return ok({
          'policy': policy,
          'updated_at': null,
          'never_automated': ['post_adjustment', 'delete_record']
        });
      case ('PUT', '/automation'):
        policy = Map.of(body);
        return ok({
          'policy': policy,
          'updated_at': '2026-11-03T09:00:00Z',
          'never_automated': ['post_adjustment', 'delete_record']
        });
    }
    // Routes with an id in the middle.
    final resolution =
        RegExp(r'^/reconciliations/rec_1/resolutions/(\w+)$').firstMatch(path);
    if (r.method == 'DELETE' && resolution != null) {
      resolutions.removeWhere((x) => x['id'] == resolution.group(1));
      return ok({}, 204);
    }
    final sms = RegExp(r'^/sms/messages/(\w+)/(\w+)$').firstMatch(path);
    if (r.method == 'POST' && sms != null) {
      final m = messages.firstWhere((m) => m['id'] == sms.group(1));
      switch (sms.group(2)) {
        case 'post':
          m['status'] = 'posted';
          return ok({
            'message': m,
            'entry_id': 'ent_9',
            'canonical_id': 'can_9',
            'reversed_entry_id': null,
            'proposed_rule': body['teach'] == true ? smsRules.first : null,
            'rule_error': null,
          });
        case 'dismiss':
          m['status'] = 'dismissed';
          return ok(m);
        case 'reverse':
          m['status'] = 'dismissed';
          return ok(m);
        case 'suggest':
          return ok({
            'fields': {'merchant': 'Example cafe LLC', 'amount': '25.50'},
            'used_today': 1,
            'daily_cap': 20,
            'note': 'A suggestion from your workspace\'s AI model.',
          });
      }
    }
    final smsRule = RegExp(r'^/sms/rules/(\w+)(?:/(\w+))?$').firstMatch(path);
    if (smsRule != null) {
      final rule = smsRules.firstWhere((x) => x['id'] == smsRule.group(1));
      if (r.method == 'DELETE') {
        smsRules.remove(rule);
        return ok({}, 204);
      }
      rule['status'] = smsRule.group(2) == 'activate' ? 'active' : 'disabled';
      return ok(rule);
    }
    final imp = RegExp(r'^/imports/(\w+)$').firstMatch(path);
    if (r.method == 'DELETE' && imp != null) {
      imports.removeWhere((x) => x['id'] == imp.group(1));
      return ok({}, 204);
    }
    final alias = RegExp(r'^/merchants/aliases/(\w+)$').firstMatch(path);
    if (alias != null) {
      if (r.method == 'DELETE') {
        aliases.removeWhere((x) => x['id'] == alias.group(1));
        return ok({}, 204);
      }
      final found = aliases.firstWhere((x) => x['id'] == alias.group(1));
      found
        ..['alias'] = body['alias']
        ..['merchant'] = body['merchant'];
      return ok(found);
    }
    final sub = RegExp(r'^/subcategories/(\w+)$').firstMatch(path);
    if (r.method == 'PATCH' && sub != null) {
      final found = subs.firstWhere((x) => x['id'] == sub.group(1));
      found
        ..['name'] = body['name']
        ..['archived'] = body['archived'];
      return ok(found);
    }
    return null;
  }

  static const rulePreview = <String, Object?>{
    'matched': 3,
    'changes': 2,
    'unchanged': 1,
    'manual_kept': 0,
    'sms_kept': 0,
    'truncated': false,
    'preview_hash':
        'b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2',
    'rows': [
      {
        'transaction_id': 'txn_1',
        'account_id': 'acc_1',
        'posted_on': '2026-10-02',
        'description': 'EXAMPLE GYM MONTHLY',
        'amount': '-150.00',
        'currency': 'AED',
        'before_category': 'other',
        'before_subcategory_id': null,
        'before_type': 'expense',
        'after_category': 'health',
        'after_subcategory_id': null,
        'after_type': 'expense',
      }
    ],
  };

  static Map<String, Object?> ledgerEntry() => {
        'id': 'ent_1',
        'canonical_id': 'can_1',
        'posted_on': '2026-11-01',
        'kind': 'salary',
        'category': 'income',
        'merchant': null,
        'description': 'Salary from Example Co',
        'source': 'sms',
        'source_ref': 'sms_2',
        'status': 'posted',
        'reverses_id': null,
        'reversed_by_id': null,
        'original_amount': null,
        'original_currency': null,
        'fx_rate': null,
        'reconciled_at': null,
        'postings': [
          {
            'ledger_account': 'asset:acc_1',
            'account_id': 'acc_1',
            'amount': '9000.00',
            'currency': 'AED'
          },
          {
            'ledger_account': 'income:salary',
            'account_id': null,
            'amount': '-9000.00',
            'currency': 'AED'
          },
        ],
        'sources': [
          {
            'kind': 'sms',
            'message_id': 'sms_2',
            'status': 'posted',
            'received_at': '2026-11-01T08:30:00Z'
          }
        ],
      };

  static Map<String, Object?> ledgerBoard(String month) {
    List<Map<String, Object?>> money(String amount) => [
          {'currency': 'AED', 'amount': amount}
        ];
    return {
      'month': month,
      'income': money('9000.00'),
      'expenses': money('25.00'),
      'surplus': money('8975.00'),
      'cashback': <Object?>[],
      'refunds': <Object?>[],
      'assets': money('10000.00'),
      'liabilities': money('500.00'),
      'net_worth': money('9500.00'),
      'in_transit': money('200.00'),
      'accounts': [
        {
          'id': 'acc_1',
          'name': 'Example bank',
          'kind': 'bank',
          'currency': 'AED',
          'last4': '1234',
          'balance': '10000.00',
          'observed_balance': '9990.00',
          'observed_at': '2026-11-02T08:30:00Z',
          'difference': '-10.00',
        }
      ],
      'cards': [
        {
          'id': 'acc_2',
          'name': 'Example card',
          'kind': 'card',
          'currency': 'AED',
          'last4': null,
          'balance': '-500.00',
          'owed': '500.00',
          'credit_limit': '5000.00',
          'utilization_percent': 10,
        }
      ],
      'loans': <Object?>[],
      'categories': [
        {'category': 'dining', 'currency': 'AED', 'amount': '25.00', 'count': 1}
      ],
      'merchants': [
        {
          'merchant': 'Example cafe',
          'currency': 'AED',
          'amount': '25.00',
          'count': 1
        }
      ],
      'trend': [
        {
          'month': month,
          'currency': 'AED',
          'income': '9000.00',
          'expenses': '25.00',
          'net': '8975.00'
        }
      ],
      'year': [
        {
          'month': month.substring(0, 4),
          'currency': 'AED',
          'income': '9000.00',
          'expenses': '25.00',
          'net': '8975.00'
        }
      ],
      'currencies': ['AED'],
      'entry_count': 2,
      'uncategorized_count': 3,
      'ledger_kind': 'personal',
      'base_currency': 'AED',
      'bills_due': [
        {
          'id': 'bill_1',
          'name': 'Example rent',
          'amount': '4000.00',
          'currency': 'AED',
          'due_on': '2026-11-20'
        }
      ],
      'goals': [
        {
          'id': 'goal_1',
          'name': 'Example trip',
          'currency': 'AED',
          'target_amount': '5000.00',
          'saved_amount': '1000.00',
          'percent': 20,
          'target_on': null
        }
      ],
      'debts': [
        {
          'id': 'debt_1',
          'name': 'Example loan',
          'currency': 'AED',
          'balance': '3000.00',
          'minimum_payment': '300.00',
          'months_to_clear': 10
        }
      ],
      'sync': {
        'enabled': true,
        'devices_active': 1,
        'last_received_at': '2026-11-02T08:30:00Z',
        'last_ok_at': null,
        'last_error': null,
        'last_error_at': null,
        'review_count': 1,
      },
      'filters': <String, Object?>{},
    };
  }
}
