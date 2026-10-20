import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'agents_models.dart';
import 'agents_repository.dart';

enum AgentsPhase { loading, ready, error }

/// The server's own sentence (it says who may do what), or the error's text.
String agentError(Object error) =>
    error is ApiException ? error.message : '$error';

/// The Agents list: category filter, search, paging, and the catalog the
/// editor builds from (docs/agents.md).
class AgentsController extends ChangeNotifier {
  AgentsController(this.repository);
  final AgentsRepository repository;

  AgentsPhase phase = AgentsPhase.loading;
  List<AgentProfile> items = const [];
  Map<String, int> counts = const {};
  String? category;
  String query = '';
  String? nextCursor;
  String? error;
  bool loadingMore = false;
  AgentCatalog? catalog;
  String? catalogError;
  int _generation = 0;

  bool get filtered => category != null || query.trim().isNotEmpty;

  Future<void> load() async {
    final generation = ++_generation;
    phase = AgentsPhase.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.list(category: category, query: query);
      if (generation != _generation) return;
      items = page.items;
      counts = page.counts;
      nextCursor = page.nextCursor;
      phase = AgentsPhase.ready;
    } catch (e) {
      if (generation != _generation) return;
      error = agentError(e);
      phase = AgentsPhase.error;
    }
    notifyListeners();
  }

  Future<void> loadCatalog() async {
    catalogError = null;
    try {
      catalog = await repository.catalog();
    } catch (e) {
      catalogError = agentError(e);
    }
    notifyListeners();
  }

  void setCategory(String? value) {
    category = value;
    load();
  }

  void setQuery(String value) {
    query = value;
    load();
  }

  void clearFilters() {
    category = null;
    query = '';
    load();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.list(
        category: category,
        query: query,
        cursor: cursor,
      );
      final seen = items.map((a) => a.id).toSet();
      items = [...items, ...page.items.where((a) => !seen.contains(a.id))];
      nextCursor = page.nextCursor;
    } catch (e) {
      error = agentError(e);
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<AgentProfile> create(AgentDraft draft) async {
    final created = await repository.create(draft.toJson());
    await load();
    return created;
  }
}

/// One agent's page: the profile, its memory, recent tasks and runs.
class AgentDetailController extends ChangeNotifier {
  AgentDetailController(this.repository, this.agentId);
  final AgentsRepository repository;
  final String agentId;

  AgentsPhase phase = AgentsPhase.loading;
  AgentProfile? agent;
  bool missing = false;
  String? error;
  String? actionError;
  bool busy = false;

  List<AgentMemory> memories = const [];
  int memoryTotal = 0;
  String? memoryError;
  bool memoriesLoaded = false;

  List<AgentTask>? tasks;
  String? tasksError;

  bool running = false;
  AgentRunOutcome? outcome;
  String? runError;
  bool runBudget = false;

  Future<void> load() async {
    phase = AgentsPhase.loading;
    error = null;
    notifyListeners();
    try {
      agent = await repository.get(agentId);
      phase = AgentsPhase.ready;
      missing = false;
    } on ApiException catch (e) {
      missing = e.statusCode == 404;
      error = e.message;
      phase = AgentsPhase.error;
    } catch (e) {
      error = agentError(e);
      phase = AgentsPhase.error;
    }
    notifyListeners();
    if (phase == AgentsPhase.ready) await loadTasks();
  }

  /// Takes a profile the Skills tab saved (it has the new version).
  void applyAgent(AgentProfile next) {
    agent = next;
    notifyListeners();
  }

  /// Reads the agent again without the loading state (after a stale edit).
  Future<void> refreshAgent() async {
    try {
      agent = await repository.get(agentId);
      notifyListeners();
    } catch (_) {
      // The old profile stays; the panel already shows why.
    }
  }

  Future<void> loadTasks() async {
    tasksError = null;
    try {
      tasks = await repository.recentTasks(agentId);
    } catch (e) {
      tasksError = agentError(e);
    }
    notifyListeners();
  }

  Future<void> loadMemories({String query = ''}) async {
    memoryError = null;
    try {
      final page = await repository.memories(agentId, query: query);
      memories = page.items;
      memoryTotal = page.total;
      memoriesLoaded = true;
    } catch (e) {
      memoryError = agentError(e);
    }
    notifyListeners();
  }

  Future<bool> addMemory(String content) async {
    if (content.trim().isEmpty) return false;
    busy = true;
    memoryError = null;
    notifyListeners();
    try {
      await repository.addMemory(agentId, content.trim());
      await loadMemories();
      return true;
    } catch (e) {
      memoryError = agentError(e);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> deleteMemory(AgentMemory memory) async {
    busy = true;
    notifyListeners();
    try {
      await repository.deleteMemory(agentId, memory.id);
      await loadMemories();
    } catch (e) {
      memoryError = agentError(e);
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> run(String prompt, {String? title}) async {
    if (prompt.trim().isEmpty || running) return false;
    running = true;
    runError = null;
    runBudget = false;
    outcome = null;
    notifyListeners();
    try {
      outcome = await repository.runTask(agentId, prompt.trim(), title: title);
      await loadTasks();
      return true;
    } on ApiException catch (e) {
      runBudget = e.isBudgetExceeded;
      runError = e.message;
      return false;
    } catch (e) {
      runError = agentError(e);
      return false;
    } finally {
      running = false;
      notifyListeners();
    }
  }

  Future<bool> update(Map<String, Object?> changes) async {
    final current = agent;
    if (current == null) return false;
    busy = true;
    actionError = null;
    notifyListeners();
    try {
      agent = await repository.update(agentId, {
        ...changes,
        'version': current.version,
      });
      return true;
    } catch (e) {
      actionError = agentError(e);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> save(AgentDraft draft) async {
    final current = agent;
    if (current == null) return false;
    return update(draft.changesFrom(current));
  }

  Future<bool> setPaused(bool paused) =>
      update({'status': paused ? 'paused' : 'active'});

  Future<bool> delete() async {
    busy = true;
    actionError = null;
    notifyListeners();
    try {
      await repository.delete(agentId);
      return true;
    } catch (e) {
      actionError = agentError(e);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }
}
