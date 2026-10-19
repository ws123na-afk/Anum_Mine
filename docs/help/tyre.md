---
id: tyre
title: Tyre planning
summary: See fleet tyre health, removal forecasts, a purchase plan, alerts and reports from your own tyre data.
keywords: tyre, tire, fleet, forecast, purchase plan, alerts, reports, tread, sync, tyrepulse, supabase
docs: ../tyre-planning.md
---

# Tyre planning

Tyre planning reads your own tyre data (for example a TyrePulse database), forecasts when each tyre needs replacing and turns that into a purchase plan, alerts and reports. ANUM only reads your data.

## Connect your tyre data

1. Open **Tyre planning** in the sidebar and click the **Connection** tab.
2. Under **Data source**, choose the **Connection kind**, fill in the address and credential, and click **Save connection**, then **Test connection**.
3. Under **Mapping**, click **Discover tables** (or **Use the TyrePulse preset**), check the columns and click **Preview on a sample**.
4. Click **Save mapping**, then **Sync now**. Sync again whenever you want fresh numbers.

## Read forecasts and handle alerts

1. Click the **Dashboard** tab for the fleet summary and the tyres due soon.
2. Click **Forecasts** and narrow the list by **Removal month**, **State** or **Confidence**, or search by serial, vehicle, brand or size. **Download CSV** saves the list.
3. Click **Purchase plan** to see how many tyres to buy by month, size and brand.
4. Click **Alerts** and click **Acknowledge** once an alert is handled. **Reopen** brings it back.

## Generate and send a report

1. Click the **Reports** tab.
2. Under **Generate a report**, pick the **Report**, an optional **From** and **To**, and click **Generate**.
3. Download it with **PDF** or **CSV**.
4. To send it, click **Send**, choose **Send by**, enter the recipient and click **Request sending**. Nothing leaves until it is approved in Approvals.

## Who can do this

- Owners: everything, including connecting the source, the credential, the mapping, the planning assumptions and disconnecting.
- Members: sync, generate, schedule, delete and send reports, and acknowledge alerts.
- Viewers: read the dashboard, forecasts, purchase plan, alerts and reports, and download CSV or PDF.

## Learn more

[Tyre planning](../tyre-planning.md)
