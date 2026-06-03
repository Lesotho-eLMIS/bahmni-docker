from odoo import http


class ElmisSyncStatusController(http.Controller):
    @http.route("/lesotho_elmis_integration/sync_status", type="json", auth="user")
    def sync_status(self):
        return http.request.env["elmis.inventory.sync"].sudo().get_sync_status()
