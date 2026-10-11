import 'package:anum_mobile/src/anum_app.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('release builds refuse a non-https API address', () {
    expect(releaseApiUrlError('https://api.anum.invalid/', release: true),
        isNull);
    expect(releaseApiUrlError('http://10.0.2.2:8000/', release: true),
        contains('https://'));
    expect(releaseApiUrlError('not a url', release: true), isNotNull);
    expect(releaseApiUrlError('http://10.0.2.2:8000/', release: false),
        isNull);
  });
}
