import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../developer/developer_assist.dart';
import '../developer/developer_assist_panel.dart';
import '../developer/developer_screen.dart' show LinkOpener, openInBrowser;
import 'chat_controller.dart';
import 'chat_models.dart';
import '../library/library_models.dart' show visibleReply;
import '../library/library_screens.dart' show ReportAnswerCard;

/// The Assistant destination: the conversation list and the streamed chat
/// (docs/conversations.md). Wide layouts show both side by side; on phones
/// the chat fills the screen and the list opens in its place (Back closes
/// it, see `WorkspaceHome`).
class AssistantScreen extends StatefulWidget {
  const AssistantScreen({
    required this.controller,
    this.active = true,
    this.onOpenSettings,
    this.developer,
    this.onOpenDeveloper,
    this.openLink = openInBrowser,
    this.onOpenReport,
    super.key,
  });

  final ChatController controller;

  /// "View Full Report" on a structured answer's card (docs/reports.md).
  final ValueChanged<String>? onOpenReport;

  /// "build ..." / "developer, ..." go to developer mode after a
  /// confirmation instead of the model (docs/developer-mode.md).
  final DeveloperAssist? developer;
  final VoidCallback? onOpenDeveloper;
  final LinkOpener openLink;

  /// The destination is the one on screen; loads on first show.
  final bool active;

  /// Opens Settings, where owners raise the monthly model budget.
  final VoidCallback? onOpenSettings;

  @override
  State<AssistantScreen> createState() => _AssistantScreenState();
}

/// At or above this width the list and the chat sit side by side.
const assistantTwoPaneWidth = 720.0;

class _AssistantScreenState extends State<AssistantScreen> {
  final _draft = TextEditingController();
  final _scroll = ScrollController();
  final _input = FocusNode();

  /// Follow the reply only while the reader is at the bottom.
  bool _stick = true;
  bool _atBottom = true;
  bool _followScheduled = false;
  String? _shownId;
  bool _wasStreaming = false;

  ChatController get controller => widget.controller;

  @override
  void initState() {
    super.initState();
    controller.addListener(_onChange);
    if (widget.active) _afterFrame(controller.ensureLoaded);
    _syncDeveloper();
  }

