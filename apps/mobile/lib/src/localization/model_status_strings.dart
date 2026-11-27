/// "AI answers are placeholders" on the phone, English and Arabic
/// (docs/model-gateway.md#placeholder-answers). The sentences match the web's
/// `model.placeholder.*` keys (apps/web/src/lib/messages/en.ts); the phone's
/// own action opens Settings, Model connection.
library;

const modelStatusEn = <String, String>{
  'model.placeholder.server':
      'AI answers are placeholders: the server has no AI model set up.',
  'model.placeholder.workspace':
      'AI answers are placeholders: this workspace is set to the placeholder model.',
  'model.placeholder.fixPhone':
      'An owner connects a real model in Settings, under Model connection. Everything else in ANUM works meanwhile.',
  'model.placeholder.setUp': 'Set up model',
};

const modelStatusAr = <String, String>{
  'model.placeholder.server':
      'إجابات الذكاء الاصطناعي نصوص مؤقتة: لم يُضبط نموذج ذكاء اصطناعي على الخادم.',
  'model.placeholder.workspace':
      'إجابات الذكاء الاصطناعي نصوص مؤقتة: مساحة العمل هذه مضبوطة على النموذج المؤقت.',
  'model.placeholder.fixPhone':
      'يربط المالك نموذجا حقيقيا من الإعدادات، في قسم ربط النموذج. وتعمل بقية أقسام ANUM في الأثناء.',
  'model.placeholder.setUp': 'إعداد النموذج',
};
