import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_models.dart';
import 'whatsapp_templates_screen.dart';

/// Opens a message's attachment. Images open in a viewer inside the app;
/// documents, audio and video go to the platform's save dialog, the way file
/// downloads do elsewhere in the app. Failures (404, 413, 502) show the
/// server's sentence in the thread.
Future<void> openWaMedia(BuildContext context,
    WhatsAppThreadController controller, WaMessage message) async {
  final media = message.media;
  if (media == null) return;
  final bytes = await controller.fetchMedia(message);
  if (bytes == null || !context.mounted) return;
  if (media.isImage) {
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => WhatsAppMediaViewer(
                message: message, bytes: bytes, controller: controller)));
    return;
  }
  await controller.saveMedia(message, bytes);
}

/// An image attachment, zoomable, with a Save action.
class WhatsAppMediaViewer extends StatelessWidget {
  const WhatsAppMediaViewer(
      {required this.message,
      required this.bytes,
      required this.controller,
      super.key});
  final WaMessage message;
  final Uint8List bytes;
  final WhatsAppThreadController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final caption = message.media?.caption ?? '';
    return Scaffold(
      appBar: AppBar(title: Text(l.t('waOps.media.image')), actions: [
        ListenableBuilder(
            listenable: controller,
            builder: (context, _) => IconButton(
                key: const Key('wa-media-save'),
                tooltip: l.t('waOps.media.save'),
                onPressed: controller.busy == null
                    ? () async {
                        final saved =
                            await controller.saveMedia(message, bytes);
                        if (saved && context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text(l.t('waOps.mediaSaved'))));
                        }
                      }
                    : null,
                icon: const Icon(Icons.download))),
      ]),
      body: AnumBackdrop(
        child: SafeArea(
          child: Column(children: [
            Expanded(
              child: InteractiveViewer(
                maxScale: 5,
                child: Center(
                  child: Semantics(
                    image: true,
                    label: caption.isNotEmpty
                        ? caption
                        : l.t('waOps.media.imageLabel'),
                    child: Image.memory(bytes,
                        fit: BoxFit.contain,
                        errorBuilder: (context, _, __) => Padding(
                            padding: const EdgeInsets.all(AnumSpacing.lg),
                            child: Text(l.t('waOps.media.cannotShow'),
                                textAlign: TextAlign.center,
                                style:
                                    text.bodyMedium?.copyWith(color: p.stop)))),
                  ),
                ),
              ),
            ),
            if (caption.isNotEmpty)
              Padding(
                  padding: const EdgeInsets.all(AnumSpacing.md),
                  child: Text(caption,
                      textDirection: waTextDirection(caption),
                      style: text.bodyMedium)),
          ]),
        ),
      ),
    );
  }
}
