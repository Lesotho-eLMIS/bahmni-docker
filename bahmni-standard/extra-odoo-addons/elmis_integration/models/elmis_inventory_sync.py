import base64
import json
import logging
from datetime import timedelta
from urllib import error, parse, request

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config
from odoo.tools.float_utils import float_compare


_logger = logging.getLogger(__name__)


class ElmisInventorySync(models.Model):
    _name = "elmis.inventory.sync"
    _description = "eLMIS Inventory Sync Service"

    @api.model
    def sync_configured_facility_inventory(self):
        params = self._get_elmis_config()
        location = self._get_configured_mirror_location()

        return self.sync_facility_inventory(location.elmis_facility_code, params["program_codes"])

    @api.model
    def sync_configured_facility_inventory_with_run(self):
        run = self._create_sync_run("inventory_sync")
        try:
            result = self.sync_configured_facility_inventory()
            run.mark_success(result)
            return result, run
        except Exception as exc:
            run.mark_failed(exc)
            if not config["test_enable"]:
                self.env.cr.commit()
            raise

    @api.model
    def _cron_sync_configured_facility_inventory(self):
        try:
            self.sudo().sync_configured_facility_inventory_with_run()
        except Exception:
            _logger.exception("Scheduled eLMIS inventory sync failed.")
            return False
        return True

    @api.model
    def test_configured_connection(self):
        params = self._get_elmis_config()
        location = self._get_configured_mirror_location()
        token = params["api_token"] or self._get_elmis_access_token(params)
        facility = self._get_elmis_facility_by_code(
            params["base_url"],
            token,
            location.elmis_facility_code,
        )
        programs = self._get_elmis_programs_by_code(
            params["base_url"],
            token,
            params["program_codes"],
        )
        stock_entries_found = 0

        for program in programs:
            summaries = self._get_elmis_stock_card_summaries(
                params["base_url"],
                token,
                facility["id"],
                program["id"],
            )
            stock_entries_found += self._count_active_stock_entries(summaries)

        return {
            "facility_code": location.elmis_facility_code,
            "facility_id": facility["id"],
            "program_codes": [program["code"] for program in programs],
            "programs_resolved": len(programs),
            "stock_entries_found": stock_entries_found,
        }

    @api.model
    def test_configured_connection_with_run(self):
        run = self._create_sync_run("test_connection")
        try:
            result = self.test_configured_connection()
            run.mark_success(result)
            return result, run
        except Exception as exc:
            run.mark_failed(exc)
            if not config["test_enable"]:
                self.env.cr.commit()
            raise

    @api.model
    def _create_sync_run(self, operation):
        values = {"operation": operation}
        try:
            params = self._get_elmis_config()
            values["program_codes"] = ", ".join(params["program_codes"])
        except UserError:
            pass

        try:
            location = self._get_configured_mirror_location()
            values.update(
                {
                    "location_id": location.id,
                    "facility_code": location.elmis_facility_code,
                }
            )
        except UserError:
            pass

        return self.env["elmis.inventory.sync.run"].sudo().create(values)

    @api.model
    def get_sync_status(self):
        cron = self._get_sync_cron()
        sync_runs = self.env["elmis.inventory.sync.run"].sudo()
        last_run = sync_runs.search([("operation", "=", "inventory_sync")], limit=1)
        last_success = sync_runs.search(
            [("operation", "=", "inventory_sync"), ("status", "=", "success")],
            limit=1,
        )
        nextcall = fields.Datetime.to_datetime(cron.nextcall) if cron and cron.nextcall else False
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        nextcall_seconds = False
        if cron and cron.active and nextcall:
            nextcall_seconds = int((nextcall - now).total_seconds())

        return {
            "enabled": bool(cron and cron.active),
            "interval_number": cron.interval_number if cron else 0,
            "interval_type": cron.interval_type if cron else False,
            "nextcall": self._datetime_to_iso(nextcall),
            "nextcall_seconds": nextcall_seconds,
            "last_run": self._format_status_run(last_run),
            "last_success": self._format_status_run(last_success),
        }

    @api.model
    def _get_sync_cron(self):
        return self.env.ref(
            "elmis_integration.ir_cron_elmis_inventory_sync",
            raise_if_not_found=False,
        )

    @api.model
    def _format_status_run(self, run):
        if not run:
            return False
        return {
            "id": run.id,
            "name": run.display_name,
            "status": run.status,
            "started_at": self._datetime_to_iso(run.started_at),
            "finished_at": self._datetime_to_iso(run.finished_at),
            "facility_code": run.facility_code,
            "items_processed": run.items_processed,
            "products_created": run.products_created,
            "lots_created": run.lots_created,
            "quants_updated": run.quants_updated,
            "error_message": run.error_message,
        }

    @api.model
    def _datetime_to_iso(self, value):
        if not value:
            return False
        value = fields.Datetime.to_datetime(value)
        return value.isoformat() + "Z"

    @api.model
    def _sync_nextcall_from_interval(self, interval_number, interval_type):
        interval_number = max(int(interval_number or 1), 1)
        interval_type = interval_type or "hours"
        deltas = {
            "minutes": timedelta(minutes=interval_number),
            "hours": timedelta(hours=interval_number),
            "days": timedelta(days=interval_number),
        }
        return fields.Datetime.now() + deltas.get(interval_type, deltas["hours"])

    @api.model
    def sync_facility_inventory(self, facility_code, program_codes=None, item_limit=None):
        params = self._get_elmis_config()
        program_codes = program_codes or params["program_codes"]
        if isinstance(program_codes, str):
            program_codes = self._split_program_codes(program_codes)

        token = params["api_token"] or self._get_elmis_access_token(params)
        facility = self._get_elmis_facility_by_code(params["base_url"], token, facility_code)
        programs = self._get_elmis_programs_by_code(params["base_url"], token, program_codes)
        items = []
        orderables = {}

        for program in programs:
            _logger.info(
                "Fetching eLMIS stock card summaries for facility %s, program %s",
                facility_code,
                program["code"],
            )
            summaries = self._get_elmis_stock_card_summaries(
                params["base_url"],
                token,
                facility["id"],
                program["id"],
            )
            items.extend(
                self._normalize_stock_card_summaries(
                    params["base_url"],
                    token,
                    summaries,
                    orderables,
                    item_limit=self._remaining_item_limit(item_limit, items),
                )
            )
            if item_limit and len(items) >= item_limit:
                break

        return self.sync_inventory_snapshot(
            {
                "facilityCode": facility_code,
                "facilityId": facility["id"],
                "items": items,
            }
        )

    @api.model
    def sync_inventory_snapshot(self, payload):
        facility_code = payload.get("facilityDhis2Code") or payload.get("facilityCode")
        if not facility_code:
            raise UserError(_("Inventory snapshot is missing a facility code."))

        location = self._get_mirror_location(facility_code)
        items = payload.get("items") or []
        result = {
            "facility_code": facility_code,
            "location_id": location.id,
            "items_processed": 0,
            "products_created": 0,
            "lots_created": 0,
            "quants_updated": 0,
        }

        for item in items:
            product, product_created = self._get_or_create_product(item)
            lot, lot_created = self._get_or_create_lot(item, product)
            quantity = self._get_stock_on_hand(item)

            if self._set_available_quantity(product, location, lot, quantity):
                result["quants_updated"] += 1

            result["items_processed"] += 1
            if product_created:
                result["products_created"] += 1
            if lot_created:
                result["lots_created"] += 1

        return result

    @api.model
    def _get_elmis_config(self):
        params = self.env["ir.config_parameter"].sudo()
        base_url = params.get_param("elmis_integration.base_url")
        program_codes = self._split_program_codes(
            params.get_param("elmis_integration.program_codes")
        )
        username = params.get_param("elmis_integration.username")
        password = params.get_param("elmis_integration.password")
        api_token = params.get_param("elmis_integration.api_token")

        if not base_url:
            raise UserError(_("eLMIS Base URL is not configured."))
        if not program_codes:
            raise UserError(_("At least one eLMIS program code must be configured."))
        if not api_token and (not username or not password):
            raise UserError(
                _("Configure either an eLMIS API token or service account credentials.")
            )

        return {
            "base_url": base_url.rstrip("/") + "/",
            "program_codes": program_codes,
            "username": username,
            "password": password,
            "api_token": api_token,
        }

    @api.model
    def _get_configured_mirror_location(self):
        location_id = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("elmis_integration.mirror_location_id")
        )
        location = self.env["stock.location"]
        if location_id:
            location = location.browse(int(location_id)).exists()

        if not location:
            raise UserError(_("Select an eLMIS mirror location in Settings first."))
        if location.usage != "internal" or not location.active:
            raise UserError(_("The configured eLMIS mirror location must be an active internal location."))
        if not location.elmis_facility_code:
            raise UserError(
                _("The configured eLMIS mirror location must have an eLMIS facility/service point code.")
            )
        return location

    @api.model
    def _split_program_codes(self, program_codes):
        if not program_codes:
            return []
        return [code.strip() for code in program_codes.split(",") if code.strip()]

    @api.model
    def _get_elmis_access_token(self, params):
        body = parse.urlencode(
            {
                "grant_type": "password",
                "username": params["username"],
                "password": params["password"],
            }
        ).encode("utf-8")
        auth = base64.b64encode(b"user-client:changeme").decode("ascii")
        response = self._elmis_request_json(
            params["base_url"],
            "oauth/token",
            method="POST",
            headers={
                "Authorization": "Basic %s" % auth,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=body,
        )
        token = response.get("access_token")
        if not token:
            raise UserError(_("eLMIS authentication response did not include access_token."))
        return token

    @api.model
    def _get_elmis_facility_by_code(self, base_url, token, facility_code):
        response = self._elmis_get_json(
            base_url,
            "facilities/full",
            token,
            {"code": facility_code, "size": 1},
        )
        facilities = self._extract_page_content(response)
        if not facilities:
            raise UserError(_("No eLMIS facility found for code %(code)s.") % {"code": facility_code})
        return facilities[0]

    @api.model
    def _get_elmis_programs_by_code(self, base_url, token, program_codes):
        response = self._elmis_get_json(base_url, "programs", token, {"code": program_codes})
        programs = response if isinstance(response, list) else self._extract_page_content(response)
        by_code = {program.get("code"): program for program in programs}
        missing = [code for code in program_codes if code not in by_code]
        if missing:
            raise UserError(
                _("No eLMIS program found for code(s): %(codes)s.")
                % {"codes": ", ".join(missing)}
            )
        return [by_code[code] for code in program_codes]

    @api.model
    def _get_elmis_stock_card_summaries(self, base_url, token, facility_id, program_id):
        response = self._elmis_get_json(
            base_url,
            "v2/stockCardSummariesResolv",
            token,
            {
                "facilityId": facility_id,
                "programId": program_id,
                "nonEmptyOnly": "true",
                "size": 2147483647,
            },
        )
        return self._extract_page_content(response)

    @api.model
    def _normalize_stock_card_summaries(
        self,
        base_url,
        token,
        summaries,
        orderables,
        item_limit=None,
    ):
        entries = []
        missing_orderable_ids = []

        for summary in summaries:
            orderable_ref = summary.get("orderable") or {}
            summary_orderable_id = orderable_ref.get("id")
            for entry in summary.get("canFulfillForMe") or []:
                if not entry.get("active", True):
                    continue
                orderable_id = (
                    (entry.get("orderable") or {}).get("id")
                    or summary_orderable_id
                )
                if not orderable_id:
                    continue
                entries.append((orderable_id, entry))
                if orderable_id not in orderables and orderable_id not in missing_orderable_ids:
                    missing_orderable_ids.append(orderable_id)
                if item_limit and len(entries) >= item_limit:
                    break
            if item_limit and len(entries) >= item_limit:
                break

        if missing_orderable_ids:
            orderables.update(
                self._get_elmis_orderables(base_url, token, missing_orderable_ids)
            )

        items = []
        for orderable_id, entry in entries:
            orderable = orderables.get(orderable_id)
            if orderable:
                items.append(self._normalize_stock_card_entry(orderable_id, orderable, entry))
        return items

    @api.model
    def _remaining_item_limit(self, item_limit, items):
        if not item_limit:
            return None
        return max(item_limit - len(items), 0)

    @api.model
    def _count_active_stock_entries(self, summaries):
        count = 0
        for summary in summaries:
            for entry in summary.get("canFulfillForMe") or []:
                if entry.get("active", True):
                    count += 1
        return count

    @api.model
    def _get_elmis_orderable(self, base_url, token, orderable_id):
        return self._elmis_get_json(base_url, "orderables/%s" % orderable_id, token)

    @api.model
    def _get_elmis_orderables(self, base_url, token, orderable_ids):
        by_id = {}
        for chunk in self._chunked(orderable_ids, 50):
            _logger.info(
                "Fetching %s eLMIS orderable records",
                len(chunk),
            )
            response = self._elmis_get_json(
                base_url,
                "orderables",
                token,
                {"id": chunk, "size": len(chunk)},
            )
            orderables = self._extract_page_content(response)
            by_id.update({orderable.get("id"): orderable for orderable in orderables})

        missing = [orderable_id for orderable_id in orderable_ids if orderable_id not in by_id]
        if missing:
            _logger.warning(
                "eLMIS did not return %s requested orderable records: %s",
                len(missing),
                ", ".join(missing),
            )
        return by_id

    @api.model
    def _chunked(self, values, size):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    @api.model
    def _normalize_stock_card_entry(self, orderable_id, orderable, entry):
        dispensable = orderable.get("dispensable") or {}
        extra_data = orderable.get("extraData") or {}
        identifiers = orderable.get("identifiers") or {}
        return {
            "elmisOrderableId": orderable_id,
            "productCode": orderable.get("productCode") or identifiers.get("commodityType"),
            "fullName": orderable.get("fullProductName") or entry.get("orderableName"),
            "genericName": extra_data.get("genericName"),
            "strength": extra_data.get("strength"),
            "dosageForm": extra_data.get("dosageForm"),
            "packSize": orderable.get("netContent"),
            "packSizeUnit": dispensable.get("displayUnit"),
            "dispensableUnit": dispensable.get("displayUnit"),
            "dispensableUnitFactor": dispensable.get("size"),
            "elmisLotId": (entry.get("lot") or {}).get("id"),
            "lot": entry.get("lotCode"),
            "expirationDate": entry.get("lotExpirationDate"),
            "stockOnHand": entry.get("stockOnHand") or 0,
        }

    @api.model
    def _extract_page_content(self, response):
        if isinstance(response, list):
            return response
        return response.get("content") or []

    @api.model
    def _elmis_get_json(self, base_url, path, token, query=None):
        headers = {"Authorization": "Bearer %s" % token}
        return self._elmis_request_json(base_url, path, query=query, headers=headers)

    @api.model
    def _elmis_request_json(self, base_url, path, method="GET", query=None, headers=None, data=None):
        url = self._build_elmis_url(base_url, path, query)
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        req = request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise UserError(
                _("eLMIS request failed: %(status)s %(reason)s\n%(detail)s")
                % {"status": exc.code, "reason": exc.reason, "detail": detail}
            ) from exc
        except error.URLError as exc:
            raise UserError(_("Could not connect to eLMIS: %(reason)s") % {"reason": exc.reason}) from exc

        return json.loads(raw) if raw else {}

    @api.model
    def _build_elmis_url(self, base_url, path, query=None):
        url = parse.urljoin(base_url, path.lstrip("/"))
        if query:
            url = "%s?%s" % (url, parse.urlencode(query, doseq=True))
        return url

    @api.model
    def _get_mirror_location(self, facility_code):
        location = self.env["stock.location"].search(
            [
                ("usage", "=", "internal"),
                ("active", "=", True),
                ("elmis_facility_code", "=", facility_code),
            ],
            limit=1,
        )
        if not location:
            raise UserError(
                _("No active internal eLMIS mirror location found for %(code)s.")
                % {"code": facility_code}
            )
        return location

    @api.model
    def _get_or_create_product(self, item):
        orderable_id = item.get("elmisOrderableId") or item.get("orderableId")
        product_code = (
            item.get("productCode")
            or item.get("elmisProductCode")
            or item.get("orderable")
            or item.get("code")
        )

        product = self.env["product.product"]
        if orderable_id:
            product = product.search([("elmis_orderable_id", "=", orderable_id)], limit=1)
        if not product and product_code:
            product = product.search([("elmis_product_code", "=", product_code)], limit=1)
        if product:
            self._update_product_from_item(product, item)
            return product, False

        if not product_code and not orderable_id:
            raise UserError(_("Inventory item is missing an eLMIS product identifier."))

        product_name = (
            item.get("fullName")
            or item.get("fullProductName")
            or item.get("name")
            or product_code
            or orderable_id
        )
        unit_uom = self.env.ref("uom.product_uom_unit")
        template = self.env["product.template"].create(
            {
                "name": product_name,
                "default_code": product_code,
                "detailed_type": "product",
                "tracking": "lot",
                "uom_id": unit_uom.id,
                "uom_po_id": unit_uom.id,
            }
        )
        product = template.product_variant_id
        self._update_product_from_item(product, item)
        return product, True

    @api.model
    def _update_product_from_item(self, product, item):
        vals = {
            "is_elmis_product": True,
            "elmis_orderable_id": item.get("elmisOrderableId") or item.get("orderableId"),
            "elmis_product_code": (
                item.get("productCode")
                or item.get("elmisProductCode")
                or item.get("orderable")
                or item.get("code")
            ),
            "elmis_generic_name": item.get("genericName"),
            "elmis_strength": item.get("strength"),
            "elmis_dosage_form": self._normalize_dosage_form(item.get("dosageForm")),
            "elmis_pack_size": item.get("packSize") or item.get("netContent"),
            "elmis_pack_size_unit": item.get("packSizeUnit"),
            "elmis_dispensable_unit": item.get("dispensableUnit"),
            "elmis_dispensable_unit_factor": item.get("dispensableUnitFactor"),
        }
        product.write({key: value for key, value in vals.items() if value not in (None, "")})

    @api.model
    def _normalize_dosage_form(self, dosage_form):
        if not dosage_form:
            return False
        value = str(dosage_form).strip().lower()
        allowed = dict(self.env["product.product"]._fields["elmis_dosage_form"].selection)
        return value if value in allowed else "other"

    @api.model
    def _get_or_create_lot(self, item, product):
        lot_id = item.get("elmisLotId") or item.get("lotId")
        lot_number = item.get("elmisLotNumber") or item.get("lot") or item.get("lotCode")

        if not lot_id and not lot_number:
            return self.env["stock.lot"], False

        lot = self.env["stock.lot"]
        if lot_id:
            lot = lot.search([("elmis_lot_id", "=", lot_id)], limit=1)
        if not lot and lot_number:
            lot = lot.search(
                [
                    ("product_id", "=", product.id),
                    "|",
                    ("elmis_lot_number", "=", lot_number),
                    ("name", "=", lot_number),
                ],
                limit=1,
            )

        if lot:
            self._update_lot_from_item(lot, item)
            return lot, False

        lot = self.env["stock.lot"].create(
            {
                "name": lot_number or lot_id,
                "product_id": product.id,
                "company_id": self.env.company.id,
                "elmis_lot_id": lot_id,
                "elmis_lot_number": lot_number,
            }
        )
        self._update_lot_from_item(lot, item)
        return lot, True

    @api.model
    def _update_lot_from_item(self, lot, item):
        vals = {
            "elmis_lot_id": item.get("elmisLotId") or item.get("lotId"),
            "elmis_lot_number": item.get("elmisLotNumber") or item.get("lot") or item.get("lotCode"),
        }
        expiration_date = item.get("expirationDate")
        if expiration_date and "expiration_date" in lot._fields:
            vals["expiration_date"] = fields.Date.to_date(expiration_date)
        lot.write({key: value for key, value in vals.items() if value not in (None, "")})

    @api.model
    def _get_stock_on_hand(self, item):
        if "stockOnHand" not in item:
            raise UserError(_("Inventory item is missing stockOnHand."))
        return float(item["stockOnHand"] or 0.0)

    @api.model
    def _set_available_quantity(self, product, location, lot, quantity):
        quant_domain = [
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ]
        if lot:
            quant_domain.append(("lot_id", "=", lot.id))
        else:
            quant_domain.append(("lot_id", "=", False))

        current_quantity = sum(self.env["stock.quant"].search(quant_domain).mapped("quantity"))
        rounding = product.uom_id.rounding or 0.0001
        delta = quantity - current_quantity
        if float_compare(delta, 0.0, precision_rounding=rounding) == 0:
            return False

        self.env["stock.quant"]._update_available_quantity(
            product,
            location,
            delta,
            lot_id=lot if lot else None,
        )
        return True
