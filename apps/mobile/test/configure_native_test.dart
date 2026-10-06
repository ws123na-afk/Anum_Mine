import 'package:flutter_test/flutter_test.dart';

import '../tool/configure_native.dart';

const gradle = '''
android {
    namespace = "com.anum.anum_mobile"
    compileSdk = flutter.compileSdkVersion

    defaultConfig {
        applicationId = "com.anum.anum_mobile"
        minSdk = flutter.minSdkVersion
    }
}
''';

const plist = '''
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
	<key>UIApplicationSceneManifest</key>
	<dict>
		<key>UIApplicationSupportsMultipleScenes</key>
		<false/>
	</dict>
</dict>
</plist>
''';

void main() {
  test('Android registers the OIDC redirect scheme for AppAuth', () {
    final value = configureAndroidGradle(gradle);
    expect(
        value,
        contains(
            'manifestPlaceholders["appAuthRedirectScheme"] = "com.anum.app"'));
    expect(value, contains('compileSdk = 37'));
    expect(configureAndroidGradle(value), value, reason: 'idempotent');
  });

  test('Android Gradle without defaultConfig fails loudly', () {
    expect(() => configureAndroidGradle('android {}'), throwsStateError);
  });

  test('Android manifest keeps permissions and is idempotent', () {
    const manifest =
        '<manifest>\n    <application android:label="ANUM">\n    </application>\n</manifest>\n';
    final value = configureAndroidManifest(manifest);
    expect(value, contains('android.permission.RECORD_AUDIO'));
    expect(value, contains('android:usesCleartextTraffic="true"'));
    expect(configureAndroidManifest(value), value);
  });

  test('iOS registers the redirect scheme in the root dictionary', () {
    final value = configureIosInfoPlist(plist);
    final urlTypes = value.indexOf('CFBundleURLTypes');
    expect(urlTypes, greaterThan(value.indexOf('</dict>')),
        reason: 'not inside the nested scene manifest dictionary');
    expect(value, contains('<string>com.anum.app</string>'));
    expect(value, contains('NSMicrophoneUsageDescription'));
    expect(configureIosInfoPlist(value), value);
  });
}
