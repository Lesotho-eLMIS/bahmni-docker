from odoo import api, fields, models


class ElmisProgram(models.Model):
    _name = "elmis.program"
    _description = "eLMIS Program"
    _order = "code"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    elmis_program_id = fields.Char(
        string="eLMIS Program UUID",
        index=True,
        copy=False,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "elmis_program_code_unique",
            "UNIQUE(code)",
            "An eLMIS program code must be unique.",
        ),
    ]

    @api.model
    def get_or_create_from_elmis(self, code, name=None, elmis_program_id=None):
        code = (code or "").strip()
        if not code:
            return self.browse()

        program = self.search([("code", "=", code)], limit=1)
        values = {
            "name": name or code.upper(),
            "code": code,
        }
        if elmis_program_id:
            values["elmis_program_id"] = elmis_program_id

        if program:
            program.write({key: value for key, value in values.items() if value})
            return program
        return self.create(values)
