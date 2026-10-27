import frappe
import requests
from frappe.utils import getdate, add_days, today, formatdate


def log_sync(status, sales_date=None, http_status=None, tracking_id=None,
             request=None, response=None, error=None):
    """Write a MAP Sync Log entry, controlled by MAP Log Settings."""
    try:
        settings = frappe.get_cached_doc("MAP Log Settings")

        if not settings.enable_logging:
            return
        if settings.log_level == "Errors Only" and status == "Success":
            return

        frappe.get_doc({
            "doctype": "MAP Sync Log",
            "status": status,
            "sales_date": sales_date,
            "http_status": http_status,
            "tracking_id": tracking_id,
            "request": frappe.as_json(request) if (settings.log_request_payload and request) else None,
            "response": frappe.as_json(response) if (settings.log_response and response) else None,
            "error": error,
        }).insert(ignore_permissions=True)

        # commit now: frappe.throw() later would roll this log back otherwise
        frappe.db.commit()
    except Exception:
        # logging must never break the sync itself
        frappe.log_error(frappe.get_traceback(), "MAP log_sync failed")


@frappe.whitelist()
def send_sales(sales_date=None):
    sales_date = getdate(sales_date or add_days(today(), -1))

    settings = frappe.get_single("MAP Settings")

    API_URL = settings.api_url
    username = settings.username
    password = settings.get_password("password")
    api_key = settings.get_password("api_key")

    totals = frappe.db.sql(
        """
        SELECT COALESCE(SUM(net_total), 0) AS net_sales,
               COUNT(name) AS transaction_count
        FROM `tabPOS Invoice`
        WHERE docstatus = 1 AND posting_date = %s
        """,
        (sales_date,),
        as_dict=True,
    )[0]

    payload = [{
        "PROPERTYCODE": "mgb",
        "LEASECODE": "t0001311",
        "SaleType": "spec",
        "SalesFromDATE": formatdate(sales_date, "MM/dd/yyyy"),
        "NetSalesAmount": float(totals.net_sales or 0),
        "NoofTransactions": int(totals.transaction_count or 0),
        "SalesFrequency": "Daily",
    }]

    headers = {
        "x-tenant-api-key": api_key,
        "x-tenant-username": username,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        r = requests.post(
            API_URL,
            headers=headers,
            auth=(username, password),
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        log_sync(
            "Failed",
            sales_date=sales_date,
            request=payload,
            error=frappe.get_traceback(),
        )
        frappe.throw("API call failed: " + str(e))

    try:
        body = r.json()
    except ValueError:
        body = r.text

    # The mall returns StatusMessage "SUCCESS" inside the JSON body
    api_ok = isinstance(body, dict) and body.get("StatusMessage") == "SUCCESS"
    tracking_id = body.get("ReqTrackingId") if isinstance(body, dict) else None
    status = "Success" if (r.status_code in (200, 201) and api_ok) else "Rejected"

    log_sync(
        status,
        sales_date=sales_date,
        http_status=r.status_code,
        tracking_id=tracking_id,
        request=payload,
        response=body,
    )

    return {
        "status": status,
        "http_status": r.status_code,
        "sales_date": str(sales_date),
        "payload": payload,
        "response": body,
    }


def daily_sales_sync():
    try:
        result = send_sales()  # defaults to yesterday; logging happens inside
        return {"status": result["status"].lower(), "sales_date": result["sales_date"]}
    except Exception:
        # network errors are already logged in send_sales; this catches anything else
        # (e.g. missing settings, DB errors)
        log_sync("Failed", error=frappe.get_traceback())
        return {"status": "failed"}