  /// The developer card polls for ready pull requests only while on screen.
  void _syncDeveloper() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) widget.developer?.setActive(widget.active);
      });

  /// Loading notifies listeners, which must not happen while building.
  void _afterFrame(Future<void> Function() action) =>
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) unawaited(action());
      });

  @override
  void didUpdateWidget(AssistantScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onChange);
      widget.controller.addListener(_onChange);
    }
    if (oldWidget.developer != widget.developer) {
      oldWidget.developer?.setActive(false);
    }
    _syncDeveloper();
    if (widget.active && !oldWidget.active) {
      _afterFrame(() async {
        await controller.ensureLoaded();
        await controller.syncWorkspace();
      });
    }
  }

  @override
  void dispose() {
    controller.removeListener(_onChange);
    widget.developer?.setActive(false);
    _draft.dispose();
    _scroll.dispose();
    _input.dispose();
    super.dispose();
  }

  void _onChange() {
    // Another conversation, or a new question: start at the newest message.
    if (controller.activeId != _shownId || controller.opening) {
      _shownId = controller.activeId;
      _setStick(true);
    }
    // A new question brings the newest message back into view.
    if (controller.streaming && !_wasStreaming) _setStick(true);
    _wasStreaming = controller.streaming;
    _follow();
  }

  void _setStick(bool value) {
    _stick = value;
    if (_atBottom != value && mounted) setState(() => _atBottom = value);
  }

  void _follow([int attempt = 0]) {
    if (!_stick || (_followScheduled && attempt == 0)) return;
    _followScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _followScheduled = false;
      if (!mounted || !_stick || !_scroll.hasClients) return;
      final position = _scroll.position;
      if (position.pixels >= position.maxScrollExtent) return;
      position.jumpTo(position.maxScrollExtent);
      // Items built lazily on the way down can grow the extent: settle on
      // the real bottom over the next frames.
      if (attempt < 8) _follow(attempt + 1);
    });
    WidgetsBinding.instance.ensureVisualUpdate();
  }

  bool _onScroll(ScrollNotification notification) {
    if (notification.depth != 0) return false;
    // Only the reader's own scrolling changes whether the list follows.
    final userDriven = switch (notification) {
      ScrollUpdateNotification(:final dragDetails) => dragDetails != null,
      UserScrollNotification() => true,
      _ => false,
    };
    if (!userDriven) return false;
    final metrics = notification.metrics;
    _setStick(metrics.maxScrollExtent - metrics.pixels <= 48);
    return false;
  }

  void _jumpToLatest() {
    _setStick(true);
    if (!_scroll.hasClients) return;
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (reduceMotion) {
      _follow();
    } else {
      unawaited(_scroll
          .animateTo(_scroll.position.maxScrollExtent,
              duration: const Duration(milliseconds: 250),
              curve: Curves.easeOut)
          .then((_) => _follow()));
    }
  }

  void _submit() {
    final content = _draft.text.trim();
    if (content.isEmpty || controller.streaming) return;
    _draft.clear();
    final developer = widget.developer;
    if (developer == null) {
      unawaited(controller.send(content));
      return;
    }
    unawaited(() async {
      final handled = await developer.intercept(content);
      if (handled == null) await controller.send(content);
    }());
  }

  void _newConversation() {
    controller.startNew();
    _input.requestFocus();
  }

  Future<void> _confirmDelete(Conversation conversation) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete conversation?'),
        content: Text('Delete "${conversation.displayTitle}" and all its '
            'messages? This cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              style: FilledButton.styleFrom(
                  backgroundColor: context.palette.stop,
                  foregroundColor:
                      Theme.of(context).brightness == Brightness.dark
                          ? context.palette.background
                          : Colors.white),
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (confirmed ?? false) await controller.delete(conversation);
  }

  @override
  Widget build(BuildContext context) => AnumBackdrop(
        child: SafeArea(
          bottom: false,
          child: ListenableBuilder(
            listenable: Listenable.merge(
                [controller, controller.listOpen, widget.developer]),
            builder: (context, _) => LayoutBuilder(builder: (context, c) {
              final wide = c.maxWidth >= assistantTwoPaneWidth;
              if (wide && controller.listOpen.value) {
                // The list is always visible here; Back must not be held.
                WidgetsBinding.instance
                    .addPostFrameCallback((_) => controller.closeList());
              }
              final gutter = c.maxWidth < AnumBreakpoints.compact
                  ? AnumSpacing.md
                  : AnumSpacing.lg;
              final padding = EdgeInsets.fromLTRB(
                  gutter, AnumSpacing.sm, gutter, AnumSpacing.sm);
              if (wide) {
                return Padding(
                  padding: padding,
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        SizedBox(
                          width: 300,
                          child: AnumSurface(
                              padding: const EdgeInsets.all(AnumSpacing.sm),
                              child: _list(compact: false)),
                        ),
                        const SizedBox(width: AnumSpacing.md),
                        Expanded(
                          child: AnumSurface(
                              padding: const EdgeInsets.all(AnumSpacing.md),
                              child: _chat(compact: false)),
                        ),
                      ]),
                );
              }
              return Padding(
                padding: padding,
                child: controller.listOpen.value
                    ? _list(compact: true)
                    : _chat(compact: true),
              );
            }),
          ),
        ),
      );

  Widget _list({required bool compact}) => _ConversationList(
        controller: controller,
        compact: compact,
        onNew: _newConversation,
        onOpen: (conversation) => unawaited(controller.open(conversation.id)),
        onDelete: _confirmDelete,
      );

  Widget _chat({required bool compact}) =>
      LayoutBuilder(builder: (context, c) => _chatColumn(compact, c.maxHeight));

  Widget _chatColumn(bool compact, double height) {
    final p = context.palette;
    // Short screens (a phone with the keyboard open, large text) keep the
    // header to one line and give notices at most a third of the height,
    // scrolling inside it, so the messages and the composer always fit.
    final dense = height < 480;
    final notices = [
      if (controller.budget != null)
        _BudgetNotice(
          message: controller.budget!,
          onOpenSettings: widget.onOpenSettings,
          onDismiss: controller.dismissBudget,
        ),
      if (controller.notice != null)
        _Notice(
            message: controller.notice!, onDismiss: controller.dismissNotice),
      if (widget.developer case final developer? when developer.hasPanel)
        DeveloperAssistPanel(
          assist: developer,
          onOpenDeveloper: widget.onOpenDeveloper,
          openLink: widget.openLink,
        ),
    ];
    // Header, messages, notices and composer, top to bottom. The notices get
    // at most a third of the height and never more than the header and the
    // composer leave (a short phone at large text), scrolling inside it.
    return CustomMultiChildLayout(
        delegate: _ChatColumnLayout(gap: AnumSpacing.xs),
        children: [
          LayoutId(
            id: _ChatSlot.header,
            child: _ChatHeader(
              controller: controller,
              compact: compact,
              dense: dense,
              onNew: _newConversation,
            ),
          ),
          LayoutId(
            id: _ChatSlot.messages,
            child: Stack(children: [
              Positioned.fill(child: _messages()),
              if (!_atBottom && controller.turns.isNotEmpty)
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: AnumSpacing.xs,
                  child: Center(
                    child: FilledButton.tonalIcon(
                      onPressed: _jumpToLatest,
                      icon: const Icon(Icons.arrow_downward, size: 18),
                      label: const Text('Jump to latest'),
                    ),
                  ),
                ),
            ]),
          ),
          if (notices.isNotEmpty)
            LayoutId(
              id: _ChatSlot.notices,
              child: SingleChildScrollView(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      for (var i = 0; i < notices.length; i++) ...[
                        if (i > 0) const SizedBox(height: AnumSpacing.xs),
                        notices[i],
                      ],
                    ]),
              ),
            ),
          LayoutId(
            id: _ChatSlot.composer,
            child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Expanded(
                child: TextField(
                  key: const Key('chat-input'),
                  controller: _draft,
                  focusNode: _input,
                  minLines: 1,
                  maxLines: 5,
                  maxLength: 8000,
                  keyboardType: TextInputType.multiline,
                  textInputAction: TextInputAction.newline,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: InputDecoration(
                    hintText: 'Ask the assistant…',
                    counterText: '',
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: AnumSpacing.md, vertical: AnumSpacing.sm),
                    hintStyle: TextStyle(color: p.faint),
                  ),
                ),
              ),
              const SizedBox(width: AnumSpacing.xs),
              if (controller.streaming)
                IconButton.filledTonal(
                  key: const Key('chat-stop'),
                  tooltip: 'Stop',
                  onPressed: controller.stop,
                  icon: const Icon(Icons.stop_rounded),
                )
              else
                ValueListenableBuilder<TextEditingValue>(
                  valueListenable: _draft,
                  builder: (context, value, _) => IconButton.filled(
                    key: const Key('chat-send'),
                    tooltip: 'Send',
                    onPressed: value.text.trim().isEmpty ? null : _submit,
                    icon: const Icon(Icons.arrow_upward),
                  ),
                ),
            ]),
          ),
        ]);
  }

  Widget _messages() {
    final p = context.palette;
    final listLoading = controller.listPhase == ChatListPhase.loading ||
        controller.listPhase == ChatListPhase.initial;
    if (controller.opening ||
        (listLoading &&
            controller.turns.isEmpty &&
            controller.activeId == null)) {
      return Center(
        child: Semantics(
          liveRegion: true,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const CircularProgressIndicator(),
            const SizedBox(height: AnumSpacing.sm),
            Text('Loading messages…', style: TextStyle(color: p.muted)),
          ]),
        ),
      );
    }
    if (controller.turns.isEmpty) {
      final listFailed = controller.listPhase == ChatListPhase.error &&
          controller.activeId == null;
      return SingleChildScrollView(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.md),
        child: listFailed
            ? AnumEmpty(
                icon: controller.listError == chatNoPermission
                    ? Icons.lock_outline
                    : Icons.error_outline,
                title: controller.listError == chatNoPermission
                    ? 'The assistant is not available to your role'
                    : 'Conversations could not be loaded',
                message: controller.listError ?? '',
                action: 'Try again',
                onAction: () => unawaited(controller.load(openFirst: true)),
              )
            : _EmptyChat(
                busy: controller.streaming,
                onAsk: (question) => unawaited(controller.send(question)),
              ),
      );
    }
    final turns = controller.turns;
    final retry = controller.canRetryLast;
    return NotificationListener<ScrollNotification>(
      onNotification: _onScroll,
      child: SelectionArea(
        child: ListView.builder(
          key: const Key('chat-log'),
          controller: _scroll,
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          itemCount: turns.length,
          // A readable column on wide screens, centred in the panel.
          itemBuilder: (context, i) => Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 880),
              child: Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                child: _TurnView(
                  turn: turns[i],
                  onOpenReport: widget.onOpenReport,
                  onRetry: retry && i == turns.length - 1
                      ? () => unawaited(controller.retry())
                      : null,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

enum _ChatSlot { header, messages, notices, composer }

/// Lays out the chat column: the header and the composer at their natural
/// height, the notices in at most a third of the height and only the space
/// those two leave, and the messages in the rest.
class _ChatColumnLayout extends MultiChildLayoutDelegate {
  _ChatColumnLayout({required this.gap});
  final double gap;

  @override
  void performLayout(Size size) {
    final width = size.width;
    BoxConstraints upTo(double height) => BoxConstraints(
        minWidth: width, maxWidth: width, maxHeight: math.max(0, height));
    // As in a Column: the header and the composer take their natural height.
    final natural = BoxConstraints(minWidth: width, maxWidth: width);
    final header = layoutChild(_ChatSlot.header, natural);
    final composer = layoutChild(_ChatSlot.composer, natural);
    var free = size.height - header.height - composer.height - 2 * gap;
    var notices = Size.zero;
    if (hasChild(_ChatSlot.notices)) {
      notices = layoutChild(
          _ChatSlot.notices, upTo(math.min(size.height / 3, free - gap)));
      free -= notices.height + gap;
    }
    layoutChild(_ChatSlot.messages,
        BoxConstraints.tightFor(width: width, height: math.max(0, free)));
    var y = 0.0;
    positionChild(_ChatSlot.header, Offset(0, y));
    y += header.height + gap;
    positionChild(_ChatSlot.messages, Offset(0, y));
    y += math.max(0, free) + gap;
    if (hasChild(_ChatSlot.notices)) {
      positionChild(_ChatSlot.notices, Offset(0, y));
      y += notices.height + gap;
    }
    positionChild(_ChatSlot.composer, Offset(0, y));
  }

  @override
  bool shouldRelayout(_ChatColumnLayout oldDelegate) => oldDelegate.gap != gap;
}

class _ChatHeader extends StatelessWidget {
  const _ChatHeader(
      {required this.controller,
      required this.compact,
      required this.dense,
      required this.onNew});
  final ChatController controller;
  final bool compact;

  /// One line only: little height to spare.
  final bool dense;
  final VoidCallback onNew;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final count = controller.conversations.length;
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (!dense) ...[
            Text('Assistant',
                style: TextStyle(
                    color: p.sky, fontSize: 12.5, fontWeight: FontWeight.w600)),
            const SizedBox(height: AnumSpacing.xxs),
          ],
          Semantics(
            header: true,
            child: Text(controller.title,
                maxLines: dense ? 1 : 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    color: p.text,
                    fontSize: 19,
                    fontWeight: FontWeight.w700,
                    height: 1.15)),
          ),
          if (controller.streaming && !dense) ...[
            const SizedBox(height: AnumSpacing.xxs),
            const AnumPill(
                label: 'Answering…',
                tone: AnumTone.info,
                icon: Icons.auto_awesome),
          ],
        ]),
      ),
      if (compact)
        IconButton(
          tooltip: count == 0 ? 'Conversations' : 'Conversations ($count)',
          onPressed: controller.showList,
          icon: const Icon(Icons.forum_outlined),
        ),
      if (compact)
        IconButton(
          tooltip: 'New conversation',
          onPressed: controller.streaming ? null : onNew,
          icon: const Icon(Icons.add_comment_outlined),
        ),
    ]);
  }
}

