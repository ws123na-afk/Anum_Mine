import 'dart:io';

void main() {
  _configureAndroid();
  _configureIos();
}

void _configureAndroid() {
  final file = File('android/app/src/main/AndroidManifest.xml');
  if (!file.existsSync()) throw StateError('Run flutter create before native configuration.');
  var value = file.readAsStringSync();
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
    </queries>
''';
  if (!value.contains('android.permission.RECORD_AUDIO')) {
    value = value.replaceFirst('<application', '$permissions    <application');
    file.writeAsStringSync(value);
  }
}

void _configureIos() {
  final file = File('ios/Runner/Info.plist');
  if (!file.existsSync()) throw StateError('Run flutter create before native configuration.');
  var value = file.readAsStringSync();
  const privacy = '''
	<key>NSMicrophoneUsageDescription</key>
	<string>ANUM listens only while you record a voice command.</string>
	<key>NSSpeechRecognitionUsageDescription</key>
	<string>ANUM converts your spoken command into an editable transcript.</string>
''';
  if (!value.contains('NSMicrophoneUsageDescription')) {
    value = value.replaceFirst('</dict>', '$privacy</dict>');
    file.writeAsStringSync(value);
  }
}
