// Chat with ANUM (mockup 07): attachments carried in the question and
// dictation into the composer.
import 'dart:convert';

import 'package:anum_mobile/features/chat/chat_attachments.dart';
import 'package:anum_mobile/features/chat/chat_screen.dart';
import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../chat/chat_fakes.dart';
import '../voice/voice_fakes.dart';
import 'lists_harness.dart';

class _Picker implements ChatAttachmentPicker {
  _Picker(this.next);
  Object next;
  @override
  Future<ChatAttachment?> pick() async {
    final value = next;
    if (value is Exception) throw value;
    return value as ChatAttachment?;
  }
}

void main() {
  test('attachments: text files only, small, named in the question', () {
    expect(readChatAttachment('notes.md', utf8.encode('Fleet notes')).text,
        'Fleet notes');
    expect(() => readChatAttachment('photo.png', [1, 2]),
        throwsA(isA<ChatAttachmentException>()));
    expect(() => readChatAttachment('big.txt', List.filled(7000, 65)),
        throwsA(isA<ChatAttachmentException>()));
    expect(() => readChatAttachment('bad.txt', [0xff, 0xfe, 0xfd]),
        throwsA(isA<ChatAttachmentException>()));
    final q = questionWithAttachments('Summarise this',
        [const ChatAttachment(name: 'notes.md', text: 'Fleet notes')]);
    expect(q, startsWith('Summarise this'));
    expect(q, contains('Attached file "notes.md":\n```\nFleet notes\n```'));
  });

  testWidgets('attach a file and dictate, then send both', (tester) async {
    final api = FakeConversationsApi();
    final events = ScriptedEvents();
    final (chat, _) = await buildChat(api, events);
    final picker = _Picker(const ChatAttachmentException('size'));
    final speech = FakeSpeech();
    await pumpList(
        tester,
        AssistantScreen(
            controller: chat, attachments: picker, dictation: speech));
    await tester.tap(find.byKey(const Key('chat-attach')));
    await tester.pumpAndSettle();
    expect(find.textContaining('too large to attach'), findsOneWidget);
    picker.next = const ChatAttachment(name: 'fleet.csv', text: 'truck,km');
    await tester.tap(find.byKey(const Key('chat-attach')));
    await tester.pumpAndSettle();
    expect(find.text('fleet.csv'), findsOneWidget);
    await tester.tap(find.byKey(const Key('chat-mic')));
    await tester.pumpAndSettle();
    speech.listens.single.onResult('Compare these trucks', true);
    speech.listens.single.onDone();
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('chat-send')));
    await tester.pump();
    final sent = events.calls.single.$2['content']! as String;
    expect(sent, startsWith('Compare these trucks'));
    expect(sent, contains('Attached file "fleet.csv"'));
    expect(find.text('fleet.csv'), findsNothing);
  });

  testWidgets('a denied microphone says how to allow it', (tester) async {
    final (chat, _) = await buildChat(FakeConversationsApi(), ScriptedEvents());
    final speech = FakeSpeech()..availability = SpeechAvailability.denied;
    await pumpList(
        tester, AssistantScreen(controller: chat, dictation: speech));
    await tester.tap(find.byKey(const Key('chat-mic')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Microphone access is off'), findsOneWidget);
  });
}
