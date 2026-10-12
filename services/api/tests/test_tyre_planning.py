"""Tyre planning: mapping, forecasts, purchase plan, alerts, reports and source connectors.

Runs on the synthetic TyrePulse-shaped fixture (tests/tyre_fixtures.py); every external
call goes to an httpx.MockTransport or a fake PostgreSQL connector.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from anum_api.settings import settings
from anum_api.tyre import sources
from anum_api.tyre.analytics import (
    AlertKind,
    Basis,
    Confidence,
    ForecastState,
    Severity,
    brand_performance,
    evaluate_alerts,
    fleet_summary,
    forecast_tyres,
    purchase_plan,
    scrap_reasons,
    theil_sen,
)
from anum_api.tyre.mapping import (
    TYREPULSE_PRESET,
    SourceColumn,
    SourceTable,
    apply_mapping,
    fit_to_schema,
    mapped_columns,
    parse_date,
    parse_number,
    suggest_mapping,
)
from anum_api.tyre.model import (
    Assumptions,
    Entity,
    EntityMapping,
    PressureUnit,
    TreadUnit,
    TyreDataset,
    TyreMapping,
    TyreStatus,
    Units,
)
from anum_api.tyre.reports import build_report, summary_text, to_csv, to_pdf
from anum_api.tyre.store import ReportKind
from anum_api.tyre.tools import choose_call, mentions_tyres, parse_month
from tyre_fixtures import AS_OF, SIZE_A, SIZE_B, TABLES, FakeSupabase, build_rows


def discovered() -> list[SourceTable]:
    return [SourceTable(name=table, columns=[SourceColumn(name=c) for c in columns]) for table, columns in TABLES.items()]


def fleet() -> TyreDataset:
    mapping = suggest_mapping(discovered())
    rows = build_rows()
    dataset, _ = apply_mapping(mapping, {entity: rows[spec.source] for entity, spec in mapping.entities.items()})
    return dataset


def by_serial(items: list[Any], serial: str) -> Any:
    return next(item for item in items if item.serial == serial)


# --- mapping ---------------------------------------------------------------------------------


def test_tyrepulse_schema_gets_the_ready_made_mapping() -> None:
    mapping = suggest_mapping(discovered())
    assert mapping.preset == "tyrepulse"
    assert mapping.problems() == []
    assert mapping.entities[Entity.TYRES].source == "tyre_records"
    assert mapping.entities[Entity.TYRES].fields["serial"] == "serial_no"
    assert mapping.entities[Entity.INSPECTIONS].fields["tyre_serial"] == "tyre_serial"
    assert mapping.entities[Entity.PRESSURE_READINGS].fields["target_pressure"] == "target_pressure"
    # Only columns that exist are kept, and only tables that exist.
    trimmed = fit_to_schema(TYREPULSE_PRESET, [t for t in discovered() if t.name != "tpms_readings"])
    assert Entity.PRESSURE_READINGS not in trimmed.entities
    no_cost = [t if t.name != "tyre_records" else SourceTable(name=t.name, columns=[c for c in t.columns if c.name != "cost_per_tyre"]) for t in discovered()]
    assert "purchase_cost" not in fit_to_schema(TYREPULSE_PRESET, no_cost).entities[Entity.TYRES].fields
    # Each table is read once, with the union of the columns its entities use.
    assert set(mapped_columns(mapping)["tyre_service_events"]) >= {"tread_depth", "pressure", "cost", "event_type"}


def test_apply_mapping_normalises_and_links_by_serial() -> None:
    mapping = suggest_mapping(discovered())
    rows = build_rows()
    dataset, stats = apply_mapping(mapping, {e: rows[s.source] for e, s in mapping.entities.items()})
    assert len(dataset.vehicles) == 6 and len(dataset.tyres) == 32
    assert sum(t.status == TyreStatus.FITTED for t in dataset.tyres) == 24
    assert sum(t.status == TyreStatus.STOCK for t in dataset.tyres) == 2
    assert sum(t.status == TyreStatus.SCRAPPED for t in dataset.tyres) == 2
    assert len(dataset.removals) == 6  # from the removal columns of tyre_records
    tyre = by_serial(dataset.tyres, "fake-sn-0101")
    readings = [i for i in dataset.inspections if i.tyre_id == tyre.id]
    assert len(readings) == 4 and readings[0].vehicle_id == "fake-truck-01"
    # Service events without readings are skipped as inspections and kept as costs.
    assert stats["inspections"].issues == {"no tread or pressure reading": 6}
    assert len(dataset.costs) == 6 and dataset.costs[0].category == "repair"
    assert stats["tyres"].kept == 32 and stats["tyres"].skipped == 0


def test_units_dates_and_problems() -> None:
    mapping = TyreMapping(
        entities={
            Entity.TYRES: EntityMapping(source="t", fields={"id": "id", "serial": "sn", "status": "state", "original_tread": "otd"}),
            Entity.INSPECTIONS: EntityMapping(
                source="i", fields={"tyre_serial": "sn", "inspected_at": "when", "tread_depth": "rtd", "pressure": "p",
                                    "odometer_km": "odo"}),
        },
        units=Units(tread=TreadUnit.THIRTY_SECONDS, pressure=PressureUnit.BAR, distance="miles"),
        status_values={"On Truck": "fitted"},
    )
    rows = {
        Entity.TYRES: [{"id": "t1", "sn": "SN-1", "state": "on truck", "otd": "20/32"}, {"id": None}, {"id": "t1"}],
        Entity.INSPECTIONS: [
            {"sn": "sn-1", "when": "15/01/2026", "rtd": 16, "p": 7.5, "odo": 1000},
            {"sn": "sn-1", "when": 1767225600000, "rtd": "12", "p": None, "odo": None},
            {"sn": "unknown", "when": "2026-01-01", "rtd": 10},
            {"sn": "sn-1", "when": "not a date", "rtd": 10},
        ],
    }
    dataset, stats = apply_mapping(mapping, rows)
    assert dataset.tyres[0].status == TyreStatus.FITTED and dataset.tyres[0].original_tread_mm == pytest.approx(15.88)
    first = dataset.inspections[0]
    assert first.inspected_at == date(2026, 1, 15) and first.tread_mm == pytest.approx(12.7)
    assert first.pressure_psi == pytest.approx(108.8) and first.odometer_km == pytest.approx(1609.3)
    assert dataset.inspections[1].inspected_at == date(2026, 1, 1)
    assert stats["tyres"].issues == {"no tyre id": 1, "duplicate tyre id": 1}
    assert stats["inspections"].issues == {"no matching tyre": 1, "no inspection date": 1}
    assert parse_number("1,234.5 km") == 1234.5 and parse_number("n/a") is None and parse_number(True) is None
    assert parse_date("2026-03-04T10:00:00Z") == date(2026, 3, 4)
    assert TyreMapping(entities={}).problems()[0].startswith("Map a table to Tyres")
    linked = TyreMapping(entities={Entity.TYRES: EntityMapping(source="t", fields={"id": "id"}),
                                   Entity.REMOVALS: EntityMapping(source="r", fields={"removed_at": "d"})})
    assert any("tyre_id" in problem for problem in linked.problems())
    with pytest.raises(ValueError):
        TyreMapping(entities={Entity.TYRES: EntityMapping(source="t", fields={"bogus": "x"})})
    with pytest.raises(ValueError):
        EntityMapping(source="t; drop table x", fields={})


def test_generic_schema_gets_a_best_guess() -> None:
    tables = [
        SourceTable(name="trucks", columns=[SourceColumn(name=n) for n in ("vehicle_id", "plate", "odometer")]),
        SourceTable(name="tires", columns=[SourceColumn(name=n) for n in ("tire_id", "serial_number", "brand", "tire_size", "vehicle_id", "wheel_position")]),
        SourceTable(name="tread_checks", columns=[SourceColumn(name=n) for n in ("serial_number", "inspection_date", "tread_depth", "psi")]),
    ]
    mapping = suggest_mapping(tables)
    assert mapping.preset is None
    assert mapping.entities[Entity.TYRES].source == "tires"
    assert mapping.entities[Entity.TYRES].fields["size"] == "tire_size"
    assert mapping.entities[Entity.INSPECTIONS].fields["tread_depth"] == "tread_depth"
    assert mapping.entities[Entity.INSPECTIONS].fields["tyre_serial"] == "serial_number"
    assert mapping.entities[Entity.VEHICLES].source == "trucks"


# --- forecasts -----------------------------------------------------------------------------


def test_theil_sen_ignores_an_outlier_and_gives_an_interval() -> None:
    fit = theil_sen([0, 30, 60, 90, 120], [14, 13, 25, 11, 10])
    assert fit is not None and fit.slope == pytest.approx(-1 / 30)
    assert fit.slope_low is not None and fit.slope_high is not None and fit.slope_low <= fit.slope <= fit.slope_high
    assert theil_sen([5, 5], [1, 2]) is None


def test_forecasts_from_wear_rate() -> None:
    dataset = fleet()
    forecasts = forecast_tyres(dataset, Assumptions(), AS_OF)
    assert len(forecasts) == 24
    fast = by_serial(forecasts, "fake-sn-0101")
    assert fast.basis == Basis.TIME and fast.state == ForecastState.PROJECTED
    # 14 - 2.4 x 3 = 6.8 mm 30 days ago; 3.8 mm left at 0.08 mm/day = 47.5 days later.
    assert fast.current_tread_mm == pytest.approx(6.8)
    assert fast.wear_mm_per_30_days == pytest.approx(2.4)
    assert fast.projected_removal == AS_OF - timedelta(days=30) + timedelta(days=47)
    due = by_serial(forecasts, "fake-sn-0103")
    assert due.state == ForecastState.DUE and due.current_tread_mm == pytest.approx(2.8)
    outlier = by_serial(forecasts, "fake-sn-0102")
    assert outlier.wear_mm_per_30_days == pytest.approx(1.0)  # the 25 mm reading did not move it
    beta = by_serial(forecasts, "fake-sn-0303")
    assert beta.wear_mm_per_30_days == pytest.approx(0.8)
    assert beta.earliest_removal <= beta.projected_removal <= beta.latest_removal
    # Never inspected: the median wear of its brand and size, low confidence.
    never = by_serial(forecasts, "fake-sn-0604")
    assert never.basis == Basis.GROUP and never.confidence == Confidence.LOW and never.projected_removal
    # Soonest first.
    dates = [f.projected_removal or date.max for f in forecasts]
    assert dates == sorted(dates)
    # A higher minimum brings removals forward.
    stricter = by_serial(forecast_tyres(dataset, Assumptions(min_tread_mm=5), AS_OF), "fake-sn-0101")
    assert stricter.projected_removal < fast.projected_removal


def test_distance_basis_uses_odometer_and_vehicle_usage() -> None:
    mapping = TyreMapping(entities={
        Entity.TYRES: EntityMapping(source="t", fields={"id": "id", "vehicle_id": "v", "fitted_km": "km", "fitted_at": "at"}),
        Entity.INSPECTIONS: EntityMapping(source="i", fields={"tyre_id": "id", "inspected_at": "d", "tread_depth": "rtd", "odometer_km": "odo"}),
    })
    rows = {
        Entity.TYRES: [{"id": "t1", "v": "truck", "km": 10_000, "at": "2026-01-01"}],
        # 300 km/day, 2 mm per 10,000 km.
        Entity.INSPECTIONS: [{"id": "t1", "d": (date(2026, 1, 1) + timedelta(days=d)).isoformat(),
                              "rtd": 16 - 2 * (d * 300) / 10_000, "odo": 10_000 + d * 300} for d in (30, 60, 90, 120)],
    }
    dataset, _ = apply_mapping(mapping, rows)
    forecast = forecast_tyres(dataset, Assumptions(), date(2026, 6, 1))[0]
    assert forecast.basis == Basis.DISTANCE
    assert forecast.wear_mm_per_1000km == pytest.approx(0.2)
    assert forecast.wear_mm_per_30_days == pytest.approx(1.8)


# --- summary, plan, alerts ---------------------------------------------------------------------


def test_fleet_summary_numbers() -> None:
    summary = fleet_summary(fleet(), Assumptions(), AS_OF)
    assert (summary.vehicles, summary.tyres_fitted, summary.tyres_in_stock) == (6, 24, 2)
    assert summary.below_minimum == 1 and summary.overdue_inspections == 1
    assert (summary.under_inflated, summary.over_inflated) == (1, 1)
    assert summary.pressure_compliant_pct == pytest.approx(round(100 * 21 / 23, 1))
    assert summary.tyres_removed_last_30_days == 2  # removed 10 and 30 days ago
    assert summary.cost_per_1000_km is not None and summary.cost_per_1000_km > 0


def test_purchase_plan_quantities_stock_and_prices() -> None:
    dataset = fleet()
    assumptions = Assumptions(safety_stock_pct=10, unit_prices={f"{SIZE_B}|brandbeta": 1600})
    plan = purchase_plan(dataset, assumptions, AS_OF)
    assert plan.months[0] == "2026-10" and len(plan.months) == 12
    october_a = next(r for r in plan.rows if r.month == "2026-10" and r.size == SIZE_A)
    # fake-sn-0101 (due 2026-10-18) + safety 1 = 2, both from the 2 tyres in stock.
    assert (october_a.due, october_a.safety, october_a.from_stock, october_a.to_buy) == (1, 1, 2, 0)
    assert october_a.unit_price == 1200 and october_a.price_source == "data_brand_size"
    october_b = next(r for r in plan.rows if r.month == "2026-10" and r.size == SIZE_B)
    assert october_b.unit_price == 1600 and october_b.price_source == "assumption"
    assert october_b.estimated_cost == 1600 * october_b.to_buy
    assert plan.total_tyres == sum(r.to_buy for r in plan.rows)
    preferred = purchase_plan(dataset, Assumptions(preferred_brands={SIZE_A: "brandgamma"}), AS_OF)
    assert {r.brand for r in preferred.rows if r.size == SIZE_A} == {"brandgamma"}
    no_low = purchase_plan(dataset, Assumptions(include_low_confidence=False), AS_OF)
    assert sum(r.due for r in no_low.rows) < sum(r.due for r in plan.rows)


def test_alerts_cover_every_kind() -> None:
    alerts = evaluate_alerts(fleet(), Assumptions(), AS_OF)
    kinds = {(a.kind, a.subject_label.split()[0]) for a in alerts}
    assert (AlertKind.LOW_TREAD, "fake-sn-0103") in kinds
    assert (AlertKind.UNDER_INFLATION, "fake-sn-0201") in kinds
    assert (AlertKind.OVER_INFLATION, "fake-sn-0202") in kinds
    assert (AlertKind.OVERDUE_INSPECTION, "fake-sn-0604") in kinds
    assert (AlertKind.ABNORMAL_WEAR, "fake-sn-0101") in kinds
    assert any(a.kind == AlertKind.COST_ANOMALY for a in alerts)
    low = next(a for a in alerts if a.kind == AlertKind.LOW_TREAD)
    assert low.severity == Severity.CRITICAL and "2.8 mm" in low.message
    under = next(a for a in alerts if a.kind == AlertKind.UNDER_INFLATION)
    assert under.severity == Severity.CRITICAL and "85 psi" in under.message
    assert len({a.key for a in alerts}) == len(alerts)


def test_breakdowns() -> None:
    dataset = fleet()
    reasons = scrap_reasons(dataset)
    assert reasons[0].reason == "worn out" and reasons[0].removals == 3 and reasons[0].share_pct == 50.0
    brands = brand_performance(dataset, Assumptions(), forecast_tyres(dataset, Assumptions(), AS_OF))
    beta = next(row for row in brands if row.brand == "brandbeta")
    assert beta.median_wear_mm_per_30_days == pytest.approx(0.8)


# --- reports -------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(ReportKind))
def test_every_report_builds_and_exports(kind: ReportKind) -> None:
    content = build_report(kind, fleet(), Assumptions(), as_of=AS_OF, period_from=None, period_to=None, data_as_of=None)
    assert content["title"] and content["highlights"] and content["tables"]
    text = to_csv(content)
    assert content["title"] in text
    pdf = to_pdf(content)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and b"/Type /Page " in pdf
    assert summary_text(content).startswith(content["title"])


def test_csv_neutralises_formulas_and_pdf_survives_arabic() -> None:
    content = {"title": "=HYPERLINK(\"x\")", "period_from": "a", "period_to": "b", "highlights": [{"label": "n", "value": -3.5}],
               "tables": [{"title": "t", "columns": ["c"], "rows": [["=1+1"], ["@cmd"], ["إطار"]]}], "notes": []}
    rows = list(csv.reader(io.StringIO(to_csv(content))))
    assert rows[0][0].startswith("'=") and ["'=1+1"] in rows and ["'@cmd"] in rows and ["إطار"] in rows
    assert ["n", "-3.5"] in rows  # numbers are not mangled
    assert to_pdf(content).startswith(b"%PDF")


def test_fleet_health_report_numbers() -> None:
    content = build_report(ReportKind.FLEET_HEALTH, fleet(), Assumptions(), as_of=AS_OF, period_from=None,
                           period_to=None, data_as_of=None, open_alerts={"critical": 2})
    values = {item["label"]: item["value"] for item in content["highlights"]}
    assert values["Tyres fitted"] == 24 and values["At or below 3 mm"] == 1 and values["Open alerts"] == 2


# --- assistant tool choice --------------------------------------------------------------------


def test_tool_choice_from_the_users_words() -> None:
    today = date(2026, 10, 11)
    assert mentions_tyres("Which tyres are due in March?") and mentions_tyres("كم إطار مستحق")
    assert not mentions_tyres("tired of waiting")
    assert choose_call("Which tyres are due in March?", today).arguments == {"month": "2027-03"}
    assert choose_call("tyres to replace next month", today).arguments == {"month": "2026-11"}
    assert choose_call("tyres due 2026-12", today).arguments == {"month": "2026-12"}
    assert choose_call("tyre purchase plan for the next 6 months", today).arguments == {"months": 6}
    assert choose_call("any tyre pressure alerts?", today).name == "tyre_alerts"
    assert choose_call("how is my tyre fleet doing", today).name == "tyre_fleet_summary"
    assert choose_call("ما هي تنبيهات الإطارات", today).name == "tyre_alerts"
    assert parse_month("إطارات مستحقة في مارس", today) == "2027-03"


# --- sources -------------------------------------------------------------------------------------


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")


def test_supabase_rest_discovers_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSupabase()
    monkeypatch.setattr(sources, "rest_client_factory", fake.client_factory())
    monkeypatch.setattr(settings, "tyre_max_rows_per_entity", 100)
    source = sources.SupabaseRestSource("https://example.supabase.co/", "example-key")
    tables = asyncio.run(source.discover())
    assert {t.name for t in tables} == set(TABLES)
    result = asyncio.run(source.fetch("tyre_service_events", ["tyre_serial", "tread_depth"]))
    assert len(result.rows) == 98 and not result.cut and set(result.rows[0]) == {"tyre_serial", "tread_depth"}
    monkeypatch.setattr(settings, "tyre_max_rows_per_entity", 50)
    cut = asyncio.run(source.fetch("tyre_service_events", ["tyre_serial"]))
    assert len(cut.rows) == 50 and cut.cut
    assert all(r.headers["apikey"] == "example-key" for r in fake.requests)
    bad = sources.SupabaseRestSource("https://example.supabase.co", "wrong-key")
    with pytest.raises(sources.TyreSourceError) as error:
        asyncio.run(bad.test())
    assert "refused the key (HTTP 401)" in error.value.message and "wrong-key" not in error.value.message


def test_supabase_url_and_postgres_string_rules(production: None) -> None:
    with pytest.raises(sources.TyreSourceError):
        sources.normalize_supabase_url("http://example.supabase.co")
    with pytest.raises(sources.TyreSourceError):
        sources.normalize_supabase_url("https://user:pw@example.supabase.co")
    assert sources.normalize_supabase_url("https://example.supabase.co/") == "https://example.supabase.co"
    with pytest.raises(sources.TyreSourceError):
        sources.parse_postgres_url("postgresql://reader:example@db.example.supabase.co:5432/postgres?sslmode=disable")
    with pytest.raises(sources.TyreSourceError):
        sources.parse_postgres_url("postgresql://db.example.supabase.co/postgres")
    with pytest.raises(sources.TyreSourceError):
        sources.parse_postgres_url("postgresql://reader:example@db.example.supabase.co/postgres?options=x")
    target = sources.parse_postgres_url("postgresql://tyre_reader:example%40word@db.example.supabase.co:6543/postgres")
    assert (target.user, target.password, target.port, target.sslmode) == ("tyre_reader", "example@word", 6543, "require")


def test_postgres_source_pins_the_checked_address_and_reads_read_only(production: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def resolve(host: str, port: int) -> list[str]:
        return {"db.example.supabase.co": ["104.16.0.10"], "internal.example": ["10.0.0.5"]}[host]

    class Cursor:
        def __init__(self, rows: list[tuple[Any, ...]]) -> None:
            self.rows = rows

        def fetchone(self) -> tuple[Any, ...]:
            return self.rows[0]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self.rows

        def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
            return self.rows[:size]

    class Connection:
        def transaction(self):  # type: ignore[no-untyped-def]
            from contextlib import nullcontext

            return nullcontext()

        def execute(self, statement: Any, params: Any = None) -> Cursor:
            text = str(statement)
            if "current_user" in text:
                return Cursor([("tyre_reader", "on")])
            if "information_schema" in text:
                return Cursor([("tyre_records", "serial_no", "text", 10, False), ("tyre_records", "brand", "text", 10, False),
                               ("vehicle_fleet", "asset_no", "text", 2, True)])
            return Cursor([("fake-sn-1", "brandalpha"), ("fake-sn-2", "brandbeta")])

        def close(self) -> None:
            pass

    def connect(**kwargs: Any) -> Connection:
        calls.append(kwargs)
        return Connection()

    monkeypatch.setattr(sources, "postgres_connect", connect)
    from anum_api import model_egress

    monkeypatch.setattr(model_egress, "resolver", resolve)
    source = sources.SupabasePostgresSource("postgresql://tyre_reader:example@db.example.supabase.co:5432/postgres")
    assert "read-only session: on" in asyncio.run(source.test())
    tables = asyncio.run(source.discover())
    assert [t.name for t in tables] == ["tyre_records", "vehicle_fleet"] and tables[1].writable
    result = asyncio.run(source.fetch("tyre_records", ["serial_no", "brand"]))
    assert result.rows == [{"serial_no": "fake-sn-1", "brand": "brandalpha"}, {"serial_no": "fake-sn-2", "brand": "brandbeta"}]
    assert calls[0]["hostaddr"] == "104.16.0.10" and calls[0]["host"] == "db.example.supabase.co"
    assert "default_transaction_read_only=on" in calls[0]["options"] and "statement_timeout" in calls[0]["options"]
    private = sources.SupabasePostgresSource("postgresql://r:example@internal.example:5432/postgres")
    with pytest.raises(sources.TyreSourceError) as error:
        asyncio.run(private.test())
    assert "public address" in error.value.message and "10.0.0.5" not in error.value.message
    odd_port = sources.SupabasePostgresSource("postgresql://r:example@db.example.supabase.co:2222/postgres")
    with pytest.raises(sources.TyreSourceError):
        asyncio.run(odd_port.test())
    monkeypatch.setattr(settings, "tyre_source_allowed_hosts", "internal.example")
    assert "Connected" in asyncio.run(private.test())


def _service_account() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return json.dumps({
        "type": "service_account", "project_id": "example-project", "private_key_id": "example",
        "private_key": pem.decode(), "client_email": "reader@example-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def test_firestore_signs_in_lists_collections_and_decodes_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == "https://oauth2.googleapis.com/token":
            assert b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "example-token", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer example-token"
        if url.endswith(":listCollectionIds"):
            return httpx.Response(200, json={"collectionIds": ["tyres"]})
        page_token = request.url.params.get("pageToken")
        doc = lambda n: {  # noqa: E731
            "name": f"projects/example-project/databases/(default)/documents/tyres/doc{n}",
            "fields": {"serial": {"stringValue": f"fake-sn-{n}"}, "tread": {"doubleValue": 9.5},
                       "count": {"integerValue": "3"}, "fitted": {"timestampValue": "2026-01-02T00:00:00Z"},
                       "specs": {"mapValue": {"fields": {"size": {"stringValue": "315/80r22.5"}}}},
                       "vehicle": {"referenceValue": "projects/p/databases/(default)/documents/vehicles/fake-truck-01"},
                       "gone": {"nullValue": None}},
        }
        if page_token is None:
            return httpx.Response(200, json={"documents": [doc(1), doc(2)], "nextPageToken": "next"})
        return httpx.Response(200, json={"documents": [doc(3)]})

    monkeypatch.setattr(sources, "google_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    source = sources.FirestoreSource(_service_account())
    assert "reader@example-project" in asyncio.run(source.test())
    tables = asyncio.run(source.discover())
    columns = {c.name: c.type for c in tables[0].columns}
    assert columns["specs.size"] == "string" and columns["tread"] == "number" and "__id__" in columns
    result = asyncio.run(source.fetch("tyres", ["serial", "specs.size"]))
    assert [row["serial"] for row in result.rows] == ["fake-sn-1", "fake-sn-2", "fake-sn-3"]
    assert result.rows[0]["vehicle"] == "fake-truck-01" and result.rows[0]["count"] == 3
    assert ("mask.fieldPaths", "specs.size") in list(seen[-1].url.params.multi_items())
    with pytest.raises(sources.TyreSourceError):
        sources.parse_service_account(json.dumps({"type": "service_account", "project_id": "p", "client_email": "e",
                                                  "private_key": "-----BEGIN PRIVATE KEY-----", "token_uri": "https://evil.example/token"}))
    with pytest.raises(sources.TyreSourceError):
        sources.parse_service_account("not json")


def test_tyre_tools_declare_their_autonomy_category_and_kind() -> None:
    # Declared rather than guessed: "purchase_plan" would otherwise read as a payment,
    # which autonomy always holds for approval (docs/autonomy.md).
    from anum_api.agent_tools import ToolRegistry
    from anum_api.autonomy import ActionKind, ToolCategory, category_of, kind_of
    from anum_api.tyre.tools import register_tyre_tools

    registry = register_tyre_tools(ToolRegistry())
    for name in ("tyre_fleet_summary", "tyres_due", "purchase_plan", "tyre_alerts"):
        definition = registry.definition(name)
        assert definition is not None
        assert category_of(name, definition) == ToolCategory.GENERAL
        assert kind_of(name, definition) == ActionKind.READ
