// What a person sees when something fails (docs/mobile.md#error-text).
import 'dart:async';
import 'dart:io';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/readable_error.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';

class _Failing implements WorkspaceRepository {
  _Failing(this.error);
  Object error;

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => throw error;

  @override
  Future<WorkspaceTask> createAndRunTask(String prompt) async => throw error;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  tearDown(() => Intl.defaultLocale = null);

  group('readableError', () {
    test('an API refusal is the API\'s own sentence', () {
      expect(
          readableError(const ApiException(409, 'The task is already running')),
          'The task is already running');
      expect(
          readableError(const ApiException(422, 'Request failed',
              details: ['Hour field: 25 is outside 0-23.'])),
          'Hour field: 25 is outside 0-23.');
    });

    test('no connection and no answer are said plainly', () {
      const unreachable =
          'The server could not be reached. Check the connection and try again.';
      expect(readableError(const SocketException('Connection refused')),
          unreachable);
      expect(readableError(http.ClientException('Connection closed')),
          unreachable);
      expect(readableError(const HandshakeException('bad certificate')),
          unreachable);
      expect(readableError(TimeoutException('after 0:00:30')),
          'The server did not answer in time. Check the connection and try again.');
    });

    test('program text never reaches the person', () {
      const generic = 'Something went wrong. Try again.';
      for (final error in <Object?>[
        StateError('No element'),
        ArgumentError.notNull('id'),
        Object(),
        null,
        Exception(),
        TypeError(),
        UnsupportedError('nope'),
        RangeError.index(3, const [1]),
        const FormatException(),
      ]) {
        expect(readableError(error), generic, reason: '$error');
      }
    });

    test('a sentence the app wrote itself is kept', () {
      expect(readableError(const WorkspaceOfflineException()),
          'The workspace is offline.');
      expect(readableError(Exception('Choose a file first.')),
          'Choose a file first.');
      expect(
          readableError(const FormatException('Enter an amount above zero.')),
          'Enter an amount above zero.');
    });

    test('the app\'s own sentences follow the app\'s language', () {
      Intl.defaultLocale = 'ar';
      expect(readableError(StateError('x')), contains('حدث خطأ'));
      expect(readableError(const SocketException('x')), contains('الخادم'));
      expect(readableError(TimeoutException('x')), contains('الخادم'));
      // The API's sentence is the API's.
      expect(
          readableError(const ApiException(403, 'Owners only')), 'Owners only');
    });
  });

  group('workspace errors', () {
    test('a failed load shows a sentence, never the exception text', () async {
      final repository = _Failing(const ApiException(500, 'Database down',
          details: ['Try again in a minute.']));
      final controller = WorkspaceController(repository);
      await controller.load();
      expect(controller.phase, LoadPhase.error);
      expect(controller.message, 'Try again in a minute.');

      repository.error = StateError('Bad state: no element');
      await controller.load();
      expect(controller.message, 'Something went wrong. Try again.');

      repository.error = const SocketException('Connection reset by peer');
      await controller.load();
      expect(controller.message, isNot(contains('SocketException')));
      expect(controller.message, contains('could not be reached'));
    });

    test('a refused change shows the API\'s sentence', () async {
      final controller = WorkspaceController(
          _Failing(const ApiException(500, 'The runtime is restarting')));
      await controller.createTask('Review the release');
      expect(controller.message, 'The runtime is restarting');
      expect(controller.message, isNot(contains('ApiException')));
    });
  });
}
