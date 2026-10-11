import 'package:flutter/material.dart';

import '../../src/widgets/depth.dart';

import '../../src/theme/anum_theme.dart';
import '../../data/api_models.dart';
import 'auth_controller.dart';
import 'account_screens.dart';

class AuthFlow extends StatelessWidget {
  const AuthFlow({required this.controller, super.key});
  final AuthController controller;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (_, __) => switch (controller.phase) {
            AuthPhase.restoring => const SplashScreen(),
            AuthPhase.signedOut => SignInScreen(controller: controller),
            AuthPhase.workspacePicker =>
              WorkspacePickerScreen(controller: controller),
            AuthPhase.noWorkspace => NoWorkspaceScreen(controller: controller),
            AuthPhase.onboarding =>
              WorkspaceSetupScreen(controller: controller),
            AuthPhase.modelSetup =>
              ModelConnectionScreen(controller: controller),
            AuthPhase.busy => const _Busy(),
            AuthPhase.error => _Failure(controller),
            AuthPhase.ready => const SizedBox.shrink(),
          });
}

class _Page extends StatelessWidget {
  const _Page(
      {required this.eyebrow,
      required this.title,
      required this.body,
      this.subtitle});
  final String eyebrow, title;
  final String? subtitle;
  final Widget body;
  @override
  Widget build(BuildContext context) => Scaffold(
      body: AnumBackdrop(
          child: SafeArea(
              child: Center(
                  child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 480),
                      child: ListView(
                          padding: const EdgeInsetsDirectional.fromSTEB(
                              AnumSpacing.lg,
                              AnumSpacing.xl,
                              AnumSpacing.lg,
                              AnumSpacing.lg),
                          children: [
                            Row(children: [
                              Container(
                                width: 32,
                                height: 32,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(10),
                                  gradient: LinearGradient(colors: [
                                    context.palette.sky,
                                    context.palette.violet,
                                    context.palette.rose
                                  ]),
                                ),
                                child: const Text('A',
                                    style: TextStyle(
                                        color: Color(0xFF0B1020),
                                        fontWeight: FontWeight.w900)),
                              ),
                              const SizedBox(width: 8),
                              Text('ANUM',
                                  style: Theme.of(context)
                                      .textTheme
                                      .titleLarge
                                      ?.copyWith(fontWeight: FontWeight.w700))
                            ]),
                            const SizedBox(height: AnumSpacing.xl),
                            Text(eyebrow.toUpperCase(),
                                style: Theme.of(context)
                                    .textTheme
                                    .labelMedium
                                    ?.copyWith(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .primary)),
                            const SizedBox(height: 4),
                            Text(title,
                                style: Theme.of(context)
                                    .textTheme
                                    .headlineMedium
                                    ?.copyWith(fontWeight: FontWeight.w700)),
                            if (subtitle != null) ...[
                              const SizedBox(height: 8),
                              Text(subtitle!)
                            ],
                            const SizedBox(height: AnumSpacing.lg),
                            body,
                          ]))))));
}

class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});
  @override
  Widget build(BuildContext context) => const _Page(
      eyebrow: 'Private agent workspace',
      title: 'Your work, coordinated.',
      subtitle: 'Restoring your encrypted session and workspace access.',
      body: Align(
          alignment: AlignmentDirectional.centerStart,
          child: CircularProgressIndicator()));
}

class SignInScreen extends StatefulWidget {
  const SignInScreen({required this.controller, super.key});
  final AuthController controller;
  @override
  State<SignInScreen> createState() => _SignInState();
}

