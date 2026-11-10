import 'dart:async';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'api_client.dart';
import 'readable_error.dart';

/// The longest a file download or export may take, headers and body
/// together: the same limit as one JSON call (`HttpApiTransport`). It only
/// stops a connection that hangs from spinning for ever.
const transferTimeout = Duration(minutes: 5);

/// Runs one download or export request with a time limit and turns network
/// failures into the sentences the API transport uses, so a screen never
/// waits for ever and never shows `SocketException` or `ClientException`
/// (docs/mobile.md#file-downloads). API refusals pass through unchanged.
Future<T> guardTransfer<T>(Future<T> Function() request,
    {Duration timeout = transferTimeout}) async {
  try {
    return await request().timeout(timeout);
  } on TimeoutException {
    throw ApiException(0, timedOutMessage);
  } on HandshakeException {
    throw ApiException(0, unreachableMessage);
  } on http.ClientException {
    throw ApiException(0, unreachableMessage);
  } on SocketException {
    throw ApiException(0, unreachableMessage);
  } on HttpException {
    throw ApiException(0, unreachableMessage);
  }
}
