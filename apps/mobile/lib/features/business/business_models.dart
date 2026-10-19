import '../../data/json_read.dart';
import 'invoice_models.dart' show PortalDecisionSummary;

/// Business, the internal sales desk (docs/business.md): what the phone reads
/// from `/api/v1/business`. The same wire shapes as packages/contracts
/// (business.ts). Values called assumptions are the owner's estimates.

class BusinessPermissions {
  const BusinessPermissions({
    required this.write,
    required this.manage,
    required this.decide,
  });
  final bool write, manage, decide;
}

class BusinessSetup {
  const BusinessSetup({
    required this.permissions,
    required this.sendingEnabled,
    required this.webSearch,
  });
  final BusinessPermissions permissions;
  final bool sendingEnabled, webSearch;

  factory BusinessSetup.fromJson(JsonReader r) {
    final p = r.object('permissions');
    return BusinessSetup(
      permissions: BusinessPermissions(
        write: p.boolean('write'),
        manage: p.boolean('manage'),
        decide: p.boolean('decide'),
      ),
      sendingEnabled: r.boolean('sending_enabled'),
      webSearch: r.boolean('web_search'),
    );
  }
}

class MoneyFigure {
  const MoneyFigure(this.currency, this.amount);
  final String currency;
  final double amount;
  factory MoneyFigure.fromJson(JsonReader r) =>
      MoneyFigure(r.string('currency'), r.number('amount'));
}

class FunnelStep {
  const FunnelStep({
    required this.stage,
    required this.reached,
    required this.of,
    this.ratePct,
  });
  final String stage;
  final int reached, of;
  final double? ratePct;
  factory FunnelStep.fromJson(JsonReader r) => FunnelStep(
        stage: r.string('stage'),
        reached: r.integer('reached'),
        of: r.integer('of'),
        ratePct: r.optNumber('rate_pct'),
      );
}

class BusinessKpi {
  const BusinessKpi({
    required this.days,
    required this.leadsCreated,
    required this.funnel,
    required this.openOpportunities,
    required this.pipelineAssumed,
    required this.contracted,
    required this.cashCollected,
    required this.proposalsAccepted,
    required this.notes,
  });
  final int days, leadsCreated, openOpportunities, proposalsAccepted;
  final List<FunnelStep> funnel;
  final List<MoneyFigure> pipelineAssumed, contracted, cashCollected;
  final List<String> notes;

  factory BusinessKpi.fromJson(JsonReader r) => BusinessKpi(
        days: r.integer('days'),
        leadsCreated: r.integer('leads_created'),
        funnel: r.objects('funnel', FunnelStep.fromJson),
        openOpportunities: r.integer('open_opportunities'),
        pipelineAssumed: r.objects('pipeline_assumed', MoneyFigure.fromJson),
        contracted: r.objects('contracted', MoneyFigure.fromJson),
        cashCollected: r.objects('cash_collected', MoneyFigure.fromJson),
        proposalsAccepted: r.integer('proposals_accepted'),
        notes: r.strings('notes'),
      );
}

class BusinessLead {
  const BusinessLead({
    required this.id,
    required this.title,
    required this.state,
    required this.accountName,
    required this.scoreTotal,
    required this.scoreKnown,
    this.productName,
    this.blockReason,
  });
  final String id, title, state, accountName;
  final int scoreTotal, scoreKnown;
  final String? productName, blockReason;

  factory BusinessLead.fromJson(JsonReader r) => BusinessLead(
        id: r.string('id'),
        title: r.string('title'),
        state: r.string('state'),
        accountName: r.string('account_name'),
        scoreTotal: r.integer('score_total'),
        scoreKnown: r.integer('score_known'),
        productName: r.optString('product_name'),
        blockReason: r.optString('block_reason'),
      );
}

class BusinessEvidence {
  const BusinessEvidence({
    required this.id,
    required this.url,
    required this.title,
    required this.confidence,
    required this.excerpt,
    this.publishedOn,
  });
  final String id, url, title, confidence, excerpt;
  final String? publishedOn;
  factory BusinessEvidence.fromJson(JsonReader r) => BusinessEvidence(
        id: r.string('id'),
        url: r.string('url'),
        title: r.string('title'),
        confidence: r.string('confidence'),
        excerpt: r.string('excerpt'),
        publishedOn: r.optString('published_on'),
      );
}

class BusinessInference {
  const BusinessInference({
    required this.statement,
    required this.status,
    required this.sources,
  });
  final String statement, status;
  final int sources;
  factory BusinessInference.fromJson(JsonReader r) => BusinessInference(
        statement: r.string('statement'),
        status: r.string('status'),
        sources: r.strings('basis_evidence_ids').length,
      );
}

