import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import 'voice_controller.dart';
import 'voice_models.dart';

class VoiceScreen extends StatelessWidget {
  const VoiceScreen({required this.controller, super.key});
  final VoiceController controller;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Text('Voice command', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: AnumSpacing.xs),
            Text('Speak a short instruction. You can edit and review it before anything runs.', style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: AnumSpacing.lg),
            _PrivacyControls(controller: controller),
            const SizedBox(height: AnumSpacing.md),
            _CapturePanel(controller: controller),
            if (controller.message != null) ...[
              const SizedBox(height: AnumSpacing.md),
              _Message(controller.message!, error: controller.phase == VoicePhase.error || controller.phase == VoicePhase.permissionDenied),
            ],
            if (controller.phase == VoicePhase.review) ...[
              const SizedBox(height: AnumSpacing.lg),
              _CommandReview(controller: controller),
            ],
            if (controller.phase == VoicePhase.submitting || controller.phase == VoicePhase.running) ...[
              const SizedBox(height: AnumSpacing.lg),
              const _Progress(),
            ],
            if (controller.phase == VoicePhase.completed && controller.command != null) ...[
              const SizedBox(height: AnumSpacing.lg),
              _Completed(controller: controller),
            ],
            if (controller.phase == VoicePhase.permissionDenied) ...[
              const SizedBox(height: AnumSpacing.md),
              OutlinedButton.icon(onPressed: controller.speech.openSettings, icon: const Icon(Icons.settings), label: const Text('Open app settings')),
            ],
          ],
        ),
      );
}

class _PrivacyControls extends StatelessWidget {
  const _PrivacyControls({required this.controller});
  final VoiceController controller;
  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.md),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Language and transcript retention', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: AnumSpacing.sm),
            DropdownButtonFormField<String>(
              initialValue: controller.locale,
              decoration: const InputDecoration(labelText: 'Recognition language'),
              items: const [DropdownMenuItem(value: 'en-US', child: Text('English (US)')), DropdownMenuItem(value: 'ar-SA', child: Text('Arabic (Saudi Arabia)'))],
              onChanged: (value) { if (value != null) controller.setLocale(value); },
            ),
            const SizedBox(height: AnumSpacing.sm),
            SegmentedButton<VoiceRetention>(
              segments: const [ButtonSegment(value: VoiceRetention.session, label: Text('Session')), ButtonSegment(value: VoiceRetention.thirtyDays, label: Text('30 days')), ButtonSegment(value: VoiceRetention.permanent, label: Text('Keep'))],
              selected: {controller.retention},
              onSelectionChanged: (value) => controller.setRetention(value.single),
              showSelectedIcon: false,
            ),
            const SizedBox(height: AnumSpacing.xs),
            Text('Session transcripts are removed when the voice session closes.', style: Theme.of(context).textTheme.bodySmall),
          ]),
        ),
      );
}

class _CapturePanel extends StatelessWidget {
  const _CapturePanel({required this.controller});
  final VoiceController controller;
  @override
  Widget build(BuildContext context) {
    final listening = controller.phase == VoicePhase.listening;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Column(children: [
          AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            width: 72, height: 72,
            decoration: BoxDecoration(shape: BoxShape.circle, color: listening ? Theme.of(context).colorScheme.errorContainer : Theme.of(context).colorScheme.primaryContainer),
            child: Icon(listening ? Icons.graphic_eq : Icons.mic, size: 34),
          ),
          const SizedBox(height: AnumSpacing.sm),
          Text(listening ? 'Listening now' : 'Microphone is off', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AnumSpacing.md),
          TextFormField(
            key: ValueKey(controller.transcript),
            initialValue: controller.transcript,
            minLines: 4, maxLines: 8,
            onChanged: controller.setTranscript,
            decoration: const InputDecoration(labelText: 'Transcript', hintText: 'Your recognized command appears here'),
          ),
          const SizedBox(height: AnumSpacing.md),
          Row(children: [
            Expanded(child: FilledButton.icon(onPressed: listening ? controller.stop : controller.start, icon: Icon(listening ? Icons.stop : Icons.mic), label: Text(listening ? 'Stop listening' : 'Start listening'))),
            if (controller.transcript.isNotEmpty) ...[const SizedBox(width: AnumSpacing.xs), IconButton.outlined(onPressed: controller.discard, tooltip: 'Discard transcript', icon: const Icon(Icons.delete_outline))],
          ]),
        ]),
      ),
    );
  }
}

class _CommandReview extends StatelessWidget {
  const _CommandReview({required this.controller}); final VoiceController controller;
  @override Widget build(BuildContext context) => Card(child: Padding(padding: const EdgeInsets.all(AnumSpacing.md), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [Text('Review command', style: Theme.of(context).textTheme.titleMedium), const SizedBox(height: AnumSpacing.xs), const Text('ANUM will create and run a task using the exact transcript above. Governed actions will pause for visual approval.'), const SizedBox(height: AnumSpacing.md), FilledButton.icon(onPressed: controller.canSubmit ? controller.submit : null, icon: const Icon(Icons.play_arrow), label: const Text('Create and run task'))])));
}

class _Progress extends StatelessWidget { const _Progress(); @override Widget build(BuildContext context) => const Card(child: Padding(padding: EdgeInsets.all(AnumSpacing.lg), child: Row(children: [CircularProgressIndicator(), SizedBox(width: AnumSpacing.md), Expanded(child: Text('Creating the task and starting the governed agent run...'))]))); }
class _Completed extends StatelessWidget { const _Completed({required this.controller}); final VoiceController controller; @override Widget build(BuildContext context) { final command=controller.command!; return Card(child: Padding(padding: const EdgeInsets.all(AnumSpacing.md), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [Text('Command accepted', style: Theme.of(context).textTheme.titleMedium), const SizedBox(height: AnumSpacing.xs), Text(command.title), const SizedBox(height: AnumSpacing.xs), Text('Task ${command.taskId} | ${command.status}'), const SizedBox(height: AnumSpacing.md), OutlinedButton.icon(onPressed: controller.speakConfirmation, icon: const Icon(Icons.volume_up_outlined), label: const Text('Read status aloud'))]))); } }
class _Message extends StatelessWidget { const _Message(this.value,{required this.error}); final String value; final bool error; @override Widget build(BuildContext context) => Material(color:error?Theme.of(context).colorScheme.errorContainer:Theme.of(context).colorScheme.secondaryContainer,borderRadius:BorderRadius.circular(8),child:Padding(padding:const EdgeInsets.all(AnumSpacing.md),child:Text(value))); }
