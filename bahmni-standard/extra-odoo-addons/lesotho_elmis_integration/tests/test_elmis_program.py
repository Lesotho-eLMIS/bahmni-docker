from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestElmisProgram(TransactionCase):
    def test_get_or_create_from_elmis_creates_program_by_code(self):
        program = self.env["elmis.program"].get_or_create_from_elmis(
            "tb",
            name="Tuberculosis",
            elmis_program_id="program-tb-id",
        )

        self.assertEqual(program.code, "tb")
        self.assertEqual(program.name, "Tuberculosis")
        self.assertEqual(program.elmis_program_id, "program-tb-id")

    def test_get_or_create_from_elmis_updates_existing_program(self):
        program = self.env["elmis.program"].create(
            {
                "code": "ncd",
                "name": "NCD",
            }
        )

        resolved = self.env["elmis.program"].get_or_create_from_elmis(
            "ncd",
            name="Non-Communicable Diseases",
            elmis_program_id="program-ncd-id",
        )

        self.assertEqual(resolved, program)
        self.assertEqual(program.name, "Non-Communicable Diseases")
        self.assertEqual(program.elmis_program_id, "program-ncd-id")