class _EmptyChat extends StatelessWidget {
  const _EmptyChat({required this.busy, required this.onAsk});
  final bool busy;
  final ValueChanged<String> onAsk;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const Center(child: AnumIconChip(icon: Icons.auto_awesome)),
      const SizedBox(height: AnumSpacing.sm),
      Text('Ask anything',
          textAlign: TextAlign.center,
          style: TextStyle(
              color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
      const SizedBox(height: AnumSpacing.xxs),
      Text(
          'Answers stream in as they are written, and follow-ups remember '
          'this conversation. To have something done, ask for a task: tasks '
          "run under this workspace's approvals.",
          textAlign: TextAlign.center,
          style: TextStyle(color: p.muted, fontSize: 13.5)),
      const SizedBox(height: AnumSpacing.md),
      for (final question in chatSuggestions) ...[
        OutlinedButton(
          onPressed: busy ? null : () => onAsk(question),
          style: OutlinedButton.styleFrom(
              alignment: AlignmentDirectional.centerStart,
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.sm)),
          child: Text(question),
        ),
        const SizedBox(height: AnumSpacing.xs),
      ],
    ]);
  }
}

class _TurnView extends StatelessWidget {
  const _TurnView({required this.turn, this.onRetry, this.onOpenReport});
  final ValueChanged<String>? onOpenReport;
  final UiTurn turn;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final body = TextStyle(color: p.text, fontSize: 15, height: 1.4);
    final meta = TextStyle(color: p.muted, fontSize: 12.5);
    final error = TextStyle(color: p.stop, fontSize: 12.5);
    if (turn.role == ChatRole.user) {
      return Padding(
        padding: const EdgeInsetsDirectional.only(start: AnumSpacing.xxl),
        child: Align(
          alignment: AlignmentDirectional.centerEnd,
          child: Semantics(
            container: true,
            label: 'You',
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: p.violet.withValues(alpha: 0.18),
                borderRadius: BorderRadius.circular(18),
                border: Border.all(color: p.violet.withValues(alpha: 0.3)),
              ),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: AnumSpacing.md, vertical: AnumSpacing.sm),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(turn.content, style: body),
                      if (turn.status == ChatTurnStatus.failed) ...[
                        const SizedBox(height: AnumSpacing.xxs),
                        Text('Not sent.', style: error),
                      ],
                    ]),
              ),
            ),
          ),
        ),
      );
    }
    final streaming = turn.status == ChatTurnStatus.streaming;
    return Padding(
      padding: const EdgeInsetsDirectional.only(end: AnumSpacing.lg),
      child: Align(
        alignment: AlignmentDirectional.centerStart,
        child: Semantics(
          container: true,
          label: 'Assistant',
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: p.surface2,
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: p.line),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.sm),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (visibleReply(turn.content).isNotEmpty)
                      Text.rich(
                          TextSpan(text: visibleReply(turn.content), children: [
                            if (streaming)
                              WidgetSpan(
                                alignment: PlaceholderAlignment.middle,
                                child: ExcludeSemantics(
                                  child: Container(
                                    width: 2,
                                    height: 16,
                                    margin: const EdgeInsetsDirectional.only(
                                        start: AnumSpacing.xxs),
                                    color: p.sky,
                                  ),
                                ),
                              ),
                          ]),
                          style: body)
                    else if (streaming)
                      Semantics(
                        liveRegion: true,
                        label: 'Thinking',
                        child: ExcludeSemantics(
                          child: Row(mainAxisSize: MainAxisSize.min, children: [
                            Icon(Icons.more_horiz, color: p.sky, size: 20),
                            const SizedBox(width: AnumSpacing.xs),
                            Flexible(child: Text('Thinking…', style: meta)),
                          ]),
                        ),
                      ),
                    if (turn.report case final card?)
                      ReportAnswerCard(card: card, onOpen: onOpenReport),
                    if (turn.status == ChatTurnStatus.cancelled) ...[
                      if (turn.content.isNotEmpty)
                        const SizedBox(height: AnumSpacing.xxs),
                      Text(
                          turn.content.isEmpty
                              ? 'Stopped before any answer.'
                              : 'Stopped.',
                          style: meta),
                    ],
                    if (turn.status == ChatTurnStatus.failed) ...[
                      if (turn.content.isNotEmpty)
                        const SizedBox(height: AnumSpacing.xxs),
                      Text(turn.error ?? 'No answer.', style: error),
                    ],
                    if (onRetry != null) ...[
                      const SizedBox(height: AnumSpacing.xxs),
                      TextButton.icon(
                        onPressed: onRetry,
                        icon: const Icon(Icons.replay, size: 18),
                        label: const Text('Retry'),
                      ),
                    ],
                  ]),
            ),
          ),
        ),
      ),
    );
  }
}

