from odoo import api, models, _
from odoo.exceptions import UserError

class QcGateHelper:
    """Pure Python mixin helper (no Odoo model)."""

    _QC_PASSED_STATES = {"passed", "pass", "success", "done", "validated"}
    _QC_FAILED_STATES = {"failed", "fail", "rejected", "cancel"}

    def _get_qc_inspection_model(self):
        candidates = ["qc.inspection", "quality.control.inspection", "quality.inspection"]
        for m in candidates:
            if m in self.env:
                return self.env[m]
        raise UserError(_("QC Gate: Could not find an OCA QC inspection model."))

    def _domain_for_inspections_linked_to(self, insp_model, record):
        model_name = record._name
        rec_id = record.id

        if "res_model" in insp_model._fields and "res_id" in insp_model._fields:
            return [("res_model", "=", model_name), ("res_id", "=", rec_id)]

        if "object_id" in insp_model._fields:
            return [("object_id", "=", f"{model_name},{rec_id}")]

        if "ref_model" in insp_model._fields and "ref_id" in insp_model._fields:
            return [("ref_model", "=", model_name), ("ref_id", "=", rec_id)]

        raise UserError(_("QC Gate: Can't determine inspection link fields."))

    def _is_inspection_passed(self, inspection):
        if "state" in inspection._fields:
            st = (inspection.state or "").strip().lower()
            if st in self._QC_PASSED_STATES:
                return True
            if st in self._QC_FAILED_STATES:
                return False

        for f in ("is_passed", "passed", "success"):
            if f in inspection._fields:
                return bool(getattr(inspection, f))

        return False

    def _check_qc_gate(self, *, require_any=True, error_title=None):
        self.ensure_one()
        insp_model = self._get_qc_inspection_model()
        domain = self._domain_for_inspections_linked_to(insp_model, self)
        inspections = insp_model.search(domain)

        if require_any and not inspections:
            raise UserError(_(
                "%s\n\nNo quality inspection found for this document.\n"
                "Create/trigger an inspection and complete it before proceeding."
            ) % (error_title or _("Quality Check Required")))

        not_passed = inspections.filtered(lambda r: not self._is_inspection_passed(r))
        if not_passed:
            raise UserError(_(
                "%s\n\nQuality inspection(s) exist but are not PASSED.\n"
                "Open the inspections, complete all lines, and ensure status is PASSED."
            ) % (error_title or _("Quality Check Required")))
