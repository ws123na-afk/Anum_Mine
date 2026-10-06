import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/features/voice/wake_word.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('matchWakeWord', () {
    test('returns the rest of the sentence after the name', () {
      expect(matchWakeWord("Anum, what's running?", 'Anum'), "what's running?");
      expect(
          matchWakeWord('anum what is my status', 'Anum'), 'what is my status');
    });

    test('accepts hey, hi, ok, okay and hello prefixes', () {
      expect(matchWakeWord('hey Anum', 'Anum'), '');
      expect(matchWakeWord('Hi Anum. Who are you', 'Anum'), 'Who are you');
      expect(matchWakeWord('OK Anum create a task', 'Anum'), 'create a task');
      expect(matchWakeWord('okay anum', 'Anum'), '');
      expect(matchWakeWord('hello Anum!', 'Anum'), '');
    });

    test('matches mid-sentence after other words', () {
      expect(matchWakeWord('so hey Anum what now', 'Anum'), 'what now');
    });

    test('understands Arabic with and without يا', () {
      expect(
          matchWakeWord('يا أنوم، ما حالة مهامي؟', 'Anum'), 'ما حالة مهامي؟');
      expect(matchWakeWord('انوم', 'Anum'), '');
      expect(matchWakeWord('يا ليلى كيف حالك', 'ليلى'), 'كيف حالك');
      expect(matchWakeWord('ياليلى', 'ليلى'), '');
    });

    test('custom names replace the default', () {
      expect(matchWakeWord('Layla, tell me a joke', 'Layla'), 'tell me a joke');
      expect(matchWakeWord('Anum, tell me a joke', 'Layla'), isNull);
    });

    test('common transcriptions of the default name still wake it', () {
      expect(matchWakeWord('hey a num what is running', 'Anum'),
          'what is running');
      expect(matchWakeWord('Anam status', 'Anum'), 'status');
    });

    test('ignores speech without the name', () {
      expect(matchWakeWord('we should ask Sam about it', 'Anum'), isNull);
      expect(matchWakeWord('Banum is not a name', 'Anum'), isNull);
      expect(matchWakeWord('give me a number', 'Anum'), isNull);
      expect(matchWakeWord('', 'Anum'), isNull);
    });
  });

  test('sanitizeAssistantName keeps what the API accepts', () {
    expect(sanitizeAssistantName('  Layla  '), 'Layla');
    expect(sanitizeAssistantName('Nova<script>'), 'Novascript');
    expect(sanitizeAssistantName('ليلى'), 'ليلى');
    expect(sanitizeAssistantName('!!!'), '');
    expect(sanitizeAssistantName('x' * 60).length, 40);
  });

  group('rankTtsVoices', () {
    test('prefers natural, high quality voices for the locale', () {
      final ranked = rankTtsVoices([
        {'name': 'en-us-x-sfg-local', 'locale': 'en-US', 'quality': 'normal'},
        {
          'name': 'en-us-x-iom-network',
          'locale': 'en-US',
          'quality': 'very high',
          'network_required': '1'
        },
        {'name': 'fr-fr-x-vlf-network', 'locale': 'fr-FR'},
        {'name': 'en-gb-x-rjs-local', 'locale': 'en-GB', 'quality': 'high'},
      ], 'en-US');
      expect(ranked.first['name'], 'en-us-x-iom-network');
      expect(ranked.map((voice) => voice['locale']), isNot(contains('fr-FR')));
      expect(ranked, hasLength(3));
    });

    test('handles iOS style names and Arabic', () {
      final ranked = rankTtsVoices([
        {'name': 'Maged', 'locale': 'ar-SA', 'quality': 'default'},
        {'name': 'Laila (Enhanced)', 'locale': 'ar_SA', 'quality': 'enhanced'},
        {'name': 'Samantha', 'locale': 'en-US'},
      ], 'ar-SA');
      expect(ranked.first['name'], 'Laila (Enhanced)');
      expect(ranked, hasLength(2));
    });
  });
}