class _ConversationList extends StatelessWidget {
  const _ConversationList({
    required this.controller,
    required this.compact,
    required this.onNew,
    required this.onOpen,
    required this.onDelete,
  });

  final ChatController controller;
  final bool compact;
  final VoidCallback onNew;
  final ValueChanged<Conversation> onOpen;
  final Future<void> Function(Conversation) onDelete;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final items = controller.conversations;
    final Widget content;
    if (controller.listPhase == ChatListPhase.loading ||
        controller.listPhase == ChatListPhase.initial) {
      content = Center(
        child: Semantics(
          liveRegion: true,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const CircularProgressIndicator(),
            const SizedBox(height: AnumSpacing.sm),
            Text('Loading conversations…', style: TextStyle(color: p.muted)),
          ]),
        ),
      );
    } else if (controller.listPhase == ChatListPhase.error && items.isEmpty) {
      content = SingleChildScrollView(
        child: AnumEmpty(
          icon: controller.listError == chatNoPermission
              ? Icons.lock_outline
              : Icons.error_outline,
          title: 'Conversations could not be loaded',
          message: controller.listError ?? '',
          action: 'Try again',
          onAction: () => unawaited(
              controller.load(openFirst: controller.activeId == null)),
        ),
      );
    } else if (items.isEmpty) {
      content = SingleChildScrollView(
        child: AnumEmpty(
          icon: Icons.forum_outlined,
          title: 'No conversations yet',
          message: 'Ask a question to start one.',
          action: compact ? 'Ask a question' : null,
          onAction: compact ? onNew : null,
        ),
      );
    } else {
      content = ListView.separated(
        key: const Key('chat-conversations'),
        itemCount: items.length,
        separatorBuilder: (_, __) => const SizedBox(height: AnumSpacing.xxs),
        itemBuilder: (context, i) => _ConversationTile(
          conversation: items[i],
          selected: items[i].id == controller.activeId,
          enabled: !controller.streaming || items[i].id == controller.activeId,
          deletable: !controller.streaming,
          onOpen: onOpen,
          onDelete: onDelete,
        ),
      );
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        if (compact)
          IconButton(
            tooltip: 'Back to chat',
            onPressed: controller.closeList,
            icon: const Icon(Icons.arrow_back),
          ),
        Expanded(
          child: Semantics(
            header: true,
            // One word: shrink at very large text instead of breaking it.
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: AlignmentDirectional.centerStart,
              child: Text('Conversations',
                  style: TextStyle(
                      color: p.text,
                      fontSize: 17,
                      fontWeight: FontWeight.w700)),
            ),
          ),
        ),
        const SizedBox(width: AnumSpacing.xs),
        if (compact)
          IconButton.filledTonal(
            tooltip: 'New conversation',
            onPressed: controller.streaming ? null : onNew,
            icon: const Icon(Icons.add),
          )
        else
          FilledButton.tonalIcon(
            onPressed: controller.streaming ? null : onNew,
            icon: const Icon(Icons.add, size: 18),
            label: const Text('New'),
          ),
      ]),
      if (controller.notice != null && compact) ...[
        const SizedBox(height: AnumSpacing.xs),
        _Notice(
            message: controller.notice!, onDismiss: controller.dismissNotice),
      ],
      const SizedBox(height: AnumSpacing.xs),
      Expanded(child: content),
    ]);
  }
}

