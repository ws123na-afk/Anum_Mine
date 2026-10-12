import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'tyre_models.dart';

/// Tyre planning over the API (docs/tyre-planning.md#api). The server checks
/// every role: everyone reads, members and owners acknowledge alerts.
abstract interface class TyreRepository {
  Future<TyreAccess> access();
  Future<TyreDashboard> dashboard();
  Future<TyrePage<TyreForecast>> due({
    required String month,
    String? query,
    int offset = 0,
    int limit = 25,
  });
  Future<TyrePage<TyreAlert>> alerts({int offset = 0, int limit = 25});
  Future<TyreAlert> acknowledge(String id);
}

class ApiTyreRepository implements TyreRepository {
  const ApiTyreRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/tyre';

  @override
  Future<TyreAccess> access() async =>
      TyreAccess.fromJson(JsonReader(await api.request('GET', '$_base/setup')));

  @override
  Future<TyreDashboard> dashboard() async => TyreDashboard.fromJson(
        JsonReader(await api.request('GET', '$_base/dashboard')),
      );

  @override
  Future<TyrePage<TyreForecast>> due({
    required String month,
    String? query,
    int offset = 0,
    int limit = 25,
  }) async {
    final uri = Uri(
      path: '$_base/forecasts',
      queryParameters: {
        'month': month,
        'limit': '$limit',
        'offset': '$offset',
        if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      },
    );
    final json = JsonReader(await api.request('GET', uri.toString()));
    return TyrePage(
      items: json.objects('items', TyreForecast.fromJson),
      total: json.integer('total'),
      offset: json.integer('offset'),
    );
  }

  @override
  Future<TyrePage<TyreAlert>> alerts({int offset = 0, int limit = 25}) async {
    final uri = Uri(
      path: '$_base/alerts',
      queryParameters: {
        'status': 'active',
        'limit': '$limit',
        'offset': '$offset',
      },
    );
    final json = JsonReader(await api.request('GET', uri.toString()));
    return TyrePage(
      items: json.objects('items', TyreAlert.fromJson),
      total: json.integer('total'),
      offset: json.integer('offset'),
    );
  }

  @override
  Future<TyreAlert> acknowledge(String id) async => TyreAlert.fromJson(
        JsonReader(
          await api.request(
            'POST',
            '$_base/alerts/${Uri.encodeComponent(id)}/acknowledge',
          ),
        ),
      );
}
