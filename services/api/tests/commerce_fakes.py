"""Fake Amazon SP-API, noon and ANUM 360 store servers for the commerce tests.

Every value is synthetic: plainly fake ids, lowercase example credentials and no real
endpoint is ever contacted (``httpx.MockTransport``). The ANUM 360 shapes follow the
store's admin API (``/api/admin/inventory``, ``/api/admin/orders``,
``/api/returns/admin/all``) with made-up products and orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx

SA = "A17E79C6D8DWNP"
AE = "A2VIGQ35RCS4UG"
SELLER = "AEXAMPLESELLER1"
NOW = datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeChannels:
    """One handler for every host the commerce clients call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.lwa_calls = 0
        self.lwa_refuse = False
        self.throttle: set[str] = set()
        self.patches: list[dict[str, Any]] = []
        self.noon_patches: list[dict[str, Any]] = []
        self.store_adjustments: list[dict[str, Any]] = []
        self.store_puts: list[dict[str, Any]] = []
        self.store_two_factor = False
        self.store_price_forbidden = True
        self.rest_puts: list[dict[str, Any]] = []
        self.supabase_queries: list[dict[str, Any]] = []
        self.store_stock = {"prod-bag-1": 2, "prod-scarf-2": 9}
        self.amazon_orders = [
            {
                "AmazonOrderId": "402-0000001-0000001",
                "PurchaseDate": iso(NOW - timedelta(hours=3)),
                "LastUpdateDate": iso(NOW - timedelta(hours=1)),
                "OrderStatus": "Unshipped",
                "FulfillmentChannel": "MFN",
                "MarketplaceId": SA,
                "OrderTotal": {"CurrencyCode": "SAR", "Amount": "900.00"},
                "NumberOfItemsUnshipped": 2,
                "NumberOfItemsShipped": 0,
                "LatestShipDate": iso(NOW - timedelta(minutes=30)),
                "ShippingAddress": {"City": "Riyadh", "CountryCode": "SA"},
            },
            {
                "AmazonOrderId": "403-0000002-0000002",
                "PurchaseDate": iso(NOW - timedelta(hours=2)),
                "LastUpdateDate": iso(NOW - timedelta(hours=1)),
                "OrderStatus": "Shipped",
                "FulfillmentChannel": "AFN",
                "MarketplaceId": AE,
                "OrderTotal": {"CurrencyCode": "AED", "Amount": "410.00"},
                "NumberOfItemsShipped": 1,
                "NumberOfItemsUnshipped": 0,
                "ShippingAddress": {"City": "Dubai", "CountryCode": "AE"},
            },
        ]

    # -- dispatch -------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        query = {k: v[0] for k, v in parse_qs(url.query.decode()).items()}
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, f"{url.host}{url.path}", {"query": query, "body": body, "headers": dict(request.headers)}))
        for fragment in self.throttle:
            if fragment in url.path:
                return httpx.Response(429, headers={"retry-after": "120"}, json={"errors": [{"code": "QuotaExceeded", "message": "slow down"}]})
        if url.host == "api.amazon.com":
            return self.lwa(body)
        if url.host == "sellingpartnerapi-eu.amazon.com":
            if request.headers.get("x-amz-access-token") not in ("lwa-access-example", "rdt-example"):
                return httpx.Response(403, json={"errors": [{"code": "Unauthorized", "message": "Access to requested resource is denied."}]})
            return self.amazon(request.method, url.path, query, body)
        if url.host == "api.noon.partners":
            if request.headers.get("authorization") != "Bearer noon-key-example":
                return httpx.Response(401, json={"message": "invalid api key"})
            return self.noon(request.method, url.path, query, body)
        if url.host == "store.example.test":
            return self.anum360(request, url.path, query, body)
        if url.host == "shop.example.test":
            return self.rest(request.method, url.path, query, body)
        if url.host == "project.supabase.example":
            self.supabase_queries.append({"path": url.path, "query": query})
            return self.supabase(url.path, query)
        return httpx.Response(404, json={"message": f"no fake for {url.host}"})

    # -- Amazon ----------------------------------------------------------------------------

    def lwa(self, form: dict[str, str]) -> httpx.Response:
        self.lwa_calls += 1
        if self.lwa_refuse or form.get("refresh_token") != "atzr-example":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "The request has an invalid grant parameter"})
        return httpx.Response(200, json={"access_token": "lwa-access-example", "token_type": "bearer", "expires_in": 3600})

    def amazon(self, method: str, path: str, query: dict[str, str], body: Any) -> httpx.Response:
        if path == "/sellers/v1/marketplaceParticipations":
            return httpx.Response(200, json={"payload": [
                {"marketplace": {"id": SA, "name": "Amazon.sa"}, "participation": {"isParticipating": True}},
                {"marketplace": {"id": AE, "name": "Amazon.ae"}, "participation": {"isParticipating": True}},
            ]})
        if path == f"/listings/2021-08-01/items/{SELLER}" and method == "GET":
            marketplace = query["marketplaceIds"]
            if marketplace == SA:
                if not query.get("pageToken"):
                    return httpx.Response(200, json={
                        "items": [self._listing("MX-2055516", "B0EXAMPLE1", SA, "Michelin Primacy 4 205/55R16 91V tyre DOT 2324", "450.00", 3)],
                        "pagination": {"nextToken": "page-2"},
                    })
                return httpx.Response(200, json={"items": [self._listing("BR-2256017", "B0EXAMPLE2", SA, "Bridgestone Turanza 225/60R17 99H", "520.00", 40)]})
            return httpx.Response(200, json={"items": [
                {**self._listing("MX-2055516", "B0EXAMPLE1", AE, "Michelin Primacy 4 205/55R16 91V tyre", "420.00", None), "fulfillmentAvailability": [{"fulfillmentChannelCode": "AMAZON_EU"}]},
            ]})
        if path == "/fba/inventory/v1/summaries":
            if query["granularityId"] == AE:
                return httpx.Response(200, json={"payload": {"inventorySummaries": [{"sellerSku": "MX-2055516", "asin": "B0EXAMPLE1", "totalQuantity": 12, "inventoryDetails": {"fulfillableQuantity": 10}}]}})
            return httpx.Response(200, json={"payload": {"inventorySummaries": []}})
        if path == "/orders/v0/orders":
            return httpx.Response(200, json={"payload": {"Orders": self.amazon_orders}})
        if path.startswith("/orders/v0/orders/") and path.endswith("/orderItems"):
            order_id = path.split("/")[4]
            if order_id == "402-0000001-0000001":
                items = [{"ASIN": "B0EXAMPLE1", "SellerSKU": "MX-2055516", "OrderItemId": "item-1", "Title": "Michelin Primacy 4 205/55R16", "QuantityOrdered": 2, "ItemPrice": {"CurrencyCode": "SAR", "Amount": "900.00"}}]
            else:
                items = [{"ASIN": "B0EXAMPLE1", "SellerSKU": "MX-2055516", "OrderItemId": "item-2", "Title": "Michelin Primacy 4 205/55R16", "QuantityOrdered": 1, "ItemPrice": {"CurrencyCode": "AED", "Amount": "410.00"}}]
            return httpx.Response(200, json={"payload": {"OrderItems": items}})
        if path == "/tokens/2021-03-01/restrictedDataToken":
            return httpx.Response(200, json={"restrictedDataToken": "rdt-example", "expiresIn": 3600})
        if path.startswith("/orders/v0/orders/"):
            return httpx.Response(200, json={"payload": {
                "AmazonOrderId": path.split("/")[-1],
                "BuyerInfo": {"BuyerEmail": "buyer-example@marketplace.amazon.sa"},
                "ShippingAddress": {"Name": "Example Buyer", "AddressLine1": "1 Example Street", "City": "Riyadh", "CountryCode": "SA", "Phone": "+966500000000"},
            }})
        if path == "/finances/v0/financialEvents":
            return httpx.Response(200, json={"payload": {"FinancialEvents": {
                "ShipmentEventList": [{
                    "AmazonOrderId": "403-0000002-0000002",
                    "PostedDate": iso(NOW - timedelta(hours=1)),
                    "ShipmentItemList": [{"SellerSKU": "MX-2055516", "ItemFeeList": [
                        {"FeeType": "Commission", "FeeAmount": {"CurrencyCode": "AED", "CurrencyAmount": -61.5}},
                        {"FeeType": "FBAPerUnitFulfillmentFee", "FeeAmount": {"CurrencyCode": "AED", "CurrencyAmount": -18.0}},
                    ]}],
                }],
                "RefundEventList": [{
                    "AmazonOrderId": "401-0000009-0000009",
                    "MarketplaceName": "Amazon.sa",
                    "PostedDate": iso(NOW - timedelta(hours=5)),
                    "ShipmentItemAdjustmentList": [{"SellerSKU": "MX-2055516", "QuantityShipped": 1, "ItemChargeAdjustmentList": [
                        {"ChargeType": "Principal", "ChargeAmount": {"CurrencyCode": "SAR", "CurrencyAmount": -450.0}},
                    ]}],
                }],
            }}})
        if path == "/finances/v0/financialEventGroups":
            return httpx.Response(200, json={"payload": {"FinancialEventGroupList": [{
                "FinancialEventGroupId": "group-example-1",
                "ProcessingStatus": "Closed",
                "FundTransferStatus": "Succeeded",
                "OriginalTotal": {"CurrencyCode": "SAR", "CurrencyAmount": 12500.0},
                "FundTransferDate": iso(NOW - timedelta(days=2)),
            }]}})
        if path.startswith("/products/pricing/v0/items/"):
            asin = path.split("/")[5]
            mine_wins = asin != "B0EXAMPLE1"
            return httpx.Response(200, json={"payload": {
                "Summary": {"BuyBoxPrices": [{"condition": "New", "LandedPrice": {"CurrencyCode": "SAR", "Amount": 430.0}}]},
                "Offers": [
                    {"MyOffer": True, "IsBuyBoxWinner": mine_wins, "ListingPrice": {"Amount": 450.0}},
                    {"MyOffer": False, "IsBuyBoxWinner": not mine_wins, "ListingPrice": {"Amount": 430.0}},
                ],
            }})
        if path.startswith(f"/listings/2021-08-01/items/{SELLER}/") and method == "PATCH":
            self.patches.append({"sku": path.split("/")[-1], "query": query, "body": body})
            return httpx.Response(200, json={"sku": path.split("/")[-1], "status": "ACCEPTED", "submissionId": "submission-example", "issues": []})
        return httpx.Response(404, json={"errors": [{"code": "NotFound", "message": path}]})

    @staticmethod
    def _listing(sku: str, asin: str, marketplace: str, title: str, price: str, quantity: int | None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "sku": sku,
            "summaries": [{"marketplaceId": marketplace, "asin": asin, "productType": "TIRE", "itemName": title, "status": ["BUYABLE", "DISCOVERABLE"]}],
            "offers": [{"marketplaceId": marketplace, "offerType": "B2C", "price": {"currencyCode": "SAR" if marketplace == SA else "AED", "amount": price}}],
        }
        if quantity is not None:
            item["fulfillmentAvailability"] = [{"fulfillmentChannelCode": "DEFAULT", "quantity": quantity}]
        return item

    # -- noon ---------------------------------------------------------------------------------

    def noon(self, method: str, path: str, query: dict[str, str], body: Any) -> httpx.Response:
        if path == "/partner/v1/whoami":
            return httpx.Response(200, json={"partner_name": "Example Tyres"})
        if path == "/offer/v1/offers":
            country = query.get("country_code")
            return httpx.Response(200, json={"offers": [{
                "partner_sku": "MX-2055516", "noon_sku": "ZEXAMPLE1", "title": "Michelin Primacy 4 205/55R16", "title_ar": "ميشلان برايمسي 4 205/55R16",
                "price": "460", "sale_price": "440", "currency": "SAR" if country == "SA" else "AED", "stock": 0, "is_active": True,
            }]})
        if path == "/order/v1/orders":
            return httpx.Response(200, json={"orders": [{
                "order_nr": "NEXAMPLE0001", "status": "Processing", "created_at": iso(NOW - timedelta(hours=4)), "updated_at": iso(NOW - timedelta(hours=1)),
                "currency": "SAR", "country_code": "SA", "city": "Jeddah", "total": "880",
                "items": [{"partner_sku": "MX-2055516", "title": "Michelin Primacy 4", "quantity": 2, "price": "440"}],
            }]})
        if path == "/return/v1/returns":
            return httpx.Response(200, json={"returns": [{
                "return_nr": "REXAMPLE1", "order_nr": "NEXAMPLE0000", "partner_sku": "MX-2055516", "quantity": 1, "reason": "Damaged", "status": "Received",
                "created_at": iso(NOW - timedelta(days=1)),
            }]})
        if path.startswith("/offer/v1/offers/") and method == "PATCH":
            self.noon_patches.append({"sku": path.split("/")[-1], "body": body})
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"message": path})

    # -- the owner's ANUM 360 store --------------------------------------------------------------

    def anum360(self, request: httpx.Request, path: str, query: dict[str, str], body: Any) -> httpx.Response:
        if path == "/api/admin/login":
            if body.get("email") != "anum-sync@store.example.test" or body.get("password") != "example-password":
                return httpx.Response(401, json={"detail": "Invalid credentials"})
            if self.store_two_factor:
                return httpx.Response(401, headers={"X-2FA-Required": "true"}, json={"detail": "Two-factor code required"})
            return httpx.Response(200, json={"message": "Login successful", "token": "store-session-example", "expires_in": 604800, "twofa_required": False, "twofa_setup_required": False})
        if request.headers.get("authorization") != "Bearer store-session-example":
            return httpx.Response(401, json={"detail": "Admin authentication required"})
        if path == "/api/admin/inventory":
            return httpx.Response(200, json={
                "products": [
                    {"id": "prod-bag-1", "name": {"en": "Leather Tote Example", "ar": "حقيبة جلدية مثال"}, "sku": "BAG-TOTE-01", "stock": self.store_stock["prod-bag-1"],
                     "pricing": {"retail": 1200.0, "sale": 999.0}, "brand": "Example Atelier"},
                    {"id": "prod-scarf-2", "name": {"en": "Silk Scarf Example", "ar": "وشاح حرير مثال"}, "sku": "", "stock": self.store_stock["prod-scarf-2"],
                     "pricing": {"retail": 350.0}, "brand": "Example Atelier"},
                ],
                "products_truncated": False, "total_products": 2, "out_of_stock": 0, "low_stock": 1, "healthy_stock": 1,
            })
        if path == "/api/admin/orders":
            skip = int(query.get("skip", 0))
            orders = [
                {"id": "order-uuid-1", "order_number": "ANM-20261011-0001", "order_status": "processing", "payment_status": "paid", "total": 1049.0,
                 "created_at": iso(NOW - timedelta(days=3)), "updated_at": iso(NOW - timedelta(hours=2)),
                 "customer_name": "Masked", "customer_email": "***", "customer_phone": "***", "contact_masked": True,
                 "items": [{"product_id": "prod-bag-1", "name": {"en": "Leather Tote Example"}, "quantity": 1, "price": 999.0, "total": 999.0}],
                 "delivery_info": {"zone": "Abu Dhabi", "fee": 50.0, "address": {"city": "Abu Dhabi", "country": "UAE"}}},
                {"id": "order-uuid-2", "order_number": "ANM-20261010-0002", "order_status": "delivered", "payment_status": "paid", "total": 350.0,
                 "created_at": iso(NOW - timedelta(hours=6)), "updated_at": iso(NOW - timedelta(hours=1)),
                 "items": [{"product_id": "prod-scarf-2", "name": {"en": "Silk Scarf Example"}, "quantity": 1, "price": 350.0, "total": 350.0}],
                 "delivery_info": {"zone": "Dubai", "address": {"city": "Dubai", "country": "UAE"}}},
                {"id": "order-uuid-old", "order_number": "ANM-20250101-0003", "order_status": "completed", "total": 10.0,
                 "created_at": iso(NOW - timedelta(days=400)), "items": [], "delivery_info": {}},
            ]
            return httpx.Response(200, json=orders[skip:])
        if path == "/api/returns/admin/all":
            return httpx.Response(200, json={"returns": [{
                "id": "return-uuid-1", "return_number": "RMA-20261011-0001", "order_id": "order-uuid-2", "order_number": "ANM-20261010-0002",
                "items": [{"product_id": "prod-scarf-2", "quantity": 1}], "reason": "changed_mind", "status": "pending", "refund_amount": 350.0,
                "created_at": iso(NOW - timedelta(hours=1)),
            }], "total": 1, "limit": 100, "skip": 0})
        if path == "/api/admin/inventory/adjust" and request.method == "POST":
            self.store_adjustments.append(query)
            self.store_stock[query["product_id"]] += int(query["adjustment"])
            return httpx.Response(200, json={"product_id": query["product_id"], "new_stock": self.store_stock[query["product_id"]]})
        if path.startswith("/api/products/") and request.method == "PUT":
            self.store_puts.append(body)
            if self.store_price_forbidden:
                return httpx.Response(403, json={"detail": {"code": "2fa_required", "message": "Two-step verification is required"}})
            return httpx.Response(200, json={"message": "Product updated successfully"})
        return httpx.Response(404, json={"detail": "Not Found"})

    # -- a store on ANUM's own-store REST contract ---------------------------------------------------

    def rest(self, method: str, path: str, query: dict[str, str], body: Any) -> httpx.Response:
        if path == "/products" and method == "GET":
            return httpx.Response(200, json={"products": [{"sku": "WEB-1", "id": "1", "title": "Pirelli P Zero 245/40ZR18 97Y", "price": 780, "currency": "SAR", "quantity": 1}]})
        if path == "/orders":
            return httpx.Response(200, json={"orders": [{"id": "W-1001", "status": "paid", "created_at": iso(NOW - timedelta(hours=1)), "total": 1560, "currency": "SAR",
                                                         "city": "Dammam", "country": "SA", "items": [{"sku": "WEB-1", "title": "Pirelli P Zero", "quantity": 2, "unit_price": 780}]}]})
        if path == "/returns":
            return httpx.Response(200, json={"returns": []})
        if path.startswith("/products/") and method == "PUT":
            self.rest_puts.append({"path": path, "body": body})
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"message": path})

    # -- Supabase (PostgREST) ----------------------------------------------------------------------

    def supabase(self, path: str, query: dict[str, str]) -> httpx.Response:
        if path == "/rest/v1/items":
            return httpx.Response(200, json=[{"code": "SB-1", "name": "Hankook Ventus 235/45R18", "price_sar": 610, "qty": 7}])
        if path == "/rest/v1/orders":
            return httpx.Response(200, json=[{"id": 77, "status": "completed", "created_at": iso(NOW - timedelta(hours=2)), "updated_at": iso(NOW - timedelta(hours=1)), "total": 610, "currency": "SAR"}])
        if path == "/rest/v1/order_items":
            return httpx.Response(200, json=[{"order_id": 77, "sku": "SB-1", "title": "Hankook Ventus", "quantity": 1, "unit_price": 610}])
        if path == "/rest/v1/returns":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": path})
