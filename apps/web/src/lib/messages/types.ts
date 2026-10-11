// Message shapes shared by the catalogs (docs/web-localization.md).

/** A message with plural forms, picked by `vars.count` through the locale's CLDR plural rules. */
export interface PluralMessage { zero?: string; one?: string; two?: string; few?: string; many?: string; other: string }
export type Message = string | PluralMessage;
