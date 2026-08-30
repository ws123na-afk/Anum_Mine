import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import 'auth_controller.dart';
import 'account_mock_screens.dart';

class AuthFlow extends StatelessWidget {
  const AuthFlow({required this.controller, super.key});
  final AuthController controller;
  @override Widget build(BuildContext context) => ListenableBuilder(listenable: controller, builder: (_, __) => switch (controller.phase) {
    AuthPhase.restoring => const SplashScreen(), AuthPhase.signedOut => SignInScreen(controller: controller),
    AuthPhase.onboarding => WorkspaceSetupScreen(controller: controller), AuthPhase.modelSetup => ModelConnectionScreen(controller: controller),
    AuthPhase.busy => const _Busy(), AuthPhase.error => _Failure(controller), AuthPhase.ready => const SizedBox.shrink(),
  });
}

class _Page extends StatelessWidget {
  const _Page({required this.eyebrow, required this.title, required this.body, this.subtitle});
  final String eyebrow, title; final String? subtitle; final Widget body;
  @override Widget build(BuildContext context) => Scaffold(body: SafeArea(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 480), child: ListView(padding: const EdgeInsetsDirectional.fromSTEB(AnumSpacing.lg, AnumSpacing.xl, AnumSpacing.lg, AnumSpacing.lg), children: [
    Row(children: [Icon(Icons.auto_awesome, color: Theme.of(context).colorScheme.primary), const SizedBox(width: 8), Text('ANUM', style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700))]),
    const SizedBox(height: AnumSpacing.xl), Text(eyebrow.toUpperCase(), style: Theme.of(context).textTheme.labelMedium?.copyWith(color: Theme.of(context).colorScheme.primary)), const SizedBox(height: 4),
    Text(title, style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w700)), if (subtitle != null) ...[const SizedBox(height: 8), Text(subtitle!)], const SizedBox(height: AnumSpacing.lg), body,
  ])))));
}

class SplashScreen extends StatelessWidget { const SplashScreen({super.key}); @override Widget build(BuildContext context) => const _Page(eyebrow: 'Private agent workspace', title: 'Your work, coordinated.', subtitle: 'Restoring your encrypted session and workspace access.', body: Align(alignment: AlignmentDirectional.centerStart, child: CircularProgressIndicator())); }

class SignInScreen extends StatefulWidget { const SignInScreen({required this.controller, super.key}); final AuthController controller; @override State<SignInScreen> createState() => _SignInState(); }
class _SignInState extends State<SignInScreen> {
  final form=GlobalKey<FormState>(), tenant=TextEditingController(text:'local'), workspace=TextEditingController(text:'default'), user=TextEditingController(text:'owner'), password=TextEditingController();
  bool revealPassword=false;
  @override void dispose(){tenant.dispose();workspace.dispose();user.dispose();password.dispose();super.dispose();}
  @override Widget build(BuildContext context) => _Page(eyebrow:'Welcome back',title:'Sign in to ANUM',subtitle:'Use your local workspace. Enterprise identity is configured by your administrator.',body:Form(key:form,child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[
    _id(tenant,'Organization ID',Icons.apartment_outlined),const SizedBox(height:AnumSpacing.md),_id(workspace,'Workspace ID',Icons.workspaces_outlined),const SizedBox(height:AnumSpacing.md),_id(user,'User ID',Icons.person_outline),const SizedBox(height:AnumSpacing.md),
    TextFormField(controller:password,obscureText:!revealPassword,decoration:InputDecoration(labelText:'Password (if configured)',prefixIcon:const Icon(Icons.lock_outline),suffixIcon:IconButton(tooltip:revealPassword?'Hide password':'Show password',onPressed:()=>setState(()=>revealPassword=!revealPassword),icon:Icon(revealPassword?Icons.visibility_off:Icons.visibility)))),const SizedBox(height:AnumSpacing.lg),
    FilledButton.icon(onPressed:(){if(form.currentState!.validate())widget.controller.signIn(tenant.text,workspace.text,user.text,password:password.text);},icon:const Icon(Icons.login),label:const Text('Continue')),
    const SizedBox(height:8),Wrap(alignment:WrapAlignment.center,children:[TextButton(onPressed:_otp,child:const Text('Use one-time code')),TextButton(onPressed:_forgotPassword,child:const Text('Forgot password?'))])])));
  Widget _id(TextEditingController c,String label,IconData icon)=>TextFormField(controller:c,textInputAction:TextInputAction.next,decoration:InputDecoration(labelText:label,prefixIcon:Icon(icon)),validator:(v)=>(v?.trim().length??0)<3?'Use at least 3 characters':null);

  Future<void> _otp() async {
    if(!form.currentState!.validate())return;
    final challenge=await widget.controller.repository.requestOtp(tenantId:tenant.text.trim(),workspaceId:workspace.text.trim(),userId:user.text.trim());
    if(!mounted)return;await Navigator.push(context,MaterialPageRoute(builder:(_)=>OtpVerificationScreen(controller:widget.controller,challengeId:challenge)));
  }

  Future<void> _forgotPassword() async {
    if(!form.currentState!.validate())return;
    if(!mounted)return;await Navigator.push(context,MaterialPageRoute(builder:(_)=>PasswordRecoveryScreen(controller:widget.controller,tenantId:tenant.text.trim(),workspaceId:workspace.text.trim(),userId:user.text.trim())));
  }
}

class WorkspaceSetupScreen extends StatefulWidget { const WorkspaceSetupScreen({required this.controller,super.key});final AuthController controller;@override State<WorkspaceSetupScreen> createState()=>_WorkspaceState();}
class _WorkspaceState extends State<WorkspaceSetupScreen>{final form=GlobalKey<FormState>(),organization=TextEditingController(),workspace=TextEditingController();@override void dispose(){organization.dispose();workspace.dispose();super.dispose();}@override Widget build(BuildContext context)=>_Page(eyebrow:'Step 2 of 3',title:'Set up your workspace',subtitle:'Create an isolated place for agents, files, approvals, and automations.',body:Form(key:form,child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[TextFormField(controller:organization,textCapitalization:TextCapitalization.words,decoration:const InputDecoration(labelText:'Organization name',prefixIcon:Icon(Icons.apartment_outlined)),validator:_required),const SizedBox(height:AnumSpacing.md),TextFormField(controller:workspace,textCapitalization:TextCapitalization.words,decoration:const InputDecoration(labelText:'Workspace name',prefixIcon:Icon(Icons.workspaces_outlined)),validator:_required),const SizedBox(height:AnumSpacing.lg),FilledButton.icon(onPressed:(){if(form.currentState!.validate())widget.controller.createWorkspace(organization.text,workspace.text);},icon:const Icon(Icons.arrow_forward),label:const Text('Create workspace'))])));}

class ModelConnectionScreen extends StatefulWidget {const ModelConnectionScreen({required this.controller,super.key});final AuthController controller;@override State<ModelConnectionScreen> createState()=>_ModelState();}
class _ModelState extends State<ModelConnectionScreen>{final form=GlobalKey<FormState>(),model=TextEditingController(text:'gpt-4.1-mini'),url=TextEditingController(text:'https://api.openai.com/v1'),key=TextEditingController();String provider='openai_compatible';bool reveal=false;@override void dispose(){model.dispose();url.dispose();key.dispose();super.dispose();}@override Widget build(BuildContext context)=>_Page(eyebrow:'Step 3 of 3',title:'Connect a model',subtitle:'Credentials are write-only and are never returned by the API.',body:Form(key:form,child:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[DropdownButtonFormField<String>(value:provider,decoration:const InputDecoration(labelText:'Provider',prefixIcon:Icon(Icons.hub_outlined)),items:const[DropdownMenuItem(value:'openai_compatible',child:Text('OpenAI compatible')),DropdownMenuItem(value:'mock',child:Text('Local mock'))],onChanged:(v)=>setState(()=>provider=v!)),const SizedBox(height:AnumSpacing.md),TextFormField(controller:model,decoration:const InputDecoration(labelText:'Model',prefixIcon:Icon(Icons.smart_toy_outlined)),validator:_required),const SizedBox(height:AnumSpacing.md),TextFormField(controller:url,textDirection:TextDirection.ltr,keyboardType:TextInputType.url,decoration:const InputDecoration(labelText:'Base URL',prefixIcon:Icon(Icons.link)),validator:(v){final u=Uri.tryParse(v??'');return u!=null&&u.isAbsolute?null:'Enter an absolute URL';}),if(provider!='mock')...[const SizedBox(height:AnumSpacing.md),TextFormField(controller:key,textDirection:TextDirection.ltr,obscureText:!reveal,decoration:InputDecoration(labelText:'API key',prefixIcon:const Icon(Icons.key_outlined),suffixIcon:IconButton(tooltip:reveal?'Hide API key':'Show API key',onPressed:()=>setState(()=>reveal=!reveal),icon:Icon(reveal?Icons.visibility_off:Icons.visibility))),validator:_required)],const SizedBox(height:AnumSpacing.lg),FilledButton.icon(onPressed:(){if(form.currentState!.validate())widget.controller.connectModel(provider:provider,model:model.text.trim(),baseUrl:url.text.trim(),apiKey:key.text.trim());},icon:const Icon(Icons.cable),label:const Text('Test, save, and continue'))])));}

class _Busy extends StatelessWidget{const _Busy();@override Widget build(BuildContext context)=>const _Page(eyebrow:'Please wait',title:'Securing your workspace',body:Row(children:[CircularProgressIndicator(),SizedBox(width:AnumSpacing.md),Expanded(child:Text('Checking access and configuration...'))]));}
class _Failure extends StatelessWidget{const _Failure(this.controller);final AuthController controller;@override Widget build(BuildContext context){final(icon,title)=switch(controller.issue){AuthIssue.offline=>(Icons.cloud_off_outlined,'You are offline'),AuthIssue.permission=>(Icons.lock_outline,'Access denied'),AuthIssue.invalidSession=>(Icons.no_accounts_outlined,'Session expired'),AuthIssue.validation=>(Icons.edit_outlined,'Check your details'),_=>(Icons.error_outline,'Something went wrong')};return _Page(eyebrow:'Unable to continue',title:title,body:Column(crossAxisAlignment:CrossAxisAlignment.stretch,children:[Icon(icon,size:42,color:Theme.of(context).colorScheme.error),const SizedBox(height:AnumSpacing.md),Text(controller.message??'Try again.'),const SizedBox(height:AnumSpacing.lg),FilledButton.icon(onPressed:controller.retry,icon:const Icon(Icons.refresh),label:const Text('Try again')),if(controller.issue==AuthIssue.invalidSession)TextButton(onPressed:controller.signOut,child:const Text('Return to sign in'))]));}}
String? _required(String? value)=>value==null||value.trim().isEmpty?'This field is required':null;
