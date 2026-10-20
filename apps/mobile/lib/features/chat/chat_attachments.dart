import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

import '../../src/infrastructure/picked_file_size.dart';

/// A text file attached to a question. The assistant has no file tools in
/// chat (docs/conversations.md), so the file's text travels inside the
/// question, labelled with its name; nothing is uploaded or stored apart
/// from the conversation itself.
class ChatAttachment {
  const ChatAttachment({required this.name, required this.text});
  final String name, text;
}

/// Why a picked file cannot be attached.
class ChatAttachmentException implements Exception {
  const ChatAttachmentException(this.reason);

  /// `type` (not text), `size` (over [chatAttachmentMaxBytes]) or
  /// `encoding` (not UTF-8).
  final String reason;
}

const chatAttachmentExtensions = ['txt', 'md', 'csv', 'json', 'log'];
const chatAttachmentMaxBytes = 6000;

/// Reads [bytes] of a file named [name] as an attachment, or throws
/// [ChatAttachmentException] saying why it cannot be one.
ChatAttachment readChatAttachment(String name, List<int> bytes) {
  final dot = name.lastIndexOf('.');
  final ext = dot < 0 ? '' : name.substring(dot + 1).toLowerCase();
  if (!chatAttachmentExtensions.contains(ext)) {
    throw const ChatAttachmentException('type');
  }
  if (bytes.length > chatAttachmentMaxBytes) {
    throw const ChatAttachmentException('size');
  }
  try {
    return ChatAttachment(name: name, text: utf8.decode(bytes));
  } on FormatException {
    throw const ChatAttachmentException('encoding');
  }
}

/// The question as sent: the typed text, then each attachment fenced and
/// named, so the model reads it as data the person supplied.
String questionWithAttachments(
    String question, List<ChatAttachment> attachments) {
  if (attachments.isEmpty) return question;
  final parts = [
    question.trim(),
    for (final a in attachments)
      'Attached file "${a.name}":\n```\n${a.text.trim()}\n```',
  ];
  return parts.where((p) => p.isNotEmpty).join('\n\n');
}

/// Picks one text file from the device; null when the person cancels.
abstract interface class ChatAttachmentPicker {
  Future<ChatAttachment?> pick();
}

class DeviceChatAttachmentPicker implements ChatAttachmentPicker {
  const DeviceChatAttachmentPicker();

  @override
  Future<ChatAttachment?> pick() async {
    final file = await FilePicker.pickFile(
        type: FileType.custom, allowedExtensions: chatAttachmentExtensions);
    if (file == null) return null;
    if (await pickedFileSize(file) > chatAttachmentMaxBytes) {
      throw const ChatAttachmentException('size');
    }
    final Uint8List bytes;
    try {
      bytes = await file.readAsBytes();
    } on Object {
      throw const ChatAttachmentException('encoding');
    }
    return readChatAttachment(file.name, bytes);
  }
}
