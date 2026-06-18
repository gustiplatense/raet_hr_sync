# -*- coding: utf-8 -*-
from odoo import fields, models


class RaetSyncLog(models.Model):
    _name = "raet.sync.log"
    _description = "RAET - Registro de sincronización"
    _order = "create_date desc"
    _rec_name = "display_name"

    company_id = fields.Many2one("res.company", string="Empresa", index=True)
    tenant = fields.Char(string="Tenant RAET", index=True)
    date_start = fields.Datetime(string="Inicio", default=fields.Datetime.now)
    date_end = fields.Datetime(string="Fin")
    updated_from = fields.Char(string="updatedFrom")
    state = fields.Selection(
        [("running", "En curso"),
         ("done", "Finalizado"),
         ("error", "Con errores")],
        string="Estado", default="running", index=True)
    created_count = fields.Integer(string="Creados", default=0)
    updated_count = fields.Integer(string="Actualizados", default=0)
    error_count = fields.Integer(string="Errores", default=0)
    total_count = fields.Integer(string="Procesados", default=0)
    message = fields.Text(string="Detalle")

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "RAET %s - %s" % (
                rec.tenant or "?",
                rec.create_date and fields.Datetime.to_string(rec.create_date) or "")
