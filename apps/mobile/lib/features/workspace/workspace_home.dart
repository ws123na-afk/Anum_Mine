import 'package:flutter/material.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'workspace_screens.dart';
import '../voice/voice_controller.dart';
import '../voice/voice_screen.dart';
import '../settings/settings_controller.dart';
import '../settings/settings_screen.dart';
import '../governance/governance_controller.dart';
import '../governance/governance_screen.dart';
import '../governance/figma_governance_screens.dart';
import '../../src/localization/anum_localizations.dart';
import 'figma_home_screen.dart';
import 'figma_tasks_screen.dart';
import 'figma_operations_screens.dart';

class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome({super.key, required this.controller, required this.voiceController, required this.settingsController, required this.governanceController, required this.localeController, this.initialIndex = 0});
  final WorkspaceController controller;
  final VoiceController voiceController;
  final SettingsController settingsController;
  final GovernanceController governanceController;
  final LocaleController localeController;
  final int initialIndex;
  @override State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome> with RestorationMixin {
  late final RestorableInt _index;
  int get index => _index.value;
  @override String? get restorationId => 'workspace_home';
  @override void restoreState(RestorationBucket? oldBucket, bool initialRestore) => registerForRestoration(_index, 'selected_destination');
  @override void initState() { super.initState(); _index = RestorableInt(widget.initialIndex); if (widget.controller.phase == LoadPhase.initial) widget.controller.load(); }
  @override Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final pages = [FigmaHomeScreen(controller:widget.controller),FigmaTasksScreen(controller: widget.controller), VoiceScreen(controller: widget.voiceController), FigmaApprovalsScreen(controller: widget.controller), FigmaAutomationsScreen(controller: widget.controller), FigmaFilesMemoryScreen(controller: widget.controller)];
      final l = context.anum;
      final destinations = [
        NavigationDestination(icon:const Icon(Icons.home_outlined),selectedIcon:const Icon(Icons.home),label:l.t('home')),
        NavigationDestination(icon: const Icon(Icons.task_alt_outlined), selectedIcon: const Icon(Icons.task_alt), label: l.t('tasks')),
        NavigationDestination(icon: const Icon(Icons.mic_none), selectedIcon: const Icon(Icons.mic), label: l.t('voice')),
        NavigationDestination(icon: Badge(isLabelVisible: widget.controller.pendingApprovals.isNotEmpty, label: Text('${widget.controller.pendingApprovals.length}'), child: const Icon(Icons.approval_outlined)), selectedIcon: const Icon(Icons.approval), label: l.t('approvals')),
        NavigationDestination(icon: const Icon(Icons.account_tree_outlined), selectedIcon: const Icon(Icons.account_tree), label: l.t('automation')),
        NavigationDestination(icon: const Icon(Icons.folder_outlined), selectedIcon: const Icon(Icons.folder), label: l.t('resources')),
      ];
      final compactDestinations=[NavigationDestination(icon:const Icon(Icons.home_outlined),selectedIcon:const Icon(Icons.home),label:l.t('home')),NavigationDestination(icon:const Icon(Icons.task_alt_outlined),selectedIcon:const Icon(Icons.task_alt),label:l.t('tasks')),NavigationDestination(icon:const Icon(Icons.folder_outlined),selectedIcon:const Icon(Icons.folder),label:l.t('resources')),const NavigationDestination(icon:Icon(Icons.more_horiz),label:'More')];
      final content = Column(children: [if (widget.controller.phase == LoadPhase.offline) _StateBanner(icon: Icons.cloud_off, text: l.t('offline')), if (widget.controller.message != null && widget.controller.phase == LoadPhase.error) _StateBanner(icon: Icons.error_outline, text: widget.controller.message!), Expanded(child: widget.controller.phase == LoadPhase.loading && widget.controller.snapshot == null ? const Center(child: CircularProgressIndicator()) : IndexedStack(index: index, children: pages))]);
      return LayoutBuilder(builder: (context, constraints) {
        final expanded = constraints.maxWidth >= 840;
        return Scaffold(
        appBar: expanded?AppBar(title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(l.t('workspace')), Text(l.t('subtitle'), style: const TextStyle(fontSize: 12, fontWeight: FontWeight.normal))]), actions: [PopupMenuButton<String>(tooltip:l.t('language'),onSelected:widget.localeController.select,itemBuilder:(_)=>[PopupMenuItem(value:'en',child:Text(l.t('english'))),PopupMenuItem(value:'ar',child:Text(l.t('arabic')))],icon:const Icon(Icons.language)),IconButton(onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => GovernanceScreen(controller: widget.governanceController))), tooltip: l.t('organization'), icon: const Icon(Icons.admin_panel_settings_outlined)), IconButton(onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => SettingsScreen(controller: widget.settingsController))), tooltip: l.t('settings'), icon: const Icon(Icons.settings_outlined)), IconButton(onPressed: widget.controller.mutating ? null : widget.controller.load, tooltip: l.t('refresh'), icon: const Icon(Icons.refresh))]):null,
        body: expanded ? Row(children: [NavigationRail(selectedIndex: index, onDestinationSelected: _select, labelType: NavigationRailLabelType.all, destinations: destinations.map((item) => NavigationRailDestination(icon: item.icon, selectedIcon: item.selectedIcon, label: Text(item.label))).toList()), const VerticalDivider(width: 1), Expanded(child: content)]) : content,
        bottomNavigationBar: expanded ? null : NavigationBar(selectedIndex:index==0?0:index==1?1:index==5?2:3,onDestinationSelected:(value){if(value==0)_select(0);else if(value==1)_select(1);else if(value==2)_select(5);else _showMore(context);},destinations:compactDestinations),
      );});
    },
  );

  void _select(int value) => setState(() => _index.value = value);

  Future<void> _showMore(BuildContext context)async{final value=await showModalBottomSheet<int>(context:context,showDragHandle:true,builder:(context)=>SafeArea(child:Column(mainAxisSize:MainAxisSize.min,children:[ListTile(leading:const Icon(Icons.mic),title:const Text('Voice'),onTap:()=>Navigator.pop(context,2)),ListTile(leading:const Icon(Icons.approval_outlined),title:const Text('Approvals'),onTap:()=>Navigator.pop(context,3)),ListTile(leading:const Icon(Icons.account_tree_outlined),title:const Text('Automations'),onTap:()=>Navigator.pop(context,4)),ListTile(leading:const Icon(Icons.policy_outlined),title:const Text('Policy packs'),onTap:(){Navigator.pop(context);Navigator.push(context,MaterialPageRoute(builder:(_)=>FigmaPolicyPacksScreen(controller:widget.governanceController)));}),ListTile(leading:const Icon(Icons.storefront_outlined),title:const Text('Marketplace'),onTap:(){Navigator.pop(context);Navigator.push(context,MaterialPageRoute(builder:(_)=>FigmaMarketplaceScreen(controller:widget.governanceController)));}),ListTile(leading:const Icon(Icons.route),title:const Text('Advanced routing'),onTap:(){Navigator.pop(context);Navigator.push(context,MaterialPageRoute(builder:(_)=>FigmaRoutingScreen(controller:widget.governanceController)));}),ListTile(leading:const Icon(Icons.settings_outlined),title:const Text('Settings'),onTap:(){Navigator.pop(context);Navigator.push(context,MaterialPageRoute(builder:(_)=>SettingsScreen(controller:widget.settingsController)));})])));if(value!=null)_select(value);}

  @override void dispose() { _index.dispose(); super.dispose(); }
}

class _StateBanner extends StatelessWidget { const _StateBanner({required this.icon, required this.text}); final IconData icon; final String text; @override Widget build(BuildContext context) => Material(color: Theme.of(context).colorScheme.errorContainer, child: SafeArea(bottom: false, child: Padding(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), child: Row(children: [Icon(icon, size: 18), const SizedBox(width: 10), Expanded(child: Text(text, style: Theme.of(context).textTheme.bodySmall))])))); }
