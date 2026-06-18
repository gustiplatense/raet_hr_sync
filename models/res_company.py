# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    raet_tenant_id = fields.Char(
        string="Tenant RAET",
        help="Código de tenant de RAET (Visma Latam) asociado a esta empresa. "
             "Es el valor enviado en el header X-RAET-Tenant-Id para traer el "
             "padrón de empleados de esta empresa.")
    raet_sync_enabled = fields.Boolean(
        string="Sincronizar con RAET",
        default=True,
        help="Si está marcado, el cron de sincronización procesará esta empresa.")
    raet_last_sync = fields.Datetime(
        string="Última sincronización RAET",
        readonly=True,
        help="Fecha/hora de la última corrida exitosa de sincronización para "
             "esta empresa. El cron usa esta fecha como 'updatedFrom'.")
