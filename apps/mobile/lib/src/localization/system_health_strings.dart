/// System health strings on the phone, English and Arabic
/// (docs/system-health.md#on-the-phone). The `health.*` keys are the web's own
/// sentences (apps/web/src/lib/messages/systemHealth.*.ts); `sh.*` are the
/// phone's short summary lines. Looked up by `AnumLocalizations.t` after the
/// main catalogs.
library;

const systemHealthEn = <String, String>{
  'health.title': 'System health',
  'health.lead':
      'What this server reads about itself right now: backups, the AI model, disk, memory, email, background jobs, errors and restarts. Read-only; nothing here changes anything.',
  'health.loading': 'Checking the server…',
  'health.recheck': 'Check again',
  'health.allGood': 'All checks are green',
  'health.fixLabel': 'What to do:',
  'health.state.green': 'Healthy',
  'health.state.amber': 'Needs attention',
  'health.state.red': 'Problem',
  'health.state.unknown': 'Not known',
  'health.item.backup_local': 'Local backup',
  'health.item.backup_offsite': 'Off-site backup',
  'health.item.restore_drill': 'Restore drill',
  'health.item.ai_model': 'AI model',
  'health.item.disk': 'Disk space',
  'health.item.memory': 'Memory',
  'health.item.email': 'Email sending',
  'health.item.scheduler': 'Background jobs',
  'health.item.errors': 'Errors',
  'health.item.restarts': 'Restarts',
  'health.item.notices': 'Owner notices',
  'health.fix.backup_local':
      'On the VM run sudo bash infra/oracle/backup.sh backup and read its output. Check the backup timer with systemctl list-timers \'anum-*\'.',
  'health.fix.backup_offsite':
      'On the VM run sudo bash infra/oracle/backup.sh offsite. Check ANUM_BACKUP_S3_BUCKET and the B2 key in infra/oracle/.env.',
  'health.fix.restore_drill':
      'On the VM run sudo bash infra/oracle/backup.sh drill. The monthly timer is anum-drill.timer (sudo bash infra/oracle/backup.sh drill-timer installs it).',
  'health.fix.ai_model':
      'Set ANUM_MODEL_PROVIDER and ANUM_MODEL_API_KEY in infra/oracle/.env, then run sudo bash infra/oracle/update.sh.',
  'health.fix.disk':
      'Free space on the VM: remove old backup copies under /var/lib/anum/backups, run docker image prune -f, or grow the boot volume.',
  'health.fix.memory':
      'Run docker stats on the VM to see which service uses the most, then restart it or lower its memory limit.',
  'health.fix.email':
      'Set ANUM_SMTP_HOST, ANUM_SMTP_FROM and the SMTP login in infra/oracle/.env, then run update.sh.',
  'health.fix.scheduler':
      'Set ANUM_AUTOMATION_SCHEDULER_ENABLED=true. If it is on, read the API log: docker compose logs --since 1h api.',
  'health.fix.errors':
      'Read the errors on the VM: docker compose logs --since 24h api | grep ERROR.',
  'health.fix.restarts':
      'Read why it stopped: docker compose logs --since 24h api, and sudo dmesg | grep -i oom for the out-of-memory killer.',
  'health.fix.notices':
      'Set ANUM_SYSTEM_HEALTH_TENANT_ID and ANUM_SYSTEM_HEALTH_WORKSPACE_ID in infra/oracle/.env (the web page shows both ids), then run update.sh.',
  'sh.attention': 'Checks that need you: {count}',
  'sh.checked': 'Checked {when}',
  'sh.webMore':
      'The numbers behind each check are on the web: Billing console, System health.',
  'sh.deniedTitle': 'Platform owners only',
  'sh.denied':
      'Only a platform operator can see System health. Ask whoever runs ANUM\'s sign-in (Keycloak) to give you the realm role platform_operator.',
  'sh.failed': 'Could not read the server\'s health: {message}',
  'sh.backup.notRecorded': 'Not recorded on this server.',
  'sh.backup.never': 'It has never succeeded.',
  'sh.backup.last': 'Last good one: {when}',
  'sh.model.placeholder': 'The mock model: answers are placeholders.',
  'sh.model.keyMissing': '{provider}, but its API key is not set.',
  'sh.model.real': 'Answers come from {provider}.',
  'sh.free': '{pct}% free',
  'sh.unreadable': 'The server could not read this value.',
  'sh.email.on': 'An SMTP server is set.',
  'sh.email.off': 'No SMTP server: ANUM sends no email.',
  'sh.scheduler.off': 'Background jobs are off.',
  'sh.scheduler.starting': 'Starting.',
  'sh.scheduler.last': 'Last pass: {when}',
  'sh.scheduler.none': 'No pass has finished recently.',
  'sh.errors': 'Errors in the last 24 hours: {count}',
  'sh.restarts.notTracked': 'Tracked once owner notices are set up.',
  'sh.restarts.last': 'Last unexpected restart: {when}',
  'sh.restarts.none': 'No unexpected restart recorded.',
  'sh.notices.on': 'Owners get a daily notice while a check is red.',
  'sh.notices.off': 'Not set up: nobody is told when a check turns red.',
};

