from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import SavepointCase, tagged


@tagged("post_install", "-at_install")
class TestPatientRegistration(SavepointCase):
    def values(self, **extra):
        return {
            "first_name": " Registration ",
            "middle_name": "Test",
            "last_name": "Patient",
            "sex": "F",
            "birth_date": "2000-02-29",
            **extra,
        }

    def test_registered_patient_is_available_for_prescriptions(self):
        result = self.env["res.partner"].register_patient(self.values(
            street="House 1", street2="Sector 2", city="Maseru", zip="100",
            district_name="Maseru", state_name="Test State",
            phone="+266 2222 3333", mobile="5888 9999", email="patient@example.com",
            birth_time="09:30",
        ))
        patient = self.env["res.partner"].browse(result["id"])
        self.assertEqual(patient.name, "Registration Test Patient")
        self.assertTrue(patient.is_patient)
        self.assertEqual(patient.customer_rank, 1)
        self.assertEqual(patient.ref, result["identifier"])
        self.assertTrue(patient.ref.startswith("ABC"))
        self.assertEqual(patient.patient_first_name, "Registration")
        self.assertEqual(patient.patient_birth_time, "09:30")
        self.assertEqual(patient.age, relativedelta(fields.Date.context_today(patient), patient.patient_birth_date).years)
        self.assertEqual(patient.street2, "Sector 2")
        self.assertEqual(patient.patient_district_name, "Maseru")
        self.assertEqual(patient.patient_state_name, "Test State")
        self.assertEqual(patient.mobile, "5888 9999")
        matches = self.env["res.partner"].with_context(show_patient_prescription_details=True).name_search(patient.ref)
        self.assertIn(patient.id, [item[0] for item in matches])

    def test_manual_identifier_and_duplicate_including_archived(self):
        values = self.values(manual_identifier=True, identifier="REG-TEST-UNIQUE")
        result = self.env["res.partner"].register_patient(values)
        self.assertEqual(result["identifier"], "REG-TEST-UNIQUE")
        self.env["res.partner"].browse(result["id"]).active = False
        with self.assertRaises(ValidationError):
            self.env["res.partner"].register_patient(values)

    def test_generated_identifiers_are_distinct(self):
        one = self.env["res.partner"].register_patient(self.values())
        two = self.env["res.partner"].register_patient(self.values())
        self.assertNotEqual(one["identifier"], two["identifier"])

    def test_age_only_and_newborn(self):
        result = self.env["res.partner"].register_patient(self.values(birth_date="", age_years="0", age_months="0", age_days="0"))
        patient = self.env["res.partner"].browse(result["id"])
        self.assertEqual(patient.patient_birth_date, fields.Date.context_today(patient))
        self.assertTrue(patient.patient_birth_estimated)
        self.assertEqual(patient.age, 0)

    def test_invalid_inputs_are_rejected_without_creating_patient(self):
        cases = [
            {"first_name": " "}, {"sex": ""}, {"birth_date": "2999-01-01"},
            {"birth_date": "bad-date"}, {"birth_date": "", "age_months": "12"},
            {"birth_date": "", "age_years": "-1"}, {"birth_date": ""},
            {"email": "invalid-email"}, {"birth_time": "25:00"}, {"phone": "not-a-number"},
            {"manual_identifier": True, "identifier": " "},
        ]
        count = self.env["res.partner"].search_count([])
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                self.env["res.partner"].register_patient(self.values(**case))
        self.assertEqual(self.env["res.partner"].search_count([]), count)

    def test_public_user_cannot_register_patients(self):
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.env.ref("base.public_user")).register_patient(self.values())

    def test_edit_existing_patient_preserves_identity_and_hidden_fields(self):
        result = self.env["res.partner"].register_patient(self.values(street="Existing house", state_name="Existing state"))
        patient = self.env["res.partner"].browse(result["id"])
        before_count = self.env["res.partner"].search_count([])
        values = patient.get_patient_registration()["values"]
        self.assertEqual(values["first_name"], "Registration")
        self.assertEqual(values["birth_date"], "2000-02-29")
        values.update(phone="+266 5555 0000", email="edited@example.com", ref="MUST-NOT-CHANGE", is_company=True)
        edited = patient.update_patient_registration(values)
        self.assertEqual(edited["id"], result["id"])
        self.assertEqual(patient.ref, result["identifier"])
        self.assertEqual(patient.street, "Existing house")
        self.assertEqual(patient.patient_state_name, "Existing state")
        self.assertEqual(patient.phone, "+266 5555 0000")
        self.assertEqual(patient.email, "edited@example.com")
        self.assertFalse(patient.is_company)
        self.assertEqual(self.env["res.partner"].search_count([]), before_count)

    def test_legacy_patient_edit_does_not_invent_birth_date(self):
        patient = self.env["res.partner"].create({"name": "Legacy Patient Name", "ref": "LEGACY-EDIT-TEST", "sex": "M", "age": 46})
        values = patient.get_patient_registration()["values"]
        self.assertEqual(values["first_name"], "Legacy")
        self.assertEqual(values["last_name"], "Patient Name")
        self.assertEqual(values["birth_date"], "")
        values["phone"] = "5555 1234"
        patient.update_patient_registration(values)
        self.assertEqual(patient.age, 46)
        self.assertFalse(patient.patient_birth_date)
        self.assertEqual(patient.name, "Legacy Patient Name")
        self.assertEqual(patient.ref, "LEGACY-EDIT-TEST")

    def test_legacy_patient_without_demographics_can_update_contacts(self):
        patient = self.env["res.partner"].create({"name": "Unknown Demographics"})
        values = patient.get_patient_registration()["values"]
        values["email"] = "updated@example.com"
        patient.update_patient_registration(values)
        self.assertFalse(patient.sex)
        self.assertFalse(patient.patient_birth_date)
        self.assertEqual(patient.email, "updated@example.com")

    def test_invalid_edit_does_not_change_patient(self):
        result = self.env["res.partner"].register_patient(self.values(phone="5555 1234"))
        patient = self.env["res.partner"].browse(result["id"])
        values = patient.get_patient_registration()["values"]
        values.update(phone="invalid", first_name="Should not save")
        with self.assertRaises(ValidationError):
            patient.update_patient_registration(values)
        self.assertEqual(patient.phone, "5555 1234")
        self.assertEqual(patient.patient_first_name, "Registration")

    def test_public_user_cannot_load_or_update_patient(self):
        patient = self.env["res.partner"].create({"name": "Private Patient"})
        public_patient = patient.with_user(self.env.ref("base.public_user"))
        with self.assertRaises(AccessError):
            public_patient.get_patient_registration()
        with self.assertRaises(AccessError):
            public_patient.update_patient_registration(self.values())

    def test_prescription_options_include_explicitly_selected_legacy_patient(self):
        self.env["res.partner"].register_patient(self.values())
        patient = self.env["res.partner"].create({"name": "Legacy Prescription Patient", "is_patient": False, "weight": 60})
        options = self.env["sale.order"].get_prescription_create_form_options(patient_id=patient.id)
        selected = next(item for item in options["patients"] if item["id"] == patient.id)
        self.assertIn(patient.name, selected["name"])
        self.assertTrue(selected["weight"])
