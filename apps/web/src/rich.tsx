// A translated message with React elements in its placeholders (a <code> id inside a sentence),
// so translators keep the element where their language needs it (docs/web-localization.md).
import { Fragment, type ReactNode } from 'react';
import { t, type MessageKey, type Vars } from './lib/i18n';

const MARK = '\u0000';

export function rich(key: MessageKey, nodes: Record<string, ReactNode>, vars: Vars = {}): ReactNode {
  const marked: Vars = { ...vars };
  for (const name of Object.keys(nodes)) marked[name] = `${MARK}${name}${MARK}`;
  // The elements isolate themselves (a <code dir="ltr">), so the text around them is not isolated.
  const parts = t(key, marked, { isolate: false }).split(MARK);
  // Odd parts are placeholder names: "Signed in as \0user\0. Everything..." -> text, name, text.
  return parts.map((part, index) => <Fragment key={index}>{index % 2 ? nodes[part] : part}</Fragment>);
}
