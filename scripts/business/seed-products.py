#!/usr/bin/env python3
"""Load the owner's products and offers into a tenant's Business catalog.

Reads scripts/business/products.json and talks only to the public ANUM API
(``/api/v1/business``, docs/business.md). Standard library only.

    # Dry run, offline: print the payloads that would be sent.
    python3 scripts/business/seed-products.py

    # Dry run against a tenant: read the catalog, print the plan, write nothing.
    ANUM_API_URL=https://api.example.com ANUM_API_TOKEN=... python3 scripts/business/seed-products.py

    # Write.
    ANUM_API_URL=... ANUM_API_TOKEN=... python3 scripts/business/seed-products.py --apply

Environment:
    ANUM_API_URL       API origin, default http://localhost:8000. Plain HTTP only for localhost.
    ANUM_API_TOKEN     Bearer token of an owner (``business:manage``). Never put it in a file in git.
    ANUM_WORKSPACE_ID  Optional ``x-workspace-id``; otherwise the token's default workspace.

Idempotent: a product is matched by its slug (the slug of an existing product's
name, or of one of the entry's aliases), so a second run changes nothing. A
changed field is patched. Offers are created as drafts and never activated: the
owner confirms the price and activates the version in the Business catalog. When
the product already has a draft, it is updated; when its active version already
matches, nothing happens; otherwise a new draft version is added.

The ``target_profile`` block of each entry is never sent: the API has no fields
for it yet (the Harvey branch adds them). It is printed in the plan so the owner
can see it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).with_name("products.json")
BASE = "/api/v1/business"
PAGE = 100
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Fields the API accepts (services/api/anum_api/business/api.py: ProductInput, ProductUpdate, OfferInput).
PRODUCT_FIELDS = ("kind", "name", "name_ar", "summary", "maturity", "integrations")
UPDATABLE_PRODUCT_FIELDS = ("name", "name_ar", "summary", "maturity", "integrations")
OFFER_FIELDS = ("currency", "price_type", "price_min", "price_max", "deliverables", "limitations", "cost", "notes")
COST_FIELDS = ("labor_hours", "hourly_cost", "external_costs", "contingency_pct")
PENDING_BLOCK = "target_profile"

Transport = Callable[[str, str, "dict[str, Any] | None"], Any]


class SeedError(Exception):
    pass


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def clean_list(values: list[str], limit: int = 300) -> list[str]:
    """The server's own normalization (business/common.py), so a re-run compares equal."""
    seen: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:limit]
        if text and text not in seen:
            seen.append(text)
    return seen