class _ConversationTile extends StatelessWidget {
  const _ConversationTile({
    required this.conversation,
    required this.selected,
    required this.enabled,
    required this.deletable,
    required this.onOpen,
    required this.onDelete,
  });

  final Conversation conversation;
  final bool selected, enabled, deletable;
  final ValueChanged<Conversation> onOpen;
  final Future<void> Function(Conversation) onDelete;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final title = conversation.displayTitle;
    return Material(
      color: selected ? p.violet.withValues(alpha: 0.16) : Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(
            color: selected
                ? p.violet.withValues(alpha: 0.35)
                : Colors.transparent),
      ),
      clipBehavior: Clip.antiAlias,
      child: Row(children: [
        Expanded(
          child: Semantics(
            selected: selected,
            button: true,
            child: InkWell(
              onTap: enabled ? () => onOpen(conversation) : null,
              child: Padding(
                padding: const EdgeInsetsDirectional.fromSTEB(
                    AnumSpacing.sm, AnumSpacing.sm, 0, AnumSpacing.sm),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                              color: enabled ? p.text : p.faint,
                              fontSize: 14.5,
                              fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(relativeTime(conversation.updatedAt),
                          style: TextStyle(color: p.faint, fontSize: 12)),
                    ]),
              ),
            ),
          ),
        ),
        IconButton(
          tooltip: 'Delete $title',
          onPressed: deletable ? () => unawaited(onDelete(conversation)) : null,
          icon: const Icon(Icons.delete_outline),
        ),
      ]),
    );
  }
}

