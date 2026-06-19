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
        [("queued", "En cola"),
         ("running", "En curso"),
         ("done", "Finalizado"),
         ("error", "Con errores")],
        string="Estado", default="running", index=True)
    created_count = fields.Integer(string="Creados", default=0)
    updated_count = fields.Integer(string="Actualizados", default=0)
    error_count = fields.Integer(string="Errores", default=0)
    total_count = fields.Integer(string="Procesados", default=0)
    message = fields.Text(string="Detalle")
    line_ids = fields.One2many(
        "raet.sync.log.line", "log_id", string="Detalle por empleado")

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "RAET %s - %s" % (
                rec.tenant or "?",
                rec.create_date and fields.Datetime.to_string(rec.create_date) or "")


class RaetSyncLogLine(models.Model):
    _name = "raet.sync.log.line"
    _description = "RAET - Detalle de sincronización por empleado"
    _order = "id"

    log_id = fields.Many2one(
        "raet.sync.log", string="Sincronización", required=True,
        ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="log_id.company_id", string="Empresa", store=True)
    raet_id = fields.Char(string="RAET ID (rh-)", index=True)
    external_id = fields.Char(string="Legajo / externalId", index=True)
    name = fields.Char(string="Empleado")
    state = fields.Selection(
        [("ok", "OK"), ("error", "Error")],
        string="Resultado", default="error", index=True)
    message = fields.Text(string="Mensaje de error")
    payload = fields.Text(
        string="JSON recibido de RAET",
        help="Respuesta cruda de RAET para este empleado, útil para verificar "
             "el mapeo de campos.")
