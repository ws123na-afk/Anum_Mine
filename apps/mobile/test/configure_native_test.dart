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
    expect(value, contains('android:allowBackup="false"'));
    expect(value, contains('android:fullBackupContent="false"'));
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

  test('the app lock has what the system prompt needs on both platforms', () {
    // docs/mobile.md#app-lock. The prompt itself needs a phone.
    const manifest =
        '<manifest>\n    <application android:label="ANUM">\n    </application>\n</manifest>\n';
    expect(configureAndroidManifest(manifest),
        contains('android.permission.USE_BIOMETRIC'));
    expect(configureIosInfoPlist(plist), contains('NSFaceIDUsageDescription'));
    expect(File('android/app/src/main/AndroidManifest.xml').readAsStringSync(),
        contains('android.permission.USE_BIOMETRIC'));
    expect(File('ios/Runner/Info.plist').readAsStringSync(),
        contains('NSFaceIDUsageDescription'));
    // local_auth needs a FragmentActivity and an AppCompat theme.
    final activity =
        File('android/app/src/main/kotlin/com/anum/anum_mobile/MainActivity.kt')
            .readAsStringSync();
    expect(
        activity, contains('class MainActivity : FlutterFragmentActivity()'));
    for (final styles in const [
      'android/app/src/main/res/values/styles.xml',
      'android/app/src/main/res/values-night/styles.xml',
    ]) {
      final value = File(styles).readAsStringSync();
      expect(RegExp('parent="Theme.AppCompat').allMatches(value), hasLength(2),
          reason: styles);
    }
    expect(File('android/app/build.gradle.kts').readAsStringSync(),
        contains('androidx.appcompat:appcompat'));
    // The Android plugin stays on its last Java release while
    // android.builtInKotlin=false (CLAUDE.md, plugins and AGP 9).
    expect(File('pubspec.yaml').readAsStringSync(),
        contains('local_auth_android: 2.0.7'));
    expect(File('android/gradle.properties').readAsStringSync(),
        contains('android.builtInKotlin=false'));
  });

  test('a calendar sign-in returns to the app; links route nowhere else', () {
    // docs/calendar.md#connecting-on-the-phone.
    final manifest =
        File('android/app/src/main/AndroidManifest.xml').readAsStringSync();
    expect(
        manifest,
        contains(
            '<data android:scheme="com.anum.app" android:path="/calendar-connected" />'));
    expect(
        manifest,
        contains(
            'android:name="flutter_deeplinking_enabled" android:value="false"'));
    // AppAuth keeps its own path; the two never overlap.
    expect(manifest, contains('android:path="/oauth2redirect"'));
    final plist = File('ios/Runner/Info.plist').readAsStringSync();
    expect(plist, contains('<key>FlutterDeepLinkingEnabled</key>\n\t<false/>'));
    expect(configureIosInfoPlist(plist), plist);
    expect(configureAndroidManifest(manifest), manifest);
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

  group('phone push builds without the owner\'s Firebase files', () {
    test('Android applies the Google services plugin only with the file', () {
      final value = configureAndroidGradle(gradle);
      expect(value, contains('if (file("google-services.json").exists())'));
      expect(
          value, contains('apply(plugin = "com.google.gms.google-services")'));
      expect(configureAndroidGradle(value), value, reason: 'idempotent');
      final settings = File('android/settings.gradle.kts').readAsStringSync();
      expect(
          settings, contains('id("com.google.gms.google-services") version'));
      expect(settings, contains('apply false'));
    });

    test('iOS allows remote notifications and copies the plist if present', () {
      final value = configureIosInfoPlist(plist);
      expect(value, contains('<string>remote-notification</string>'));
      expect(configureIosInfoPlist(value), value);
      final project =
          File('ios/Runner.xcodeproj/project.pbxproj').readAsStringSync();
      expect(project, contains('/* Copy Firebase config */ = {'));
      expect(project, contains(r'if [ -f \"$PLIST\" ]'));
      final podfile = configureIosPodfile(iosPodfileTemplate);
      expect(podfile, contains("\nplatform :ios, '15.0'"));
      expect(configureIosPodfile(podfile), podfile);
    });

    test('the Firebase files stay out of git', () {
      final ignored = File('.gitignore').readAsStringSync();
      expect(ignored, contains('/android/app/google-services.json'));
      expect(ignored, contains('/ios/Runner/GoogleService-Info.plist'));
    });
  });
}