const systemHealthAr = <String, String>{
  'health.title': 'صحة النظام',
  'health.lead':
      'ما يقرؤه هذا الخادم عن نفسه الآن: النسخ الاحتياطية ونموذج الذكاء الاصطناعي والقرص والذاكرة والبريد والمهام في الخلفية والأخطاء وإعادات التشغيل. للقراءة فقط، ولا يغيّر شيئًا.',
  'health.loading': 'جارٍ فحص الخادم…',
  'health.recheck': 'افحص مرة أخرى',
  'health.allGood': 'كل الفحوص سليمة',
  'health.fixLabel': 'ما العمل:',
  'health.state.green': 'سليم',
  'health.state.amber': 'يحتاج إلى انتباه',
  'health.state.red': 'مشكلة',
  'health.state.unknown': 'غير معروف',
  'health.item.backup_local': 'النسخة الاحتياطية المحلية',
  'health.item.backup_offsite': 'النسخة الاحتياطية الخارجية',
  'health.item.restore_drill': 'تمرين الاستعادة',
  'health.item.ai_model': 'نموذج الذكاء الاصطناعي',
  'health.item.disk': 'مساحة القرص',
  'health.item.memory': 'الذاكرة',
  'health.item.email': 'إرسال البريد',
  'health.item.scheduler': 'المهام في الخلفية',
  'health.item.errors': 'الأخطاء',
  'health.item.restarts': 'إعادات التشغيل',
  'health.item.notices': 'تنبيهات المالك',
  'health.fix.backup_local':
      'على الخادم الافتراضي شغّل sudo bash infra/oracle/backup.sh backup واقرأ مخرجاته. وتحقق من مؤقت النسخ بالأمر systemctl list-timers \'anum-*\'.',
  'health.fix.backup_offsite':
      'على الخادم الافتراضي شغّل sudo bash infra/oracle/backup.sh offsite. وتحقق من ANUM_BACKUP_S3_BUCKET ومفتاح B2 في infra/oracle/.env.',
  'health.fix.restore_drill':
      'على الخادم الافتراضي شغّل sudo bash infra/oracle/backup.sh drill. المؤقت الشهري هو anum-drill.timer ويثبّته الأمر sudo bash infra/oracle/backup.sh drill-timer.',
  'health.fix.ai_model':
      'اضبط ANUM_MODEL_PROVIDER وANUM_MODEL_API_KEY في infra/oracle/.env، ثم شغّل sudo bash infra/oracle/update.sh.',
  'health.fix.disk':
      'وفّر مساحة على الخادم الافتراضي: احذف النسخ القديمة تحت /var/lib/anum/backups، أو شغّل docker image prune -f، أو كبّر قرص الإقلاع.',
  'health.fix.memory':
      'شغّل docker stats على الخادم الافتراضي لترى أي خدمة تستهلك أكثر، ثم أعد تشغيلها أو اخفض حد ذاكرتها.',
  'health.fix.email':
      'اضبط ANUM_SMTP_HOST وANUM_SMTP_FROM وبيانات دخول SMTP في infra/oracle/.env، ثم شغّل update.sh.',
  'health.fix.scheduler':
      'اضبط ANUM_AUTOMATION_SCHEDULER_ENABLED=true. وإن كان مفعّلًا فاقرأ سجل الواجهة البرمجية: docker compose logs --since 1h api.',
  'health.fix.errors':
      'اقرأ الأخطاء على الخادم الافتراضي: docker compose logs --since 24h api | grep ERROR.',
  'health.fix.restarts':
      'اقرأ سبب التوقف: docker compose logs --since 24h api، وsudo dmesg | grep -i oom لمعرفة ما إذا أوقفها النظام لنفاد الذاكرة.',
  'health.fix.notices':
      'اضبط ANUM_SYSTEM_HEALTH_TENANT_ID وANUM_SYSTEM_HEALTH_WORKSPACE_ID في infra/oracle/.env (تعرض صفحة الويب المعرّفين)، ثم شغّل update.sh.',
  'sh.attention': 'فحوص تحتاج إليك: {count}',
  'sh.checked': 'فُحص في {when}',
  'sh.webMore': 'الأرقام وراء كل فحص على الويب: لوحة الفوترة، صحة النظام.',
  'sh.deniedTitle': 'لمالكي المنصة فقط',
  'sh.denied':
      'صحة النظام متاحة لمشغّل المنصة فقط. اطلب ممن يدير تسجيل الدخول في ANUM (Keycloak) أن يمنحك دور النطاق platform_operator.',
  'sh.failed': 'تعذّرت قراءة صحة الخادم: {message}',
  'sh.backup.notRecorded': 'غير مسجّل على هذا الخادم.',
  'sh.backup.never': 'لم ينجح قط.',
  'sh.backup.last': 'آخر نجاح: {when}',
  'sh.model.placeholder': 'النموذج التجريبي mock: الإجابات نصوص مؤقتة.',
  'sh.model.keyMissing': '{provider}، لكن مفتاحه غير مضبوط.',
  'sh.model.real': 'الإجابات من {provider}.',
  'sh.free': '{pct}% حرة',
  'sh.unreadable': 'تعذّر على الخادم قراءة هذه القيمة.',
  'sh.email.on': 'خادم SMTP مضبوط.',
  'sh.email.off': 'لا يوجد خادم SMTP: لا يرسل ANUM أي بريد.',
  'sh.scheduler.off': 'المهام في الخلفية متوقفة.',
  'sh.scheduler.starting': 'قيد البدء.',
  'sh.scheduler.last': 'آخر جولة: {when}',
  'sh.scheduler.none': 'لم تنتهِ أي جولة مؤخرًا.',
  'sh.errors': 'الأخطاء في آخر 24 ساعة: {count}',
  'sh.restarts.notTracked': 'تُتابع بعد إعداد تنبيهات المالك.',
  'sh.restarts.last': 'آخر إعادة تشغيل غير متوقعة: {when}',
  'sh.restarts.none': 'لم تُسجَّل أي إعادة تشغيل غير متوقعة.',
  'sh.notices.on': 'يتلقى المالكون تنبيهًا يوميًا ما دام أحد الفحوص مشكلة.',
  'sh.notices.off': 'غير مُعدّ: لا يُبلَّغ أحد حين يصبح أحد الفحوص مشكلة.',
};
