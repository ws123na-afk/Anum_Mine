import '../lib/data/api_client.dart';
import '../lib/data/api_models.dart';
import '../lib/data/session_store.dart';
import '../lib/features/workspace/api_workspace_repository.dart';
import '../lib/features/workspace/workspace_models.dart';
import 'package:flutter_test/flutter_test.dart';

class ContractTransport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/automation/workflows') && request.method == 'POST') return ApiResponse(statusCode: 201, body: _workflow);
    if (path.endsWith('/automation/schedules') && request.method == 'POST') return ApiResponse(statusCode: 201, body: _schedule);
    final lists = <String, List<Object?>>{
      '/api/v1/tasks': [_task], '/api/v1/approvals': const [], '/api/v1/automation/runs': [_run], '/api/v1/files': const [], '/api/v1/memories': const [],
      '/api/v1/automation/workflows': [_workflow], '/api/v1/automation/schedules': [_schedule], '/api/v1/skills/versions': const [], '/api/v1/skills/installations': const [], '/api/v1/integrations': const [],
    };
    return ApiResponse(statusCode: 200, body: {'data': lists[path] ?? const []});
  }
  static const _time = '2026-08-30T10:00:00Z';
  static const _task = {'id':'task_1','title':'Live task','prompt':'Run it','status':'running','tenant_id':'tenant_test','workspace_id':'workspace_test','created_at':_time,'updated_at':_time};
  static const _workflow = {'id':'workflow_1','tenant_id':'tenant_test','workspace_id':'workspace_test','name':'Daily review','description':'Review work','status':'active','version':1,'steps':[{'id':'execute','name':'Execute work','action':'task.execute','input':{},'max_attempts':3}],'created_at':_time,'updated_at':_time};
  static const _run = {'id':'run_1','workflow_id':'workflow_1','tenant_id':'tenant_test','workspace_id':'workspace_test','status':'running','current_step':0,'steps':[],'created_at':_time,'updated_at':_time};
  static const _schedule = {'id':'schedule_1','workflow_id':'workflow_1','tenant_id':'tenant_test','workspace_id':'workspace_test','name':'Weekdays','cron':'0 9 * * 1','timezone':'UTC','enabled':true,'created_at':_time,'updated_at':_time};
}
class NoFiles implements WorkspaceFileTransfer { @override Future<void> download(WorkspaceFile file) async {} @override Future<JsonMap> upload(String path)=>throw UnimplementedError(); }

void main() { test('workspace maps automation joins and create contracts', () async {
  final sessions=MemorySessionStore(); await sessions.write(LocalSession(accessToken:'token',tokenType:'bearer',expiresAt:DateTime.now().toUtc().add(const Duration(hours:1)),context:const TenantContext(tenantId:'tenant_test',workspaceId:'workspace_test',userId:'user_test',roles:['owner'])));
  final transport=ContractTransport(); final repository=ApiWorkspaceRepository(AnumApiClient(baseUri:Uri.parse('http://localhost:8000'),transport:transport,sessions:sessions),fileTransfer:NoFiles());
  final snapshot=await repository.loadWorkspace();
  expect(snapshot.automations.single.workflowId,'workflow_1'); expect(snapshot.automations.single.name,'Daily review');
  await repository.createAutomation(name:'Daily review',description:'Review work',action:'task.execute');
  final workflowRequest=transport.requests.last; expect(workflowRequest.uri.path,'/api/v1/automation/workflows'); expect((workflowRequest.body!['steps'] as List).length,1);
  await repository.createSchedule(workflowId:'workflow_1',name:'Weekdays',cron:'0 9 * * 1',timezone:'UTC');
  final scheduleRequest=transport.requests.last; expect(scheduleRequest.uri.path,'/api/v1/automation/schedules'); expect(scheduleRequest.body!['enabled'],isTrue);
}); }
