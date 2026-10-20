import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'business_models.dart';

/// Prospecting and outreach over the live API (docs/business.md#prospecting-pipeline,
/// docs/business.md#outreach). The phone runs and follows discovery and profile
/// runs, reads a lead's product fit and score suggestions, and works the outbox;
/// target profiles, signals and sending settings are chosen on the web.

class PipelineSettings {
  const PipelineSettings({
    required this.sendingEnabled,
    required this.paused,
    required this.pausedReason,
    required this.webSearch,
    required this.pageFetch,
    required this.canManage,
  });
  final bool sendingEnabled, paused, webSearch, pageFetch, canManage;
  final String pausedReason;

  factory PipelineSettings.fromJson(JsonReader json) {
    final tools = json.optObject('tools');
    return PipelineSettings(
      sendingEnabled: json.optBool('sending_enabled') ?? false,
      paused: json.optBool('paused') ?? false,
      pausedReason: json.optString('paused_reason') ?? '',
      webSearch: tools?.optBool('web_search') ?? false,
      pageFetch: tools?.optBool('page_fetch') ?? false,
      canManage: json.optBool('can_manage') ?? false,
    );
  }
}

class RunStepView {
  const RunStepView(this.status, this.summary, this.target);
  final String status, summary;
  final String? target;
}

class PipelineRunView {
  const PipelineRunView({
    required this.id,
    required this.kind,
    required this.status,
    required this.fits,
    required this.problems,
    required this.searches,
    required this.pageFetches,
    required this.modelCalls,
    required this.cost,
    required this.queries,
    required this.usedSearches,
    required this.usedPages,
    required this.steps,
    required this.stopReason,
    required this.leads,
  });
  final String id, kind, status, stopReason;
  final bool fits;
  final List<String> problems, queries;
  final int searches, pageFetches, modelCalls, usedSearches, usedPages;
  final double? cost;
  final List<RunStepView> steps;
  final List<({String id, String name, String? domain})> leads;

  bool get running => status == 'running';

  factory PipelineRunView.fromJson(JsonReader json) {
    final estimate = json.optObject('estimate');
    final used = json.optObject('used');
    final plan = json.optObject('plan');
    return PipelineRunView(
      id: json.string('id'),
      kind: json.string('kind'),
      status: json.string('status'),
      fits: estimate?.optBool('fits') ?? false,
      problems: [
        for (final item in estimate?.optList('problems') ?? const [])
          if (item is String) item
      ],
      queries: [
        for (final item in plan?.optList('queries') ?? const [])
          if (item is String) item
      ],
      searches: estimate?.optInt('searches') ?? 0,
      pageFetches: estimate?.optInt('page_fetches') ?? 0,
      modelCalls: estimate?.optInt('model_calls') ?? 0,
      cost: estimate?.optNumber('estimated_cost_usd'),
      usedSearches: used?.optInt('searches') ?? 0,
      usedPages: used?.optInt('page_fetches') ?? 0,
      steps: json.optObjects(
          'steps',
          (step) => RunStepView(step.optString('status') ?? '',
              step.optString('summary') ?? '', step.optString('target'))),
      stopReason: json.optString('stop_reason') ?? '',
      leads: json.optObjects(
          'leads',
          (lead) => (
                id: lead.string('id'),
                name: lead.optString('account_name') ?? '',
                domain: lead.optString('domain')
              )),
    );
  }
}

class ProductFitView {
  const ProductFitView(
      this.productName, this.score, this.disqualified, this.reasons);
  final String productName;
  final int? score;
  final bool disqualified;
  final List<String> reasons;
}

class CriterionLineView {
  const CriterionLineView({
    required this.criterion,
    required this.score,
    required this.conversationOnly,
    required this.suggestedScore,
    required this.suggestedReason,
    required this.suggestedEvidence,
  });
  final String criterion;
  final int? score, suggestedScore;
  final bool conversationOnly;
  final String suggestedReason;
  final List<String> suggestedEvidence;
}