class _SignInState extends State<SignInScreen> {
  final form = GlobalKey<FormState>(),
      tenant = TextEditingController(text: 'local'),
      workspace = TextEditingController(text: 'default'),
      user = TextEditingController(text: 'owner'),
      password = TextEditingController();
  bool revealPassword = false;
  @override
  void dispose() {
    tenant.dispose();
    workspace.dispose();
    user.dispose();
    password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.controller.oidcEnabled
      ? _Page(
          eyebrow: 'Welcome back',
          title: 'Sign in to ANUM',
          subtitle:
              'Continue with your organization account. Sign-in opens in your browser and returns here.',
          body:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            FilledButton.icon(
                onPressed: widget.controller.signInWithOidc,
                icon: const Icon(Icons.login),
                label: const Text('Continue with organization account')),
          ]))
      : _localSignIn(context);

  Widget _localSignIn(BuildContext context) => _Page(
      eyebrow: 'Welcome back',
      title: 'Sign in to ANUM',
      subtitle:
          'Use your local workspace. Enterprise identity is configured by your administrator.',
      body: Form(
          key: form,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            _id(tenant, 'Organization ID', Icons.apartment_outlined),
            const SizedBox(height: AnumSpacing.md),
            _id(workspace, 'Workspace ID', Icons.workspaces_outlined),
            const SizedBox(height: AnumSpacing.md),
            _id(user, 'User ID', Icons.person_outline),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
                controller: password,
                obscureText: !revealPassword,
                decoration: InputDecoration(
                    labelText: 'Password (if configured)',
                    prefixIcon: const Icon(Icons.lock_outline),
                    suffixIcon: IconButton(
                        tooltip:
                            revealPassword ? 'Hide password' : 'Show password',
                        onPressed: () =>
                            setState(() => revealPassword = !revealPassword),
                        icon: Icon(revealPassword
                            ? Icons.visibility_off
                            : Icons.visibility)))),
            const SizedBox(height: AnumSpacing.lg),
            FilledButton.icon(
                onPressed: () {
                  if (form.currentState!.validate()) {
                    widget.controller.signIn(
                        tenant.text, workspace.text, user.text,
                        password: password.text);
                  }
                },
                icon: const Icon(Icons.login),
                label: const Text('Continue')),
            const SizedBox(height: 8),
            Wrap(alignment: WrapAlignment.center, children: [
              TextButton(
                  onPressed: _otp, child: const Text('Use one-time code')),
              TextButton(
                  onPressed: _forgotPassword,
                  child: const Text('Forgot password?'))
            ])
          ])));
  Widget _id(
          TextEditingController c, String label, IconData icon) =>
      TextFormField(
          controller: c,
          textInputAction: TextInputAction.next,
          decoration: InputDecoration(labelText: label, prefixIcon: Icon(icon)),
          validator: (v) =>
              (v?.trim().length ?? 0) < 3 ? 'Use at least 3 characters' : null);

  Future<void> _otp() async {
    if (!form.currentState!.validate()) return;
    final challenge = await widget.controller.repository.requestOtp(
        tenantId: tenant.text.trim(),
        workspaceId: workspace.text.trim(),
        userId: user.text.trim());
    if (!mounted) return;
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => OtpVerificationScreen(
                controller: widget.controller, challengeId: challenge)));
  }

  Future<void> _forgotPassword() async {
    if (!form.currentState!.validate()) return;
    if (!mounted) return;
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => PasswordRecoveryScreen(
                controller: widget.controller,
                tenantId: tenant.text.trim(),
                workspaceId: workspace.text.trim(),
                userId: user.text.trim())));
  }
}

/// First run without a default workspace: the token names none and the
/// caller has several memberships (docs/mobile.md#first-run).
class WorkspacePickerScreen extends StatelessWidget {
  const WorkspacePickerScreen({required this.controller, super.key});
  final AuthController controller;

