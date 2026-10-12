import 'package:flutter/material.dart';

import '../chat/chat_controller.dart';
import '../chat/chat_repository.dart';
import '../chat/chat_screen.dart';
import 'agents_models.dart';

/// The Assistant's chat on one agent's threads (docs/agents.md#chat): the
/// agent's persona, model route and memories answer; it still has no tools.
class AgentChat extends StatefulWidget {
  const AgentChat({required this.agent, required this.repository, super.key});
  final AgentProfile agent;
  final ChatRepository Function(String agentId) repository;
  @override
  State<AgentChat> createState() => _AgentChatState();
}

class _AgentChatState extends State<AgentChat> {
  late final ChatController _controller =
      ChatController(widget.repository(widget.agent.id));

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      AssistantScreen(controller: _controller);
}
