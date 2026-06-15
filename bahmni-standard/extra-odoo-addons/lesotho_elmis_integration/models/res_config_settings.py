import os

from odoo import _, fields, models

CONFIG_PREFIX = "lesotho_elmis_integration."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    elmis_base_url = fields.Char(
        string="eLMIS Base URL",
        config_parameter="lesotho_elmis_integration.base_url",
        default=lambda self: os.environ.get(
            "ELMIS_BASE_URL", "https://dev.elmis.gov.ls/api/"
        ),
    )
    elmis_program_codes = fields.Char(
        string="eLMIS Program Codes",
        config_parameter="lesotho_elmis_integration.program_codes",
        default=lambda self: os.environ.get("ELMIS_PROGRAM_CODES", "art,em,lab,ois"),
        help="Comma-separated eLMIS program codes to include in facility inventory sync.",
    )
    elmis_username = fields.Char(
        string="eLMIS Service Account Username",
        config_parameter="lesotho_elmis_integration.username",
        default=lambda self: os.environ.get("ELMIS_USERNAME"),
    )
    elmis_password = fields.Char(
        string="eLMIS Service Account Password",
        config_parameter="lesotho_elmis_integration.password",
        default=lambda self: os.environ.get("ELMIS_PASSWORD"),
    )
    elmis_api_token = fields.Char(
        string="eLMIS API Token",
        config_parameter="lesotho_elmis_integration.api_token",
        default=lambda self: os.environ.get("ELMIS_API_TOKEN"),
        help="Optional token. If provided, it takes precedence over username/password.",
    )
    elmis_mirror_location_id = fields.Many2one(
        "stock.location",
        string="Legacy eLMIS Mirror Location",
        config_parameter="lesotho_elmis_integration.mirror_location_id",
        domain=[
            ("usage", "=", "internal"),
            ("active", "=", True),
            ("elmis_facility_code", "!=", False),
        ],
        help=(
            "Legacy single-location setting. Use eLMIS Mirror Locations for new deployments."
        ),
    )
    elmis_mirror_location_ids = fields.Many2many(
        "stock.location",
        string="eLMIS Mirror Locations",
        domain=[
            ("usage", "=", "internal"),
            ("active", "=", True),
            ("elmis_facility_code", "!=", False),
        ],
        help=(
            "Internal Odoo stock locations that mirror configured eLMIS "
            "facility/service point inventory."
        ),
    )
    elmis_sync_cron_active = fields.Boolean(
        string="Run Scheduled eLMIS Sync",
        config_parameter="lesotho_elmis_integration.sync_cron_active",
        default=lambda self: os.environ.get("ELMIS_SYNC_CRON_ACTIVE", "False")
        == "True",
        help="Automatically sync the configured eLMIS facility inventory on a schedule.",
    )
    elmis_sync_interval_number = fields.Integer(
        string="Sync Every",
        config_parameter="lesotho_elmis_integration.sync_interval_number",
        default=lambda self: int(os.environ.get("ELMIS_SYNC_INTERVAL_NUMBER", "6")),
    )
    elmis_sync_interval_type = fields.Selection(
        [
            ("minutes", "Minutes"),
            ("hours", "Hours"),
            ("days", "Days"),
        ],
        string="Sync Interval Unit",
        config_parameter="lesotho_elmis_integration.sync_interval_type",
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
        previous_values = self._get_existing_elmis_config_values()
        super().set_values()
        self._preserve_elmis_scalar_config_values(previous_values)
        self._set_elmis_mirror_location_ids(previous_values)
        self._apply_elmis_sync_cron_settings()

    def get_values(self):
        values = super().get_values()
        sync = self.env["elmis.inventory.sync"]
        cron = sync._get_sync_cron()
        for field_name, key in [
            ("elmis_base_url", "base_url"),
            ("elmis_program_codes", "program_codes"),
            ("elmis_username", "username"),
            ("elmis_password", "password"),
            ("elmis_api_token", "api_token"),
        ]:
            if not values.get(field_name):
                values[field_name] = sync._get_config_param(key)

        location_ids = sync._split_location_ids(sync._get_config_param("mirror_location_ids"))
        if not location_ids:
            legacy_location_id = sync._get_config_param("mirror_location_id")
            location_ids = sync._split_location_ids(legacy_location_id)
            if legacy_location_id and not values.get("elmis_mirror_location_id"):
                values["elmis_mirror_location_id"] = int(legacy_location_id)
        if location_ids:
            self._write_mirror_location_params(location_ids)
        values["elmis_mirror_location_ids"] = [(6, 0, location_ids)]
        if cron:
            values.update(
                {
                    "elmis_sync_cron_active": bool(cron.active),
                    "elmis_sync_interval_number": cron.interval_number,
                    "elmis_sync_interval_type": cron.interval_type,
                }
            )
        return values

    def _get_existing_elmis_config_values(self):
        params = self.env["ir.config_parameter"].sudo()
        keys = [
            "base_url",
            "program_codes",
            "username",
            "password",
            "api_token",
            "mirror_location_id",
            "mirror_location_ids",
            "sync_cron_active",
            "sync_interval_number",
            "sync_interval_type",
        ]
        return {
            key: params.get_param("%s%s" % (CONFIG_PREFIX, key))
            for key in keys
        }

    def _preserve_elmis_scalar_config_values(self, previous_values):
        settings = self[-1] if self else self
        params = self.env["ir.config_parameter"].sudo()
        for field_name, key in [
            ("elmis_base_url", "base_url"),
            ("elmis_program_codes", "program_codes"),
            ("elmis_username", "username"),
            ("elmis_password", "password"),
            ("elmis_api_token", "api_token"),
        ]:
            previous_value = previous_values.get(key)
            current_value = settings[field_name]
            default_value = self._get_elmis_field_default(field_name)

            if previous_value and current_value in (None, False, ""):
                params.set_param("%s%s" % (CONFIG_PREFIX, key), previous_value)
                continue

            # Action buttons can execute sparse transient settings records. In that
            # case Odoo field defaults must not reset an already configured site.
            if (
                previous_value
                and default_value not in (None, False, "")
                and current_value == default_value
                and current_value != previous_value
            ):
                params.set_param("%s%s" % (CONFIG_PREFIX, key), previous_value)

    def _get_elmis_field_default(self, field_name):
        field = self._fields[field_name]
        default = field.default
        if callable(default):
            return default(self)
        return default

    def _set_elmis_mirror_location_ids(self, previous_values=None):
        settings = self[-1] if self else self
        previous_values = previous_values or self._get_existing_elmis_config_values()
        location_ids = settings.elmis_mirror_location_ids.ids
        if not location_ids and settings.elmis_mirror_location_id:
            location_ids = [settings.elmis_mirror_location_id.id]
        if not location_ids:
            sync = self.env["elmis.inventory.sync"]
            location_ids = sync._split_location_ids(
                previous_values.get("mirror_location_ids")
                or previous_values.get("mirror_location_id")
            )
        self._write_mirror_location_params(location_ids)

    def _write_mirror_location_params(self, location_ids):
        params = self.env["ir.config_parameter"].sudo()
        location_ids = [int(location_id) for location_id in location_ids if location_id]
        values = {
            "mirror_location_ids": ",".join(
                str(location_id) for location_id in location_ids
            ),
            "mirror_location_id": str(location_ids[0]) if location_ids else "",
        }
        for key, value in values.items():
            param_key = "%s%s" % (CONFIG_PREFIX, key)
            if params.get_param(param_key) != value:
                params.set_param(param_key, value)

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
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(
            "%ssync_cron_active" % CONFIG_PREFIX,
            "True" if cron.active else "False",
        )
        params.set_param(
            "%ssync_interval_number" % CONFIG_PREFIX,
            str(cron.interval_number),
        )
        params.set_param(
            "%ssync_interval_type" % CONFIG_PREFIX,
            cron.interval_type,
        )

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
            "eLMIS connection successful. Facilities: %(facility)s, programs: "
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
