import 'dart:io';

/// Custom URI scheme of the `anum-flutter` Keycloak client's redirect
/// (`com.anum.app:/oauth2redirect`, see infra/keycloak/anum-realm.json).
const oidcRedirectScheme = 'com.anum.app';

/// Android applicationId and iOS bundle identifier of the shipping app. It
/// equals the redirect scheme so the private-use scheme is one the app owns
/// (RFC 8252 section 7.1), and differs from the frozen Kotlin client's
/// `com.anum.mobile` so both can be installed side by side.
const applicationId = 'com.anum.app';

/// Name shown under the launcher icon and on the iOS home screen.
const displayName = 'ANUM';

/// Configures the committed `android/` and `ios/` projects. They were made by
/// `flutter create --platforms=android,ios --org com.anum
/// --project-name anum_mobile .`; run this again after regenerating them.
/// Every step is idempotent, and test/configure_native_test.dart checks that
/// the committed files are already configured.
void main() {
  _configureAndroid();
  _configureIos();
}

void _configureAndroid() {
  final file = File('android/app/src/main/AndroidManifest.xml');
  final gradleFile = File('android/app/build.gradle.kts');
  if (!file.existsSync()) {
    throw StateError('Run flutter create before native configuration.');
  }
  if (!gradleFile.existsSync()) {
    throw StateError('Android Gradle configuration is missing.');
  }
  file.writeAsStringSync(configureAndroidManifest(file.readAsStringSync()));
  gradleFile
      .writeAsStringSync(configureAndroidGradle(gradleFile.readAsStringSync()));
  File('android/app/src/release/AndroidManifest.xml')
    ..createSync(recursive: true)
    ..writeAsStringSync(androidReleaseManifest);
}

void _configureIos() {
  final file = File('ios/Runner/Info.plist');
  final project = File('ios/Runner.xcodeproj/project.pbxproj');
  if (!file.existsSync() || !project.existsSync()) {
    throw StateError('Run flutter create before native configuration.');
  }
  file.writeAsStringSync(configureIosInfoPlist(file.readAsStringSync()));
  project.writeAsStringSync(configureIosProject(project.readAsStringSync()));
  final podfile = File('ios/Podfile');
  podfile.writeAsStringSync(configureIosPodfile(
      podfile.existsSync() ? podfile.readAsStringSync() : iosPodfileTemplate));
}

String configureAndroidManifest(String manifest) {
  var value = manifest;
  const permissions = '''
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
    <queries>
        <intent>
            <action android:name="android.speech.RecognitionService" />
        </intent>
        <intent>
            <action android:name="android.intent.action.TTS_SERVICE" />
        </intent>
        <intent>
            <action android:name="android.support.customtabs.action.CustomTabsService" />
        </intent>
    </queries>
''';
  if (!value.contains('android.permission.RECORD_AUDIO')) {
    value = value.replaceFirst('<application', '$permissions    <application');
  }
  // Debug and profile builds talk to local plain-HTTP services; the release
  // overlay (androidReleaseManifest) turns cleartext traffic off again.
  if (!value.contains('android:usesCleartextTraffic')) {
    value = value.replaceFirst(
      '<application',
      '<application android:usesCleartextTraffic="true"',
    );
  }
  return value.replaceFirst(
    RegExp(r'android:label="[^"]*"'),
    'android:label="$displayName"',
  );
}

/// Merged over the main manifest for release builds only: production traffic
/// is HTTPS, so release builds refuse cleartext connections.
const androidReleaseManifest = '''
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">
    <!-- Written by tool/configure_native.dart. Release builds use HTTPS only;
         debug and profile builds keep cleartext for local services. -->
    <application
        android:usesCleartextTraffic="false"
        tools:replace="android:usesCleartextTraffic" />
</manifest>
''';

/// Marker of the release-signing block, so configuration stays idempotent.
const _signingMarker = 'ANUM_ANDROID_KEYSTORE_PATH';

