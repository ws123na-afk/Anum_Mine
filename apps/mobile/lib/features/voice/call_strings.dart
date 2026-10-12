/// English and Arabic text for the call screen (docs/voice.md#call-mode).
library;

import 'call_models.dart';

String callText(String key, bool arabic,
    [Map<String, Object> values = const {}]) {
  var text = (arabic ? _ar : _en)[key] ?? _en[key] ?? key;
  values.forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
  return text;
}

String callProblemText(CallProblem problem, bool arabic,
    {String? detail, String? visionReason}) {
  switch (problem) {
    case CallProblem.visionOff:
      if (!arabic && visionReason != null) return visionReason;
    case CallProblem.provider:
    case CallProblem.budget:
      if (detail != null && !arabic) return detail;
    default:
  }
  return callText('problem.${problem.name}', arabic);
}

const _en = <String, String>{
  'title': 'Call with {name}',
  'status.idle': 'Ready',
  'status.connecting': 'Connecting',
  'status.listening': 'Listening',
  'status.thinking': 'Thinking',
  'status.speaking': 'Speaking',
  'status.ending': 'Call ended',
  'status.ended': 'Call ended',
  'status.muted': 'Muted',
  'timer': 'Call time {time}',
  'hint': 'Just talk. Pause when you are done.',
  'mute': 'Mute',
  'unmute': 'Unmute',
  'video': 'Video',
  'speaker': 'Speaker',
  'chat': 'Chat',
  'more': 'More',
  'end': 'End call',
  'muteLabel': 'Mute microphone',
  'unmuteLabel': 'Unmute microphone',
  'videoLabel': 'Show a photo',
  'speakerOnLabel': 'Speak replies aloud',
  'speakerOffLabel': 'Stop speaking replies aloud',
  'chatLabel': 'Show transcript',
  'moreLabel': 'Call details',
  'interrupt': 'Stop the reply',
  'transcript': 'Transcript',
  'empty': 'Nothing said yet. Talk, or type below.',
  'you': 'You',
  'typeTo': 'Type to {name}',
  'send': 'Send',
  'createTask': 'Create task',
  'notNow': 'Not now',
  'taskCreated': 'Task created',
  'openApprovals': 'Open Approvals',
  'approvalsWaiting': 'Approvals waiting: {count}',
  'photoTitle': 'Show {name} a photo',
  'photoText':
      'Live camera video is not in the app yet. Take or choose a photo and {name} answers about it. The photo is sent once and never stored.',
  'photoPick': 'Choose a photo',
  'photoSent': 'Photo sent with your question.',
  'detailsLatency': 'Reply speed',
  'detailsLatencyNone': 'No reply yet (target {seconds} s)',
  'detailsLatencyValue':
      'Last reply started after {seconds} s (target {target} s)',
  'detailsBargeIns': 'Times you cut in',
  'detailsEngine': 'Speech',
  'detailsEngineValue': 'Recognition and voice on this device',
  'detailsCamera': 'Photo answers',
  'detailsApprovals':
      'Nothing is approved, deleted or paid by voice. Proposed tasks wait for your tap; approvals stay on the Approvals screen.',
  'saveTitle': 'Save this call?',
  'saveText':
      'Saving keeps a summary and the transcript as a conversation you can reopen and delete. Not saving keeps nothing beyond your transcript setting.',
  'save': 'Save summary',
  'saving': 'Saving...',
  'discard': 'End without saving',
  'endedTitle': 'Call ended',
  'summaryByModel': 'Summary written by the workspace model:',
  'summaryFromTranscript': 'Summary from the transcript:',
  'nothingKept': 'Nothing from this call was saved.',
  'openConversation': 'Open conversation',
  'close': 'Close',
  'tryAgain': 'Try again',
  'openSettings': 'Open settings',
  'problem.micDenied':
      'The microphone is blocked. Allow it for ANUM in the phone settings, then try again.',
  'problem.speechUnavailable':
      'Speech recognition is not available on this phone. Type in Chat; replies are still spoken.',
  'problem.offline':
      'No connection. The call answers again when you are back online.',
  'problem.provider':
      'The model did not answer. Try again in a moment; a workspace owner can check Model settings.',
  'problem.budget': 'This workspace has used its monthly model budget.',
  'problem.limit':
      'This call reached its 60-question limit. End it and start a new call.',
  'problem.session':
      'This call\'s session was closed. End it and start a new call.',
  'problem.forbidden':
      'Your role cannot start calls. Ask a workspace owner or admin for member access.',
  'problem.visionOff':
      'Photo answers need a vision model. An owner can choose one in Model settings.',
  'problem.photoTooLarge': 'That photo is too large. Choose a smaller one.',
  'problem.heardNothing': 'I did not catch that. Say it again.',
  'problem.summary':
      'The summary could not be saved. Retry, or end without saving.',
};

