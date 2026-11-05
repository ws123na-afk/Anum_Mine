"""The action browser: the ``web.act`` agent tool (docs/action-browser.md).

* ``classify``: decides, outside the model, whether a step may run, needs the owner's
  approval, needs the owner's hands (a captcha or a code) or is refused. Pure.
* ``handles``: saved-secret handles (``{{login:emirates.password}}``), scope checks and masking. Pure.
* ``proxy``, ``kernel``, ``service_routes``: the browser side (no ANUM settings, no secrets but
  the one value being typed), run inside the browser service container in action mode.
* ``planner``: asks the model for the next step from an untrusted, labelled page snapshot.
* ``store``, ``vault``: sessions, the step log and the encrypted saved logins and cards.
* ``tool``: the registered handler; ``api``: the routes for the web app.

``classify``, ``handles``, ``proxy``, ``kernel`` and ``service_routes`` import nothing from the
API's settings, so the browser container needs no ANUM configuration.
"""

ACT_TOOL = "web.act"
ACTION_INTEGRATION_ID = "action-browser"