class LeadProfileView {
  const LeadProfileView(
      this.fits, this.best, this.lines, this.facts, this.total, this.known);
  final List<ProductFitView> fits;
  final ProductFitView? best;
  final List<CriterionLineView> lines;
  final List<({String label, String value, String url})> facts;
  final int total, known;

  factory LeadProfileView.fromJson(JsonReader json) {
    ProductFitView fit(JsonReader item) => ProductFitView(
          item.optString('product_name') ?? '',
          item.optInt('score'),
          item.optBool('disqualified') ?? false,
          [
            for (final reason in item.optList('reasons') ?? const [])
              if (reason is String) reason
          ],
        );
    final labels = json.optMap('signal_labels') ?? const {};
    final best = json.optObject('best_fit');
    return LeadProfileView(
      json.optObjects('product_fits', fit),
      best == null ? null : fit(best),
      json.optObjects('breakdown', (line) {
        final suggestion = line.optObject('suggestion');
        return CriterionLineView(
          criterion: line.string('criterion'),
          score: line.optInt('score'),
          conversationOnly: line.optBool('conversation_only') ?? false,
          suggestedScore: suggestion?.optInt('score'),
          suggestedReason: suggestion?.optString('reason') ?? '',
          suggestedEvidence: [
            for (final id in suggestion?.optList('evidence_ids') ?? const [])
              if (id is String) id
          ],
        );
      }),
      json.optObjects('observations', (item) {
        final code = item.optString('signal_code') ?? '';
        final value = item.optString('value_text') ?? '';
        return (
          label: (labels[code] as String?) ?? code,
          value:
              value.isEmpty ? '${item.optNumber('value_num') ?? '-'}' : value,
          url: item.optString('url') ?? ''
        );
      }),
      json.optInt('score_total') ?? 0,
      json.optInt('score_known') ?? 0,
    );
  }
}

class OutboxItemView {
  const OutboxItemView({
    required this.id,
    required this.leadId,
    required this.company,
    required this.channel,
    required this.step,
    required this.status,
    required this.recipient,
    required this.subject,
    required this.body,
    required this.note,
    required this.due,
    required this.waitingApproval,
  });
  final String id, leadId, company, channel, status, recipient, subject, body;
  final String note;
  final int step;
  final bool due, waitingApproval;

  factory OutboxItemView.fromJson(JsonReader json) => OutboxItemView(
        id: json.string('id'),
        leadId: json.string('lead_id'),
        company: json.optString('company') ?? '',
        channel: json.optString('channel') ?? 'email',
        step: json.optInt('step') ?? 1,
        status: json.string('status'),
        recipient: json.optString('recipient') ?? '',
        subject: json.optString('subject') ?? '',
        body: json.optString('body') ?? '',
        note: json.optString('note') ?? '',
        due: json.optBool('due') ?? false,
        waitingApproval: json.optObject('approval') != null,
      );
}

class ReadyDraftView {
  const ReadyDraftView(this.draftId, this.company, this.channel, this.subject);
  final String draftId, company, channel, subject;
}

abstract interface class BusinessPipelineRepository {
  Future<PipelineSettings> settings();
  Future<BusinessPage<PipelineRunView>> runs({required int offset});
  Future<PipelineRunView> run(String id);
  Future<PipelineRunView> estimateDiscovery(
      String productId, List<String> terms);
  Future<PipelineRunView> confirm(String id);
  Future<PipelineRunView> stop(String id);
  Future<LeadProfileView> profile(String leadId);
  Future<void> applySuggestion(String leadId, String criterion, int score,
      String reason, List<String> evidence);
  Future<List<({String id, String name})>> products();
  Future<BusinessPage<OutboxItemView>> outbox({required int offset});
  Future<List<ReadyDraftView>> ready();
  Future<void> queue(String draftId);
  Future<void> requestApproval(String itemId);
  Future<void> cancel(String itemId, String reason);
  Future<String> recordReply(String leadId, String channel, String text);
}

