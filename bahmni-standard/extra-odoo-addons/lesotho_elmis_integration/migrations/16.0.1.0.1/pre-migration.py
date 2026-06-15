CONFIG_XML_IDS = (
    "ir_config_parameter_elmis_base_url",
    "ir_config_parameter_elmis_program_codes",
    "ir_config_parameter_elmis_sync_cron_active",
    "ir_config_parameter_elmis_mirror_location_ids",
    "ir_config_parameter_elmis_sync_interval_number",
    "ir_config_parameter_elmis_sync_interval_type",
)


def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM ir_model_data
        WHERE module = 'lesotho_elmis_integration'
          AND model = 'ir.config_parameter'
          AND name IN %s
        """,
        (CONFIG_XML_IDS,),
    )
