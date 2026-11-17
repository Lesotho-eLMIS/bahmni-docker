import re
from datetime import time

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import MissingError, ValidationError
from odoo.tools import email_normalize


class PatientRegistration(models.Model):
    _inherit = "res.partner"

    patient_district_name = fields.Char(string="District", copy=False)
    patient_state_name = fields.Char(string="State", copy=False)

    patient_first_name = fields.Char(string="First Name", copy=False)
    patient_middle_name = fields.Char(string="Middle Name", copy=False)
    patient_last_name = fields.Char(string="Last Name", copy=False)
    patient_birth_date = fields.Date(string="Date of Birth", copy=False)
    patient_birth_estimated = fields.Boolean(string="Estimated Date of Birth", copy=False)
    patient_birth_time = fields.Char(string="Birth Time", copy=False)

    @api.model
    def patient_registration_options(self, patient_id=None):
        if patient_id:
            patient = self.browse(patient_id)
            patient.check_access_rights("read")
            patient.check_access_rule("read")
        else:
            self.check_access_rights("create")
        sequence = self.env.ref("lesotho_sale.sequence_patient_registration")
        return {
            "today": fields.Date.to_string(fields.Date.context_today(self)),
            "identifier_prefix": sequence.prefix or "",
        }

    def get_patient_registration(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        if not self.exists():
            raise MissingError(_("This patient no longer exists."))
        parts = (self.name or "").split(maxsplit=1)
        first_name = self.patient_first_name or (parts[0] if parts else "")
        last_name = self.patient_last_name or (parts[1] if len(parts) > 1 and not self.patient_first_name else "")
        age = relativedelta(fields.Date.context_today(self), self.patient_birth_date) if self.patient_birth_date else None
        return {
            "id": self.id,
            "name": self.name,
            "identifier": self.ref or "",
            "values": {
                "first_name": first_name,
                "middle_name": self.patient_middle_name or "",
                "last_name": last_name,
                "sex": self.sex or "",
                "age_years": str(age.years) if age is not None else str(self.age) if self.age else "",
                "age_months": str(age.months) if age is not None else "",
                "age_days": str(age.days) if age is not None else "",
                "birth_date": fields.Date.to_string(self.patient_birth_date) or "",
                "estimated": self.patient_birth_estimated,
                "birth_time": self.patient_birth_time or "",
                "street2": self.street2 or "",
                "city": self.city or self.village_id.name or "",
                "district_name": self.patient_district_name or self.district_id.name or "",
                "zip": self.zip or "",
                "phone": self.phone or "",
                "mobile": self.mobile or "",
                "email": self.email or "",
            },
        }

    def update_patient_registration(self, values):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if not self.exists():
            raise MissingError(_("This patient no longer exists."))
        self.write(self._prepare_patient_registration_values(values, patient=self))
        return {"id": self.id, "name": self.name, "identifier": self.ref or ""}

    @api.model
    def _prepare_patient_registration_values(self, values, patient=None):
        first_name = (values.get("first_name") or "").strip()
        middle_name = (values.get("middle_name") or "").strip()
        last_name = (values.get("last_name") or "").strip()
        if not first_name:
            raise ValidationError(_("Enter the patient's first name."))
        sex = values.get("sex") or False
        if sex not in dict(self._fields["sex"].selection) and (not patient or sex or patient.sex):
            raise ValidationError(_("Select the patient's gender."))
        today = fields.Date.context_today(self)
        estimated = bool(values.get("estimated"))
        try:
            birth_date = fields.Date.to_date(values.get("birth_date"))
        except (TypeError, ValueError):
            raise ValidationError(_("Enter a valid date of birth."))
        if not birth_date:
            age_parts = [values.get(key) for key in ("age_years", "age_months", "age_days")]
            if all(part in (None, "") for part in age_parts):
                if not patient or patient.patient_birth_date or patient.age:
                    raise ValidationError(_("Enter an age or date of birth."))
            try:
                if any(part not in (None, "") and not re.fullmatch(r"\d+", str(part)) for part in age_parts):
                    raise ValueError()
                years, months, days = [int(part or 0) for part in age_parts]
                if years < 0 or not 0 <= months <= 11 or not 0 <= days <= 30:
                    raise ValueError()
                birth_date = today - relativedelta(years=years, months=months, days=days)
            except (ValueError, OverflowError):
                raise ValidationError(_("Enter a valid age: months 0–11, days 0–30, and non-negative whole years."))
            legacy_age_unchanged = patient and not patient.patient_birth_date and years == patient.age and not months and not days
            if legacy_age_unchanged or all(part in (None, "") for part in age_parts):
                birth_date = False
            else:
                estimated = True
        if birth_date and birth_date > today:
            raise ValidationError(_("Date of birth cannot be in the future."))
        birth_time = (values.get("birth_time") or "").strip()
        if birth_time:
            try:
                time.fromisoformat(birth_time)
            except ValueError:
                raise ValidationError(_("Enter a valid birth time."))
        email = (values.get("email") or "").strip()
        if email and not email_normalize(email):
            raise ValidationError(_("Enter a valid email address."))
        for key in ("phone", "mobile"):
            number = (values.get(key) or "").strip()
            if number and (not re.fullmatch(r"\+?[\d\s()./-]+", number) or not re.search(r"\d", number)):
                raise ValidationError(_("Enter a valid phone number."))

        country = (patient.country_id if patient else False) or self.env.ref("base.ls", raise_if_not_found=False) or self.env.company.country_id
        state_name = (patient.patient_state_name or patient.state_id.name or "") if patient else (values.get("state_name") or "").strip()
        district_name = (values.get("district_name") or "").strip()
        country_domain = [("country_id", "=", country.id)] if country else []
        states = self.env["res.country.state"].search(country_domain + [("name", "=ilike", state_name)], limit=2) if state_name else self.env["res.country.state"]
        state = states if len(states) == 1 else self.env["res.country.state"]
        district_domain = country_domain + [("name", "=ilike", district_name)]
        if state:
            district_domain.append(("state_id", "=", state.id))
        districts = self.env["state.district"].search(district_domain, limit=2) if district_name else self.env["state.district"]
        district = districts if len(districts) == 1 else self.env["state.district"]
        # Preserve entered text even when address reference data has not been imported.
        if not state_name:
            state = district.state_id
        city = (values.get("city") or "").strip()
        village_domain = [("name", "=ilike", city)]
        if district:
            village_domain.append(("district_id", "=", district.id))
        if state:
            village_domain.append(("state_id", "=", state.id))
        if country:
            village_domain.append(("country_id", "=", country.id))
        villages = self.env["village.village"].search(village_domain, limit=2) if city else self.env["village.village"]
        village = villages if len(villages) == 1 else self.env["village.village"]
        prepared = {
            "name": " ".join(part for part in (first_name, middle_name, last_name) if part),
            "patient_first_name": first_name,
            "patient_middle_name": middle_name,
            "patient_last_name": last_name,
            "is_patient": True,
            "customer_rank": max(patient.customer_rank, 1) if patient else 1,
            "sex": sex,
            "age": relativedelta(today, birth_date).years if birth_date else patient.age,
            "patient_birth_date": birth_date,
            "patient_birth_estimated": estimated,
            "patient_birth_time": birth_time,
            "street2": (values.get("street2") or "").strip(),
            "city": city,
            "zip": (values.get("zip") or "").strip(),
            "country_id": country.id,
            "state_id": state.id,
            "district_id": district.id,
            "patient_district_name": district_name,
            "patient_state_name": state_name or state.name,
            "village_id": village.id,
            "phone": (values.get("phone") or "").strip(),
            "mobile": (values.get("mobile") or "").strip(),
            "email": email,
        }
        if patient:
            # Fields removed from registration remain untouched on existing records.
            for key in ("country_id", "state_id", "patient_state_name"):
                prepared.pop(key)
            if city == (patient.city or patient.village_id.name or "") and district_name == (patient.patient_district_name or patient.district_id.name or ""):
                for key in ("district_id", "village_id"):
                    prepared.pop(key)
        else:
            prepared.update({"is_company": False, "street": (values.get("street") or "").strip()})
        return prepared

    @api.model
    def register_patient(self, values):
        """Create a patient using ordinary contact permissions and record rules."""
        self.check_access_rights("create")
        prepared = self._prepare_patient_registration_values(values)
        identifier = (values.get("identifier") or "").strip() if values.get("manual_identifier") else ""
        if values.get("manual_identifier") and not identifier:
            raise ValidationError(_("Enter the patient identifier."))
        if identifier and self.with_context(active_test=False).search_count([("ref", "=", identifier)]):
            raise ValidationError(_("This patient identifier is already in use."))
        if not identifier:
            sequence = self.env.ref("lesotho_sale.sequence_patient_registration")
            identifier = sequence.next_by_id()
            while self.with_context(active_test=False).search_count([("ref", "=", identifier)]):
                identifier = sequence.next_by_id()

        prepared["ref"] = identifier
        patient = self.create(prepared)
        return {"id": patient.id, "name": patient.name, "identifier": patient.ref}
