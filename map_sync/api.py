import frappe
import requests
from frappe.utils import getdate, add_days, today, formatdate
    

API_URL = "https://api-dev.eaglehills.com/tenants/mgmall/salesimport"





@frappe.whitelist()
def send_sales(sales_date=None):
    sales_date = getdate(sales_date or add_days(today(), -1))

    username = "maldoseri@aldoseriventures.com"
    password = 'LD@dev789789'
    api_key = 'dev:AvW5^d)(h#45#+c}!PmmF'

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
        frappe.log_error(frappe.get_traceback(), "MAP sales sync failed")
        frappe.throw("API call failed: " + str(e))

    try:
        body = r.json()
    except ValueError:
        body = r.text

    return {
        "http_status": r.status_code,
        "sales_date": str(sales_date),
        "payload": payload,
        "response": body,
    }



def daily_sales_sync():
    try:
        result = send_sales()  # defaults to yesterday
        http_status = result["http_status"]
        body = result["response"]

        # The mall returns StatusMessage "SUCCESS" inside the JSON body
        api_ok = isinstance(body, dict) and body.get("StatusMessage") == "SUCCESS"

        if http_status in (200, 201) and api_ok:
            frappe.log_error(
                "Sales date: {0}\nTracking ID: {1}\n\n{2}".format(
                    result["sales_date"],
                    body.get("ReqTrackingId"),
                    frappe.as_json(result),
                ),
                "MAP daily sync SUCCESS",
            )
            return {"status": "success", "sales_date": result["sales_date"], }

        frappe.log_error(frappe.as_json(result), "MAP daily sync rejected")
        return {"status": "rejected", "sales_date": result["sales_date"]}

    except Exception:
        frappe.log_error(frappe.get_traceback(), "MAP daily sync failed")
        return {"status": "failed"}
