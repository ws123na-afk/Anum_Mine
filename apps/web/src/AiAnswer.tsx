import { MessageMarkdown } from './MessageMarkdown';
import { ActionMenu, CopyAction, MessageActions, useToast } from './MessageActions';
import { copyText } from './lib/clipboard';
import { markdownToPlainText } from './lib/markdown';
import { t } from './lib/i18n';

/**
 * An AI-written answer outside the chat (a task's answer, a command's result): the same
 * safe Markdown rendering and the same Copy actions as an Assistant answer
 * (docs/conversations.md#message-actions), with its own "Copied" announcement.
 */
export function AiAnswer({ text }: { text: string }) {
  const { toast, say } = useToast();
  const copied = (ok: boolean) => say(ok ? t('message.copied') : t('message.copyFailed'));
  return <div className="aiAnswer">
    <MessageMarkdown text={text} onCopied={copied} />
    <MessageActions label={t('answer.actions')}>
      <CopyAction text={text} label={t('answer.copy')} onCopied={copied} showLabel />
      <ActionMenu label={t('message.moreCopy')} items={[
        { id: 'markdown', label: t('message.copyMarkdown'), onSelect: () => void copyText(text).then(copied) },
        { id: 'text', label: t('message.copyText'), onSelect: () => void copyText(markdownToPlainText(text)).then(copied) },
      ]} />
    </MessageActions>
    {toast}
  </div>;
}
