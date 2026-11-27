import 'package:flutter/scheduler.dart';
import 'package:flutter/widgets.dart';

import '../../src/widgets/model_placeholder_banner.dart';
import 'settings_controller.dart';

/// The placeholder-answers banner fed by [SettingsController.modelStatus]
/// (docs/model-gateway.md#placeholder-answers). Settings can change while
/// another widget builds (sign-out resets it), so a change that arrives
/// during a build is applied after that frame.
class SettingsModelBanner extends StatefulWidget {
  const SettingsModelBanner(
      {required this.settings, required this.onSetUp, super.key});

  final SettingsController settings;
  final VoidCallback onSetUp;

  @override
  State<SettingsModelBanner> createState() => _SettingsModelBannerState();
}

class _SettingsModelBannerState extends State<SettingsModelBanner> {
  @override
  void initState() {
    super.initState();
    widget.settings.addListener(_changed);
  }

  @override
  void didUpdateWidget(SettingsModelBanner old) {
    super.didUpdateWidget(old);
    if (old.settings != widget.settings) {
      old.settings.removeListener(_changed);
      widget.settings.addListener(_changed);
    }
  }

  @override
  void dispose() {
    widget.settings.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (!mounted) return;
    if (SchedulerBinding.instance.schedulerPhase ==
        SchedulerPhase.persistentCallbacks) {
      SchedulerBinding.instance.addPostFrameCallback((_) {
        if (mounted) setState(() {});
      });
    } else {
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final status = widget.settings.modelStatus;
    return AnumModelPlaceholderBanner(
      placeholder: status?.placeholder,
      workspaceSource: status?.source == 'workspace',
      onSetUp: widget.onSetUp,
    );
  }
}
