import 'package:flutter/widgets.dart';

/// Creates a controller once for a pushed screen and disposes it when the
/// screen leaves the tree, so screens opened from Settings do not leak their
/// listeners (members, budgets, approval policy, accept invitation).
class OwnedController<T extends ChangeNotifier> extends StatefulWidget {
  const OwnedController(
      {required this.create, required this.builder, super.key});
  final T Function() create;
  final Widget Function(BuildContext context, T controller) builder;

  @override
  State<OwnedController<T>> createState() => _OwnedControllerState<T>();
}

class _OwnedControllerState<T extends ChangeNotifier>
    extends State<OwnedController<T>> {
  late final T controller = widget.create();

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.builder(context, controller);
}
