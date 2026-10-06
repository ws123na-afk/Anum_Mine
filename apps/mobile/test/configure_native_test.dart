import 'dart:io';

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

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            signingConfig = signingConfigs.getByName("debug")
        }
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

  test('Android uses the shipping applicationId', () {
    final value = configureAndroidGradle(gradle);
    expect(value, contains('applicationId = "com.anum.app"'));
    expect(value, isNot(contains('com.anum.anum_mobile"\n        minSdk')));
  });

  test('Android release signing reads the environment, never a committed key',
      () {
    final value = configureAndroidGradle(gradle);
    for (final name in [
      'ANUM_ANDROID_KEYSTORE_PATH',
      'ANUM_ANDROID_KEYSTORE_PASSWORD',
      'ANUM_ANDROID_KEY_ALIAS',
      'ANUM_ANDROID_KEY_PASSWORD',
      'ANUM_ANDROID_REQUIRE_RELEASE_SIGNING',
    ]) {
      expect(value, contains(name));
    }
    expect(value, contains('rootProject.file("key.properties")'));
    expect(value, contains('create("release")'));
    expect(
        value,
        contains('signingConfigs.getByName(if (hasReleaseSigning) '
            '"release" else "debug")'));
    expect(value, contains('logger.warn('));
    expect(value.indexOf('signingConfigs {'),
        lessThan(value.indexOf('buildTypes {')));
    expect(value, isNot(contains('TODO: Add your own signing config')));
    expect(configureAndroidGradle(value), value, reason: 'idempotent');
  });

  test('Android release builds refuse cleartext traffic', () {
    expect(androidReleaseManifest,
        contains('android:usesCleartextTraffic="false"'));
    expect(androidReleaseManifest,
        contains('tools:replace="android:usesCleartextTraffic"'));
  });

  test('iOS uses the shipping bundle identifier', () {
    const project = '''
				PRODUCT_BUNDLE_IDENTIFIER = com.anum.anumMobile;
				PRODUCT_BUNDLE_IDENTIFIER = com.anum.anumMobile.RunnerTests;
''';
    final value = configureIosProject(project);
    expect(value, contains('PRODUCT_BUNDLE_IDENTIFIER = com.anum.app;'));
    expect(value,
        contains('PRODUCT_BUNDLE_IDENTIFIER = com.anum.app.RunnerTests;'));
    expect(value, isNot(contains('anumMobile')));
    expect(configureIosProject(value), value);
  });

  test('iOS Podfile compiles in the microphone and speech permissions', () {
    final value = configureIosPodfile(iosPodfileTemplate);
    expect(value, contains("'PERMISSION_MICROPHONE=1'"));
    expect(value, contains("'PERMISSION_SPEECH_RECOGNIZER=1'"));
    expect(configureIosPodfile(value), value);
  });

  test('committed platform projects are already configured', () {
    final files = {
      'android/app/build.gradle.kts': configureAndroidGradle,
      'android/app/src/main/AndroidManifest.xml': configureAndroidManifest,
      'ios/Runner/Info.plist': configureIosInfoPlist,
      'ios/Runner.xcodeproj/project.pbxproj': configureIosProject,
      'ios/Podfile': configureIosPodfile,
    };
    for (final MapEntry(key: path, value: configure) in files.entries) {
      final source = File(path).readAsStringSync();
      expect(configure(source), source,
          reason: '$path: run dart run tool/configure_native.dart');
    }
    expect(
        File('android/app/src/release/AndroidManifest.xml').readAsStringSync(),
        androidReleaseManifest);
    expect(File('ios/Runner/Info.plist').readAsStringSync(),
        contains('NSSpeechRecognitionUsageDescription'));
  });
}
