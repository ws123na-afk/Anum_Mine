import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'learning_models.dart';

/// What ANUM learned (docs/learning.md): the caller's own data only.
abstract interface class LearningRepository {
  Future<LearningOverview> overview();
  Future<LearningOverview> setEnabled(bool enabled);
  Future<void> saveStyle(StyleOverrides overrides);
  Future<int> resetStyle();
  Future<LearningPage<LearnedPreference>> preferences({
    String query = '',
    int offset = 0,
  });
  Future<void> addPreference(String scope, String text);
  Future<void> updatePreference(String id, String scope, String text);
  Future<void> deletePreference(String id);
  Future<List<LearnedRoutine>> routines();
  Future<(List<LearnedRoutine>, int)> detectRoutines(String timezone);
  Future<LearnedRoutine> routineAction(String id, String action);
  Future<void> deleteRoutine(String id);
  Future<List<ImprovementNote>> notes();
  Future<ImprovementSuggestion> suggestionAction(String id, String action);
  Future<LearningPage<LearningSignal>> signals({
    String query = '',
    int offset = 0,
  });
  Future<void> deleteSignal(String id);
}

const learningPageSize = 20;
const _base = '/api/v1/learning';

class ApiLearningRepository implements LearningRepository {
  ApiLearningRepository(this.api);
  final AnumApiClient api;

  String _query(Map<String, Object> params) => Uri(
        queryParameters: {
          for (final entry in params.entries)
            if ('${entry.value}'.isNotEmpty) entry.key: '${entry.value}',
        },
      ).query;

  @override
  Future<LearningOverview> overview() async =>
      LearningOverview.fromJson(await api.request('GET', _base));

  @override
  Future<LearningOverview> setEnabled(bool enabled) async =>
      LearningOverview.fromJson(
        await api.request('PUT', '$_base/settings', body: {'enabled': enabled}),
      );

  @override
  Future<void> saveStyle(StyleOverrides overrides) async =>
      api.request('PUT', '$_base/profile', body: overrides.toJson());

  @override
  Future<int> resetStyle() async => JsonReader.of(
        await api
            .request('POST', '$_base/profile/reset', body: {'confirm': true}),
      ).integer('signals_deleted');

  @override
  Future<LearningPage<LearnedPreference>> preferences({
    String query = '',
    int offset = 0,
  }) async {
    final json = JsonReader.of(
      await api.request(
        'GET',
        '$_base/preferences?${_query({
              'q': query,
              'limit': learningPageSize,
              'offset': offset
            })}',
      ),
    );
    return LearningPage(
      json.objects('items', LearnedPreference.fromJson),
      json.integer('total'),
    );
  }

  @override
  Future<void> addPreference(String scope, String text) async => api.request(
        'POST',
        '$_base/preferences',
        body: {'scope': scope, 'text': text},
      );

  @override
  Future<void> updatePreference(String id, String scope, String text) async =>
      api.request(
        'PUT',
        '$_base/preferences/${Uri.encodeComponent(id)}',
        body: {'scope': scope, 'text': text},
      );

  @override
  Future<void> deletePreference(String id) async =>
      api.request('DELETE', '$_base/preferences/${Uri.encodeComponent(id)}');

  @override
  Future<List<LearnedRoutine>> routines() async =>
      JsonReader.of(await api.request('GET', '$_base/routines'))
          .objects('items', LearnedRoutine.fromJson);

  @override
  Future<(List<LearnedRoutine>, int)> detectRoutines(String timezone) async {
    final json = JsonReader.of(
      await api.request(
        'POST',
        '$_base/routines/detect',
        body: {'timezone': timezone},
      ),
    );
    return (
      json.objects('items', LearnedRoutine.fromJson),
      json.integer('detected'),
    );
  }

  @override
  Future<LearnedRoutine> routineAction(String id, String action) async =>
      LearnedRoutine.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$_base/routines/${Uri.encodeComponent(id)}/$action',
          ),
        ),
      );

  @override
  Future<void> deleteRoutine(String id) async =>
      api.request('DELETE', '$_base/routines/${Uri.encodeComponent(id)}');

  @override
  Future<List<ImprovementNote>> notes() async =>
      JsonReader.of(await api.request('GET', '$_base/notes?limit=4'))
          .objects('items', ImprovementNote.fromJson);

  @override
  Future<ImprovementSuggestion> suggestionAction(
    String id,
    String action,
  ) async =>
      ImprovementSuggestion.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$_base/suggestions/${Uri.encodeComponent(id)}/$action',
          ),
        ),
      );

  @override
  Future<LearningPage<LearningSignal>> signals({
    String query = '',
    int offset = 0,
  }) async {
    final json = JsonReader.of(
      await api.request(
        'GET',
        '$_base/signals?${_query({
              'q': query,
              'limit': learningPageSize,
              'offset': offset
            })}',
      ),
    );
    return LearningPage(
      json.objects('items', LearningSignal.fromJson),
      json.integer('total'),
    );
  }

  @override
  Future<void> deleteSignal(String id) async =>
      api.request('DELETE', '$_base/signals/${Uri.encodeComponent(id)}');
}
