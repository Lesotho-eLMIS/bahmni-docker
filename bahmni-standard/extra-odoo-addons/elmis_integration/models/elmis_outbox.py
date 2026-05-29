import json
import uuid
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero


class ElmisOutbox(models.Model):
    _name = "elmis.outbox"
    _description = "eLMIS Outbox Event"
    _order = "created_at desc, id desc"

    message_id = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
        help="Stable idempotency key for this eLMIS transaction.",
    )
    transaction_type = fields.Selection(
        [
            ("DISPENSE", "Dispense"),
            ("ADJUSTMENT", "Adjustment"),
            ("PREPACK", "Prepack"),
            ("EMERGENCY", "Emergency"),
        ],
        required=True,
        default="DISPENSE",
        index=True,
    )
    facility_code = fields.Char(
        string="eLMIS Facility Code",
        required=True,
        index=True,
    )
    program_id = fields.Many2one(
        "elmis.program",
        string="eLMIS Program",
        required=True,
        index=True,
    )
    elmis_orderable_id = fields.Many2one(
        "product.product",
        string="eLMIS Orderable",
        required=True,
        index=True,
        domain=[("is_elmis_product", "=", True)],
    )
    lot_id = fields.Many2one(
        "stock.lot",
        string="Lot",
        index=True,
    )
    source_sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Source Sale Order Line",
        index=True,
        copy=False,
        readonly=True,
        help="Dispensing line that produced this outbox event.",
    )
    source_stock_scrap_id = fields.Many2one(
        "stock.scrap",
        string="Source Scrap",
        index=True,
        copy=False,
        readonly=True,
        help="Move to unserviceable record that produced this outbox event.",
    )
    source_stock_move_id = fields.Many2one(
        "stock.move",
        string="Source Stock Move",
        index=True,
        copy=False,
        readonly=True,
        help="Inventory adjustment move that produced this outbox event.",
    )
    quantity = fields.Float(required=True, digits=(16, 4))
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure", required=True)
    transaction_date = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        help="Actual event time, not delivery time.",
    )
    prescription_ref = fields.Char(index=True)
    adjustment_reason = fields.Char(
        help="eLMIS stock card line item reason name. Dispenses default to Consumed.",
    )
    status = fields.Selection(
        [
            ("PENDING", "Pending"),
            ("SENT", "Sent"),
            ("DELIVERED", "Delivered"),
            ("FAILED", "Failed"),
            ("DLQ", "Dead Letter"),
        ],
        required=True,
        default="PENDING",
        index=True,
    )
    created_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    sent_at = fields.Datetime(readonly=True)
    delivered_at = fields.Datetime(readonly=True)
    error_message = fields.Text(readonly=True)
    attempt_count = fields.Integer(default=0, readonly=True)

    _sql_constraints = [
        (
            "message_id_unique",
            "UNIQUE(message_id)",
            "Each eLMIS outbox message must have a unique message id.",
        ),
        (
            "source_sale_order_line_id_unique",
            "UNIQUE(source_sale_order_line_id)",
            "Each sale order line can create only one eLMIS outbox event.",
        ),
        (
            "source_stock_scrap_id_unique",
            "UNIQUE(source_stock_scrap_id)",
            "Each stock scrap can create only one eLMIS outbox event.",
        ),
        (
            "source_stock_move_id_unique",
            "UNIQUE(source_stock_move_id)",
            "Each stock move can create only one eLMIS outbox event.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("message_id"):
                vals["message_id"] = str(uuid.uuid4())
            if vals.get("transaction_type") == "DISPENSE" and not vals.get("adjustment_reason"):
                vals["adjustment_reason"] = "Consumed"
        return super().create(vals_list)

    def unlink(self):
        raise UserError(_("eLMIS outbox records are permanent and cannot be deleted."))

    @api.model
    def drain_pending_to_elmis(self, limit=50):
        events = self.sudo().search(
            [("status", "in", ("PENDING", "FAILED"))],
            order="created_at asc, id asc",
            limit=limit,
        )
        result = events.submit_to_elmis()
        result["selected"] = len(events)
        return result

    @api.model
    def _cron_drain_pending_to_elmis(self):
        params = self.env["ir.config_parameter"].sudo()
        limit = int(params.get_param("elmis_integration.outbox_batch_limit", "50") or 50)
        return self.drain_pending_to_elmis(limit=limit)

    @api.model
    def action_drain_pending_to_elmis(self):
        result = self.drain_pending_to_elmis()
        return self._get_submission_notification(result)

    def action_submit_to_elmis(self):
        result = self.submit_to_elmis()
        return self._get_submission_notification(result)

    def action_mark_dlq(self):
        candidates = self.filtered(lambda event: event.status in ("FAILED", "PENDING", "SENT"))
        candidates.write({"status": "DLQ"})
        return self._get_status_notification(
            _("Moved %(count)s eLMIS outbox event(s) to dead letter.")
            % {"count": len(candidates)}
        )

    def action_reset_to_pending(self):
        candidates = self.filtered(lambda event: event.status in ("FAILED", "DLQ", "SENT"))
        candidates.write(
            {
                "status": "PENDING",
                "error_message": False,
            }
        )
        return self._get_status_notification(
            _("Reopened %(count)s eLMIS outbox event(s) for retry.")
            % {"count": len(candidates)}
        )

    def submit_to_elmis(self):
        result = {
            "processed": 0,
            "delivered": 0,
            "failed": 0,
            "skipped": 0,
        }
        for event in self:
            if event.status in ("DELIVERED", "DLQ"):
                result["skipped"] += 1
                continue

            result["processed"] += 1
            if event._submit_one_to_elmis():
                result["delivered"] += 1
            else:
                result["failed"] += 1
        return result

    @api.model
    def _get_submission_notification(self, result):
        message = _(
            "Processed %(processed)s eLMIS outbox event(s): %(delivered)s delivered, "
            "%(failed)s failed, %(skipped)s skipped."
        ) % result
        return self._get_status_notification(
            message,
            notification_type="success" if not result.get("failed") else "warning",
            sticky=bool(result.get("failed")),
        )

    @api.model
    def _get_status_notification(self, message, notification_type="success", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS Outbox Submission"),
                "message": message,
                "type": notification_type,
                "sticky": sticky,
            },
        }

    @api.model
    def get_health_summary(self):
        counts = {
            "pending": 0,
            "sent": 0,
            "delivered": 0,
            "failed": 0,
            "dlq": 0,
            "retryable": 0,
        }
        for group in self.read_group([], ["status"], ["status"]):
            key = (group.get("status") or "").lower()
            if key in counts:
                counts[key] = group["status_count"]
        counts["retryable"] = counts["pending"] + counts["failed"]

        oldest_retryable = self.search(
            [("status", "in", ("PENDING", "FAILED"))],
            order="created_at asc, id asc",
            limit=1,
        )
        last_delivered = self.search(
            [("status", "=", "DELIVERED"), ("delivered_at", "!=", False)],
            order="delivered_at desc, id desc",
            limit=1,
        )
        last_failed = self.search(
            [("status", "=", "FAILED")],
            order="write_date desc, id desc",
            limit=1,
        )

        oldest_created_at = oldest_retryable.created_at if oldest_retryable else False
        return {
            **counts,
            "oldest_retryable_at": self._datetime_to_iso(oldest_created_at),
            "oldest_retryable_age_seconds": self._age_seconds(oldest_created_at),
            "last_delivered_at": self._datetime_to_iso(last_delivered.delivered_at),
            "last_error": last_failed.error_message if last_failed else False,
        }

    @api.model
    def _age_seconds(self, value):
        if not value:
            return False
        value = fields.Datetime.to_datetime(value)
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        return max(int((now - value).total_seconds()), 0)

    @api.model
    def _datetime_to_iso(self, value):
        if not value:
            return False
        value = fields.Datetime.to_datetime(value)
        if isinstance(value, datetime):
            return value.isoformat() + "Z"
        return False

    def _submit_one_to_elmis(self):
        self.ensure_one()
        try:
            self.write(
                {
                    "status": "SENT",
                    "sent_at": fields.Datetime.now(),
                    "attempt_count": self.attempt_count + 1,
                    "error_message": False,
                }
            )
            self._post_public_stock_event()
        except Exception as exc:
            self.write(
                {
                    "status": "FAILED",
                    "error_message": str(exc),
                }
            )
            return False

        self.write(
            {
                "status": "DELIVERED",
                "delivered_at": fields.Datetime.now(),
                "error_message": False,
            }
        )
        return True

    def _post_public_stock_event(self):
        self.ensure_one()
        sync_service = self.env["elmis.inventory.sync"]
        params = sync_service._get_elmis_config()
        token = params["api_token"] or sync_service._get_elmis_access_token(params)
        payload = json.dumps(self.to_public_stock_event_payload()).encode("utf-8")

        return sync_service._elmis_request_json(
            params["base_url"],
            "public/stockEvents",
            method="POST",
            headers={
                "Authorization": "Bearer %s" % token,
                "Content-Type": "application/json",
            },
            data=payload,
        )

    def to_public_stock_event_payload(self):
        self.ensure_one()
        product_code = self.elmis_orderable_id.elmis_product_code
        if not product_code:
            raise UserError(_("Outbox event is missing an eLMIS product code."))
        if not self.program_id.code:
            raise UserError(_("Outbox event is missing an eLMIS program code."))

        return {
            "facility": self.facility_code,
            "program": self.program_id.code,
            "signature": "Odoo",
            "documentNumber": self.prescription_ref or self.message_id,
            "items": [
                {
                    "orderable": product_code,
                    "lot": self._get_public_lot_code(),
                    "occurredDate": self._get_public_occurred_date(),
                    "quantity": self._get_public_quantity(),
                    "reason": self.adjustment_reason or "Consumed",
                }
            ],
        }

    def _get_public_lot_code(self):
        self.ensure_one()
        if not self.lot_id:
            return None
        return self.lot_id.elmis_lot_number or self.lot_id.name

    def _get_public_occurred_date(self):
        self.ensure_one()
        transaction_date = fields.Datetime.to_datetime(self.transaction_date)
        return fields.Date.to_string(transaction_date.date())

    def _get_public_quantity(self):
        self.ensure_one()
        rounded_quantity = int(round(self.quantity))
        if not float_is_zero(self.quantity - rounded_quantity, precision_digits=6):
            raise UserError(_("eLMIS public stock events require whole-number quantities."))
        return rounded_quantity