class ApiBusinessPipelineRepository implements BusinessPipelineRepository {
  ApiBusinessPipelineRepository(this.api);
  final AnumApiClient api;
  static const _pipe = '/api/v1/business/pipeline';
  static const _out = '/api/v1/business/outreach';

  String _id(String value) => Uri.encodeComponent(value);
  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', path));
  Future<JsonReader> _post(String path, [JsonMap? body]) async =>
      JsonReader.of(await api.request('POST', path, body: body ?? {}));

  @override
  Future<PipelineSettings> settings() async {
    return PipelineSettings.fromJson(await _get('$_pipe/settings'));
  }

  @override
  Future<BusinessPage<PipelineRunView>> runs({required int offset}) async {
    final json = await _get('$_pipe/runs?limit=20&offset=$offset');
    return BusinessPage(
        json.objects('items', PipelineRunView.fromJson), json.integer('total'));
  }

  @override
  Future<PipelineRunView> run(String id) async =>
      PipelineRunView.fromJson(await _get('$_pipe/runs/${_id(id)}'));

  @override
  Future<PipelineRunView> estimateDiscovery(
          String productId, List<String> terms) async =>
      PipelineRunView.fromJson(await _post('$_pipe/runs/estimate',
          {'kind': 'discover', 'product_id': productId, 'terms': terms}));

  @override
  Future<PipelineRunView> confirm(String id) async =>
      PipelineRunView.fromJson(await _post('$_pipe/runs/${_id(id)}/confirm'));

  @override
  Future<PipelineRunView> stop(String id) async =>
      PipelineRunView.fromJson(await _post('$_pipe/runs/${_id(id)}/stop'));

  @override
  Future<LeadProfileView> profile(String leadId) async =>
      LeadProfileView.fromJson(
          await _get('$_pipe/leads/${_id(leadId)}/profile'));

  @override
  Future<void> applySuggestion(String leadId, String criterion, int score,
      String reason, List<String> evidence) async {
    await api
        .request('PUT', '/api/v1/business/leads/${_id(leadId)}/score', body: {
      'criteria': {
        criterion: {'score': score, 'reason': reason, 'evidence_ids': evidence}
      }
    });
  }

  @override
  Future<List<({String id, String name})>> products() async {
    final json = await _get('/api/v1/business/products?limit=100&offset=0');
    return json.objects(
        'items', (item) => (id: item.string('id'), name: item.string('name')));
  }

  @override
  Future<BusinessPage<OutboxItemView>> outbox({required int offset}) async {
    final json = await _get('$_out/items?limit=20&offset=$offset');
    return BusinessPage(
        json.objects('items', OutboxItemView.fromJson), json.integer('total'));
  }

  @override
  Future<List<ReadyDraftView>> ready() async {
    final json = await _get('$_out/ready?limit=50');
    return json.objects('items', (item) {
      final draft = item.object('draft');
      return ReadyDraftView(
          draft.string('id'),
          item.optString('company') ?? '',
          draft.optString('channel') ?? 'email',
          draft.optString('subject') ?? '');
    });
  }

  @override
  Future<void> queue(String draftId) =>
      _post('$_out/drafts/${_id(draftId)}/queue');

  @override
  Future<void> requestApproval(String itemId) =>
      _post('$_out/items/${_id(itemId)}/request-approval');

  @override
  Future<void> cancel(String itemId, String reason) =>
      _post('$_out/items/${_id(itemId)}/cancel', {'reason': reason});

  @override
  Future<String> recordReply(String leadId, String channel, String text) async {
    final json = await _post('$_out/leads/${_id(leadId)}/replies',
        {'channel': channel, 'kind': 'reply', 'text': text});
    return json.optString('kind') ?? 'reply';
  }
}