def _money(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


def load_entries(path: Path = DATA_FILE) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["products"]
    slugs = [entry["slug"] for entry in entries]
    if len(set(slugs)) != len(slugs):
        raise SeedError("Two entries share a slug.")
    for entry in entries:
        if slugify(entry["product"]["name"]) != entry["slug"]:
            raise SeedError(f"Slug {entry['slug']!r} does not match the name {entry['product']['name']!r}.")
    return entries


def product_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Body for POST /products. Only API fields; target_profile stays out."""
    source = entry["product"]
    unknown = set(source) - set(PRODUCT_FIELDS)
    if unknown:
        raise SeedError(f"{entry['slug']}: fields the API does not accept: {sorted(unknown)}. Put pending fields in {PENDING_BLOCK}.")
    return {
        "kind": source["kind"],
        "name": " ".join(source["name"].split()),
        "name_ar": source.get("name_ar", ""),
        "summary": source.get("summary", ""),
        "maturity": source.get("maturity", "idea"),
        "integrations": clean_list(source.get("integrations", []), 80),
    }


def offer_payload(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Body for POST /products/{id}/versions and PATCH /offers/{id}, or None for no offer."""
    source = entry.get("offer")
    if source is None:
        return None
    unknown = set(source) - set(OFFER_FIELDS)
    if unknown:
        raise SeedError(f"{entry['slug']}: offer fields the API does not accept: {sorted(unknown)}.")
    price_type = source.get("price_type", "fixed")
    price_min, price_max = _money(source.get("price_min")), _money(source.get("price_max"))
    if price_type == "quote":
        price_min = price_max = None
    elif price_min is None:
        raise SeedError(f"{entry['slug']}: a {price_type} price needs price_min; use 'quote' until the owner confirms a price.")
    elif price_type != "range":
        price_max = None
    cost = source.get("cost") or {}
    return {
        "currency": source.get("currency", "SAR"),
        "price_type": price_type,
        "price_min": price_min,
        "price_max": price_max,
        "deliverables": clean_list(source.get("deliverables", [])),
        "limitations": clean_list(source.get("limitations", [])),
        "cost": {name: _money(cost.get(name, 0)) for name in COST_FIELDS},
        "notes": source.get("notes", ""),
    }


def offer_matches(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    current = {
        "currency": existing.get("currency"),
        "price_type": existing.get("price_type"),
        "price_min": _money(existing.get("price_min")),
        "price_max": _money(existing.get("price_max")),
        "deliverables": list(existing.get("deliverables") or []),
        "limitations": list(existing.get("limitations") or []),
        "cost": {name: _money((existing.get("cost") or {}).get(name, 0)) for name in COST_FIELDS},
        "notes": existing.get("notes", ""),
    }
    return current == desired


def product_changes(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    return {name: desired[name] for name in UPDATABLE_PRODUCT_FIELDS if existing.get(name) != desired[name]}


@dataclass
class Step:
    slug: str
    action: str
    method: str = ""
    path: str = ""
    body: dict[str, Any] | None = None

    def describe(self) -> str:
        target = f" {self.method} {self.path}" if self.method else ""
        return f"[{self.slug}] {self.action}{target}"


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)

    @property
    def writes(self) -> list[Step]:
        return [step for step in self.steps if step.method]


def match_product(entry: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any] | None:
    keys = {entry["slug"], *(slugify(alias) for alias in entry.get("aliases", []))}
    found = [item for item in products if slugify(item.get("name", "")) in keys]
    exact = [item for item in found if slugify(item.get("name", "")) == entry["slug"]]
    return (exact or found or [None])[0]


def list_products(transport: Transport) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = transport("GET", f"{BASE}/products?limit={PAGE}&offset={offset}", None)
        items.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return items


def seed(entries: list[dict[str, Any]], transport: Transport, *, apply: bool) -> Report:
    """Plan (and with ``apply`` perform) the writes that bring the catalog to ``entries``."""
    report = Report()
    products = list_products(transport)

    def write(step: Step) -> Any:
        report.steps.append(step)
        return transport(step.method, step.path, step.body) if apply else None

    for entry in entries:
        slug = entry["slug"]
        desired_product = product_payload(entry)
        desired_offer = offer_payload(entry)
        existing = match_product(entry, products)

        if existing is None:
            detail = write(Step(slug, "create product", "POST", f"{BASE}/products", desired_product))
            if desired_offer is None:
                continue
            product_id = detail["id"] if detail else "<new product id>"
            write(Step(slug, "add draft offer", "POST", f"{BASE}/products/{product_id}/versions", desired_offer))
            continue

        product_id = existing["id"]
        if existing.get("kind") != desired_product["kind"]:
            report.steps.append(Step(slug, f"kind is {existing.get('kind')}, wanted {desired_product['kind']}: the API cannot change it, left as is"))
        changes = product_changes(existing, desired_product)
        if changes:
            write(Step(slug, f"update product fields {sorted(changes)}", "PATCH", f"{BASE}/products/{product_id}", changes))
        else:
            report.steps.append(Step(slug, "product up to date"))

        if desired_offer is None:
            continue
        offers = transport("GET", f"{BASE}/products/{product_id}", None).get("offers", [])
        draft = next((item for item in offers if item.get("status") == "draft"), None)
        active = next((item for item in offers if item.get("status") == "active"), None)
        if draft is not None:
            if offer_matches(draft, desired_offer):
                report.steps.append(Step(slug, f"draft version {draft.get('version')} up to date"))
            else:
                write(Step(slug, f"update draft version {draft.get('version')}", "PATCH", f"{BASE}/offers/{draft['id']}", desired_offer))
        elif active is not None and offer_matches(active, desired_offer):
            report.steps.append(Step(slug, f"active version {active.get('version')} up to date"))
        else:
            write(Step(slug, "add draft offer", "POST", f"{BASE}/products/{product_id}/versions", desired_offer))
    return report


# -- HTTP --------------------------------------------------------------------------------------


def http_transport(base_url: str, token: str, workspace_id: str | None = None, timeout: float = 30.0) -> Transport:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SeedError("ANUM_API_URL must be an http(s) origin.")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise SeedError("Refusing to send a token over plain HTTP to a non-local host. Use https.")
    origin = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if workspace_id:
        headers["x-workspace-id"] = workspace_id

    def call(method: str, path: str, body: dict[str, Any] | None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 (scheme checked above)
            origin + path, data=data, method=method,
            headers={**headers, **({"Content-Type": "application/json"} if data is not None else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310: scheme checked above
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise SeedError(f"{method} {path} failed with {error.code}: {detail}") from None
        except urllib.error.URLError as error:
            raise SeedError(f"Cannot reach {origin}: {error.reason}") from None
        return json.loads(raw) if raw else None

    return call


# -- output ------------------------------------------------------------------------------------


def print_offline(entries: list[dict[str, Any]]) -> None:
    print("Dry run, offline (no ANUM_API_TOKEN): the payloads below are what would be sent.\n")
    for entry in entries:
        print(f"== {entry['slug']} ({entry.get('doc', '-')})")
        print("POST /products " + json.dumps(product_payload(entry), ensure_ascii=False, indent=2))
        offer = offer_payload(entry)
        print("POST /products/{id}/versions " + (json.dumps(offer, ensure_ascii=False, indent=2) if offer else "(no offer)"))
        print_pending(entry)
        print()


def print_pending(entry: dict[str, Any]) -> None:
    pending = entry.get(PENDING_BLOCK)
    if pending:
        print(f"{PENDING_BLOCK} (NOT SENT, the API has no fields for it yet): " + json.dumps(pending, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the owner's products and offers into the Business catalog.")
    parser.add_argument("--apply", action="store_true", help="write to the tenant (default: dry run)")
    parser.add_argument("--data", type=Path, default=DATA_FILE, help="products file (default: products.json next to this script)")
    args = parser.parse_args(argv)

    try:
        entries = load_entries(args.data)
        token = os.environ.get("ANUM_API_TOKEN", "").strip()
        if not token:
            if args.apply:
                raise SeedError("--apply needs ANUM_API_TOKEN (an owner's bearer token) in the environment.")
            print_offline(entries)
            return 0
        transport = http_transport(os.environ.get("ANUM_API_URL", "http://localhost:8000"), token, os.environ.get("ANUM_WORKSPACE_ID") or None)
        report = seed(entries, transport, apply=args.apply)
    except SeedError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("Applied:" if args.apply else "Dry run (use --apply to write):")
    for step in report.steps:
        print("  " + step.describe())
    for entry in entries:
        print_pending(entry)
    print(f"{len(report.writes)} write(s) {'made' if args.apply else 'planned'}. Offers stay drafts: confirm the price and activate them in Business > Catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
