# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RaetSyncWizard(models.TransientModel):
    _name = "raet.sync.wizard"
    _description = "RAET - Asistente de sincronización manual"

    company_ids = fields.Many2many(
        "res.company", string="Empresas",
        domain=[("raet_tenant_id", "!=", False)],
        help="Empresas (tenants) a sincronizar. Si se deja vacío se procesan "
             "todas las que tengan Tenant RAET configurado.")
    mode = fields.Selection(
        [("incremental", "Incremental (novedades desde fecha)"),
         ("full", "Completo (todo el padrón)")],
        string="Modo", default="incremental", required=True)
    updated_from = fields.Date(
        string="Novedades desde",
        default=lambda self: fields.Date.today() - timedelta(days=60),
        help="Sólo se traen empleados modificados desde esta fecha "
             "(parámetro updatedFrom de RAET). Ignorado en modo Completo.")

    @api.onchange("mode")
    def _onchange_mode(self):
        if self.mode == "full":
            self.updated_from = False
        elif not self.updated_from:
            self.updated_from = fields.Date.today() - timedelta(days=60)

    def action_sync(self):
        self.ensure_one()
        Employee = self.env["hr.employee"].sudo()
        company_ids = self.company_ids.ids or None
        updated_from = False
        if self.mode == "incremental":
            updated_from = self.updated_from and fields.Date.to_string(self.updated_from) or False

        if company_ids:
            companies = self.company_ids
        else:
            companies = self.env["res.company"].sudo().search([
                ("raet_tenant_id", "!=", False),
                ("raet_sync_enabled", "=", True)])
        if not companies:
            raise UserError(_("No hay empresas con Tenant RAET configurado."))

        client = Employee._raet_get_client()
        logs = self.env["raet.sync.log"]
        for company in companies:
            logs |= Employee._raet_sync_company(
                company, client=client, updated_from=updated_from)

        return {
            "type": "ir.actions.act_window",
            "name": _("Resultado de sincronización RAET"),
            "res_model": "raet.sync.log",
            "view_mode": "list,form",
            "domain": [("id", "in", logs.ids)],
            "target": "current",
        }
