import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'admin_controller.dart';
import 'admin_models.dart';
import 'approval_policy.dart';

/// Owner screen: the workspace approval policy (docs/approvals-and-risk.md,
/// Workspace Approval Policy). Members see it read-only; a 403 on saving shows
/// the API's answer like the other owner screens.
class ApprovalPolicyScreen extends StatefulWidget {
  const ApprovalPolicyScreen(
      {required this.controller, required this.workspaceId, super.key});
  final ApprovalPolicyController controller;
  final String workspaceId;
  @override
  State<ApprovalPolicyScreen> createState() => _ApprovalPolicyScreenState();
}

class _ApprovalPolicyScreenState extends State<ApprovalPolicyScreen> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          return Scaffold(
            appBar: AppBar(title: const Text('Approval policy'), actions: [
              IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.saving ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                eyebrow: 'Workspace safety',
                title: 'Who must approve risky actions',
                subtitle:
                    'The approval rules for agent actions in ${widget.workspaceId}. Every change is recorded in the audit log.',
                large: true,
              ),
              ...switch (c.phase) {
                AdminPhase.initial || AdminPhase.loading => [
                    const Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: Center(child: CircularProgressIndicator()))
                  ],
                AdminPhase.forbidden => [
                    AnumSurface(
                        accent: p.warn,
                        child: AnumEmpty(
                          icon: Icons.lock_outline,
                          title: 'Access required',
                          message:
                              'Reading the approval policy needs an active membership in this workspace.\n'
                              'The API answered: ${c.loadMessage}',
                        )),
                  ],
                AdminPhase.offline || AdminPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                          icon: c.phase == AdminPhase.offline
                              ? Icons.cloud_off_outlined
                              : Icons.error_outline,
                          title: 'The approval policy could not be loaded',
                          message: c.loadMessage ?? 'Try again.',
                          action: 'Retry',
                          onAction: c.load,
                        )),
                  ],
                AdminPhase.ready => _ready(context, c),
              },
            ]),
          );
        },
      );

  List<Widget> _ready(BuildContext context, ApprovalPolicyController c) {
    final p = context.palette;
    final draft = c.draft!;
    final role = c.role;
    return [
      if (c.readOnly)
        AnumSurface(
            key: const Key('policy-owner-required'),
            accent: p.warn,
            child: AnumEmpty(
              icon: Icons.lock_outline,
              title: 'Owner access required',
              message: [
                'Only workspace owners can change the approval policy.'
                    '${role != null && role != 'owner' ? ' Your role here is ${roleLabel(role).toLowerCase()}.' : ''}'
                    ' What is in force is shown below; ask an owner to change it.',
                if (c.denied != null) 'The API answered: ${c.denied}',
              ].join('\n'),
            )),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const AnumHeader(
            eyebrow: 'In force',
            title: 'Approval rules',
            subtitle:
                "High-risk tools always pause for an owner's approval. These two switches add to that.",
          ),
          for (final key in PolicySwitch.values)
            _SwitchRow(
              item: key,
              on: draft.value(key),
              enabled: !c.readOnly && !c.saving,
              onChanged: (on) => c.toggle(key, on),
            ),
          if (c.warning != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(c.warning!,
                key: const Key('policy-warning'),
                style: TextStyle(color: p.warn, fontSize: 13)),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Text(policyUpdatedLabel(c.policy!, exactTime),
              style: TextStyle(color: p.faint, fontSize: 12)),
          if (c.notice != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(c.notice!,
                key: const Key('policy-notice'),
                style: TextStyle(color: c.noticeFailed ? p.stop : p.ok)),
          ],
          if (!c.readOnly) ...[
            const SizedBox(height: AnumSpacing.md),
            Wrap(spacing: 8, runSpacing: 8, children: [
              FilledButton.icon(
                  onPressed: c.saving || !c.changed ? null : c.save,
                  icon: const Icon(Icons.save_outlined),
                  label: Text(c.saving ? 'Saving...' : 'Save policy')),
              if (c.changed)
                OutlinedButton(
                    onPressed: c.saving ? null : c.undo,
                    child: const Text('Undo changes')),
            ]),
          ],
        ]),
      ),
    ];
  }
}

class _SwitchRow extends StatelessWidget {
  const _SwitchRow(
      {required this.item,
      required this.on,
      required this.enabled,
      required this.onChanged});
  final PolicySwitch item;
  final bool on, enabled;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = policySwitchText[item]!;
    Widget line(String label, String body) => Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text.rich(TextSpan(children: [
            TextSpan(
                text: '$label  ',
                style: TextStyle(color: p.muted, fontWeight: FontWeight.w700)),
            TextSpan(text: body),
          ])),
        );
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.md),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Divider(height: 1, color: p.line),
        // A plain row rather than SwitchListTile: the surface paints its own
        // background, which would hide a ListTile's ink.
        MergeSemantics(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(children: [
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(text.title,
                          style: TextStyle(
                              color: p.text, fontWeight: FontWeight.w700)),
                      Text(on ? 'On' : 'Off',
                          style: TextStyle(
                              color: on ? p.ok : p.faint, fontSize: 12.5)),
                    ]),
              ),
              Switch(
                key: Key('policy-${item.name}'),
                value: on,
                onChanged: enabled ? onChanged : null,
              ),
            ]),
          ),
        ),
        DefaultTextStyle.merge(
          style: TextStyle(color: p.faint, fontSize: 12.5),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            DefaultTextStyle.merge(
                style: TextStyle(color: on ? p.text : p.faint),
                child: line('When on', text.on)),
            DefaultTextStyle.merge(
                style: TextStyle(color: on ? p.faint : p.text),
                child: line('When off', text.off)),
          ]),
        ),
      ]),
    );
  }
}
