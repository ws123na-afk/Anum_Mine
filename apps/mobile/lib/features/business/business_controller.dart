import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'business_models.dart';
import 'business_repository.dart';
import 'project_money.dart';

enum BusinessLoad { idle, loading, ready, denied, error }

/// One paged list (leads, opportunities, proposals, projects): first page,
/// "load more" and its own error, so a failed page keeps the rest on screen.
class PagedList<T> {
  PagedList(this.fetch);
  final Future<BusinessPage<T>> Function(int offset) fetch;
  BusinessLoad state = BusinessLoad.idle;
  final List<T> items = [];
  int total = 0;
  bool loadingMore = false;
  String? error;
  bool get hasMore => items.length < total;
}

/// State of the phone's Business screen (docs/business.md).
class BusinessController extends ChangeNotifier {
  BusinessController(this.repository, {this.money}) {
    leads = PagedList(
      (offset) => repository.leads(
        query: query,
        state: stateFilter,
        offset: offset,
        limit: pageSize,
      ),
    );
    opportunities = PagedList(
      (offset) => repository.opportunities(offset: offset, limit: pageSize),
    );
    proposals = PagedList(
      (offset) => repository.proposals(offset: offset, limit: pageSize),
    );
    projects = PagedList(
      (offset) => repository.projects(offset: offset, limit: pageSize),
    );
  }

  final BusinessRepository repository;

  /// Change orders, the ledger and profitability of a project (null hides them).
  final ProjectMoneyRepository? money;
  static const pageSize = 20;

  BusinessLoad setupState = BusinessLoad.idle;
  BusinessSetup? setup;
  String? setupError;
  BusinessLoad kpiState = BusinessLoad.idle;
  BusinessKpi? kpi;
  String? kpiError;
  int days = 30;
  String? query;
  String? stateFilter;
  late final PagedList<BusinessLead> leads;
  late final PagedList<BusinessOpportunity> opportunities;
  late final PagedList<BusinessProposal> proposals;
  late final PagedList<BusinessProject> projects;

  BusinessLoad _stateOf(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? BusinessLoad.denied
          : BusinessLoad.error;
  String _message(Object error) =>
      error is ApiException ? error.message : '$error';

  Future<void> load() async {
    setupState = BusinessLoad.loading;
    setupError = null;
    notifyListeners();
    try {
      setup = await repository.setup();
      setupState = BusinessLoad.ready;
    } catch (error) {
      setupState = _stateOf(error);
      setupError = _message(error);
      notifyListeners();
      return;
    }
    notifyListeners();
    await Future.wait([loadKpi(), first(leads)]);
  }

  Future<void> loadKpi() async {
    kpiState = BusinessLoad.loading;
    kpiError = null;
    notifyListeners();
    try {
      kpi = await repository.kpi(days);
      kpiState = BusinessLoad.ready;
    } catch (error) {
      kpiState = _stateOf(error);
      kpiError = _message(error);
    }
    notifyListeners();
  }

  Future<void> setDays(int value) async {
    days = value;
    await loadKpi();
  }

  Future<void> search(String? text, String? state) async {
    query = (text ?? '').trim().isEmpty ? null : text!.trim();
    stateFilter = state;
    await first(leads);
  }

  Future<void> first<T>(PagedList<T> list) async {
    list.state = BusinessLoad.loading;
    list.error = null;
    notifyListeners();
    try {
      final page = await list.fetch(0);
      list.items
        ..clear()
        ..addAll(page.items);
      list.total = page.total;
      list.state = BusinessLoad.ready;
    } catch (error) {
      list.state = _stateOf(error);
      list.error = _message(error);
    }
    notifyListeners();
  }

  Future<void> more<T>(PagedList<T> list) async {
    if (list.loadingMore || !list.hasMore) return;
    list.loadingMore = true;
    notifyListeners();
    try {
      final page = await list.fetch(list.items.length);
      list.items.addAll(page.items);
      list.total = page.total;
    } catch (error) {
      list.error = _message(error);
    } finally {
      list.loadingMore = false;
      notifyListeners();
    }
  }

  /// Moves a lead; returns the fresh detail, or throws the API's refusal.
  Future<LeadDetail> transition(String id, String to, String reason) async {
    final detail = await repository.transition(id, to, reason);
    await first(leads);
    return detail;
  }

  /// After an approved change order: the project's contract total and milestones changed.
  Future<void> refreshProjects() => first(projects);

  Future<BusinessProject> milestone(String id, String status) async {
    final project = await repository.updateMilestone(id, status);
    final index = projects.items.indexWhere((item) => item.id == project.id);
    if (index >= 0) projects.items[index] = project;
    notifyListeners();
    return project;
  }

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    setup = null;
    kpi = null;
    for (final list in [leads, opportunities, proposals, projects]) {
      list.items.clear();
      list.total = 0;
      list.state = BusinessLoad.idle;
    }
    setupState = kpiState = BusinessLoad.idle;
    notifyListeners();
  }
}
