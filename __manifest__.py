# -*- coding: utf-8 -*-
{
    "name": "RAET / Visma Latam HR Sync",
    "version": "19.0.1.0.0",
    "category": "Human Resources/Employees",
    "summary": "Importa y sincroniza el padrón de empleados desde RAET (Visma Latam) "
               "hacia Odoo, por empresa (tenant), vía API REST.",
    "description": """
RAET / Visma Latam HR Sync
==========================

Conecta Odoo con la API de RAET (Visma Latam) para traer el padrón completo de
empleados de distintas empresas (tenants) y crear/actualizar los registros en
``hr.employee``.

Funcionalidades
---------------
* Configuración de credenciales globales de la API en *Ajustes*.
* Campo **Tenant RAET** en cada empresa (``res.company``) para asociar el código
  de tenant de RAET con la empresa de Odoo.
* Login OAuth (grant_type=password) y consumo de los endpoints de RAET:
  ``/authentication/login``, ``/account/tenants``, ``/employees`` (paginado),
  ``/employees/rh-{id}``, ``/employees/rh-{id}/phases``,
  ``/employees/rh-{id}/structures`` y ``/employees/rh-{id}/addresses``.
* Mapeo 1 a 1 de la mayor cantidad de campos posibles a los campos estándar de
  ``hr.employee``; los campos de RAET que no existen en Odoo se crean como
  campos ``x_raet_*``.
* Identificación / *upsert* de empleados por número de legajo (**codbar** =
  ``externalId`` de RAET), guardando además el id interno de RAET.
* Sincronización por **cron** (incremental por ``updatedFrom``) y por **botón
  manual** (asistente).
* Log de cada corrida de sincronización.
""",
    "author": "Custom Development",
    "website": "https://www.vismalatam.com",
    "license": "LGPL-3",
    "depends": ["hr"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/raet_config_params.xml",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/res_company_views.xml",
        "views/hr_employee_views.xml",
        "views/raet_sync_log_views.xml",
        "wizard/raet_sync_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