const _signingSetup = r'''

// Release signing (docs/mobile.md, "Release builds"). Values come from the
// environment or from the gitignored android/key.properties; neither the
// keystore nor its passwords are ever committed. When nothing is configured,
// release builds fall back to the debug key with a warning so CI can still
// build an unsigned-for-store bundle; set ANUM_ANDROID_REQUIRE_RELEASE_SIGNING
// to true to make a missing key fail the build instead.
val keystoreProperties =
    java.util.Properties().apply {
        val source = rootProject.file("key.properties")
        if (source.exists()) source.inputStream().use { load(it) }
    }

fun releaseSigningValue(environment: String, property: String): String? =
    System.getenv(environment)?.takeIf { it.isNotBlank() }
        ?: keystoreProperties.getProperty(property)?.takeIf { it.isNotBlank() }

val releaseKeystorePath = releaseSigningValue("ANUM_ANDROID_KEYSTORE_PATH", "storeFile")
val releaseKeystorePassword = releaseSigningValue("ANUM_ANDROID_KEYSTORE_PASSWORD", "storePassword")
val releaseKeyAlias = releaseSigningValue("ANUM_ANDROID_KEY_ALIAS", "keyAlias")
val releaseKeyPassword = releaseSigningValue("ANUM_ANDROID_KEY_PASSWORD", "keyPassword")
val releaseSigningValues =
    listOf(releaseKeystorePath, releaseKeystorePassword, releaseKeyAlias, releaseKeyPassword)
val hasReleaseSigning = releaseSigningValues.all { it != null }
if (!hasReleaseSigning && releaseSigningValues.any { it != null }) {
    throw GradleException(
        "Android release signing is partly configured: set the keystore path, " +
            "keystore password, key alias and key password together.",
    )
}
gradle.taskGraph.whenReady {
    val releaseTask = allTasks.any { it.project == project && it.name.contains("Release") }
    if (releaseTask && !hasReleaseSigning) {
        if (System.getenv("ANUM_ANDROID_REQUIRE_RELEASE_SIGNING") == "true") {
            throw GradleException("ANUM_ANDROID_REQUIRE_RELEASE_SIGNING is set but no release keystore is configured.")
        }
        logger.warn(
            "warning: no Android release keystore configured " +
                "(ANUM_ANDROID_KEYSTORE_PATH or android/key.properties); " +
                "signing the release build with the debug key. Do not upload it to Play.",
        )
    }
}
''';

const _signingConfigs = '''
    signingConfigs {
        create("release") {
            if (hasReleaseSigning) {
                storeFile = rootProject.file(releaseKeystorePath!!)
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

''';

/// Sets the SDK level, the applicationId, the release signing configuration
/// and the `appAuthRedirectScheme` manifest placeholder that flutter_appauth's
/// merged manifest uses for the intent filter of AppAuth's
/// RedirectUriReceiverActivity, so `com.anum.app:/oauth2redirect` returns to
/// the app after Keycloak sign-in and sign-out.
String configureAndroidGradle(String gradle) {
  var value = gradle.replaceFirst(
    'compileSdk = flutter.compileSdkVersion',
    'compileSdk = 37',
  );
  value = value.replaceFirst(
    RegExp(r'applicationId = "[^"]*"'),
    'applicationId = "$applicationId"',
  );
  value = value.replaceFirst(
    RegExp(r'\n *// TODO: Specify your own unique Application ID[^\n]*'),
    '',
  );
  if (!value.contains('appAuthRedirectScheme')) {
    final defaultConfig = RegExp(r'defaultConfig\s*\{').firstMatch(value);
    if (defaultConfig == null) {
      throw StateError('android/app/build.gradle.kts has no defaultConfig.');
    }
    value = value.replaceRange(
      defaultConfig.end,
      defaultConfig.end,
      '\n        manifestPlaceholders["appAuthRedirectScheme"] = '
      '"$oidcRedirectScheme"',
    );
  }
  if (!value.contains(_signingMarker)) {
    final android = RegExp(r'^android\s*\{', multiLine: true).firstMatch(value);
    final buildTypes =
        RegExp(r'^    buildTypes\s*\{', multiLine: true).firstMatch(value);
    if (android == null || buildTypes == null) {
      throw StateError('android/app/build.gradle.kts has no buildTypes.');
    }
    value =
        value.replaceRange(buildTypes.start, buildTypes.start, _signingConfigs);
    value = value.replaceRange(
        android.start, android.start, '${_signingSetup.trimLeft()}\n');
    value = value.replaceFirst(
      RegExp(r'release \{[^}]*\}'),
      'release {\n'
      '            // The release key when configured, else the debug key '
      '(see the warning above).\n'
      '            signingConfig = signingConfigs.getByName(if (hasReleaseSigning) '
      '"release" else "debug")\n'
      '        }',
    );
  }
  return value;
}

