import 'dart:io';

/// Custom URI scheme of the `anum-flutter` Keycloak client's redirect
/// (`com.anum.app:/oauth2redirect`, see infra/keycloak/anum-realm.json).
const oidcRedirectScheme = 'com.anum.app';

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
}

void _configureIos() {
  final file = File('ios/Runner/Info.plist');
  if (!file.existsSync()) {
    throw StateError('Run flutter create before native configuration.');
  }
  file.writeAsStringSync(configureIosInfoPlist(file.readAsStringSync()));
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
  if (!value.contains('android:usesCleartextTraffic')) {
    value = value.replaceFirst(
      '<application',
      '<application android:usesCleartextTraffic="true"',
    );
  }
  return value;
}

/// Sets the SDK level and the `appAuthRedirectScheme` manifest placeholder
/// that flutter_appauth's merged manifest uses for the intent filter of
/// AppAuth's RedirectUriReceiverActivity, so `com.anum.app:/oauth2redirect`
/// returns to the app after Keycloak sign-in and sign-out.
String configureAndroidGradle(String gradle) {
  var value = gradle.replaceFirst(
    'compileSdk = flutter.compileSdkVersion',
    'compileSdk = 37',
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
  return value;
}

/// Info.plist nests dictionaries; new top-level keys go before the root
/// dictionary's closing tag, which is the last one in the file.
String _insertBeforeLastDictClose(String plist, String entry) {
  final index = plist.lastIndexOf('</dict>');
  if (index < 0) throw StateError('Info.plist has no root dictionary.');
  return plist.replaceRange(index, index, entry);
}
