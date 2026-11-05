"""Vehicle analytics are pure functions (docs/vehicles.md#analytics)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from anum_api.vehicles import analytics as A
from anum_api.vehicles.models import FuelEntry, OdometerReading, PaymentRef, ServiceRecord, Vehicle

T0 = datetime(2026, 5, 1, 8, tzinfo=UTC)
SCOPE = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u"}
CHILD = {**SCOPE, "vehicle_id": "veh_1", "visibility": "personal", "vehicle_owner_id": "u"}


def car(**extra: object) -> Vehicle:
    return Vehicle(id="veh_1", ownership="personal", registration="ABC", country="SA", currency="SAR", fuel_type="petrol_91",
                   created_at=T0, updated_at=T0, **SCOPE, **extra)  # type: ignore[arg-type]


def reading(km: str, day: int, correction: bool = False) -> OdometerReading:
    return OdometerReading(id=f"r{day}{km}", reading_km=Decimal(km), read_at=T0 + timedelta(days=day), source="correction" if correction else "manual",
                           correction=correction, reason="fix" if correction else None, created_at=T0, **CHILD)


def fill(i: int, litres: str, odometer: str | None, full: bool = True, paid: str | None = None, currency: str = "SAR",
         price: str | None = "2.33") -> FuelEntry:
    payment = PaymentRef(link_id=f"l{i}", canonical_id=f"c{i}", status="active", amount=Decimal(paid), currency=currency, paid_by="u",
                         linked_at=T0) if paid else None
    return FuelEntry(id=f"f{i}", filled_at=T0 + timedelta(days=i), fuel_type="petrol_91", litres=Decimal(litres), currency=currency,
                     odometer_km=Decimal(odometer) if odometer else None, full_tank=full, payment=payment, idem_key=f"k{i}",
                     price_per_litre=Decimal(price) if price else None, created_at=T0, updated_at=T0, **CHILD)


def test_distance_skips_the_jump_to_a_correction() -> None:
    readings = [reading("1000", 0), reading("1500", 1), reading("1200", 2, correction=True), reading("1300", 3)]
    assert A.distance_km(readings) == Decimal("600")
    assert A.current_odometer(readings) == Decimal("1300")


def test_l_per_100_only_from_full_to_full_chains() -> None:
    fills = [fill(0, "40", "1000"), fill(1, "10", None, full=False), fill(2, "30", "1500"), fill(3, "20", None), fill(4, "35", "2000"),
             fill(5, "40", "2400")]
    segments = A.consumption(fills)
    # 1000 to 1500: 10 + 30 litres over 500 km. A full fill without an odometer value breaks the chain, so 1500 to 2000 is skipped.
    assert [(s.start_id, s.end_id, s.litres, s.per_100) for s in segments] == [("f0", "f2", Decimal("40"), Decimal("8.00")),
                                                                              ("f4", "f5", Decimal("40"), Decimal("10.00"))]
    assert A.average_per_100(segments) == Decimal("8.89")
    # An odometer correction in between restarts the chain.
    assert A.consumption(fills[:3], corrections=[T0 + timedelta(days=1, hours=1)]) == []
    assert A.consumption([fill(0, "10", None, full=False), fill(1, "40", "100")]) == []


def test_money_comes_only_from_active_links_and_never_across_currencies() -> None:
    fills = [fill(0, "40", "1000", paid="100"), fill(1, "40", "1500", paid="90", currency="AED"), fill(2, "40", "2000")]
    service = [ServiceRecord(id="s1", kind="oil", service_on=date(2026, 5, 3), currency="SAR", idem_key="s",
                             payment=PaymentRef(link_id="l", canonical_id="c", status="needs_review", amount=Decimal("50"), currency="SAR",
                                                paid_by="u", linked_at=T0), created_at=T0, updated_at=T0, **CHILD)]
    report = A.vehicle_report(car(), [reading("1000", 0), reading("2000", 2)], fills, service, start=None, end=None, today=date(2026, 6, 1))
    assert report["spend"] == [{"currency": "AED", "amount": "90.00"}, {"currency": "SAR", "amount": "100.00"}]
    assert report["cost_per_km"] == [{"currency": "AED", "amount": "0.090"}, {"currency": "SAR", "amount": "0.100"}]
    assert report["unlinked_records"] == 1
    assert {a["kind"] for a in report["anomalies"]} == {"payment_changed"}
    overview = A.overview([car()], fills, service, start=None, end=None)
    assert overview["monthly"] == [{"month": "2026-05", "currency": "AED", "amount": "90.00"}, {"month": "2026-05", "currency": "SAR", "amount": "100.00"}]


def test_price_trend_is_litres_weighted() -> None:
    trend = A.price_trend([fill(0, "10", None, price="2.00"), fill(1, "30", None, price="3.00")])
    assert trend == [{"month": "2026-05", "currency": "SAR", "fuel_type": "petrol_91", "price_per_litre": "2.750"}]


def test_anomalies_tank_price_consumption_and_payment_gap() -> None:
    fills = [fill(0, "40", "1000"), fill(1, "40", "1500"), fill(2, "40", "2000"), fill(3, "40", "2500"), fill(4, "80", "2700", price="4.00"),
             fill(5, "40", "3200", paid="200")]
    fills[5].total = Decimal("93.20")
    kinds = {(a.kind, a.record_id) for a in A.anomalies(car(tank_litres=Decimal("60")), fills, [], A.consumption(fills))}
    assert ("over_tank", "f4") in kinds and ("price_outlier", "f4") in kinds and ("high_consumption", "f4") in kinds
    assert ("payment_mismatch", "f5") in kinds


def test_depreciation_is_straight_line_and_capped() -> None:
    vehicle = car(purchase_price=Decimal("100000"), residual_value=Decimal("40000"), useful_life_months=24, acquisition_date=date(2024, 1, 15))
    assert A.depreciation(vehicle, date(2025, 1, 14)) == {"currency": "SAR", "purchase_price": "100000.00", "residual_value": "40000.00",
                                                          "monthly": "2500.00", "months": 11, "accumulated": "27500.00", "book_value": "72500.00"}
    assert A.depreciation(vehicle, date(2030, 1, 1))["book_value"] == "40000.00"
    assert A.depreciation(car(), date(2030, 1, 1)) is None
