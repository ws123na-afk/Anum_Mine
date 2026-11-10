import 'dart:async';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';

import 'api_client.dart';

bool get _arabic => Intl.getCurrentLocale().toLowerCase().startsWith('ar');

/// "The server could not be reached", in the app's language.
String get unreachableMessage => _arabic
    ? 'تعذّر الوصول إلى الخادم. تحقّق من الاتصال وحاول مرة أخرى.'
    : 'The server could not be reached. Check the connection and try again.';

/// "The server did not answer in time", in the app's language.
String get timedOutMessage => _arabic
    ? 'لم يستجب الخادم في الوقت المناسب. تحقّق من الاتصال وحاول مرة أخرى.'
    : 'The server did not answer in time. Check the connection and try again.';

/// What is shown when nothing better is known, in the app's language.
String get genericErrorMessage => _arabic
    ? 'حدث خطأ غير متوقع. حاول مرة أخرى.'
    : 'Something went wrong. Try again.';

/// Text that is a program's own description of an error, never a sentence
/// for a person: `ApiException(500, ...)`, `Instance of 'Foo'`,
/// `Bad state: ...`, `type 'Null' is not a subtype of ...`.
final _technical =
    RegExp(r"^(Instance of |[\w.]*(Exception|Error|Failure)\b\s*([(:]|$)|"
        r'Bad state|Null check operator|type |NoSuchMethodError|RangeError|'
        r'Invalid argument|Concurrent modification|Unsupported operation|'
        r'LateInitializationError|Stack Overflow|Out of Memory)');

/// The sentence to show a person for [error] (docs/mobile.md#error-text):
///
/// * an API refusal: the API's own sentence (`ApiException.readable`);
/// * no connection or no answer in time: a plain sentence saying so;
/// * a message the app itself wrote (a `FormatException` from a form or an
///   unexpected response, an exception whose text is already a sentence):
///   that message;
/// * anything else: "Something went wrong. Try again.", never the raw
///   exception text.
///
/// The app's own sentences follow the app's language (English or Arabic).
String readableError(Object? error) {
  if (error == null) return genericErrorMessage;
  if (error is ApiException) return error.readable;
  if (error is TimeoutException) return timedOutMessage;
  if (error is SocketException ||
      error is HandshakeException ||
      error is http.ClientException ||
      error is HttpException) {
    return unreachableMessage;
  }
  if (error is FormatException) {
    final message = error.message.trim();
    return message.isEmpty ? genericErrorMessage : message;
  }
  var text = error.toString().trim();
  if (text.startsWith('Exception: ')) text = text.substring(11).trim();
  if (text.isEmpty || _technical.hasMatch(text)) return genericErrorMessage;
  return text;
}
