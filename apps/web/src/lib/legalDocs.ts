// The legal documents' Markdown, bundled from docs/legal at build time so the pages show exactly
// what the docs say (docs/legal-pages.md). The web Dockerfile copies docs/legal for this.
import terms from '../../../../docs/legal/terms-of-service.md?raw';
import privacy from '../../../../docs/legal/privacy-policy.md?raw';
import dpa from '../../../../docs/legal/data-processing-addendum.md?raw';
import aup from '../../../../docs/legal/acceptable-use-policy.md?raw';
import subprocessors from '../../../../docs/legal/subprocessors.md?raw';
import type { LegalDocId } from './publicSite';

export const LEGAL_SOURCES: Record<LegalDocId, string> = { terms, privacy, dpa, aup, subprocessors };
