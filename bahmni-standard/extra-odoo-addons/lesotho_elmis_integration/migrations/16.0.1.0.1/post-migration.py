from odoo import SUPERUSER_ID, api

from odoo.addons.lesotho_elmis_integration.hooks import DEFAULT_CONFIG


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    params = env["ir.config_parameter"].sudo()
    existing_keys = set(
        params.search([("key", "in", list(DEFAULT_CONFIG))]).mapped("key")
    )
    for key, value in DEFAULT_CONFIG.items():
        if key not in existing_keys:
            params.set_param(key, value)