String configureIosInfoPlist(String plist) {
  var value = plist;
  const privacy = '''
	<key>NSMicrophoneUsageDescription</key>
	<string>ANUM listens only while you record a voice command.</string>
	<key>NSSpeechRecognitionUsageDescription</key>
	<string>ANUM converts your spoken command into an editable transcript.</string>
''';
  if (!value.contains('NSMicrophoneUsageDescription')) {
    value = _insertBeforeLastDictClose(value, privacy);
  }
  // AppAuth on iOS returns from the sign-in browser through this scheme.
  if (!value.contains('<string>$oidcRedirectScheme</string>')) {
    const urlTypes = '''
	<key>CFBundleURLTypes</key>
	<array>
		<dict>
			<key>CFBundleTypeRole</key>
			<string>Editor</string>
			<key>CFBundleURLSchemes</key>
			<array>
				<string>$oidcRedirectScheme</string>
			</array>
		</dict>
	</array>
''';
    value = _insertBeforeLastDictClose(value, urlTypes);
  }
  return value.replaceFirst(
    RegExp(r'(<key>CFBundleDisplayName</key>\s*<string>)[^<]*(</string>)'),
    '<key>CFBundleDisplayName</key>\n\t<string>$displayName</string>',
  );
}

/// Replaces the generated bundle identifier (`com.anum.anumMobile`) in the
/// Runner and RunnerTests targets.
String configureIosProject(String pbxproj) => pbxproj.replaceAllMapped(
      RegExp(r'PRODUCT_BUNDLE_IDENTIFIER = [A-Za-z0-9.\-]+?(\.RunnerTests)?;'),
      (match) => 'PRODUCT_BUNDLE_IDENTIFIER = $applicationId${match[1] ?? ''};',
    );

/// permission_handler compiles every iOS permission out unless its macro is
/// set, so the microphone and speech-recognition requests are enabled here.
const _podPermissions = '''
    target.build_configurations.each do |config|
      # permission_handler: compile in only the permissions ANUM requests.
      config.build_settings['GCC_PREPROCESSOR_DEFINITIONS'] ||= [
        '\$(inherited)',
        'PERMISSION_MICROPHONE=1',
        'PERMISSION_SPEECH_RECOGNIZER=1',
      ]
    end
''';

String configureIosPodfile(String podfile) {
  if (podfile.contains('PERMISSION_MICROPHONE=1')) return podfile;
  const hook = '    flutter_additional_ios_build_settings(target)\n';
  if (!podfile.contains(hook)) {
    throw StateError(
        'ios/Podfile has no flutter_additional_ios_build_settings.');
  }
  return podfile.replaceFirst(hook, '$hook$_podPermissions');
}

/// Flutter's own Podfile template (packages/flutter_tools/templates/cocoapods/
/// Podfile-ios), used when the generated project has no Podfile yet.
const iosPodfileTemplate = r'''
# Uncomment this line to define a global platform for your project
# platform :ios, '15.0'

# CocoaPods analytics sends network stats synchronously affecting flutter build latency.
ENV['COCOAPODS_DISABLE_STATS'] = 'true'

project 'Runner', {
  'Debug' => :debug,
  'Profile' => :release,
  'Release' => :release,
}

def flutter_root
  generated_xcode_build_settings_path = File.expand_path(File.join('..', 'Flutter', 'Generated.xcconfig'), __FILE__)
  unless File.exist?(generated_xcode_build_settings_path)
    raise "#{generated_xcode_build_settings_path} must exist. If you're running pod install manually, make sure flutter pub get is executed first"
  end

  File.foreach(generated_xcode_build_settings_path) do |line|
    matches = line.match(/FLUTTER_ROOT\=(.*)/)
    return matches[1].strip if matches
  end
  raise "FLUTTER_ROOT not found in #{generated_xcode_build_settings_path}. Try deleting Generated.xcconfig, then run flutter pub get"
end

require File.expand_path(File.join('packages', 'flutter_tools', 'bin', 'podhelper'), flutter_root)

flutter_ios_podfile_setup

target 'Runner' do
  use_frameworks!

  flutter_install_all_ios_pods File.dirname(File.realpath(__FILE__))
  target 'RunnerTests' do
    inherit! :search_paths
  end
end

post_install do |installer|
  installer.pods_project.targets.each do |target|
    flutter_additional_ios_build_settings(target)
  end
end
''';

/// Info.plist nests dictionaries; new top-level keys go before the root
/// dictionary's closing tag, which is the last one in the file.
String _insertBeforeLastDictClose(String plist, String entry) {
  final index = plist.lastIndexOf('</dict>');
  if (index < 0) throw StateError('Info.plist has no root dictionary.');
  return plist.replaceRange(index, index, entry);
}
