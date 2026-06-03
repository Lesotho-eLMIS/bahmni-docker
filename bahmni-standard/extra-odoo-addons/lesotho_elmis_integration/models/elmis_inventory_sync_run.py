from odoo import _, api, fields, models


class ElmisInventorySyncRun(models.Model):
    _name = "elmis.inventory.sync.run"
    _description = "eLMIS Inventory Sync Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, readonly=True, default=lambda self: _("New"))
    operation = fields.Selection(
        [
            ("test_connection", "Test Connection"),
            ("inventory_sync", "Inventory Sync"),
        ],
        required=True,
        readonly=True,
        default="inventory_sync",
    )
    status = fields.Selection(
        [
            ("running", "Running"),
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        required=True,
        readonly=True,
        default="running",
    )
    started_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    finished_at = fields.Datetime(readonly=True)
    duration_seconds = fields.Float(readonly=True)
    location_id = fields.Many2one("stock.location", readonly=True)
    facility_code = fields.Char(readonly=True)
    facility_id = fields.Char(readonly=True)
    program_codes = fields.Char(readonly=True)
    programs_resolved = fields.Integer(readonly=True)
    stock_entries_found = fields.Integer(readonly=True)
    items_processed = fields.Integer(readonly=True)
    products_created = fields.Integer(readonly=True)
    lots_created = fields.Integer(readonly=True)
    quants_updated = fields.Integer(readonly=True)
    message = fields.Text(readonly=True)
    error_message = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self._build_name(vals)
        return super().create(vals_list)

    @api.model
    def _build_name(self, vals):
        operation = dict(self._fields["operation"].selection).get(
            vals.get("operation") or "inventory_sync",
            _("Inventory Sync"),
        )
        facility_code = vals.get("facility_code") or _("unconfigured facility")
        return _("eLMIS %(operation)s - %(facility)s") % {
            "operation": operation,
            "facility": facility_code,
        }

    def mark_success(self, result):
        self.ensure_one()
        values = self._finish_values("success")
        values.update(
            {
                "facility_code": result.get("facility_code") or self.facility_code,
                "facility_id": result.get("facility_id") or self.facility_id,
                "program_codes": self._format_program_codes(result.get("program_codes"))
                or self.program_codes,
                "programs_resolved": result.get("programs_resolved")
                or self.programs_resolved,
                "stock_entries_found": result.get("stock_entries_found")
                or self.stock_entries_found,
                "items_processed": result.get("items_processed") or self.items_processed,
                "products_created": result.get("products_created") or self.products_created,
                "lots_created": result.get("lots_created") or self.lots_created,
                "quants_updated": result.get("quants_updated") or self.quants_updated,
                "message": result.get("message") or self.message,
                "error_message": False,
            }
        )
        if result.get("location_id") and not self.location_id:
            values["location_id"] = result["location_id"]
        self.write(values)

    def mark_failed(self, error):
        self.ensure_one()
        values = self._finish_values("failed")
        values.update({"error_message": str(error)})
        self.write(values)

    def _finish_values(self, status):
        self.ensure_one()
        finished_at = fields.Datetime.now()
        started_at = fields.Datetime.to_datetime(self.started_at)
        duration = 0.0
        if started_at:
            duration = (fields.Datetime.to_datetime(finished_at) - started_at).total_seconds()
        return {
            "status": status,
            "finished_at": finished_at,
            "duration_seconds": duration,
        }

    @api.model
    def _format_program_codes(self, program_codes):
        if isinstance(program_codes, str):
            return program_codes
        if program_codes:
            return ", ".join(program_codes)
        return False
