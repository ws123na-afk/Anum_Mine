import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';

import 'auth_controller.dart';
import 'auth_repository.dart';

/// Page shell for account flows: depth background, readable column, no fake chrome.
class AccountPage extends StatelessWidget {
  const AccountPage(
      {super.key,
      required this.eyebrow,
      required this.title,
      required this.subtitle,
      required this.children});
  final String eyebrow, title, subtitle;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(),
      extendBodyBehindAppBar: true,
      body: AnumBackdrop(
          child: SafeArea(
              child: Center(
                  child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 440),
                      child: ListView(
                          padding: const EdgeInsets.fromLTRB(24, 24, 24, 28),
                          children: [
                            AnumHeader(
                                eyebrow: eyebrow,
                                title: title,
                                subtitle: subtitle,
                                large: true),
                            const SizedBox(height: 20),
                            for (var i = 0; i < children.length; i++) ...[
                              children[i],
                              if (i < children.length - 1)
                                const SizedBox(height: 14)
                            ]
                          ]))))));
}

/// An informational or selectable panel.
class AccountPanel extends StatelessWidget {
  const AccountPanel(
      {super.key,
      required this.title,
      required this.subtitle,
      this.selected = false,
      this.onTap});
  final String title, subtitle;
  final bool selected;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
        onTap: onTap,
        accent: selected ? p.violet : null,
        child: Row(children: [
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(title,
                    style: TextStyle(
                        color: p.text,
                        fontSize: 15,
                        fontWeight: FontWeight.w700)),
                const SizedBox(height: 4),
                Text(subtitle, style: TextStyle(color: p.muted, fontSize: 13)),
              ])),
          if (selected) Icon(Icons.check_circle, color: p.violet),
        ]));
  }
}

class OtpVerificationScreen extends StatefulWidget {
  const OtpVerificationScreen(
      {super.key, required this.controller, required this.challengeId});
  final AuthController controller;
  final String challengeId;
  @override
  State<OtpVerificationScreen> createState() => _OtpVerificationScreenState();
}

class _OtpVerificationScreenState extends State<OtpVerificationScreen> {
  final code = TextEditingController();
  bool busy = false;
  @override
  void dispose() {
    code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AccountPage(
          eyebrow: 'Security check',
          title: 'Enter verification code',
          subtitle: 'We sent a six-digit code to your configured work account.',
          children: [
            TextField(
                controller: code,
                maxLength: 6,
                keyboardType: TextInputType.number,
                textAlign: TextAlign.center,
                style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 12),
                decoration: const InputDecoration(
                    counterText: '',
                    helperText: 'Codes expire after 10 minutes')),
            FilledButton(
                onPressed: busy ? null : _verify,
                child: Text(busy ? 'Verifying...' : 'Verify')),
            OutlinedButton(
                onPressed: busy ? null : () => Navigator.pop(context),
                child: const Text('Send another code')),
            const AccountPanel(
                title: 'Use another method',
                subtitle: 'Password · Recovery code')
          ]);
  Future<void> _verify() async {
    setState(() => busy = true);
    await widget.controller.completeExternalSignIn(() => widget
        .controller.repository
        .verifyOtp(challengeId: widget.challengeId, code: code.text.trim()));
    if (mounted) Navigator.pop(context);
  }
}

class PasswordRecoveryScreen extends StatefulWidget {
  const PasswordRecoveryScreen(
      {super.key,
      required this.controller,
      required this.tenantId,
      required this.workspaceId,
      required this.userId});
  final AuthController controller;
  final String tenantId, workspaceId, userId;
  @override
  State<PasswordRecoveryScreen> createState() => _PasswordRecoveryScreenState();
}

class _PasswordRecoveryScreenState extends State<PasswordRecoveryScreen> {
  bool busy = false;
  @override
  Widget build(BuildContext context) => AccountPage(
          eyebrow: 'Account recovery',
          title: 'Reset your password',
          subtitle:
              'Send a recovery challenge for the work account associated with ANUM.',
          children: [
            AccountPanel(title: 'Work account', subtitle: widget.userId),
            FilledButton(
                onPressed: busy ? null : _send,
                child: Text(busy ? 'Sending...' : 'Send recovery link')),
            OutlinedButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Back to sign in')),
            const AccountPanel(
                title: 'Enterprise account',
                subtitle:
                    'Contact your organization administrator if sign-in is managed by SSO.')
          ]);
  Future<void> _send() async {
    setState(() => busy = true);
    final challenge = await widget.controller.repository.requestPasswordReset(
        tenantId: widget.tenantId,
        workspaceId: widget.workspaceId,
        userId: widget.userId);
    if (!mounted) return;
    final token = await _entry(context, 'Reset token');
    if (token == null || !mounted) return;
    final password = await _entry(context, 'New password', obscure: true);
    if (password != null) {
      await widget.controller.completeExternalSignIn(() =>
          widget.controller.repository.resetPassword(
              challengeId: challenge, token: token, newPassword: password));
    }
    if (mounted) Navigator.pop(context);
  }
}

Future<String?> _entry(BuildContext context, String label,
    {bool obscure = false}) async {
  final c = TextEditingController();
  final result = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (context) => Padding(
          padding: EdgeInsets.fromLTRB(
              24, 24, 24, MediaQuery.viewInsetsOf(context).bottom + 24),
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(label, style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 16),
                TextField(
                    controller: c,
                    autofocus: true,
                    obscureText: obscure,
                    decoration: InputDecoration(labelText: label)),
                const SizedBox(height: 16),
                FilledButton(
                    onPressed: () => Navigator.pop(context, c.text.trim()),
                    child: const Text('Continue'))
              ])));
  c.dispose();
  return result == null || result.isEmpty ? null : result;
}

/// Shows the workspace you are really in and switches by workspace ID through
/// the API. ANUM has no endpoint that lists every workspace you belong to, so
/// this screen does not invent a list.
class WorkspaceSwitcherScreen extends StatefulWidget {
  const WorkspaceSwitcherScreen(
      {super.key,
      required this.repository,
      required this.currentWorkspaceId,
      required this.role,
      this.onSwitched});
  final AuthRepository repository;
  final String currentWorkspaceId, role;
  final Future<void> Function()? onSwitched;
  @override
  State<WorkspaceSwitcherScreen> createState() =>
      _WorkspaceSwitcherScreenState();
}

class _WorkspaceSwitcherScreenState extends State<WorkspaceSwitcherScreen> {
  final _id = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _id.dispose();
    super.dispose();
  }

  Future<void> _switch() async {
    final id = _id.text.trim();
    if (id.isEmpty || id == widget.currentWorkspaceId) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.repository.switchWorkspace(id);
      await widget.onSwitched?.call();
      if (mounted) Navigator.pop(context);
    } on Object {
      if (mounted) {
        setState(() => _error =
            'You are not an active member of “$id”, or it does not exist.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => AccountPage(
          eyebrow: 'Workspace',
          title: 'Switch workspace',
          subtitle:
              'Your role and what agents may do change with the workspace.',
          children: [
            AccountPanel(
                title: widget.currentWorkspaceId,
                subtitle: 'Current workspace · ${widget.role}',
                selected: true),
            TextField(
                controller: _id,
                textDirection: TextDirection.ltr,
                decoration: InputDecoration(
                    labelText: 'Workspace ID to switch to', errorText: _error),
                onSubmitted: (_) => _switch()),
            FilledButton(
                onPressed: _busy ? null : _switch,
                child: Text(_busy ? 'Switching...' : 'Switch workspace')),
            const AccountPanel(
                title: 'Need a new workspace?',
                subtitle:
                    'Sign out and sign in with a new workspace ID. You become its owner during setup.'),
          ]);
}
