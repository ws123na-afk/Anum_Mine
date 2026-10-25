# Flutter Sales CRM

The Sales CRM screen uses the existing ANUM authenticated API, role enforcement and workspace-scoped backend data. The screen covers search, stage filter, funnel indicators, sales assignment, company details, budget, status transitions, quotation drafts and follow-up proposals in English/Arabic.

The repository adapter uses `AnumApiClient`, so it shares existing session headers and account handling. No autonomous send is performed.

The screen entrypoint is `MobileSalesScreen(repository: MobileSalesRepository(api))`. It must be wired into the WhatsApp navigation before calling it live; at present this branch contains the screen implementation, not a verified navigation integration. Staging device tests and Flutter compile checks remain required.
