/// App lock (docs/mobile.md#app-lock): Settings, the lock screen and the
/// reasons shown in the system prompt.
const appLockEn = <String, String>{
  'lock.title': 'App lock',
  'lock.subtitleOff':
      "Ask for this phone's fingerprint, face or screen lock before sensitive areas open.",
  'lock.subtitleOn':
      'On. Unlocks with this phone\'s fingerprint, face or screen lock.',
  'lock.after': 'Lock after',
  'lock.after.0': 'Immediately',
  'lock.after.60': '1 minute',
  'lock.after.300': '5 minutes',
  'lock.after.900': '15 minutes',
  'lock.afterHelp':
      'How long ANUM may be in the background before it locks again. An approval decision asks again when the last unlock is older than this.',
  'lock.scope': 'What to lock',
  'lock.scope.sensitive': 'Finance, Books and approval decisions',
  'lock.scope.wholeApp': 'The whole app',
  'lock.reason.enable': 'Confirm it is you to turn on the app lock',
  'lock.reason.change': 'Confirm it is you to change the app lock',
  'lock.reason.unlock': 'Unlock ANUM',
  'lock.reason.decision': 'Confirm it is you to approve or reject',
  'lock.saved.on': 'App lock is on.',
  'lock.saved.off': 'App lock is off.',
  'lock.saved': 'Saved.',
  'lock.notConfirmed': 'Not changed: the check was cancelled or did not pass.',
  'lock.saveFailed':
      'Not changed: the setting could not be saved on this phone. Try again.',
  'lock.unsupported':
      'This phone cannot check a fingerprint, face or screen lock, so the app lock cannot be turned on here.',
  'lock.notEnrolled.title': 'Set a screen lock first',
  'lock.notEnrolled.body':
      'The app lock uses this phone\'s own screen lock, and none is set up. Set one, then turn the app lock on.',
  'lock.notEnrolled.how': 'How to set a screen lock',
  'lock.howTo.title': 'Set a screen lock',
  'lock.howTo.android':
      'Android: open the phone\'s Settings, then Security (or "Security and privacy"), then Screen lock. Choose a PIN, pattern or password. You can add a fingerprint or face there too.',
  'lock.howTo.ios':
      'iPhone or iPad: open Settings, then "Face ID and Passcode" (or "Touch ID and Passcode"), then Turn Passcode On. You can set up Face ID or Touch ID there too.',
  'lock.howTo.then': 'Then come back here and turn the app lock on.',
  'lock.close': 'Close',
  'lock.screen.title': 'ANUM is locked',
  'lock.screen.titleArea': 'This part of ANUM is locked',
  'lock.screen.body':
      'Unlock with this phone\'s fingerprint, face or screen lock.',
  'lock.screen.unlock': 'Unlock',
  'lock.screen.checking': 'Waiting for the phone to check',
  'lock.screen.signOut': 'Sign out',
  'lock.screen.back': 'Go back',
  'lock.signOut.title': 'Sign out?',
  'lock.signOut.body':
      'You will need to sign in again. The app lock is turned off with the session.',
  'lock.signOut.cancel': 'Cancel',
  'lock.failed.cancelled': 'Still locked: the check was cancelled. Try again.',
  'lock.failed.failed':
      'Still locked: the phone did not recognise you. Try again.',
  'lock.failed.lockedOut':
      'Too many tries. The phone has paused the check: wait about 30 seconds, then try again.',
  'lock.failed.lockedOutUntilDeviceUnlock':
      'Too many tries. The phone has turned off fingerprint and face for now: lock the phone, unlock it with its PIN, pattern or passcode, then try again.',
  'lock.failed.notEnrolled':
      'This phone has no screen lock, fingerprint or face set up any more, so ANUM cannot unlock. Set a screen lock in the phone\'s Settings and try again, or sign out.',
  'lock.failed.unavailable':
      'The phone could not run the check. Try again, or sign out and sign in again.',
  'lock.decisionRefused':
      'Not sent: confirm it is you with this phone\'s fingerprint, face or screen lock, then try again.',
};

