"""Browser research: the read-only ``web.browse_page`` agent tool (docs/browser-research.md).

* ``guard``: URL rules, the pinned fetcher (resolve once, refuse non-public addresses, dial
  the checked address) and robots.txt.
* ``kernel``: one page in a throwaway Chromium whose every request is answered by the guard.
* ``service``: the small HTTP service that runs the kernel in its own container (no ANUM
  secrets there), so a browser never runs inside the API process.
* ``tool``: the registered tool the API and the Temporal worker call; it talks to the service.

Only ``tool`` imports the API's settings; ``guard``, ``kernel`` and ``service`` stay light so
the browser container needs no ANUM configuration.
"""

BROWSE_TOOL = "web.browse_page"
BROWSER_INTEGRATION_ID = "browser-research"
