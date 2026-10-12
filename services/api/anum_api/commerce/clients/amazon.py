"""Amazon Selling Partner API client (docs/commerce.md#amazon).

Authorization: Login with Amazon (LWA). The owner's app (client id and secret) and the
seller's refresh token are exchanged at ``https://api.amazon.com/auth/o2/token`` for an
access token (one hour), sent as ``x-amz-access-token``; SP-API no longer needs AWS
signatures. Amazon.sa (``A17E79C6D8DWNP``) and Amazon.ae (``A2VIGQ35RCS4UG``) are served
by the Europe endpoint.

Buyer details are restricted data: they are read only when the connector's
``fetch_buyer_details`` setting is on, only for orders the seller ships itself and that
are still unshipped, each through a Restricted Data Token
(``POST /tokens/2021-03-01/restrictedDataToken``) scoped to that one order.

Rates and bursts are the SP-API defaults per operation (``RATES``); the shared layer in
``commerce.http`` waits for a token, retries 429s and adopts ``x-amzn-RateLimit-Limit``.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from ..config import commerce_config
from ..http import ChannelAuthError, ChannelError, request_json
from ..models import AMAZON_MARKETPLACES, OrderStatus
from .base import (
    PageResult,
    RemoteBuyer,
    RemoteCompetition,
    RemoteFee,
    RemoteLine,
    RemoteListing,
    RemoteOrder,
    RemoteReturn,
    integer,
    iso,
    number,
    parse_time,
    text,
)

# operation -> (requests per second, burst)
RATES: dict[str, tuple[float, float]] = {
    "lwa": (1.0, 5),
    "getMarketplaceParticipations": (0.016, 15),
    "getOrders": (0.0167, 20),
    "getOrderItems": (0.5, 30),
    "getOrder": (0.5, 30),
    "createRestrictedDataToken": (1.0, 10),
    "searchListingsItems": (5.0, 5),
    "patchListingsItem": (5.0, 5),
    "getInventorySummaries": (2.0, 2),
    "getItemOffers": (0.5, 1),
    "listFinancialEvents": (0.5, 30),
    "listFinancialEventGroups": (0.5, 30),
}

STATUS = {
    "Pending": OrderStatus.PENDING,
    "PendingAvailability": OrderStatus.PENDING,
    "Unshipped": OrderStatus.UNSHIPPED,
    "PartiallyShipped": OrderStatus.PARTIALLY_SHIPPED,
    "Shipped": OrderStatus.SHIPPED,
    "InvoiceUnconfirmed": OrderStatus.SHIPPED,
    "Canceled": OrderStatus.CANCELLED,
    "Unfulfillable": OrderStatus.CANCELLED,
}

# connector key -> (access token, monotonic expiry)
_access_tokens: dict[str, tuple[str, float]] = {}


def reset_tokens() -> None:
    _access_tokens.clear()


class AmazonClient:
    channel_name = "Amazon"
    capabilities = frozenset(
        {"listings", "orders", "finances", "fba_inventory", "competition", "update_price", "update_stock", "buyer_details"}
    )

    def __init__(
        self,
        connector_key: str,
        credentials: dict[str, str],
        settings: dict[str, Any],
        marketplaces: list[str],
    ) -> None:
        self.key = connector_key
        self.client_id = credentials.get("lwa_client_id", "")
        self.client_secret = credentials.get("lwa_client_secret", "")
        self.refresh_token = credentials.get("refresh_token", "")
        self.seller_id = str(settings.get("seller_id") or "")
        self.fetch_buyer_details = bool(settings.get("fetch_buyer_details", False))
        self.marketplaces = [m for m in marketplaces if m in AMAZON_MARKETPLACES] or list(AMAZON_MARKETPLACES)
        self.endpoint = commerce_config.amazon_eu_endpoint.rstrip("/")

    # -- plumbing ------------------------------------------------------------------

    async def _token(self) -> str:
        cached = _access_tokens.get(self.key)
        if cached and cached[1] > time.monotonic() + 60:
            return cached[0]
        rate, burst = RATES["lwa"]
        try:
            body = await request_json(
                "POST",
                commerce_config.amazon_lwa_url,
                channel_name="Login with Amazon",
                operation="refresh token",
                connector_key=self.key,
                rate=rate,
                burst=burst,
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
        except ChannelError as exc:
            if exc.status in (400, 401, 403) or isinstance(exc, ChannelAuthError):
                raise ChannelAuthError(
                    "Login with Amazon refused the app credentials or refresh token. Authorize the app again in "
                    "Seller Central and paste the new refresh token.",
                    status=exc.status,
                    code=exc.code or "invalid_grant",
                ) from None
            raise
        token = str(body.get("access_token") or "")
        if not token:
            raise ChannelAuthError("Login with Amazon returned no access token.", code="no_token")
        _access_tokens[self.key] = (token, time.monotonic() + float(body.get("expires_in") or 3600))
        return token

    async def _call(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        token: str | None = None,
    ) -> Any:
        rate, burst = RATES[operation]
        return await request_json(
            method,
            f"{self.endpoint}{path}",
            channel_name="Amazon",
            operation=operation,
            connector_key=self.key,
            rate=rate,
            burst=burst,
            headers={"x-amz-access-token": token or await self._token(), "accept": "application/json"},
            params=params,
            json_body=json_body,
        )

    # -- health --------------------------------------------------------------------

    async def check(self) -> str:
        body = await self._call("GET", "/sellers/v1/marketplaceParticipations", "getMarketplaceParticipations")
        joined = {
            str((row.get("marketplace") or {}).get("id"))
            for row in body.get("payload") or []
            if (row.get("participation") or {}).get("isParticipating")
        }
        missing = [m for m in self.marketplaces if m not in joined]
        if missing:
            names = ", ".join(AMAZON_MARKETPLACES[m]["name"] for m in missing)
            raise ChannelError(f"This seller account does not sell on {names}. Remove it from the connection or register there.", code="not_participating")
        return "Connected to " + ", ".join(AMAZON_MARKETPLACES[m]["name"] for m in self.marketplaces)

    # -- listings and inventory ----------------------------------------------------------

    async def fetch_listings(self, marketplace_id: str, page_token: str | None) -> PageResult:
        body = await self._call(
            "GET",
            f"/listings/2021-08-01/items/{quote(self.seller_id, safe='')}",
            "searchListingsItems",
            params={
                "marketplaceIds": marketplace_id,
                "includedData": "summaries,offers,fulfillmentAvailability",
                "pageSize": 20,
                "pageToken": page_token,
            },
        )
        currency = AMAZON_MARKETPLACES.get(marketplace_id, {}).get("currency")
        items: list[RemoteListing] = []
        for item in body.get("items") or []:
            sku = str(item.get("sku") or "").strip()
            if not sku:
                continue
            summary = next((s for s in item.get("summaries") or [] if s.get("marketplaceId") == marketplace_id), None) or {}
            offer = next(
                (o for o in item.get("offers") or [] if o.get("marketplaceId") == marketplace_id and o.get("offerType", "B2C") == "B2C"),
                None,
            ) or {}
            price = offer.get("price") or {}
            availability = next(iter(item.get("fulfillmentAvailability") or []), None) or {}
            channel_code = str(availability.get("fulfillmentChannelCode") or "DEFAULT")
            statuses = [str(s) for s in summary.get("status") or []]
            quantity = integer(availability.get("quantity"))
            items.append(
                RemoteListing(
                    marketplace_id=marketplace_id,
                    sku=sku,
                    external_id=summary.get("asin"),
                    title=text(summary.get("itemName")),
                    product_type=summary.get("productType"),
                    price=number(price.get("amount") if isinstance(price, dict) else price),
                    currency=(price.get("currencyCode") or price.get("currency") or currency) if isinstance(price, dict) else currency,
                    quantity=quantity,
                    status="active" if "BUYABLE" in statuses else "inactive",
                    fulfillment="merchant" if channel_code == "DEFAULT" else "amazon",
                )
            )
        return PageResult(items=items, next_token=(body.get("pagination") or {}).get("nextToken"))

    async def fetch_inventory(self, marketplace_id: str, page_token: str | None) -> PageResult:
        """FBA stock: ``(sku, fulfillable quantity)`` per item, for listings Amazon fulfils."""
        body = await self._call(
            "GET",
            "/fba/inventory/v1/summaries",
            "getInventorySummaries",
            params={
                "details": "true",
                "granularityType": "Marketplace",
                "granularityId": marketplace_id,
                "marketplaceIds": marketplace_id,
                "nextToken": page_token,
            },
        )
        payload = body.get("payload") or {}
        rows = []
        for row in payload.get("inventorySummaries") or []:
            details = row.get("inventoryDetails") or {}
            quantity = integer(details.get("fulfillableQuantity"))
            if quantity is None:
                quantity = integer(row.get("totalQuantity")) or 0
            if row.get("sellerSku"):
                rows.append((str(row["sellerSku"]), quantity))
        return PageResult(items=rows, next_token=(body.get("pagination") or {}).get("nextToken"))

    # -- orders ----------------------------------------------------------------------

    async def fetch_orders(self, since: datetime, page_token: str | None) -> PageResult:
        params: dict[str, Any] = {"MarketplaceIds": ",".join(self.marketplaces), "MaxResultsPerPage": 100}
        if page_token:
            params["NextToken"] = page_token
        else:
            params["LastUpdatedAfter"] = iso(since)
        body = await self._call("GET", "/orders/v0/orders", "getOrders", params=params)
        payload = body.get("payload") or {}
        orders: list[RemoteOrder] = []
        for row in payload.get("Orders") or []:
            order_id = str(row.get("AmazonOrderId") or "")
            if not order_id:
                continue
            marketplace = str(row.get("MarketplaceId") or self.marketplaces[0])
            status = STATUS.get(str(row.get("OrderStatus")), OrderStatus.PENDING)
            total = row.get("OrderTotal") or {}
            address = row.get("ShippingAddress") or {}
            fulfillment = "amazon" if row.get("FulfillmentChannel") == "AFN" else "merchant"
            lines = await self._lines(order_id) if status != OrderStatus.CANCELLED or row.get("NumberOfItemsShipped") else []
            buyer = None
            if self.fetch_buyer_details and fulfillment == "merchant" and status in (OrderStatus.UNSHIPPED, OrderStatus.PARTIALLY_SHIPPED):
                buyer = await self._buyer(order_id)
            orders.append(
                RemoteOrder(
                    marketplace_id=marketplace,
                    external_id=order_id,
                    status=status,
                    purchased_at=parse_time(row.get("PurchaseDate")) or since,
                    updated_at=parse_time(row.get("LastUpdateDate")),
                    ship_by=parse_time(row.get("LatestShipDate")),
                    total_amount=number(total.get("Amount")) or 0.0,
                    currency=total.get("CurrencyCode") or AMAZON_MARKETPLACES.get(marketplace, {}).get("currency"),
                    item_count=(integer(row.get("NumberOfItemsShipped")) or 0) + (integer(row.get("NumberOfItemsUnshipped")) or 0),
                    fulfillment=fulfillment,
                    ship_city=address.get("City"),
                    ship_country=address.get("CountryCode"),
                    lines=lines,
                    buyer=buyer,
                )
            )
        return PageResult(items=orders, next_token=payload.get("NextToken"))

    async def _lines(self, order_id: str) -> list[RemoteLine]:
        lines: list[RemoteLine] = []
        token: str | None = None
        for _ in range(10):
            body = await self._call(
                "GET",
                f"/orders/v0/orders/{quote(order_id, safe='')}/orderItems",
                "getOrderItems",
                params={"NextToken": token} if token else None,
            )
            payload = body.get("payload") or {}
            for item in payload.get("OrderItems") or []:
                quantity = integer(item.get("QuantityOrdered")) or 0
                total = number(item.get("ItemPrice"))
                lines.append(
                    RemoteLine(
                        sku=str(item.get("SellerSKU") or item.get("ASIN") or "unknown"),
                        external_line_id=item.get("OrderItemId"),
                        title=text(item.get("Title")),
                        quantity=quantity,
                        total=total,
                        unit_price=round(total / quantity, 2) if total is not None and quantity else None,
                    )
                )
            token = payload.get("NextToken")
            if not token:
                break
        return lines

    async def _buyer(self, order_id: str) -> RemoteBuyer | None:
        path = f"/orders/v0/orders/{order_id}"
        rdt = await self._call(
            "POST",
            "/tokens/2021-03-01/restrictedDataToken",
            "createRestrictedDataToken",
            json_body={"restrictedResources": [{"method": "GET", "path": path, "dataElements": ["buyerInfo", "shippingAddress"]}]},
        )
        token = rdt.get("restrictedDataToken")
        if not token:
            return None
        body = await self._call("GET", f"/orders/v0/orders/{quote(order_id, safe='')}", "getOrder", token=str(token))
        payload = body.get("payload") or {}
        info = payload.get("BuyerInfo") or {}
        address = payload.get("ShippingAddress") or {}
        lines = [address.get(key) for key in ("AddressLine1", "AddressLine2", "AddressLine3", "City", "StateOrRegion", "PostalCode", "CountryCode")]
        return RemoteBuyer(
            reference=str(info.get("BuyerEmail") or order_id),
            name=address.get("Name") or info.get("BuyerName"),
            email=info.get("BuyerEmail"),
            phone=address.get("Phone"),
            address=", ".join(str(part) for part in lines if part) or None,
        )

    # -- money -------------------------------------------------------------------------

    async def fetch_fees(self, since: datetime, page_token: str | None) -> PageResult:
        """Financial events (fees and refunds), then financial event groups (settlements).

        The page token says which phase continues: ``events:<token>`` or ``groups:<token>``.
        """
        if page_token and page_token.startswith("groups:"):
            return await self._groups(since, page_token[len("groups:"):] or None)
        token = page_token[len("events:"):] if page_token and page_token.startswith("events:") else None
        params: dict[str, Any] = {"MaxResultsPerPage": 100}
        if token:
            params["NextToken"] = token
        else:
            params["PostedAfter"] = iso(since)
        body = await self._call("GET", "/finances/v0/financialEvents", "listFinancialEvents", params=params)
        payload = body.get("payload") or {}
        events = payload.get("FinancialEvents") or {}
        items: list[Any] = []
        for shipment in events.get("ShipmentEventList") or []:
            order_id = str(shipment.get("AmazonOrderId") or "")
            posted = parse_time(shipment.get("PostedDate")) or since
            for item in shipment.get("ShipmentItemList") or []:
                sku = str(item.get("SellerSKU") or "")
                for fee in item.get("ItemFeeList") or []:
                    amount = number(fee.get("FeeAmount"))
                    if amount is None:
                        continue
                    fee_type = str(fee.get("FeeType") or "Fee")
                    items.append(
                        RemoteFee(
                            external_id=f"fee:{order_id}:{sku}:{fee_type}:{iso(posted)}",
                            order_external_id=order_id or None,
                            kind="fee",
                            description=fee_type,
                            amount=amount,
                            currency=(fee.get("FeeAmount") or {}).get("CurrencyCode"),
                            posted_at=posted,
                        )
                    )
        for refund in events.get("RefundEventList") or []:
            order_id = str(refund.get("AmazonOrderId") or "")
            posted = parse_time(refund.get("PostedDate")) or since
            marketplace = next((m for m, info in AMAZON_MARKETPLACES.items() if info["name"] == refund.get("MarketplaceName")), None)
            for item in refund.get("ShipmentItemAdjustmentList") or []:
                sku = str(item.get("SellerSKU") or "")
                charges = [number(c.get("ChargeAmount")) or 0.0 for c in item.get("ItemChargeAdjustmentList") or []]
                currency = next(
                    ((c.get("ChargeAmount") or {}).get("CurrencyCode") for c in item.get("ItemChargeAdjustmentList") or []), None
                )
                refunded = round(-sum(charges), 2)
                items.append(
                    RemoteReturn(
                        marketplace_id=marketplace,
                        external_id=f"refund:{order_id}:{sku}:{iso(posted)}",
                        order_external_id=order_id,
                        sku=sku or None,
                        quantity=abs(integer(item.get("QuantityShipped")) or 1),
                        status="refunded",
                        amount=refunded,
                        currency=currency,
                        returned_at=posted,
                    )
                )
                items.append(
                    RemoteFee(
                        external_id=f"refund:{order_id}:{sku}:{iso(posted)}",
                        order_external_id=order_id or None,
                        kind="refund",
                        description="Refund",
                        amount=round(sum(charges), 2),
                        currency=currency,
                        posted_at=posted,
                    )
                )
        next_token = payload.get("NextToken")
        return PageResult(items=items, next_token=f"events:{next_token}" if next_token else "groups:")

    async def _groups(self, since: datetime, token: str | None) -> PageResult:
        params: dict[str, Any] = {"MaxResultsPerPage": 100}
        if token:
            params["NextToken"] = token
        else:
            params["FinancialEventGroupStartedAfter"] = iso(since)
        body = await self._call("GET", "/finances/v0/financialEventGroups", "listFinancialEventGroups", params=params)
        payload = body.get("payload") or {}
        items: list[Any] = []
        for group in payload.get("FinancialEventGroupList") or []:
            total = group.get("OriginalTotal") or {}
            amount = number(total)
            if amount is None or not group.get("FinancialEventGroupId"):
                continue
            status = group.get("FundTransferStatus") or group.get("ProcessingStatus") or ""
            items.append(
                RemoteFee(
                    external_id=f"settlement:{group['FinancialEventGroupId']}",
                    kind="settlement",
                    description=f"Settlement {status}".strip(),
                    amount=amount,
                    currency=total.get("CurrencyCode"),
                    posted_at=parse_time(group.get("FundTransferDate")) or parse_time(group.get("FinancialEventGroupStart")) or since,
                )
            )
        next_token = payload.get("NextToken")
        return PageResult(items=items, next_token=f"groups:{next_token}" if next_token else None)

    async def fetch_returns(self, since: datetime, page_token: str | None) -> PageResult:
        # Amazon refunds arrive with the financial events (fetch_fees).
        return PageResult(items=[])

    # -- competition -----------------------------------------------------------------

    async def fetch_competition(self, marketplace_id: str, external_id: str) -> RemoteCompetition | None:
        body = await self._call(
            "GET",
            f"/products/pricing/v0/items/{quote(external_id, safe='')}/offers",
            "getItemOffers",
            params={"MarketplaceId": marketplace_id, "ItemCondition": "New"},
        )
        payload = body.get("payload") or {}
        summary = payload.get("Summary") or {}
        buy_box = next(iter(summary.get("BuyBoxPrices") or []), None) or {}
        offers = payload.get("Offers") or []
        mine = [o for o in offers if o.get("MyOffer")]
        others = [number(o.get("ListingPrice")) for o in offers if not o.get("MyOffer")]
        others = [price for price in others if price is not None]
        return RemoteCompetition(
            buy_box_price=number(buy_box.get("LandedPrice") or buy_box.get("ListingPrice")),
            buy_box_winner=any(o.get("IsBuyBoxWinner") for o in mine) if offers else None,
            lowest_competitor_price=min(others) if others else None,
        )

    # -- changes (executed only by the approved tools) ------------------------------------

    async def _patch(self, marketplace_id: str, sku: str, product_type: str | None, patch: dict[str, Any]) -> str:
        body = await self._call(
            "PATCH",
            f"/listings/2021-08-01/items/{quote(self.seller_id, safe='')}/{quote(sku, safe='')}",
            "patchListingsItem",
            params={"marketplaceIds": marketplace_id},
            json_body={"productType": product_type or "PRODUCT", "patches": [patch]},
        )
        status = str(body.get("status") or "")
        if status != "ACCEPTED":
            issues = body.get("issues") or []
            reason = issues[0].get("message") if issues and isinstance(issues[0], dict) else status or "no status"
            raise ChannelError(f"Amazon did not accept the change: {reason}", code="invalid")
        return str(body.get("submissionId") or "")

    async def update_price(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, price: float, currency: str) -> str:
        submission = await self._patch(
            marketplace_id,
            sku,
            product_type,
            {
                "op": "replace",
                "path": "/attributes/purchasable_offer",
                "value": [
                    {
                        "marketplace_id": marketplace_id,
                        "currency": currency,
                        "our_price": [{"schedule": [{"value_with_tax": price}]}],
                    }
                ],
            },
        )
        return f"Amazon accepted the new price (submission {submission or 'accepted'}); it can take up to 15 minutes to show."

    async def update_stock(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, quantity: int) -> str:
        submission = await self._patch(
            marketplace_id,
            sku,
            product_type,
            {
                "op": "replace",
                "path": "/attributes/fulfillment_availability",
                "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": quantity}],
            },
        )
        return f"Amazon accepted the new quantity (submission {submission or 'accepted'})."
