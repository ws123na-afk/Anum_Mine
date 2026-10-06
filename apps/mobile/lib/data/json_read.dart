/// Typed reads of decoded JSON (API responses, stored sessions).
///
/// Every read names the field it expected and the type it wanted, so a
/// malformed payload fails with one clear [JsonShapeException] (a
/// [FormatException]) such as `"data[2].created_at" should be a string,
/// found a number` instead of a bare cast error. Valid payloads read exactly
/// as the explicit casts they replace did: numbers accept ints and doubles,
/// optional reads return null for a missing or null field.
library;

/// A JSON value did not have the shape a model expects.
class JsonShapeException extends FormatException {
  JsonShapeException(this.path, this.expected, this.found)
      : super('Unexpected response: "$path" should be $expected, '
            'found $found.');

  /// Dotted path of the field, with list indexes: `approvers[1].user_id`.
  final String path;

  /// What the model expected, e.g. `a string`.
  final String expected;

  /// What was there instead, e.g. `nothing` or `a number`.
  final String found;

  @override
  String toString() => message;
}

String _describe(Object? value) => switch (value) {
      null => 'null',
      String() => 'a string',
      bool() => 'a boolean',
      num() => 'a number',
      List<Object?>() => 'a list',
      Map<Object?, Object?>() => 'an object',
      _ => 'a ${value.runtimeType}',
    };

/// The decoded value as a JSON object, or a [JsonShapeException] naming
/// [path] (for example the whole response body).
Map<String, Object?> jsonObject(Object? decoded, [String path = 'response']) {
  if (decoded is Map<String, Object?>) return decoded;
  // An untyped map literal (`{}`) with string keys is still a JSON object.
  if (decoded is Map && decoded.keys.every((key) => key is String)) {
    return decoded.cast<String, Object?>();
  }
  throw JsonShapeException(path, 'an object', _describe(decoded));
}

/// Path-aware reader over one JSON object.
class JsonReader {
  const JsonReader(this.json, [this.path = '']);

  /// Reads [decoded] as an object, failing with [path] when it is not one.
  factory JsonReader.of(Object? decoded, [String path = 'response']) =>
      JsonReader(jsonObject(decoded, path), path == 'response' ? '' : path);

  final Map<String, Object?> json;

  /// Path of this object inside the payload; empty for the top level.
  final String path;

  /// The full path of [key] in this object.
  String at(String key) => path.isEmpty ? key : '$path.$key';

  /// The raw value, for tolerant reads that check the type themselves.
  Object? operator [](String key) => json[key];

  Never _fail(String key, String expected) => throw JsonShapeException(at(key),
      expected, json.containsKey(key) ? _describe(json[key]) : 'nothing');

  T _required<T extends Object>(String key, String expected) {
    final value = json[key];
    if (value is T) return value;
    _fail(key, expected);
  }

  T? _optional<T extends Object>(String key, String expected) {
    final value = json[key];
    if (value is T) return value;
    if (value == null) return null;
    _fail(key, expected);
  }

  String string(String key) => _required<String>(key, 'a string');
  String? optString(String key) => _optional<String>(key, 'a string');

  bool boolean(String key) => _required<bool>(key, 'a boolean');
  bool? optBool(String key) => _optional<bool>(key, 'a boolean');

  int integer(String key) => _required<num>(key, 'a number').toInt();
  int? optInt(String key) => _optional<num>(key, 'a number')?.toInt();

  double number(String key) => _required<num>(key, 'a number').toDouble();
  double? optNumber(String key) => _optional<num>(key, 'a number')?.toDouble();

  DateTime date(String key) => _parseDate(key, string(key));
  DateTime? optDate(String key) {
    final value = optString(key);
    return value == null ? null : _parseDate(key, value);
  }

  DateTime _parseDate(String key, String value) {
    final parsed = DateTime.tryParse(value);
    if (parsed == null) _fail(key, 'an ISO 8601 date-time');
    return parsed;
  }

  /// A nested object, as a reader whose paths start at [key].
  JsonReader object(String key) => JsonReader(_map(key), at(key));
  JsonReader? optObject(String key) =>
      json[key] == null ? null : JsonReader(_map(key), at(key));

  /// A nested object kept as a plain map (free-form data such as arguments),
  /// or null when missing or null.
  Map<String, Object?>? optMap(String key) =>
      json[key] == null ? null : _map(key);

  Map<String, Object?> _map(String key) {
    final value = json[key];
    if (value is Map) {
      try {
        return jsonObject(value, at(key));
      } on JsonShapeException {
        _fail(key, 'an object with string keys');
      }
    }
    _fail(key, 'an object');
  }

  List<Object?>? optList(String key) => _optional<List<Object?>>(key, 'a list');

  /// A list of objects, each read with [read]; a missing list fails.
  List<T> objects<T>(String key, T Function(JsonReader item) read) =>
      _objects(key, _required<List<Object?>>(key, 'a list'), read);

  /// A list of objects; a missing or null list reads as empty.
  List<T> optObjects<T>(String key, T Function(JsonReader item) read) =>
      _objects(key, optList(key) ?? const [], read);

  List<T> _objects<T>(
      String key, List<Object?> values, T Function(JsonReader item) read) {
    final items = <T>[];
    for (var i = 0; i < values.length; i++) {
      final itemPath = '${at(key)}[$i]';
      items.add(read(JsonReader(jsonObject(values[i], itemPath), itemPath)));
    }
    return items;
  }

  /// A list of strings; a missing list fails.
  List<String> strings(String key) =>
      _strings(key, _required<List<Object?>>(key, 'a list'));

  /// A list of strings, or null when missing or null.
  List<String>? optStrings(String key) {
    final values = optList(key);
    return values == null ? null : _strings(key, values);
  }

  List<String> _strings(String key, List<Object?> values) => [
        for (var i = 0; i < values.length; i++)
          switch (values[i]) {
            final String value => value,
            final other => throw JsonShapeException(
                '${at(key)}[$i]', 'a string', _describe(other)),
          }
      ];
}
