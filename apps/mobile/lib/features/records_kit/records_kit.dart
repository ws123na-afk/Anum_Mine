import 'dart:math';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Shared plumbing of the record modules on the phone (Document vault,
/// Attendance, Vehicles, Purchases, Business books): pages, paged lists,
/// load states, refusals and idempotency keys. Each module keeps its own
/// models, repository, controller and strings.

/// Where a screen or list is: loading, ready, the caller may not see it
/// (403), or the load failed.
enum RecordsPhase { loading, ready, denied, error }

/// One page of a list and the token for the next one (an offset or a
/// cursor), null at the end.
class RecordsChunk<T> {
  const RecordsChunk(this.items, {this.next, this.total});
  final List<T> items;
  final String? next;

  /// The whole count when the API sends one.
  final int? total;

  /// An offset page (`{items, total, limit, offset}`).
  static RecordsChunk<T> offsetPage<T>(
      JsonReader page, T Function(JsonReader item) read,
      {String key = 'items', int? offset}) {
    final items = page.objects(key, read);
    // Some pages (`{items, total}`) do not echo the offset they were asked.
    offset ??= page.optInt('offset') ?? 0;
    final total = page.optInt('total') ?? items.length;
    final end = offset + items.length;
    return RecordsChunk(items,
        next: items.isNotEmpty && end < total ? '$end' : null, total: total);
  }
}

/// A refusal or failure as the screens show it: the API's own sentence
/// (403s name who may do it) and whether it was a permission refusal.
class RecordsFailure {
  const RecordsFailure(this.message,
      {this.denied = false, this.code, this.status = 0, this.offline = false});

  factory RecordsFailure.from(Object error) => switch (error) {
        ApiException(statusCode: 0) =>
          RecordsFailure(error.readable, offline: true),
        final ApiException e => RecordsFailure(e.readable,
            denied: e.isPermissionDenied, code: e.code, status: e.statusCode),
        final FormatException e => RecordsFailure(e.message),
        _ => RecordsFailure('$error'),
      };

  final String message;
  final bool denied;
  final bool offline;
  final String? code;
  final int status;
}

/// A searchable, filterable list loaded a page at a time.
class RecordsList<T> extends ChangeNotifier {
  RecordsList(this._fetch);

  final Future<RecordsChunk<T>> Function(
      String query, String filter, String? token) _fetch;

  RecordsPhase phase = RecordsPhase.loading;
  List<T> items = const [];
  int? total;
  String query = '';
  String filter = '';
  RecordsFailure? failure;

  /// A failed "Load more": the list stays, the message shows under it.
  String? moreError;
  bool loadingMore = false;
  String? _next;
  int _generation = 0;
  bool _disposed = false;

  bool get hasMore => _next != null;
  bool get isEmpty => phase == RecordsPhase.ready && items.isEmpty;

  /// Whether a search or filter narrows the list (empty then means "no
  /// match", not "nothing yet").
  bool get narrowed => query.isNotEmpty || filter.isNotEmpty;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load({String? query, String? filter}) async {
    if (query != null) this.query = query.trim();
    if (filter != null) this.filter = filter;
    final generation = ++_generation;
    phase = RecordsPhase.loading;
    moreError = null;
    _notify();
    try {
      final chunk = await _fetch(this.query, this.filter, null);
      if (generation != _generation) return;
      items = chunk.items;
      total = chunk.total;
      _next = chunk.next;
      failure = null;
      phase = RecordsPhase.ready;
    } on Object catch (error) {
      if (generation != _generation) return;
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
  }

  Future<void> more() async {
    final token = _next;
    if (loadingMore || token == null) return;
    final generation = _generation;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final chunk = await _fetch(query, filter, token);
      if (generation != _generation) return;
      items = [...items, ...chunk.items];
      total = chunk.total ?? total;
      _next = chunk.next;
    } on Object catch (error) {
      moreError = RecordsFailure.from(error).message;
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  /// Puts a changed or new record in place without a reload.
  void upsert(T item, bool Function(T existing) same) {
    final index = items.indexWhere(same);
    items = index < 0
        ? [item, ...items]
        : [...items.take(index), item, ...items.skip(index + 1)];
    if (index < 0 && total != null) total = total! + 1;
    if (phase != RecordsPhase.ready) phase = RecordsPhase.ready;
    _notify();
  }

  void remove(bool Function(T existing) same) {
    final before = items.length;
    items = [
      for (final item in items)
        if (!same(item)) item
    ];
    if (total != null) total = total! - (before - items.length);
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

final _random = Random.secure();

/// A fresh idempotency key (32 hex characters) for a write the phone may
/// retry: the same key records the write once.
String newIdempotencyKey([String prefix = 'm']) {
  final buffer = StringBuffer(prefix);
  for (var i = 0; i < 16; i++) {
    buffer.write(_random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}

/// `YYYY-MM-DD` for a local calendar day.
String isoDay(DateTime day) => '${day.year.toString().padLeft(4, '0')}-'
    '${day.month.toString().padLeft(2, '0')}-'
    '${day.day.toString().padLeft(2, '0')}';

/// A `YYYY-MM-DD` value as a local date, or null when it is not one.
DateTime? parseDay(String? value) {
  if (value == null || value.isEmpty) return null;
  final parsed = DateTime.tryParse(value);
  return parsed == null ? null : DateTime(parsed.year, parsed.month, parsed.day);
}

/// A query string from [params], leaving out empty values.
String recordsQuery(Map<String, Object?> params) {
  final parts = <String, String>{
    for (final entry in params.entries)
      if (entry.value != null && '${entry.value}'.isNotEmpty)
        entry.key: '${entry.value}',
  };
  if (parts.isEmpty) return '';
  return '?${Uri(queryParameters: parts).query}';
}

/// The API answers a JSON list as `{"data": [...]}` (the transport wraps
/// it); some routes answer a page object instead. Reads either.
List<T> recordsListBody<T>(
        Map<String, Object?> body, T Function(JsonReader item) read,
        {String key = 'data'}) =>
    JsonReader(body).optObjects(key, read);

/// A plain decimal string the API accepts (`12.5`), or null when [text] is
/// not one. Arabic-Indic digits and a comma decimal are read as Western.
String? decimalText(String text) {
  const arabic = '٠١٢٣٤٥٦٧٨٩';
  var value = text.trim();
  for (var i = 0; i < arabic.length; i++) {
    value = value.replaceAll(arabic[i], '$i');
  }
  value = value.replaceAll('٫', '.').replaceAll(',', '.');
  if (!RegExp(r'^\d+(\.\d+)?$').hasMatch(value)) return null;
  return value;
}