class _BudgetNotice extends StatelessWidget {
  const _BudgetNotice(
      {required this.message, required this.onDismiss, this.onOpenSettings});
  final String message;
  final VoidCallback onDismiss;
  final VoidCallback? onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      key: const Key('chat-budget'),
      accent: p.stop,
      padding: const EdgeInsets.fromLTRB(
          AnumSpacing.md, AnumSpacing.sm, AnumSpacing.xs, AnumSpacing.sm),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
          child: Semantics(
            liveRegion: true,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Monthly model budget reached',
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
              const SizedBox(height: AnumSpacing.xxs),
              Text(message, style: TextStyle(color: p.muted, fontSize: 13)),
              const SizedBox(height: AnumSpacing.xxs),
              Text('Owners can raise it in Settings › Model budgets.',
                  style: TextStyle(color: p.faint, fontSize: 12)),
              if (onOpenSettings != null)
                TextButton(
                    onPressed: onOpenSettings,
                    child: const Text('Open Settings')),
            ]),
          ),
        ),
        IconButton(
            tooltip: 'Dismiss',
            onPressed: onDismiss,
            icon: const Icon(Icons.close)),
      ]),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({required this.message, required this.onDismiss});
  final String message;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: p.stop.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: p.stop.withValues(alpha: 0.3)),
      ),
      child: Padding(
        padding: const EdgeInsetsDirectional.fromSTEB(
            AnumSpacing.sm, AnumSpacing.xxs, 0, AnumSpacing.xxs),
        child: Row(children: [
          Icon(Icons.error_outline, color: p.stop, size: 20),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
            child: Semantics(
              liveRegion: true,
              child: Text(message, style: TextStyle(color: p.text)),
            ),
          ),
          IconButton(
              tooltip: 'Dismiss',
              onPressed: onDismiss,
              icon: const Icon(Icons.close, size: 20)),
        ]),
      ),
    );
  }
}
