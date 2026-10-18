import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of in-app calls (docs/in-app-calls.md).
/// test/calls/app_calls_test.dart checks that both catalogs have the same keys
/// and placeholders.
class CallText {
  const CallText(this.arabic);
  final bool arabic;

  static CallText of(BuildContext context) =>
      CallText(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key) => (arabic ? ar : en)[key] ?? en[key] ?? key;

  String f(String key, Map<String, Object> values) {
    var text = t(key);
    values.forEach((k, v) => text = text.replaceAll('{$k}', '$v'));
    return text;
  }

  static const en = <String, String>{
    'calls': 'Calls',
    'callsHint': 'Call teammates in this workspace, and see missed calls.',
    'people': 'People',
    'recent': 'Recent',
    'all': 'All calls',
    'missed': 'Missed',
    'call': 'Call',
    'video': 'Video',
    'callPerson': 'Call {name}',
    'videoPerson': 'Video call {name}',
    'off':
        'Calls are off on this server. Ask the person who runs ANUM to set up the calls relay.',
    'stunOnly':
        'No relay server is set up yet, so a call can fail on some office or mobile networks.',
    'denied':
        'Your role cannot make calls in this workspace. A workspace owner can change your role.',
    'configFailed':
        'Could not check whether calls work here. Pull down to try again.',
    'offline': 'You are offline. Calls need an internet connection.',
    'alreadyInCall': 'You are already in a call. Hang up first.',
    'voiceCall': 'Voice call',
    'videoCall': 'Video call',
    'preparing': 'Getting your microphone ready',
    'ringing': 'Ringing',
    'incoming': 'is calling you',
    'connecting': 'Connecting',
    'reconnecting': 'Connection lost. Reconnecting',
    'answer': 'Answer',
    'answerVideo': 'Answer with video',
    'decline': 'Decline',
    'hangUp': 'Hang up',
    'mute': 'Mute',
    'unmute': 'Unmute',
    'speakerOn': 'Speaker on',
    'speakerOff': 'Speaker off',
    'cameraOn': 'Turn camera on',
    'cameraOff': 'Turn camera off',
    'close': 'Close',
    'callAgain': 'Call again',
    'answeredElsewhere': 'You answered this call on another device.',
    'startFailed': 'The call could not start: {message}',
    'answerFailed': 'The call could not be answered: {message}',
    'word.completed': 'Call ended',
    'word.missed': 'Missed call',
    'word.noAnswer': 'No answer',
    'word.cancelled': 'Cancelled',
    'word.declinedByYou': 'Declined',
    'word.declinedByPeer': 'Declined',
    'word.busyPeer': 'On another call',
    'word.failed': 'Could not connect',
    'word.dropped': 'Call dropped',
    'ended.declinedByPeer': '{name} declined the call.',
    'ended.busyPeer': '{name} is on another call. Try again later.',
    'ended.noAnswer': '{name} did not answer. They will see a missed call.',
    'ended.failed':
        'The call could not connect. Check your internet connection, then call again.',
    'ended.dropped': 'The call dropped because the connection was lost.',
    'ended.duration': 'Call lasted {duration}.',
    'media.denied':
        'ANUM cannot use your microphone or camera. Allow it in your phone settings (Apps, ANUM, Permissions), then try again.',
    'media.noDevice': 'No microphone or camera was found on this device.',
    'media.other': 'Your microphone or camera could not be opened: {message}',
    'empty': 'No calls yet',
    'emptyText':
        'Call a teammate from People. Calls you make and receive show here.',
    'emptyMissed': 'No missed calls',
    'noPeople':
        'Nobody else is in this workspace yet. A workspace owner can invite teammates.',
    'loadFailed': 'Calls could not be loaded: {message}',
    'retry': 'Try again',
    'loadOlder': 'Load older calls',
    'incomingCall': 'Incoming',
    'outgoingCall': 'Outgoing',
    'loading': 'Loading',
  };

