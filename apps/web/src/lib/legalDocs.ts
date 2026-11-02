// The legal documents' Markdown, bundled from docs/legal at build time so the pages show exactly
// what the docs say (docs/legal-pages.md). The Arabic text is docs/legal/ar, with the same
// placeholders; the page language follows the site's language switch. The web Dockerfile copies
// docs/legal for this.
import terms from '../../../../docs/legal/terms-of-service.md?raw';
import privacy from '../../../../docs/legal/privacy-policy.md?raw';
import dpa from '../../../../docs/legal/data-processing-addendum.md?raw';
import aup from '../../../../docs/legal/acceptable-use-policy.md?raw';
import subprocessors from '../../../../docs/legal/subprocessors.md?raw';
import termsAr from '../../../../docs/legal/ar/terms-of-service.md?raw';
import privacyAr from '../../../../docs/legal/ar/privacy-policy.md?raw';
import dpaAr from '../../../../docs/legal/ar/data-processing-addendum.md?raw';
import aupAr from '../../../../docs/legal/ar/acceptable-use-policy.md?raw';
import subprocessorsAr from '../../../../docs/legal/ar/subprocessors.md?raw';
import type { LegalDocId, LegalLanguage } from './publicSite';

export const LEGAL_SOURCES: Record<LegalLanguage, Record<LegalDocId, string>> = {
  en: { terms, privacy, dpa, aup, subprocessors },
  ar: { terms: termsAr, privacy: privacyAr, dpa: dpaAr, aup: aupAr, subprocessors: subprocessorsAr },
};
