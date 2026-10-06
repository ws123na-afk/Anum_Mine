import 'package:flutter/material.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'work_status.dart';
import '../../src/widgets/depth.dart';

class FileDetailSheet extends StatelessWidget {
  const FileDetailSheet(
      {super.key,
      required this.file,
      required this.onDownload,
      required this.onDelete});
  final WorkspaceFile file;
  final VoidCallback onDownload, onDelete;
  @override
  Widget build(BuildContext context) => SafeArea(
      child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(file.name, style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                Text('${file.contentType} · ${_bytes(file.sizeBytes)}'),
                Text('Created ${_when(file.createdAt)}'),
                const SizedBox(height: 18),
                FilledButton.icon(
                    onPressed: () {
                      Navigator.pop(context);
                      onDownload();
                    },
                    icon: const Icon(Icons.download),
                    label: const Text('Download')),
                OutlinedButton.icon(
                    onPressed: () {
                      Navigator.pop(context);
                      onDelete();
                    },
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('Delete'))
              ])));
}

class AutomationDetailScreen extends StatelessWidget {
  const AutomationDetailScreen(
      {super.key, required this.controller, required this.run});
  final WorkspaceController controller;
  final WorkspaceAutomation run;
  @override
  Widget build(BuildContext context) {
    final matches = controller.workflowDefinitions
        .where((x) => x.id == run.workflowId)
        .toList();
    final workflow = matches.isEmpty ? null : matches.first;
    return Scaffold(
        appBar: AppBar(title: const Text('Automation run')),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          Row(children: [
            Expanded(
                child: Text(run.name,
                    style: Theme.of(context).textTheme.titleLarge)),
            Chip(label: Text(run.status))
          ]),
          const SizedBox(height: 8),
          Text(
              '${run.currentStep} of ${run.stepCount} steps · updated ${_when(run.updatedAt)}'),
          if (workflow != null) ...[
            const Divider(height: 32),
            Text(workflow.description),
            ...workflow.steps.asMap().entries.map((x) => ListTile(
                leading: CircleAvatar(child: Text('${x.key + 1}')),
                title: Text(x.value)))
          ],
          const SizedBox(height: 16),
          Wrap(spacing: 8, children: [
            if (run.status == 'paused')
              FilledButton(
                  onPressed: () =>
                      controller.transitionAutomation(run.id, 'resume'),
                  child: const Text('Resume')),
            if (run.status == 'failed' || run.status == 'cancelled')
              FilledButton(
                  onPressed: () =>
                      controller.transitionAutomation(run.id, 'retry'),
                  child: const Text('Retry')),
            if (run.status != 'completed' && run.status != 'cancelled')
              OutlinedButton(
                  onPressed: () =>
                      controller.transitionAutomation(run.id, 'cancel'),
                  child: const Text('Cancel'))
          ])
        ]));
  }
}

class AutomationCreateSheet extends StatefulWidget {
  const AutomationCreateSheet({super.key, required this.controller});
  final WorkspaceController controller;
  @override
  State<AutomationCreateSheet> createState() => _AutomationCreateState();
}

class _AutomationCreateState extends State<AutomationCreateSheet> {
  final name = TextEditingController(),
      description = TextEditingController(),
      action = TextEditingController(text: 'task.execute');
  @override
  void dispose() {
    name.dispose();
    description.dispose();
    action.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SafeArea(
      child: Padding(
          padding: EdgeInsets.fromLTRB(
              20, 0, 20, MediaQuery.viewInsetsOf(context).bottom + 20),
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Create automation',
                    style: Theme.of(context).textTheme.titleLarge),
                TextField(
                    controller: name,
                    decoration: const InputDecoration(labelText: 'Name')),
                TextField(
                    controller: description,
                    decoration:
                        const InputDecoration(labelText: 'Description')),
                TextField(
                    controller: action,
                    decoration:
                        const InputDecoration(labelText: 'First action')),
                const SizedBox(height: 16),
                FilledButton(
                    onPressed: () {
                      if (name.text.trim().isEmpty) return;
                      widget.controller.createAutomation(name.text.trim(),
                          description.text.trim(), action.text.trim());
                      Navigator.pop(context);
                    },
                    child: const Text('Create'))
              ])));
}

class ScheduleCreateSheet extends StatefulWidget {
  const ScheduleCreateSheet({super.key, required this.controller});
  final WorkspaceController controller;
  @override
  State<ScheduleCreateSheet> createState() => _ScheduleCreateState();
}

class _ScheduleCreateState extends State<ScheduleCreateSheet> {
  String? workflow;
  final name = TextEditingController(),
      cron = TextEditingController(text: '0 9 * * 1'),
      timezone = TextEditingController(text: 'UTC');
  @override
  void dispose() {
    name.dispose();
    cron.dispose();
    timezone.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SafeArea(
      child: Padding(
          padding: EdgeInsets.fromLTRB(
              20, 0, 20, MediaQuery.viewInsetsOf(context).bottom + 20),
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Schedule workflow',
                    style: Theme.of(context).textTheme.titleLarge),
                DropdownButtonFormField<String>(
                    initialValue: workflow,
                    decoration: const InputDecoration(labelText: 'Workflow'),
                    items: widget.controller.workflowDefinitions
                        .map((x) =>
                            DropdownMenuItem(value: x.id, child: Text(x.name)))
                        .toList(),
                    onChanged: (v) => setState(() => workflow = v)),
                TextField(
                    controller: name,
                    decoration:
                        const InputDecoration(labelText: 'Schedule name')),
                TextField(
                    controller: cron,
                    decoration:
                        const InputDecoration(labelText: 'Cron expression')),
                TextField(
                    controller: timezone,
                    decoration: const InputDecoration(labelText: 'Timezone')),
                const SizedBox(height: 16),
                FilledButton(
                    onPressed: workflow == null
                        ? null
                        : () {
                            widget.controller.createSchedule(
                                workflow!,
                                name.text.trim(),
                                cron.text.trim(),
                                timezone.text.trim());
                            Navigator.pop(context);
                          },
                    child: const Text('Create schedule'))
              ])));
}

String _when(DateTime value) => relativeTime(value);
String _bytes(int value) => formatBytes(value);
