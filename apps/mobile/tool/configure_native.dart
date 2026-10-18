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
  // In-app calls (docs/in-app-calls.md#flutter): the camera for video calls,
  // audio routing (speaker) and network changes for WebRTC.
  for (final permission in const [
    'android.permission.CAMERA',
    'android.permission.MODIFY_AUDIO_SETTINGS',
    'android.permission.ACCESS_NETWORK_STATE',
  ]) {
    if (!value.contains(permission)) {
      value = value.replaceFirst('<uses-permission android:name="android.permission.RECORD_AUDIO" />',
          '<uses-permission android:name="android.permission.RECORD_AUDIO" />\n    <uses-permission android:name="$permission" />');
    }
  }
  // Debug and profile builds talk to local plain-HTTP services; the release
  // overlay (androidReleaseManifest) turns cleartext traffic off again.
  if (!value.contains('android:usesCleartextTraffic')) {
    value = value.replaceFirst(
      '<application',
      '<application android:usesCleartextTraffic="true"',
    );
  }
  // Sessions and tokens live in app storage: keep them out of Android backups.
  if (!value.contains('android:allowBackup')) {
    value = value.replaceFirst(
      '<application',
      '<application android:allowBackup="false" '
          'android:fullBackupContent="false"',
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

const _propertiesImport = 'import java.util.Properties';

const _signingSetup = r'''

// Release signing (docs/mobile.md, "Release builds"). Values come from the
// environment or from the gitignored android/key.properties; neither the
// keystore nor its passwords are ever committed. When nothing is configured,
// release builds fall back to the debug key with a warning so CI can still
// build an unsigned-for-store bundle; set ANUM_ANDROID_REQUIRE_RELEASE_SIGNING
// to true to make a missing key fail the build instead.
val keystoreProperties =
    Properties().apply {
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
  // `java` is the java-plugin extension inside the Kotlin DSL, so a fully
  // qualified `java.util.Properties` does not resolve: import it instead.
  if (!value.contains(_propertiesImport)) {
    value = '$_propertiesImport\n\n$value';
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
  if (!value.contains(_googleServicesMarker)) {
    final plugins =
        RegExp(r'^plugins\s*\{[^}]*\}\n', multiLine: true).firstMatch(value);
    // After the plugins block (as Flutter generates it), else at the end.
    final at = plugins?.end ?? value.length;
    value = value.replaceRange(at, at, _googleServices);
  }
  return value;
}

/// Marker of the phone-push block, so configuration stays idempotent.
const _googleServicesMarker = 'file("google-services.json").exists()';

/// Phone push (docs/mobile.md#phone-push): the Google services plugin
/// (declared in settings.gradle.kts) runs only when the owner's gitignored
/// google-services.json is present, so the app builds without it.
const _googleServices = '''

// Phone push (docs/mobile.md#phone-push): the Firebase project comes from the
// owner's google-services.json, which is gitignored. Without it the build still
// succeeds and the app reports push as not set up in this build.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}
''';

String configureIosInfoPlist(String plist) {
  var value = plist;
  const privacy = '''
	<key>NSMicrophoneUsageDescription</key>
	<string>ANUM uses the microphone while you record a voice command or are in a call with a teammate.</string>
	<key>NSSpeechRecognitionUsageDescription</key>
	<string>ANUM converts your spoken command into an editable transcript.</string>
''';
  if (!value.contains('NSMicrophoneUsageDescription')) {
    value = _insertBeforeLastDictClose(value, privacy);
  }
  if (!value.contains('NSCameraUsageDescription')) {
    value = _insertBeforeLastDictClose(value,
        '\t<key>NSCameraUsageDescription</key>\n\t<string>ANUM uses the camera only during a video call with a teammate.</string>\n');
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
  // Phone push: lets iOS deliver remote notifications (FCM through APNs).
  if (!value.contains('<string>remote-notification</string>')) {
    const backgroundModes = '''
	<key>UIBackgroundModes</key>
	<array>
		<string>remote-notification</string>
	</array>
''';
    value = _insertBeforeLastDictClose(value, backgroundModes);
  }
  return value.replaceFirst(
    RegExp(r'(<key>CFBundleDisplayName</key>\s*<string>)[^<]*(</string>)'),
    '<key>CFBundleDisplayName</key>\n\t<string>$displayName</string>',
  );
}

/// Replaces the generated bundle identifier (`com.anum.anumMobile`) in the
/// Runner and RunnerTests targets.
/// Also adds the "Copy Firebase config" build phase (phone push).
String configureIosProject(String pbxproj) =>
    _configureFirebasePhase(pbxproj.replaceAllMapped(
      RegExp(r'PRODUCT_BUNDLE_IDENTIFIER = [A-Za-z0-9.\-]+?(\.RunnerTests)?;'),
      (match) => 'PRODUCT_BUNDLE_IDENTIFIER = $applicationId${match[1] ?? ''};',
    ));

/// The "Copy Firebase config" shell script phase of the Runner target.
const _firebasePhaseRef = 'A11CE0F1BA5E0000000F1BA5 /* Copy Firebase config */';

/// Copies the owner's gitignored GoogleService-Info.plist into the app when it
/// is present (docs/mobile.md#phone-push). Without it the build succeeds and
/// the app reports push as not set up in this build.
const _firebasePhase = r'''
		A11CE0F1BA5E0000000F1BA5 /* Copy Firebase config */ = {
			isa = PBXShellScriptBuildPhase;
			alwaysOutOfDate = 1;
			buildActionMask = 2147483647;
			files = (
			);
			inputPaths = (
			);
			name = "Copy Firebase config";
			outputPaths = (
			);
			runOnlyForDeploymentPostprocessing = 0;
			shellPath = /bin/sh;
			shellScript = "# Phone push (docs/mobile.md#phone-push): the owner's gitignored Firebase file.\nPLIST=\"${SRCROOT}/Runner/GoogleService-Info.plist\"\nif [ -f \"$PLIST\" ]; then\n  cp \"$PLIST\" \"${TARGET_BUILD_DIR}/${UNLOCALIZED_RESOURCES_FOLDER_PATH}/GoogleService-Info.plist\"\nfi\n";
		};
''';

String _configureFirebasePhase(String pbxproj) {
  if (pbxproj.contains(_firebasePhaseRef)) return pbxproj;
  const resources = '97C146EC1CF9000F007C117D /* Resources */,\n';
  const section = '/* End PBXShellScriptBuildPhase section */';
  // Fragments (tests) without the Runner target's phases are left alone.
  if (!pbxproj.contains(resources) || !pbxproj.contains(section)) {
    return pbxproj;
  }
  return pbxproj
      .replaceFirst(resources, '$resources\t\t\t\t$_firebasePhaseRef,\n')
      .replaceFirst(section, '$_firebasePhase$section');
}

/// permission_handler compiles every iOS permission out unless its macro is
/// set, so the microphone and speech-recognition requests are enabled here.
const _podPermissions = '''
    target.build_configurations.each do |config|
      # permission_handler: compile in only the permissions ANUM requests.
      config.build_settings['GCC_PREPROCESSOR_DEFINITIONS'] ||= [
        '\$(inherited)',
        'PERMISSION_MICROPHONE=1',
        'PERMISSION_CAMERA=1',
        'PERMISSION_SPEECH_RECOGNIZER=1',
      ]
    end
''';

String configureIosPodfile(String source) {
  // Firebase (phone push) and the app's deployment target are iOS 15.
  final podfile = source.replaceFirst(
      "# Uncomment this line to define a global platform for your project\n"
          "# platform :ios, '15.0'\n",
      "# Firebase (phone push) and the app's deployment target are iOS 15.\n"
          "platform :ios, '15.0'\n");
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