  @override
  Widget build(BuildContext context) => _Page(
      eyebrow: 'Choose a workspace',
      title: 'Where do you want to work?',
      subtitle: 'You are a member of several workspaces. ANUM opens the one '
          'you choose next time; switch any time in Settings.',
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (final membership in controller.workspaceChoices) ...[
          AccountPanel(
              key: ValueKey('choose-${membership.workspaceId}'),
              title: membership.workspaceName ?? membership.workspaceId,
              subtitle: _membershipLine(membership),
              onTap: () => controller.chooseWorkspace(membership.workspaceId)),
          const SizedBox(height: AnumSpacing.sm),
        ],
        const SizedBox(height: AnumSpacing.sm),
        TextButton(
            onPressed: controller.signOut,
            child: const Text('Sign in with another account')),
      ]));

  static String _membershipLine(CallerMembership membership) => [
        if (membership.workspaceName != null) membership.workspaceId,
        'Your role: ${membership.role}',
      ].join(' · ');
}

/// Signed in, but no active membership in any workspace of the tenant and no
/// default workspace to create one in.
class NoWorkspaceScreen extends StatelessWidget {
  const NoWorkspaceScreen({required this.controller, super.key});
  final AuthController controller;

  @override
  Widget build(BuildContext context) => _Page(
      eyebrow: 'No workspace yet',
      title: 'You are not in a workspace',
      subtitle: 'Your account signed in, but it is not an active member of any '
          'workspace in your organization, and no default workspace is set '
          'for it.',
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Ask a workspace owner to invite you, or ask your '
            'administrator to set the default_workspace_id attribute on your '
            'account. Then check again.'),
        const SizedBox(height: AnumSpacing.lg),
        FilledButton.icon(
            onPressed: controller.checkWorkspacesAgain,
            icon: const Icon(Icons.refresh),
            label: const Text('Check again')),
        const SizedBox(height: AnumSpacing.sm),
        TextButton(
            onPressed: controller.signOut,
            child: const Text('Sign in with another account')),
      ]));
}

class WorkspaceSetupScreen extends StatefulWidget {
  const WorkspaceSetupScreen({required this.controller, super.key});
  final AuthController controller;
  @override
  State<WorkspaceSetupScreen> createState() => _WorkspaceState();
}

class _WorkspaceState extends State<WorkspaceSetupScreen> {
  final form = GlobalKey<FormState>(),
      organization = TextEditingController(),
      workspace = TextEditingController();
  @override
  void dispose() {
    organization.dispose();
    workspace.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => _Page(
      eyebrow: 'Step 2 of 3',
      title: 'Set up your workspace',
      subtitle:
          'Create an isolated place for agents, files, approvals, and automations.',
      body: Form(
          key: form,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextFormField(
                controller: organization,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(
                    labelText: 'Organization name',
                    prefixIcon: Icon(Icons.apartment_outlined)),
                validator: _required),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
                controller: workspace,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(
                    labelText: 'Workspace name',
                    prefixIcon: Icon(Icons.workspaces_outlined)),
                validator: _required),
            const SizedBox(height: AnumSpacing.lg),
            FilledButton.icon(
                onPressed: () {
                  if (form.currentState!.validate()) {
                    widget.controller
                        .createWorkspace(organization.text, workspace.text);
                  }
                },
                icon: const Icon(Icons.arrow_forward),
                label: const Text('Create workspace'))
          ])));
}

class ModelConnectionScreen extends StatefulWidget {
  const ModelConnectionScreen({required this.controller, super.key});
  final AuthController controller;
  @override
  State<ModelConnectionScreen> createState() => _ModelState();
}

class _ModelState extends State<ModelConnectionScreen> {
  final form = GlobalKey<FormState>(),
      model = TextEditingController(text: 'gpt-4.1-mini'),
      url = TextEditingController(text: 'https://api.openai.com/v1'),
      key = TextEditingController();
  String provider = 'openai_compatible';
  bool reveal = false;

  /// Defaults per provider. Ollama is free and runs on the user's computer.
  /// The ANUM API server (not the phone) calls this URL, so it is the address
  /// of Ollama as seen from the server: localhost when both run on one machine.
  static const _presets = <String, (String, String)>{
    'openai_compatible': ('gpt-4.1-mini', 'https://api.openai.com/v1'),
    'ollama': ('llama3.2', 'http://localhost:11434/v1'),
  };

