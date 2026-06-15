import csv
import io

from odoo import http
from odoo.http import content_disposition, request


class ElmisInventoryExportController(http.Controller):
    CSV_COLUMNS = [
        ("location", "Location"),
        ("facility_code", "eLMIS Facility Code"),
        ("product_code", "Product Code"),
        ("product", "Product"),
        ("generic_name", "Generic Name"),
        ("dosage_form", "Dosage Form"),
        ("programs", "Programs"),
        ("stock_status", "Stock Status"),
        ("stock_on_hand", "Stock On Hand (Units)"),
        ("reserved", "Reserved (Units)"),
        ("available", "Available (Units)"),
        ("reorder_minimum", "Reorder Minimum (Units)"),
        ("reorder_maximum", "Reorder Maximum (Units)"),
        ("unit", "Stock Unit"),
        ("pack_size", "Pack Size"),
        ("pack_size_unit", "Pack Size Unit"),
        ("stock_on_hand_complete_packs", "Stock On Hand (Complete Packs)"),
        ("stock_on_hand_loose_units", "Stock On Hand (Loose Units)"),
        ("reserved_complete_packs", "Reserved (Complete Packs)"),
        ("reserved_loose_units", "Reserved (Loose Units)"),
        ("available_complete_packs", "Available (Complete Packs)"),
        ("available_loose_units", "Available (Loose Units)"),
        ("batch", "Batch"),
        ("batch_stock_on_hand", "Batch Stock On Hand (Units)"),
        ("batch_reserved", "Batch Reserved (Units)"),
        ("batch_available", "Batch Available (Units)"),
        (
            "batch_stock_on_hand_complete_packs",
            "Batch Stock On Hand (Complete Packs)",
        ),
        ("batch_stock_on_hand_loose_units", "Batch Stock On Hand (Loose Units)"),
        ("batch_reserved_complete_packs", "Batch Reserved (Complete Packs)"),
        ("batch_reserved_loose_units", "Batch Reserved (Loose Units)"),
        ("batch_available_complete_packs", "Batch Available (Complete Packs)"),
        ("batch_available_loose_units", "Batch Available (Loose Units)"),
        ("expiry_date", "Expiry Date"),
        ("days_to_expiry", "Days to Expiry"),
        ("expiry_status", "Expiry Status"),
        ("expiry_bucket", "Expiry Bucket"),
    ]

    @http.route(
        "/lesotho_elmis_integration/inventory/export",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def export_inventory(self, **params):
        options = {
            "location_id": params.get("location_id"),
            "program_id": params.get("program_id"),
            "search": params.get("search"),
            "status": params.get("status"),
            "sort": params.get("sort"),
        }
        data = request.env["stock.quant"].get_elmis_inventory_export_data(options)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([label for _key, label in self.CSV_COLUMNS])
        for row in data["rows"]:
            writer.writerow([row[key] for key, _label in self.CSV_COLUMNS])

        facility = data["facility_code"] or "location"
        filename = "elmis_inventory_%s.csv" % facility.replace("/", "_")
        return request.make_response(
            "\ufeff" + output.getvalue(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", content_disposition(filename)),
            ],
        )

    STOCK_CARD_COLUMNS = [
        ("date", "Date and Time"),
        ("movement_label", "Movement"),
        ("reason", "Reason"),
        ("reference", "Reference"),
        ("counterparty_location", "From / To"),
        ("performed_by", "Performed By"),
        ("quantity_in", "Quantity In (Units)"),
        ("quantity_out", "Quantity Out (Units)"),
        ("balance_after", "Balance After (Units)"),
        ("elmis_status_label", "eLMIS Status"),
        ("message_id", "eLMIS Message ID"),
    ]

    @http.route(
        "/lesotho_elmis_integration/stock-card/export",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def export_stock_card(self, **params):
        options = {
            "product_id": params.get("product_id"),
            "lot_id": params.get("lot_id"),
            "location_id": params.get("location_id"),
            "movement_type": params.get("movement_type"),
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
        }
        data = request.env["stock.quant"].get_elmis_stock_card_export_data(
            options
        )
        card = data["card"]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Product", card["product"]["name"]])
        writer.writerow(["Product Code", card["product"]["code"]])
        writer.writerow(["Batch", card["lot"]["name"]])
        writer.writerow(["Expiry", card["lot"]["expiry"] or ""])
        writer.writerow(["Location", card["location"]["name"]])
        writer.writerow(
            ["eLMIS Facility Code", card["location"]["facility_code"]]
        )
        writer.writerow(["Current Balance (Units)", card["summary"]["current_balance"]])
        writer.writerow([])
        writer.writerow([label for _key, label in self.STOCK_CARD_COLUMNS])
        for row in card["rows"]:
            writer.writerow(
                [row[key] for key, _label in self.STOCK_CARD_COLUMNS]
            )

        filename = data["filename"].replace("/", "_")
        return request.make_response(
            "\ufeff" + output.getvalue(),
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", content_disposition(filename)),
            ],
        )
