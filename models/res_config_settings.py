# -*- coding: utf-8 -*-
from odoo import api, fields, models

# Claves de ir.config_parameter usadas para guardar la configuración global.
PARAM_LOGIN_URL = "raet_hr_sync.login_url"
PARAM_ADMIN_URL = "raet_hr_sync.admin_url"
PARAM_API_URL = "raet_hr_sync.api_url"
PARAM_USERNAME = "raet_hr_sync.username"
PARAM_PASSWORD = "raet_hr_sync.password"
PARAM_GRANT_TYPE = "raet_hr_sync.grant_type"
PARAM_PAGE_SIZE = "raet_hr_sync.page_size"
PARAM_DEFAULT_DAYS = "raet_hr_sync.default_days"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    raet_login_url = fields.Char(
        string="URL de Login RAET",
        config_parameter=PARAM_LOGIN_URL,
        default="https://webapiadmin.vismalatam.com/authentication/login",
        help="Endpoint de autenticación (grant_type=password).")
    raet_admin_url = fields.Char(
        string="URL Admin RAET",
        config_parameter=PARAM_ADMIN_URL,
        default="https://webapiadmin.vismalatam.com",
        help="Base de la API de administración (tenants).")
    raet_api_url = fields.Char(
        string="URL API RAET",
        config_parameter=PARAM_API_URL,
        default="https://webapi.vismalatam.com",
        help="Base de la API de datos (empleados, fases, estructuras, domicilios).")
    raet_username = fields.Char(
        string="Usuario API RAET",
        config_parameter=PARAM_USERNAME)
    raet_password = fields.Char(
        string="Contraseña API RAET",
        config_parameter=PARAM_PASSWORD)
    raet_grant_type = fields.Char(
        string="Grant Type",
        config_parameter=PARAM_GRANT_TYPE,
        default="password")
    raet_page_size = fields.Integer(
        string="Tamaño de página",
        config_parameter=PARAM_PAGE_SIZE,
        default=500,
        help="Cantidad de empleados por página al consultar la API.")
    raet_default_days = fields.Integer(
        string="Días por defecto (updatedFrom)",
        config_parameter=PARAM_DEFAULT_DAYS,
        default=60,
        help="Ventana de novedades, en días, usada por el cron y como valor "
             "por defecto del asistente de sincronización.")

    def action_raet_test_connection(self):
        """Prueba el login contra RAET con la configuración guardada."""
        self.ensure_one()
        # Asegurar que los parámetros estén persistidos antes de probar.
        self.set_values()
        client = self.env["hr.employee"]._raet_get_client()
        client.login()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "RAET",
                "message": "Conexión exitosa: se obtuvo el token de acceso.",
                "type": "success",
                "sticky": False,
            },
        }