  static const ar = <String, String>{
    'calls': 'المكالمات',
    'callsHint':
        'اتصل بزملائك في مساحة العمل هذه واطّلع على المكالمات الفائتة.',
    'people': 'الأشخاص',
    'recent': 'الأخيرة',
    'all': 'كل المكالمات',
    'missed': 'الفائتة',
    'call': 'اتصال',
    'video': 'فيديو',
    'callPerson': 'اتصل بـ {name}',
    'videoPerson': 'مكالمة فيديو مع {name}',
    'off':
        'المكالمات متوقفة على هذا الخادم. اطلب من المسؤول عن تشغيل ANUM إعداد خادم ترحيل المكالمات.',
    'stunOnly':
        'لم يُعدّ خادم ترحيل بعد، لذلك قد تفشل المكالمة على بعض شبكات المكاتب أو الجوال.',
    'denied':
        'دورك لا يسمح بإجراء المكالمات في مساحة العمل هذه. يمكن لمالك مساحة العمل تغيير دورك.',
    'configFailed':
        'تعذّر التحقق من عمل المكالمات هنا. اسحب للأسفل للمحاولة مرة أخرى.',
    'offline': 'أنت غير متصل بالإنترنت. تحتاج المكالمات إلى اتصال بالإنترنت.',
    'alreadyInCall': 'أنت في مكالمة بالفعل. أنهِها أولًا.',
    'voiceCall': 'مكالمة صوتية',
    'videoCall': 'مكالمة فيديو',
    'preparing': 'جارٍ تجهيز الميكروفون',
    'ringing': 'يرن',
    'incoming': 'يتصل بك',
    'connecting': 'جارٍ الاتصال',
    'reconnecting': 'انقطع الاتصال. جارٍ إعادة الاتصال',
    'answer': 'رد',
    'answerVideo': 'رد بالفيديو',
    'decline': 'رفض',
    'hangUp': 'إنهاء المكالمة',
    'mute': 'كتم الصوت',
    'unmute': 'إلغاء الكتم',
    'speakerOn': 'تشغيل السماعة',
    'speakerOff': 'إيقاف السماعة',
    'cameraOn': 'تشغيل الكاميرا',
    'cameraOff': 'إيقاف الكاميرا',
    'close': 'إغلاق',
    'callAgain': 'اتصل مرة أخرى',
    'answeredElsewhere': 'رددت على هذه المكالمة من جهاز آخر.',
    'startFailed': 'تعذّر بدء المكالمة: {message}',
    'answerFailed': 'تعذّر الرد على المكالمة: {message}',
    'word.completed': 'انتهت المكالمة',
    'word.missed': 'مكالمة فائتة',
    'word.noAnswer': 'لا رد',
    'word.cancelled': 'أُلغيت',
    'word.declinedByYou': 'رُفضت',
    'word.declinedByPeer': 'رُفضت',
    'word.busyPeer': 'في مكالمة أخرى',
    'word.failed': 'تعذّر الاتصال',
    'word.dropped': 'انقطعت المكالمة',
    'ended.declinedByPeer': 'رفض {name} المكالمة.',
    'ended.busyPeer': '{name} في مكالمة أخرى. حاول لاحقًا.',
    'ended.noAnswer': 'لم يرد {name}. سيرى مكالمة فائتة.',
    'ended.failed':
        'تعذّر إتمام المكالمة. تحقق من اتصالك بالإنترنت ثم اتصل مرة أخرى.',
    'ended.dropped': 'انقطعت المكالمة لأن الاتصال فُقد.',
    'ended.duration': 'مدة المكالمة {duration}.',
    'media.denied':
        'لا يستطيع ANUM استخدام الميكروفون أو الكاميرا. اسمح بذلك من إعدادات الهاتف (التطبيقات، ANUM، الأذونات) ثم حاول مرة أخرى.',
    'media.noDevice': 'لم يُعثر على ميكروفون أو كاميرا في هذا الجهاز.',
    'media.other': 'تعذّر فتح الميكروفون أو الكاميرا: {message}',
    'empty': 'لا مكالمات بعد',
    'emptyText':
        'اتصل بزميل من قائمة الأشخاص. تظهر هنا المكالمات التي تجريها وتتلقاها.',
    'emptyMissed': 'لا مكالمات فائتة',
    'noPeople':
        'لا يوجد أحد آخر في مساحة العمل هذه بعد. يمكن لمالك مساحة العمل دعوة الزملاء.',
    'loadFailed': 'تعذّر تحميل المكالمات: {message}',
    'retry': 'حاول مرة أخرى',
    'loadOlder': 'تحميل مكالمات أقدم',
    'incomingCall': 'واردة',
    'outgoingCall': 'صادرة',
    'loading': 'جارٍ التحميل',
  };
}