const appLockAr = <String, String>{
  'lock.title': 'قفل التطبيق',
  'lock.subtitleOff':
      'اطلب بصمة الإصبع أو الوجه أو قفل شاشة هذا الهاتف قبل فتح الأقسام الحساسة.',
  'lock.subtitleOn':
      'مفعّل. يُفتح ببصمة الإصبع أو الوجه أو قفل شاشة هذا الهاتف.',
  'lock.after': 'القفل بعد',
  'lock.after.0': 'فورًا',
  'lock.after.60': 'دقيقة واحدة',
  'lock.after.300': '5 دقائق',
  'lock.after.900': '15 دقيقة',
  'lock.afterHelp':
      'المدة التي يبقى فيها أنوم في الخلفية قبل أن يُقفل من جديد. قرار الموافقة يطلب التحقق مجددًا إذا كان آخر فتح أقدم من هذه المدة.',
  'lock.scope': 'ما الذي يُقفل',
  'lock.scope.sensitive': 'المالية والدفاتر وقرارات الموافقة',
  'lock.scope.wholeApp': 'التطبيق كله',
  'lock.reason.enable': 'أكّد هويتك لتفعيل قفل التطبيق',
  'lock.reason.change': 'أكّد هويتك لتغيير قفل التطبيق',
  'lock.reason.unlock': 'افتح قفل أنوم',
  'lock.reason.decision': 'أكّد هويتك للموافقة أو الرفض',
  'lock.saved.on': 'تم تفعيل قفل التطبيق.',
  'lock.saved.off': 'تم إيقاف قفل التطبيق.',
  'lock.saved': 'تم الحفظ.',
  'lock.notConfirmed': 'لم يتغير شيء: أُلغي التحقق أو لم ينجح.',
  'lock.saveFailed':
      'لم يتغير شيء: تعذّر حفظ الإعداد على هذا الهاتف. حاول مجددًا.',
  'lock.unsupported':
      'هذا الهاتف لا يستطيع التحقق ببصمة الإصبع أو الوجه أو قفل الشاشة، لذلك لا يمكن تفعيل قفل التطبيق هنا.',
  'lock.notEnrolled.title': 'اضبط قفل الشاشة أولًا',
  'lock.notEnrolled.body':
      'قفل التطبيق يستخدم قفل شاشة هذا الهاتف، ولا يوجد قفل مضبوط. اضبط قفلًا ثم فعّل قفل التطبيق.',
  'lock.notEnrolled.how': 'طريقة ضبط قفل الشاشة',
  'lock.howTo.title': 'ضبط قفل الشاشة',
  'lock.howTo.android':
      'أندرويد: افتح إعدادات الهاتف، ثم الأمان (أو "الأمان والخصوصية")، ثم قفل الشاشة. اختر رقمًا سريًا أو نمطًا أو كلمة مرور. يمكنك إضافة بصمة الإصبع أو الوجه من هناك أيضًا.',
  'lock.howTo.ios':
      'آيفون أو آيباد: افتح الإعدادات، ثم "بصمة الوجه ورمز الدخول" (أو "بصمة الإصبع ورمز الدخول")، ثم تشغيل رمز الدخول. يمكنك إعداد بصمة الوجه أو الإصبع من هناك أيضًا.',
  'lock.howTo.then': 'ثم عد إلى هنا وفعّل قفل التطبيق.',
  'lock.close': 'إغلاق',
  'lock.screen.title': 'أنوم مقفل',
  'lock.screen.titleArea': 'هذا القسم من أنوم مقفل',
  'lock.screen.body':
      'افتح القفل ببصمة الإصبع أو الوجه أو قفل شاشة هذا الهاتف.',
  'lock.screen.unlock': 'فتح القفل',
  'lock.screen.checking': 'بانتظار تحقق الهاتف',
  'lock.screen.signOut': 'تسجيل الخروج',
  'lock.screen.back': 'رجوع',
  'lock.signOut.title': 'تسجيل الخروج؟',
  'lock.signOut.body':
      'ستحتاج إلى تسجيل الدخول من جديد. يتوقف قفل التطبيق مع انتهاء الجلسة.',
  'lock.signOut.cancel': 'إلغاء',
  'lock.failed.cancelled': 'ما زال مقفلًا: أُلغي التحقق. حاول مجددًا.',
  'lock.failed.failed': 'ما زال مقفلًا: لم يتعرّف الهاتف عليك. حاول مجددًا.',
  'lock.failed.lockedOut':
      'محاولات كثيرة. أوقف الهاتف التحقق مؤقتًا: انتظر نحو 30 ثانية ثم حاول مجددًا.',
  'lock.failed.lockedOutUntilDeviceUnlock':
      'محاولات كثيرة. أوقف الهاتف بصمة الإصبع والوجه مؤقتًا: اقفل الهاتف وافتحه برقمه السري أو النمط أو رمز الدخول، ثم حاول مجددًا.',
  'lock.failed.notEnrolled':
      'لم يعد في هذا الهاتف قفل شاشة أو بصمة إصبع أو وجه، لذلك لا يستطيع أنوم فتح القفل. اضبط قفل الشاشة من إعدادات الهاتف وحاول مجددًا، أو سجّل الخروج.',
  'lock.failed.unavailable':
      'تعذّر على الهاتف إجراء التحقق. حاول مجددًا، أو سجّل الخروج ثم سجّل الدخول من جديد.',
  'lock.decisionRefused':
      'لم يُرسل: أكّد هويتك ببصمة الإصبع أو الوجه أو قفل شاشة هذا الهاتف ثم حاول مجددًا.',
};
