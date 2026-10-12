"""E-commerce operations across Amazon, noon and the owner's own store (docs/commerce.md).

* ``models``: the unified commerce model (connectors, products with tyre attributes,
  channel listings, orders and lines, customers, returns, fees, alerts, settings).
* ``tyre``: tyre size, load/speed index and DOT parsing.
* ``clients``: one HTTP client per channel (Amazon SP-API, noon, own store) behind a
  shared rate-limit-aware transport.
* ``store``: the store protocol, the in-memory store and ``open_commerce_store``.
* ``sync``: incremental sync of a connector into the store, health and alerts.
* ``tools``: the agent tools (``commerce.sales_summary`` ... ``commerce.<channel>.update_price``).
* ``api``: the HTTP routes under ``/api/v1/commerce``.
"""
