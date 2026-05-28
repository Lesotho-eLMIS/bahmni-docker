from odoo import _, fields, models
from odoo.exceptions import UserError


class ElmisInventorySyncWizard(models.TransientModel):
    _name = "elmis.inventory.sync.wizard"
    _description = "Trigger eLMIS Inventory Sync"

    mirror_location_id = fields.Many2one(
        "stock.location",
        string="eLMIS Mirror Location",
        readonly=True,
    )
    facility_code = fields.Char(readonly=True)
    program_codes = fields.Char(readonly=True)

    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        service = self.env["elmis.inventory.sync"]

        try:
            params = service._get_elmis_config()
            values["program_codes"] = ", ".join(params["program_codes"])
        except UserError:
            values["program_codes"] = False

        try:
            location = service._get_configured_mirror_location()
            values.update(
                {
                    "mirror_location_id": location.id,
                    "facility_code": location.elmis_facility_code,
                }
            )
        except UserError:
            values.update({"mirror_location_id": False, "facility_code": False})

        return values

    def action_test_connection(self):
        self.ensure_one()
        result, run = self.env["elmis.inventory.sync"].test_configured_connection_with_run()
        message = _(
            "eLMIS connection successful. Facility: %(facility)s, programs: "
            "%(programs)s, active stock rows found: %(rows)s. Audit run: %(run)s."
        ) % {
            "facility": result["facility_code"],
            "programs": ", ".join(result["program_codes"]),
            "rows": result["stock_entries_found"],
            "run": run.display_name,
        }
        return self._notification(_("eLMIS Connection Test"), message)

    def action_sync_inventory(self):
        self.ensure_one()
        result, run = self.env["elmis.inventory.sync"].sync_configured_facility_inventory_with_run()
        message = _(
            "eLMIS inventory sync completed. Items: %(items)s, products created: "
            "%(products)s, lots created: %(lots)s, stock rows updated: %(quants)s. "
            "Audit run: %(run)s."
        ) % {
            "items": result["items_processed"],
            "products": result["products_created"],
            "lots": result["lots_created"],
            "quants": result["quants_updated"],
            "run": run.display_name,
        }
        return self._notification(_("eLMIS Inventory Sync"), message)

    def _notification(self, title, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }
