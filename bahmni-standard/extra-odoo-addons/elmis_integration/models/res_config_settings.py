import os

from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    elmis_base_url = fields.Char(
        string="eLMIS Base URL",
        config_parameter="elmis_integration.base_url",
        default=lambda self: os.environ.get(
            "ELMIS_BASE_URL", "https://dev.elmis.gov.ls/api/"
        ),
    )
    elmis_program_codes = fields.Char(
        string="eLMIS Program Codes",
        config_parameter="elmis_integration.program_codes",
        default=lambda self: os.environ.get("ELMIS_PROGRAM_CODES", "art,em,lab,ois"),
        help="Comma-separated eLMIS program codes to include in facility inventory sync.",
    )
    elmis_username = fields.Char(
        string="eLMIS Service Account Username",
        config_parameter="elmis_integration.username",
        default=lambda self: os.environ.get("ELMIS_USERNAME"),
    )
    elmis_password = fields.Char(
        string="eLMIS Service Account Password",
        config_parameter="elmis_integration.password",
        default=lambda self: os.environ.get("ELMIS_PASSWORD"),
    )
    elmis_api_token = fields.Char(
        string="eLMIS API Token",
        config_parameter="elmis_integration.api_token",
        default=lambda self: os.environ.get("ELMIS_API_TOKEN"),
        help="Optional token. If provided, it takes precedence over username/password.",
    )
    elmis_mirror_location_id = fields.Many2one(
        "stock.location",
        string="eLMIS Mirror Location",
        config_parameter="elmis_integration.mirror_location_id",
        domain=[
            ("usage", "=", "internal"),
            ("active", "=", True),
            ("elmis_facility_code", "!=", False),
        ],
        help=(
            "Internal Odoo stock location that mirrors the configured eLMIS "
            "facility/service point inventory."
        ),
    )
    elmis_sync_cron_active = fields.Boolean(
        string="Run Scheduled eLMIS Sync",
        config_parameter="elmis_integration.sync_cron_active",
        default=lambda self: os.environ.get("ELMIS_SYNC_CRON_ACTIVE", "False")
        == "True",
        help="Automatically sync the configured eLMIS facility inventory on a schedule.",
    )
    elmis_sync_interval_number = fields.Integer(
        string="Sync Every",
        config_parameter="elmis_integration.sync_interval_number",
        default=lambda self: int(os.environ.get("ELMIS_SYNC_INTERVAL_NUMBER", "6")),
    )
    elmis_sync_interval_type = fields.Selection(
        [
            ("minutes", "Minutes"),
            ("hours", "Hours"),
            ("days", "Days"),
        ],
        string="Sync Interval Unit",
        config_parameter="elmis_integration.sync_interval_type",
        default=lambda self: os.environ.get("ELMIS_SYNC_INTERVAL_TYPE", "hours"),
    )
    elmis_sync_nextcall = fields.Datetime(
        string="Next Scheduled Sync",
        compute="_compute_elmis_sync_schedule_status",
    )
    elmis_sync_last_success_at = fields.Datetime(
        string="Last Successful Sync",
        compute="_compute_elmis_sync_schedule_status",
    )

    def set_values(self):
        super().set_values()
        self._apply_elmis_sync_cron_settings()

    def _apply_elmis_sync_cron_settings(self):
        settings = self[-1] if self else self
        cron = self.env["elmis.inventory.sync"]._get_sync_cron()
        if not cron:
            return

        interval_number = max(settings.elmis_sync_interval_number or 1, 1)
        interval_type = settings.elmis_sync_interval_type or "hours"
        reset_nextcall = (
            settings.elmis_sync_cron_active
            and (
                not cron.active
                or cron.interval_number != interval_number
                or cron.interval_type != interval_type
                or not cron.nextcall
            )
        )
        values = {
            "active": bool(settings.elmis_sync_cron_active),
            "interval_number": interval_number,
            "interval_type": interval_type,
            "numbercall": -1,
        }
        if reset_nextcall:
            values["nextcall"] = self.env[
                "elmis.inventory.sync"
            ]._sync_nextcall_from_interval(interval_number, interval_type)
        cron.sudo().write(values)

    def _compute_elmis_sync_schedule_status(self):
        cron = self.env["elmis.inventory.sync"]._get_sync_cron()
        last_success = self.env["elmis.inventory.sync.run"].sudo().search(
            [("operation", "=", "inventory_sync"), ("status", "=", "success")],
            limit=1,
        )
        for settings in self:
            settings.elmis_sync_nextcall = cron.nextcall if cron and cron.active else False
            settings.elmis_sync_last_success_at = (
                last_success.finished_at if last_success else False
            )

    def action_elmis_test_connection(self):
        self.execute()
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
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS Connection Test"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    def action_elmis_sync_inventory(self):
        self.execute()
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
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS Inventory Sync"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }
