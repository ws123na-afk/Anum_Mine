import 'package:flutter/material.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'workspace_screens.dart';
import '../voice/voice_controller.dart';
import '../voice/voice_screen.dart';

class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome({super.key, required this.controller, required this.voiceController});
  final WorkspaceController controller;
  final VoiceController voiceController;
  @override State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome> {
  int index = 0;
  @override void initState() { super.initState(); if (widget.controller.phase == LoadPhase.initial) widget.controller.load(); }
  @override Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final pages = [TasksScreen(controller: widget.controller), VoiceScreen(controller: widget.voiceController), ApprovalsScreen(controller: widget.controller), AutomationsScreen(controller: widget.controller), WorkspaceResourcesScreen(controller: widget.controller)];
      return Scaffold(
        appBar: AppBar(title: const Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('ANUM Workspace'), Text('Governed agent operations', style: TextStyle(fontSize: 12, fontWeight: FontWeight.normal))]), actions: [IconButton(onPressed: widget.controller.mutating ? null : widget.controller.load, tooltip: 'Refresh', icon: const Icon(Icons.refresh))]),
        body: Column(children: [if (widget.controller.phase == LoadPhase.offline) const _StateBanner(icon: Icons.cloud_off, text: 'Offline. Showing the most recent workspace state.'), if (widget.controller.message != null && widget.controller.phase == LoadPhase.error) _StateBanner(icon: Icons.error_outline, text: widget.controller.message!), Expanded(child: widget.controller.phase == LoadPhase.loading && widget.controller.snapshot == null ? const Center(child: CircularProgressIndicator()) : IndexedStack(index: index, children: pages))]),
        bottomNavigationBar: NavigationBar(selectedIndex: index, onDestinationSelected: (value) => setState(() => index = value), destinations: [const NavigationDestination(icon: Icon(Icons.task_alt_outlined), selectedIcon: Icon(Icons.task_alt), label: 'Tasks'), const NavigationDestination(icon: Icon(Icons.mic_none), selectedIcon: Icon(Icons.mic), label: 'Voice'), NavigationDestination(icon: Badge(isLabelVisible: widget.controller.pendingApprovals.isNotEmpty, label: Text('${widget.controller.pendingApprovals.length}'), child: const Icon(Icons.approval_outlined)), selectedIcon: const Icon(Icons.approval), label: 'Approvals'), const NavigationDestination(icon: Icon(Icons.account_tree_outlined), selectedIcon: Icon(Icons.account_tree), label: 'Automation'), const NavigationDestination(icon: Icon(Icons.folder_outlined), selectedIcon: Icon(Icons.folder), label: 'Resources')]),
      );
    },
  );
}

class _StateBanner extends StatelessWidget { const _StateBanner({required this.icon, required this.text}); final IconData icon; final String text; @override Widget build(BuildContext context) => Material(color: Theme.of(context).colorScheme.errorContainer, child: SafeArea(bottom: false, child: Padding(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), child: Row(children: [Icon(icon, size: 18), const SizedBox(width: 10), Expanded(child: Text(text, style: Theme.of(context).textTheme.bodySmall))])))); }