  bool get _needsKey => provider == 'openai_compatible';

  void _selectProvider(String next) {
    final previous = _presets[provider];
    final preset = _presets[next];
    setState(() {
      // Only replace values the user has not edited away from the old preset.
      if (preset != null) {
        if (previous == null || model.text.trim() == previous.$1) {
          model.text = preset.$1;
        }
        if (previous == null || url.text.trim() == previous.$2) {
          url.text = preset.$2;
        }
      }
      provider = next;
    });
  }

  @override
  void dispose() {
    model.dispose();
    url.dispose();
    key.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.controller.canConfigureModel
      ? _ownerSetup(context)
      : _memberWait(context);

  /// Members and viewers may not save the workspace's model (owners only).
  Widget _memberWait(BuildContext context) => _Page(
      eyebrow: 'Step 3 of 3',
      title: 'Connect a model',
      subtitle: 'Only a workspace owner can connect this workspace to a model.',
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SkipStatus(controller: widget.controller, owner: false),
        const SizedBox(height: AnumSpacing.sm),
        TextButton(
            onPressed: widget.controller.signOut,
            child: const Text('Sign in with another account')),
      ]));

  Widget _ownerSetup(BuildContext context) => _Page(
      eyebrow: 'Step 3 of 3',
      title: 'Connect a model',
      subtitle: 'Credentials are write-only and are never returned by the API.',
      body: Form(
          key: form,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            DropdownButtonFormField<String>(
                initialValue: provider,
                decoration: const InputDecoration(
                    labelText: 'Provider',
                    prefixIcon: Icon(Icons.hub_outlined)),
                isExpanded: true,
                items: const [
                  DropdownMenuItem(
                      value: 'ollama',
                      child: Text('Ollama (free, runs on your computer)')),
                  DropdownMenuItem(
                      value: 'openai_compatible',
                      child: Text('OpenAI compatible')),
                  DropdownMenuItem(value: 'mock', child: Text('Local mock'))
                ],
                onChanged: (v) {
                  if (v != null) _selectProvider(v);
                }),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
                controller: model,
                decoration: const InputDecoration(
                    labelText: 'Model',
                    prefixIcon: Icon(Icons.smart_toy_outlined)),
                validator: _required),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
                controller: url,
                textDirection: TextDirection.ltr,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                    labelText: 'Base URL',
                    prefixIcon: const Icon(Icons.link),
                    helperMaxLines: 3,
                    helperText: provider == 'ollama'
                        ? 'The ANUM server calls this address, not your phone. '
                            'Keep localhost when Ollama runs on the same '
                            'computer as the ANUM server.'
                        : null),
                validator: (v) {
                  final u = Uri.tryParse(v ?? '');
                  return u != null && u.isAbsolute
                      ? null
                      : 'Enter an absolute URL';
                }),
            if (_needsKey) ...[
              const SizedBox(height: AnumSpacing.md),
              TextFormField(
                  controller: key,
                  textDirection: TextDirection.ltr,
                  obscureText: !reveal,
                  decoration: InputDecoration(
                      labelText: 'API key',
                      prefixIcon: const Icon(Icons.key_outlined),
                      suffixIcon: IconButton(
                          tooltip: reveal ? 'Hide API key' : 'Show API key',
                          onPressed: () => setState(() => reveal = !reveal),
                          icon: Icon(reveal
                              ? Icons.visibility_off
                              : Icons.visibility))),
                  validator: _required)
            ],
            const SizedBox(height: AnumSpacing.lg),
            FilledButton.icon(
                onPressed: () {
                  if (form.currentState!.validate()) {
                    widget.controller.connectModel(
                        provider: provider,
                        model: model.text.trim(),
                        baseUrl: url.text.trim(),
                        apiKey: _needsKey ? key.text.trim() : '');
                  }
                },
                icon: const Icon(Icons.cable),
                label: const Text('Test, save, and continue')),
            const SizedBox(height: AnumSpacing.md),
            _SkipStatus(controller: widget.controller, owner: true),
          ])));
}

/// The skip action, shown only when a model answers without this step: the
/// workspace already saved one or the server is configured with one. While
/// that is being checked, or when the check fails, it says so instead.
class _SkipStatus extends StatelessWidget {
  const _SkipStatus({required this.controller, required this.owner});
  final AuthController controller;
  final bool owner;

  @override
  Widget build(BuildContext context) {
    final availability = controller.modelAvailability;
    if (controller.checkingModel) {
      return Semantics(
          liveRegion: true,
          child: const Row(children: [
            SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2)),
            SizedBox(width: AnumSpacing.md),
            Expanded(
                child: Text('Checking whether the server already has a '
                    'model...'))
          ]));
    }
    final error = controller.modelCheckError;
    if (error != null || availability == null) {
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('Could not check whether the server already has a model: '
            '${error ?? 'no answer'}. Check your connection and try again.'),
        const SizedBox(height: AnumSpacing.sm),
        OutlinedButton.icon(
            onPressed: controller.checkModelAvailability,
            icon: const Icon(Icons.refresh),
            label: const Text('Check again')),
      ]);
    }
    if (availability.canSkip) {
      final label = availability.workspaceConfigured
          ? "Continue with this workspace's model"
          : "Skip and use the server's model (${availability.serverModel})";
      return owner
          ? TextButton(onPressed: controller.useServerModel, child: Text(label))
          : FilledButton(
              onPressed: controller.useServerModel, child: Text(label));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(owner
          ? 'Skipping is not available: neither this workspace nor the ANUM '
              'server has a model configured, so agents would have nothing '
              'to answer with. Connect one above to continue.'
          : 'Neither this workspace nor the ANUM server has a model '
              'configured yet. Ask a workspace owner to connect one in '
              'Settings, Model connection, then check again.'),
      const SizedBox(height: AnumSpacing.sm),
      OutlinedButton.icon(
          onPressed: controller.checkModelAvailability,
          icon: const Icon(Icons.refresh),
          label: const Text('Check again')),
    ]);
  }
}

class _Busy extends StatelessWidget {
  const _Busy();
  @override
  Widget build(BuildContext context) => const _Page(
      eyebrow: 'Please wait',
      title: 'Securing your workspace',
      body: Row(children: [
        CircularProgressIndicator(),
        SizedBox(width: AnumSpacing.md),
        Expanded(child: Text('Checking access and configuration...'))
      ]));
}

class _Failure extends StatelessWidget {
  const _Failure(this.controller);
  final AuthController controller;
  @override
  Widget build(BuildContext context) {
    final (icon, title) = switch (controller.issue) {
      AuthIssue.offline => (Icons.cloud_off_outlined, 'You are offline'),
      AuthIssue.permission => (Icons.lock_outline, 'Access denied'),
      AuthIssue.invalidSession => (
          Icons.no_accounts_outlined,
          'Session expired'
        ),
      AuthIssue.validation => (Icons.edit_outlined, 'Check your details'),
      _ => (Icons.error_outline, 'Something went wrong')
    };
    return _Page(
        eyebrow: 'Unable to continue',
        title: title,
        body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Icon(icon, size: 42, color: Theme.of(context).colorScheme.error),
          const SizedBox(height: AnumSpacing.md),
          Text(controller.message ?? 'Try again.'),
          const SizedBox(height: AnumSpacing.lg),
          FilledButton.icon(
              onPressed: controller.retry,
              icon: const Icon(Icons.refresh),
              label: const Text('Try again')),
          if (controller.issue == AuthIssue.invalidSession)
            TextButton(
                onPressed: controller.signOut,
                child: const Text('Return to sign in'))
        ]));
  }
}

String? _required(String? value) =>
    value == null || value.trim().isEmpty ? 'This field is required' : null;