const _ar = <String, String>{
  'title': 'مكالمة مع {name}',
  'status.idle': 'جاهز',
  'status.connecting': 'جارٍ الاتصال',
  'status.listening': 'يستمع',
  'status.thinking': 'يفكر',
  'status.speaking': 'يتحدث',
  'status.ending': 'انتهت المكالمة',
  'status.ended': 'انتهت المكالمة',
  'status.muted': 'الميكروفون مكتوم',
  'timer': 'مدة المكالمة {time}',
  'hint': 'تحدث فقط، وتوقف عندما تنتهي.',
  'mute': 'كتم',
  'unmute': 'إلغاء الكتم',
  'video': 'فيديو',
  'speaker': 'السماعة',
  'chat': 'محادثة',
  'more': 'المزيد',
  'end': 'إنهاء المكالمة',
  'muteLabel': 'اكتم الميكروفون',
  'unmuteLabel': 'ألغِ كتم الميكروفون',
  'videoLabel': 'أرِه صورة',
  'speakerOnLabel': 'انطق الردود',
  'speakerOffLabel': 'أوقف نطق الردود',
  'chatLabel': 'اعرض النص',
  'moreLabel': 'تفاصيل المكالمة',
  'interrupt': 'أوقف الرد',
  'transcript': 'النص',
  'empty': 'لم يُقل شيء بعد. تحدث أو اكتب أدناه.',
  'you': 'أنت',
  'typeTo': 'اكتب إلى {name}',
  'send': 'أرسل',
  'createTask': 'أنشئ المهمة',
  'notNow': 'ليس الآن',
  'taskCreated': 'أُنشئت المهمة',
  'openApprovals': 'افتح الموافقات',
  'approvalsWaiting': 'موافقات بانتظارك: {count}',
  'photoTitle': 'أرِ {name} صورة',
  'photoText':
      'فيديو الكاميرا المباشر غير متاح في التطبيق بعد. التقط صورة أو اخترها وسيجيب {name} عنها. تُرسل الصورة مرة واحدة ولا تُخزَّن.',
  'photoPick': 'اختر صورة',
  'photoSent': 'أُرسلت الصورة مع سؤالك.',
  'detailsLatency': 'سرعة الرد',
  'detailsLatencyNone': 'لا رد بعد (الهدف {seconds} ث)',
  'detailsLatencyValue': 'بدأ آخر رد بعد {seconds} ث (الهدف {target} ث)',
  'detailsBargeIns': 'مرات المقاطعة',
  'detailsEngine': 'الكلام',
  'detailsEngineValue': 'التعرف والصوت على هذا الجهاز',
  'detailsCamera': 'إجابات الصور',
  'detailsApprovals':
      'لا تتم الموافقة أو الحذف أو الدفع بالصوت. المهام المقترحة تنتظر نقرتك، والموافقات تبقى في شاشة الموافقات.',
  'saveTitle': 'هل تحفظ هذه المكالمة؟',
  'saveText':
      'الحفظ يحتفظ بملخص والنص كمحادثة يمكنك فتحها وحذفها. عدم الحفظ لا يحتفظ بشيء سوى ما يحدده إعداد النص.',
  'save': 'احفظ الملخص',
  'saving': 'جارٍ الحفظ...',
  'discard': 'إنهاء دون حفظ',
  'endedTitle': 'انتهت المكالمة',
  'summaryByModel': 'ملخص كتبه نموذج مساحة العمل:',
  'summaryFromTranscript': 'ملخص من النص:',
  'nothingKept': 'لم يُحفظ شيء من هذه المكالمة.',
  'openConversation': 'افتح المحادثة',
  'close': 'إغلاق',
  'tryAgain': 'حاول مرة أخرى',
  'openSettings': 'افتح الإعدادات',
  'problem.micDenied':
      'الميكروفون محظور. اسمح به لتطبيق ANUM من إعدادات الهاتف، ثم حاول مرة أخرى.',
  'problem.speechUnavailable':
      'التعرف على الكلام غير متاح على هذا الهاتف. اكتب في المحادثة، وستُنطق الردود.',
  'problem.offline': 'لا يوجد اتصال. ستعود المكالمة للرد عند عودة الاتصال.',
  'problem.provider':
      'لم يُجب النموذج. حاول بعد قليل؛ ويمكن لمالك مساحة العمل مراجعة إعدادات النموذج.',
  'problem.budget': 'استنفدت مساحة العمل ميزانية النموذج الشهرية.',
  'problem.limit': 'بلغت المكالمة حد 60 سؤالاً. أنهها وابدأ مكالمة جديدة.',
  'problem.session': 'أُغلقت جلسة هذه المكالمة. أنهها وابدأ مكالمة جديدة.',
  'problem.forbidden':
      'دورك لا يسمح ببدء المكالمات. اطلب من مالك مساحة العمل أو المسؤول صلاحية عضو.',
  'problem.visionOff':
      'إجابات الصور تحتاج نموذج رؤية. يمكن للمالك اختياره في إعدادات النموذج.',
  'problem.photoTooLarge': 'هذه الصورة كبيرة جدًا. اختر صورة أصغر.',
  'problem.heardNothing': 'لم أسمع ذلك جيدًا. أعد قوله.',
  'problem.summary': 'تعذر حفظ الملخص. أعد المحاولة أو أنهِ دون حفظ.',
};
