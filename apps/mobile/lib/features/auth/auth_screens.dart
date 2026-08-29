import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import 'auth_controller.dart';

class AuthFlow extends StatelessWidget {
  const AuthFlow({required this.controller, super.key});
  final AuthController controller;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) => switch (controller.phase) {
          AuthPhase.restoring => const _Splash(),
          AuthPhase.signedOut || AuthPhase.error => _SignIn(controller: controller),
          AuthPhase.onboarding => _WorkspaceSetup(controller: controller),
          AuthPhase.modelSetup => _ModelSetup(controller: controller),
          AuthPhase.busy => const Scaffold(body: Center(child: CircularProgressIndicator())),
          AuthPhase.ready => const SizedBox.shrink(),
        },
      );
}

class _Page extends StatelessWidget {
  const _Page({required this.eyebrow, required this.title, required this.body});
  final String eyebrow;
  final String title;
  final Widget body;
  @override
  Widget build(BuildContext context) => Scaffold(
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: ListView(
                padding: const EdgeInsets.all(AnumSpacing.lg),
                children: [
                  Text('ANUM', style: Theme.of(context).textTheme.titleLarge?.copyWith(color: Theme.of(context).colorScheme.primary, fontWeight: FontWeight.bold)),
                  const SizedBox(height: AnumSpacing.xl),
                  Text(eyebrow.toUpperCase(), style: Theme.of(context).textTheme.labelMedium?.copyWith(color: Theme.of(context).colorScheme.primary)),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(title, style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.bold)),
                  const SizedBox(height: AnumSpacing.lg),
                  body,
                ],
              ),
            ),
          ),
        ),
      );
}

class _Splash extends StatelessWidget {
  const _Splash();
  @override
  Widget build(BuildContext context) => const _Page(
        eyebrow: 'Private agent workspace',
        title: 'Your work, coordinated.',
        body: Center(child: CircularProgressIndicator()),
      );
}

class _SignIn extends StatefulWidget {
  const _SignIn({required this.controller});
  final AuthController controller;
  @override State<_SignIn> createState() => _SignInState();
}

class _SignInState extends State<_SignIn> {
  final tenant = TextEditingController(text: 'local');
  final workspace = TextEditingController(text: 'default');
  final user = TextEditingController(text: 'owner');
  @override
  Widget build(BuildContext context) => _Page(
        eyebrow: 'Welcome back',
        title: 'Sign in to ANUM',
        body: Column(children: [
          if (widget.controller.message != null) _Error(widget.controller.message!),
          TextField(controller: tenant, decoration: const InputDecoration(labelText: 'Tenant ID')),
          const SizedBox(height: AnumSpacing.md),
          TextField(controller: workspace, decoration: const InputDecoration(labelText: 'Workspace ID')),
          const SizedBox(height: AnumSpacing.md),
          TextField(controller: user, decoration: const InputDecoration(labelText: 'User ID')),
          const SizedBox(height: AnumSpacing.lg),
          SizedBox(width: double.infinity, child: ElevatedButton(onPressed: () => widget.controller.signIn(tenant.text, workspace.text, user.text), child: const Text('Continue'))),
        ]),
      );
}

class _WorkspaceSetup extends StatefulWidget { const _WorkspaceSetup({required this.controller}); final AuthController controller; @override State<_WorkspaceSetup> createState() => _WorkspaceSetupState(); }
class _WorkspaceSetupState extends State<_WorkspaceSetup> {
  final organization = TextEditingController(); final workspace = TextEditingController();
  @override Widget build(BuildContext context) => _Page(eyebrow: 'Step 2 of 3', title: 'Create your workspace', body: Column(children: [TextField(controller: organization, decoration: const InputDecoration(labelText: 'Organization name')), const SizedBox(height: AnumSpacing.md), TextField(controller: workspace, decoration: const InputDecoration(labelText: 'Workspace name')), const SizedBox(height: AnumSpacing.lg), SizedBox(width: double.infinity, child: ElevatedButton(onPressed: () => widget.controller.createWorkspace(organization.text, workspace.text), child: const Text('Create workspace')))]));
}

class _ModelSetup extends StatefulWidget { const _ModelSetup({required this.controller}); final AuthController controller; @override State<_ModelSetup> createState() => _ModelSetupState(); }
class _ModelSetupState extends State<_ModelSetup> {
  final provider = TextEditingController(text: 'openai'); final model = TextEditingController(text: 'gpt-5'); final baseUrl = TextEditingController(text: 'https://api.openai.com/v1'); final key = TextEditingController();
  @override Widget build(BuildContext context) => _Page(eyebrow: 'Step 3 of 3', title: 'Connect a model', body: Column(children: [TextField(controller: provider, decoration: const InputDecoration(labelText: 'Provider')), const SizedBox(height: AnumSpacing.md), TextField(controller: model, decoration: const InputDecoration(labelText: 'Model')), const SizedBox(height: AnumSpacing.md), TextField(controller: baseUrl, keyboardType: TextInputType.url, decoration: const InputDecoration(labelText: 'Base URL')), const SizedBox(height: AnumSpacing.md), TextField(controller: key, obscureText: true, decoration: const InputDecoration(labelText: 'API key')), const SizedBox(height: AnumSpacing.lg), SizedBox(width: double.infinity, child: ElevatedButton(onPressed: () => widget.controller.connectModel(provider: provider.text, model: model.text, baseUrl: baseUrl.text, apiKey: key.text), child: const Text('Save and enter workspace')))]));
}

class _Error extends StatelessWidget { const _Error(this.message); final String message; @override Widget build(BuildContext context) => Padding(padding: const EdgeInsets.only(bottom: AnumSpacing.md), child: Text(message, style: TextStyle(color: Theme.of(context).colorScheme.error))); }
