from odoo import SUPERUSER_ID, api


DEFAULT_CONFIG = {
    "lesotho_elmis_integration.base_url": "https://dev.elmis.gov.ls/api/",
    "lesotho_elmis_integration.program_codes": (
        "art,epi,fp,lab,medical_supplies,nutrition,ois,oral,tb,other,em"
    ),
    "lesotho_elmis_integration.sync_cron_active": "False",
    "lesotho_elmis_integration.mirror_location_ids": "",
    "lesotho_elmis_integration.sync_interval_number": "6",
    "lesotho_elmis_integration.sync_interval_type": "hours",
}


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    params = env["ir.config_parameter"].sudo()
    existing_keys = set(
        params.search([("key", "in", list(DEFAULT_CONFIG))]).mapped("key")
    )
    for key, value in DEFAULT_CONFIG.items():
        if key not in existing_keys:
            params.set_param(key, value)
