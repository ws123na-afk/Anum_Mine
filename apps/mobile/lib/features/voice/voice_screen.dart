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
        builder: (context, _) => ColoredBox(color:const Color(0xFFF3F5F6),child:ListView(
          padding: const EdgeInsets.fromLTRB(24,20,24,28),
          children: [
            const Row(mainAxisAlignment:MainAxisAlignment.spaceBetween,children:[Text('9:41',style:TextStyle(fontSize:12,color:Color(0xFF667980))),Text('5G  100%',style:TextStyle(fontSize:12,color:Color(0xFF667980)))]),
            const SizedBox(height:14),Text('VOICE COMMAND · ${controller.phase==VoicePhase.listening?'LISTENING':'READY'}',style:const TextStyle(fontSize:11,fontWeight:FontWeight.w600,color:Color(0xFF087568))),
            const SizedBox(height:14),const Text('Speak a command',style:TextStyle(fontSize:26,fontWeight:FontWeight.w700,color:Color(0xFF172026))),
            const SizedBox(height:14),const Text('Short commands work best. Nothing runs until you review the transcript.',style:TextStyle(fontSize:13,color:Color(0xFF667980))),
            const SizedBox(height:14),
            _PrivacyControls(controller: controller),
            const SizedBox(height:14),
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
            const SizedBox(height:14),Container(minHeight:104,padding:const EdgeInsets.all(16),decoration:BoxDecoration(color:Colors.white,border:Border.all(color:const Color(0xFFD8E0E3)),borderRadius:BorderRadius.circular(8)),child:const Column(crossAxisAlignment:CrossAxisAlignment.start,mainAxisAlignment:MainAxisAlignment.center,children:[Text('Privacy',style:TextStyle(fontSize:14,fontWeight:FontWeight.w600)),SizedBox(height:8),Text('Audio is handled by device speech recognition. The editable transcript is sent to ANUM only after review.',style:TextStyle(fontSize:12,color:Color(0xFF667980)))])),
          ],
        )),
      );
}

class _PrivacyControls extends StatelessWidget {
  const _PrivacyControls({required this.controller});
  final VoiceController controller;
  @override
  Widget build(BuildContext context) => Container(
        minHeight:104,decoration:BoxDecoration(color:Colors.white,border:Border.all(color:const Color(0xFFD8E0E3)),borderRadius:BorderRadius.circular(8)),child:Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(controller.locale=='ar-SA'?'Arabic (Saudi Arabia)':'English (US)', style: const TextStyle(fontSize:14,fontWeight:FontWeight.w600)),
            const SizedBox(height: AnumSpacing.sm),
            Text('Transcript retention · ${controller.retention==VoiceRetention.session?'Session only':controller.retention==VoiceRetention.thirtyDays?'30 days':'Permanent'}',style:const TextStyle(fontSize:12,color:Color(0xFF667980))),
            Align(alignment:AlignmentDirectional.centerEnd,child:TextButton(onPressed:()=>_voiceOptions(context,controller),child:const Text('Change'))),
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
    return Column(children:[Container(
      minHeight:150,decoration:BoxDecoration(color:const Color(0xFFE7F2EF),borderRadius:BorderRadius.circular(8)),child:Padding(
        padding: const EdgeInsets.all(16),
        child: Column(children: [
          Text(listening?'LISTENING':'MICROPHONE OFF',style:const TextStyle(fontSize:13,fontWeight:FontWeight.w600,color:Color(0xFF087568))),const SizedBox(height:4),Text(listening?'Speak now':'Tap below when ready',style:const TextStyle(fontSize:18,fontWeight:FontWeight.w600)),
          if(controller.transcript.isNotEmpty)...[const SizedBox(height:12),TextFormField(
            key: ValueKey(controller.transcript),
            initialValue: controller.transcript,
            minLines: 4, maxLines: 8,
            onChanged: controller.setTranscript,
            decoration: const InputDecoration(labelText: 'Transcript', hintText: 'Your recognized command appears here'),
          )],
        ]),
      )),const SizedBox(height:14),SizedBox(width:double.infinity,height:48,child:FilledButton(onPressed:listening?controller.stop:controller.start,style:FilledButton.styleFrom(backgroundColor:const Color(0xFF087568),shape:RoundedRectangleBorder(borderRadius:BorderRadius.circular(8))),child:Text(listening?'Stop listening':'Start listening'))),if(controller.transcript.isNotEmpty)TextButton.icon(onPressed:controller.discard,icon:const Icon(Icons.delete_outline),label:const Text('Discard transcript'))]);
  }
}

Future<void> _voiceOptions(BuildContext context,VoiceController controller)=>showModalBottomSheet(context:context,showDragHandle:true,builder:(context)=>SafeArea(child:Column(mainAxisSize:MainAxisSize.min,children:[ListTile(title:const Text('English (US)'),onTap:(){controller.setLocale('en-US');Navigator.pop(context);}),ListTile(title:const Text('Arabic (Saudi Arabia)'),onTap:(){controller.setLocale('ar-SA');Navigator.pop(context);}),const Divider(),...VoiceRetention.values.map((value)=>RadioListTile<VoiceRetention>(value:value,groupValue:controller.retention,onChanged:(next){if(next!=null)controller.setRetention(next);Navigator.pop(context);},title:Text(value==VoiceRetention.session?'Session only':value==VoiceRetention.thirtyDays?'30 days':'Permanent')))])));

class _CommandReview extends StatelessWidget {
  const _CommandReview({required this.controller}); final VoiceController controller;
  @override Widget build(BuildContext context) => Card(child: Padding(padding: const EdgeInsets.all(AnumSpacing.md), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [Text('Review command', style: Theme.of(context).textTheme.titleMedium), const SizedBox(height: AnumSpacing.xs), const Text('ANUM will create and run a task using the exact transcript above. Governed actions will pause for visual approval.'), const SizedBox(height: AnumSpacing.md), FilledButton.icon(onPressed: controller.canSubmit ? controller.submit : null, icon: const Icon(Icons.play_arrow), label: const Text('Create and run task'))])));
}

class _Progress extends StatelessWidget { const _Progress(); @override Widget build(BuildContext context) => const Card(child: Padding(padding: EdgeInsets.all(AnumSpacing.lg), child: Row(children: [CircularProgressIndicator(), SizedBox(width: AnumSpacing.md), Expanded(child: Text('Creating the task and starting the governed agent run...'))]))); }
class _Completed extends StatelessWidget { const _Completed({required this.controller}); final VoiceController controller; @override Widget build(BuildContext context) { final command=controller.command!; return Card(child: Padding(padding: const EdgeInsets.all(AnumSpacing.md), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [Text('Command accepted', style: Theme.of(context).textTheme.titleMedium), const SizedBox(height: AnumSpacing.xs), Text(command.title), const SizedBox(height: AnumSpacing.xs), Text('Task ${command.taskId} | ${command.status}'), const SizedBox(height: AnumSpacing.md), OutlinedButton.icon(onPressed: controller.speakConfirmation, icon: const Icon(Icons.volume_up_outlined), label: const Text('Read status aloud'))]))); } }
class _Message extends StatelessWidget { const _Message(this.value,{required this.error}); final String value; final bool error; @override Widget build(BuildContext context) => Material(color:error?Theme.of(context).colorScheme.errorContainer:Theme.of(context).colorScheme.secondaryContainer,borderRadius:BorderRadius.circular(8),child:Padding(padding:const EdgeInsets.all(AnumSpacing.md),child:Text(value))); }
