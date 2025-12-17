from odoo import _
from odoo.exceptions import UserError

_QC_PASSED_STATES = {"passed", "pass", "success", "done", "validated"}
_QC_FAILED_STATES = {"failed", "fail", "rejected", "cancel"}

def _get_inspection_model(env):
    # Try common OCA QC inspection model names
    candidates = ["qc.inspection", "quality.control.inspection", "quality.inspection"]
    for m in candidates:
        if m in env:
            return env[m]
    raise UserError(_(
        "QC Gate: Could not find an OCA QC inspection model. "
        "Check which inspection model your OCA QC modules installed."
    ))

def _domain_for_record(insp_model, record):
    model_name = record._name
    rec_id = record.id

    # Common link pattern: res_model/res_id
    if "res_model" in insp_model._fields and "res_id" in insp_model._fields:
        return [("res_model", "=", model_name), ("res_id", "=", rec_id)]

    # Common link pattern: reference stored as 'model,id'
    if "object_id" in insp_model._fields:
        return [("object_id", "=", f"{model_name},{rec_id}")]

    # Alternative link pattern
    if "ref_model" in insp_model._fields and "ref_id" in insp_model._fields:
        return [("ref_model", "=", model_name), ("ref_id", "=", rec_id)]

    raise UserError(_(
        "QC Gate: Can't determine how inspections are linked to %s. "
        "Open an Inspection in developer mode and identify the linking fields."
    ) % model_name)

def _is_passed(inspection):
    # Prefer state field if exists
    if "state" in inspection._fields:
        st = (inspection.state or "").strip().lower()
        if st in _QC_PASSED_STATES:
            return True
        if st in _QC_FAILED_STATES:
            return False

    # Fallback booleans if any exist
    for f in ("is_passed", "passed", "success"):
        if f in inspection._fields:
            return bool(getattr(inspection, f))

    return False  # safe default

def check_qc_gate(record, *, require_any=True, error_title=None):
    """Raise UserError if inspections missing or not passed."""
    record.ensure_one()
    insp_model = _get_inspection_model(record.env)
    domain = _domain_for_record(insp_model, record)
    inspections = insp_model.search(domain)

    if require_any and not inspections:
        raise UserError(_(
            "%s\n\nNo quality inspection found for this document.\n"
            "Create/trigger an inspection and complete it before proceeding."
        ) % (error_title or _("Quality Check Required")))

    not_passed = inspections.filtered(lambda r: not _is_passed(r))
    if not_passed:
        raise UserError(_(
            "%s\n\nQuality inspection(s) exist but are not PASSED.\n"
            "Open the inspection(s), complete all lines, and ensure status is PASSED."
        ) % (error_title or _("Quality Check Required")))