class BusinessActivity {
  const BusinessActivity({
    required this.summary,
    required this.actor,
    required this.createdAt,
  });
  final String summary, actor;
  final DateTime createdAt;
  factory BusinessActivity.fromJson(JsonReader r) => BusinessActivity(
        summary: r.string('summary'),
        actor: r.string('actor'),
        createdAt: r.date('created_at'),
      );
}

class LeadDetail {
  const LeadDetail({
    required this.lead,
    required this.website,
    required this.contactName,
    required this.evidence,
    required this.inferences,
    required this.activities,
    required this.gaps,
    required this.transitions,
    this.consentGap,
  });
  final BusinessLead lead;
  final String? website, contactName, consentGap;
  final List<BusinessEvidence> evidence;
  final List<BusinessInference> inferences;
  final List<BusinessActivity> activities;
  final List<String> gaps, transitions;

  factory LeadDetail.fromJson(JsonReader r) => LeadDetail(
        lead: BusinessLead.fromJson(r.object('lead')),
        website: r.object('account').optString('website'),
        contactName: r.optObject('contact')?.string('name'),
        evidence: r.objects('evidence', BusinessEvidence.fromJson),
        inferences: r.objects('inferences', BusinessInference.fromJson),
        activities: r.objects('activities', BusinessActivity.fromJson),
        gaps: r.strings('qualification_gaps'),
        transitions: r.strings('allowed_transitions'),
        consentGap: r.optString('consent_gap'),
      );
}

class BusinessOpportunity {
  const BusinessOpportunity({
    required this.id,
    required this.name,
    required this.stage,
    required this.accountName,
    required this.currency,
    this.value,
    this.probability,
    this.weighted,
  });
  final String id, name, stage, accountName, currency;
  final double? value, probability, weighted;
  factory BusinessOpportunity.fromJson(JsonReader r) => BusinessOpportunity(
        id: r.string('id'),
        name: r.string('name'),
        stage: r.string('stage'),
        accountName: r.string('account_name'),
        currency: r.string('currency'),
        value: r.optNumber('value_amount'),
        probability: r.optNumber('probability_pct'),
        weighted: r.optNumber('weighted_value'),
      );
}

class BusinessProposal {
  const BusinessProposal({
    required this.id,
    required this.title,
    required this.status,
    required this.accountName,
    required this.version,
    required this.currency,
    required this.total,
    required this.approvalState,
    required this.decisionNote,
    required this.issues,
    this.portalDecision,
  });

  /// What the customer decided in their portal about the version waiting.
  final PortalDecisionSummary? portalDecision;
  final String id, title, status, accountName, currency, approvalState;
  final String decisionNote;
  final int version;
  final double total;
  final List<String> issues;
  factory BusinessProposal.fromJson(JsonReader r) {
    final current = r.object('current');
    return BusinessProposal(
      id: r.string('id'),
      title: r.string('title'),
      status: r.string('status'),
      accountName: r.string('account_name'),
      version: r.integer('current_version'),
      currency: current.string('currency'),
      total: current.number('total'),
      approvalState: r.string('approval_state'),
      decisionNote: r.string('decision_note'),
      issues: current.strings('issues'),
      portalDecision: PortalDecisionSummary.maybe(r),
    );
  }
}

class BusinessMilestone {
  const BusinessMilestone({
    required this.id,
    required this.title,
    required this.amount,
    required this.status,
    this.paidAmount,
  });
  final String id, title, status;
  final double amount;
  final double? paidAmount;
  factory BusinessMilestone.fromJson(JsonReader r) => BusinessMilestone(
        id: r.string('id'),
        title: r.string('title'),
        amount: r.number('amount'),
        status: r.string('status'),
        paidAmount: r.optNumber('paid_amount'),
      );
}

class BusinessProject {
  const BusinessProject({
    required this.id,
    required this.name,
    required this.status,
    required this.accountName,
    required this.currency,
    required this.contractTotal,
    required this.paidValue,
    required this.milestones,
  });
  final String id, name, status, accountName, currency;
  final double contractTotal, paidValue;
  final List<BusinessMilestone> milestones;
  factory BusinessProject.fromJson(JsonReader r) => BusinessProject(
        id: r.string('id'),
        name: r.string('name'),
        status: r.string('status'),
        accountName: r.string('account_name'),
        currency: r.string('currency'),
        contractTotal: r.number('contract_total'),
        paidValue: r.number('paid_value'),
        milestones: r.objects('milestones', BusinessMilestone.fromJson),
      );
}

class BusinessPage<T> {
  const BusinessPage(this.items, this.total);
  final List<T> items;
  final int total;
  factory BusinessPage.fromJson(
    JsonReader r,
    T Function(JsonReader item) read,
  ) =>
      BusinessPage(r.objects('items', read), r.integer('total'));
}
