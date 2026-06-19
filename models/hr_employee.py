# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .raet_api import RaetClient, RaetApiError
from .res_config_settings import (
    PARAM_LOGIN_URL, PARAM_ADMIN_URL, PARAM_API_URL, PARAM_USERNAME,
    PARAM_PASSWORD, PARAM_GRANT_TYPE, PARAM_PAGE_SIZE, PARAM_DEFAULT_DAYS,
)

_logger = logging.getLogger(__name__)

# Mapeo de type.id de /structures (igual que el proyecto VB.NET original).
STRUCT_COST_CENTER = "5"     # centro de imputación / costo
STRUCT_CONTRACT = "18"       # tipo de contrato
STRUCT_WORKING_DAY = "21"    # jornada
STRUCT_POSITION = "4"        # puesto / cargo
STRUCT_DEPARTMENT = "6"      # departamento

# Mapeo de estado civil (descripción RAET -> selection de Odoo).
MARITAL_MAP = {
    "soltero": "single", "soltera": "single", "single": "single",
    "casado": "married", "casada": "married", "married": "married",
    "divorciado": "divorced", "divorciada": "divorced", "divorced": "divorced",
    "viudo": "widower", "viuda": "widower", "widow": "widower",
    "union": "cohabitant", "unión": "cohabitant", "concubinato": "cohabitant",
    "conviviente": "cohabitant", "cohabitant": "cohabitant",
}


def _raet_parse_date(value):
    """Convierte una fecha de RAET (str ISO) a 'YYYY-MM-DD' o False."""
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    # RAET suele devolver 'YYYY-MM-DDTHH:MM:SS' o 'YYYY/MM/DD'.
    text = text.replace("/", "-")[:10]
    if text in ("1900-01-01", "0001-01-01", "0000-00-00"):
        return False
    try:
        fields.Date.from_string(text)
    except (ValueError, TypeError):
        return False
    return text


def _raet_pick(source, *keys):
    """Devuelve el primer valor no vacío entre varias claves de ``source``.

    Permite leer un mismo dato venga en camelCase (formato documentado) o en
    snake_case plano (formato real observado), p. ej.
    ``_raet_pick(detail, "firstName", "name")``. Las claves anidadas se pueden
    expresar con puntos: ``"genre.code"``.
    """
    if not isinstance(source, dict):
        return ""
    for key in keys:
        value = source
        for part in str(key).split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value not in ("", [], {}, None):
            return value
    return ""


def _raet_to_bool(value, default=False):
    """Normaliza un flag de RAET ('1'/'0'/'true'/'S'/bool) a booleano."""
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "s", "si", "sí", "yes", "y", "x"):
        return True
    if text in ("0", "false", "f", "n", "no"):
        return False
    return default


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    # ---- Identificadores RAET -------------------------------------------- #
    x_raet_internal_id = fields.Char(
        string="RAET ID interno", index=True, copy=False,
        help="Id interno de RAET usado en las llamadas rh-{id}. Estable, "
             "sirve como clave técnica de sincronización.")
    x_raet_external_id = fields.Char(
        string="RAET externalId (Legajo)", index=True, copy=False,
        help="externalId de RAET. Número de legajo / codbar usado para el "
             "match con el empleado de Odoo.")
    x_raet_tenant = fields.Char(string="RAET Tenant", index=True, copy=False)

    # ---- Fechas / situación laboral -------------------------------------- #
    x_raet_hiring_date = fields.Date(string="Fecha de ingreso (RAET)")
    x_raet_entry_date = fields.Date(string="Fecha de ingreso al país (RAET)")
    x_raet_end_date = fields.Date(string="Fecha de baja (RAET)")
    x_raet_low_motive = fields.Char(string="Motivo de baja (RAET)")
    x_raet_organization_model = fields.Char(string="Modelo organizativo (RAET)")
    x_raet_reports_to_external = fields.Char(
        string="Reporta a (externalId)", copy=False,
        help="externalId del jefe directo en RAET (reportsToExternalId). "
             "Se usa para resolver el jefe directo en Odoo.")

    # ---- Datos personales adicionales ------------------------------------ #
    x_raet_nickname = fields.Char(string="Apodo (RAET)")
    x_raet_nationality_desc = fields.Char(string="Nacionalidad (RAET)")
    x_raet_marital_desc = fields.Char(string="Estado civil (RAET)")
    x_raet_study_level = fields.Char(string="Nivel de estudios (RAET)")
    x_raet_fiscal_number = fields.Char(string="Número fiscal / CUIT (RAET)")
    x_raet_fiscal_type = fields.Char(string="Tipo número fiscal (RAET)")
    x_raet_handicapped = fields.Boolean(string="Discapacidad (RAET)")
    x_raet_handicap_type = fields.Char(string="Tipo discapacidad (RAET)")
    x_raet_image_url = fields.Char(string="URL imagen (RAET)")

    # ---- Estructura organizativa (de /structures) ------------------------ #
    x_raet_imput_center = fields.Char(string="Centro de imputación (RAET)")
    x_raet_cost_center = fields.Char(string="Centro de costo (RAET)")
    x_raet_contract_type = fields.Char(string="Tipo de contrato (RAET)")
    x_raet_working_day = fields.Char(string="Jornada (RAET)")
    x_raet_position_code = fields.Char(string="Código de puesto (RAET)")
    x_raet_department_code = fields.Char(string="Código departamento (RAET)")

    # ---- Domicilio (de /addresses) --------------------------------------- #
    x_raet_address_reference = fields.Char(string="Entre calles / referencia (RAET)")

    # ---- Auditoría ------------------------------------------------------- #
    x_raet_last_sync = fields.Datetime(string="Última sync RAET", copy=False)
    x_raet_raw = fields.Text(
        string="RAET payload (debug)", copy=False,
        help="Último JSON de detalle recibido de RAET (para diagnóstico).")

    # ===================================================================== #
    # Configuración / cliente
    # ===================================================================== #
    @api.model
    def _raet_get_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "login_url": icp.get_param(PARAM_LOGIN_URL),
            "admin_url": icp.get_param(PARAM_ADMIN_URL),
            "api_url": icp.get_param(PARAM_API_URL),
            "username": icp.get_param(PARAM_USERNAME),
            "password": icp.get_param(PARAM_PASSWORD),
            "grant_type": icp.get_param(PARAM_GRANT_TYPE) or "password",
            "page_size": int(icp.get_param(PARAM_PAGE_SIZE) or 500),
            "default_days": int(icp.get_param(PARAM_DEFAULT_DAYS) or 60),
        }

    @api.model
    def _raet_get_client(self):
        cfg = self._raet_get_config()
        if not cfg["login_url"] or not cfg["username"]:
            raise UserError(_(
                "Falta configurar las credenciales de RAET en "
                "Ajustes > Empleados > Integración RAET."))
        return RaetClient(
            login_url=cfg["login_url"],
            admin_url=cfg["admin_url"],
            api_url=cfg["api_url"],
            username=cfg["username"],
            password=cfg["password"],
            grant_type=cfg["grant_type"],
            page_size=cfg["page_size"],
        )

    # ===================================================================== #
    # Helpers de mapeo
    # ===================================================================== #
    @api.model
    def _raet_map_gender(self, value):
        if not value:
            return False
        initial = str(value).strip()[:1].upper()
        if initial == "M":
            return "male"
        if initial == "F":
            return "female"
        return "other"

    @api.model
    def _raet_map_marital(self, value):
        if not value:
            return False
        key = str(value).strip().lower()
        for token, odoo_val in MARITAL_MAP.items():
            if token in key:
                return odoo_val
        return False

    @api.model
    def _raet_find_country(self, description):
        if not description:
            return False
        Country = self.env["res.country"]
        name = str(description).strip()
        country = Country.search(["|",
                                  ("name", "=ilike", name),
                                  ("code", "=ilike", name)], limit=1)
        return country.id if country else False

    @api.model
    def _raet_get_department(self, name, company):
        if not name:
            return False
        Dept = self.env["hr.department"]
        domain = [("name", "=ilike", name.strip())]
        if "company_id" in Dept._fields:
            domain += ["|", ("company_id", "=", company.id),
                       ("company_id", "=", False)]
        dept = Dept.search(domain, limit=1)
        if not dept:
            vals = {"name": name.strip()}
            if "company_id" in Dept._fields:
                vals["company_id"] = company.id
            dept = Dept.create(vals)
        return dept.id

    @api.model
    def _raet_get_job(self, name, company):
        if not name:
            return False
        Job = self.env["hr.job"]
        domain = [("name", "=ilike", name.strip())]
        if "company_id" in Job._fields:
            domain += ["|", ("company_id", "=", company.id),
                       ("company_id", "=", False)]
        job = Job.search(domain, limit=1)
        if not job:
            vals = {"name": name.strip()}
            if "company_id" in Job._fields:
                vals["company_id"] = company.id
            job = Job.create(vals)
        return job.id

    def _raet_set_if_field(self, vals, field, value):
        """Asigna value a vals[field] sólo si el campo existe en el modelo."""
        if value not in (None, "") and field in self._fields:
            vals[field] = value

    # ===================================================================== #
    # Construcción de valores de un empleado
    # ===================================================================== #
    @api.model
    def _raet_build_values(self, detail, phases, structures, addresses,
                           tenant, company):
        """Arma el dict de valores de hr.employee a partir del detalle RAET."""
        detail = detail or {}
        vals = {}

        # --- Nombre (camelCase documentado o snake_case plano) ---
        parts = [
            _raet_pick(detail, "firstName", "name"),
            _raet_pick(detail, "middleName"),
            _raet_pick(detail, "lastName", "first_last_name"),
            _raet_pick(detail, "familyName", "second_last_name"),
        ]
        name = " ".join(str(p).strip() for p in parts if p and str(p).strip())
        vals["name"] = name or (
            _raet_pick(detail, "externalId", "id_code_internal") or "Empleado RAET")

        # --- Identificadores ---
        external_id = _raet_pick(detail, "externalId")
        internal_id = _raet_pick(detail, "id", "id_code_internal")
        vals["x_raet_external_id"] = external_id and str(external_id) or False
        vals["x_raet_internal_id"] = internal_id and str(internal_id) or False
        vals["x_raet_tenant"] = str(tenant)
        # codbar / legajo -> campo nativo 'barcode'
        self._raet_set_if_field(vals, "barcode",
                                external_id and str(external_id) or False)
        vals["company_id"] = company.id
        if "sw_active" in detail:
            vals["active"] = _raet_to_bool(detail.get("sw_active"), default=True)
        else:
            vals["active"] = bool(detail.get("isActive", True))

        # --- Datos personales ---
        self._raet_set_if_field(
            vals, "gender",
            self._raet_map_gender(_raet_pick(detail, "genre", "id_code_gender")))
        self._raet_set_if_field(
            vals, "birthday",
            _raet_parse_date(_raet_pick(detail, "dateOfBirth", "birth_date")))
        self._raet_set_if_field(vals, "place_of_birth",
                                _raet_pick(detail, "placeOfBirth"))
        self._raet_set_if_field(
            vals, "country_of_birth",
            self._raet_find_country(_raet_pick(detail, "countryOfBirth")))
        vals["x_raet_nickname"] = _raet_pick(detail, "nickname") or False
        vals["x_raet_organization_model"] = \
            _raet_pick(detail, "organizationModel") or False
        vals["x_raet_image_url"] = \
            _raet_pick(detail, "imageUrl", "url_img_user") or False

        # Nacionalidad: sub-lista camelCase o campo plano 'nationality'
        nationalities = detail.get("nationalities") or []
        nat_desc = (nationalities[0].get("description") if nationalities
                    else _raet_pick(detail, "nationality"))
        if nat_desc:
            vals["x_raet_nationality_desc"] = nat_desc or False
            self._raet_set_if_field(vals, "country_id",
                                    self._raet_find_country(nat_desc))

        # Estado civil
        marital = detail.get("maritalStatus") or {}
        marital_desc = marital.get("description") if isinstance(marital, dict) else ""
        marital_desc = marital_desc or _raet_pick(detail, "estado_civil")
        if marital_desc:
            vals["x_raet_marital_desc"] = marital_desc
            self._raet_set_if_field(vals, "marital",
                                    self._raet_map_marital(marital_desc))

        # Documento de identidad (DNI): sub-lista camelCase o campo plano
        nids = detail.get("nationalIdentificationNumbers") or []
        doc_number = (str(nids[0].get("number"))
                      if (nids and nids[0].get("number"))
                      else _raet_pick(detail, "identificacion"))
        if doc_number:
            self._raet_set_if_field(vals, "identification_id", str(doc_number))

        # Número fiscal / CUIT
        fiscals = detail.get("fiscalNumbers") or []
        if fiscals:
            vals["x_raet_fiscal_number"] = fiscals[0].get("number") or False
            ftype = fiscals[0].get("type") or {}
            vals["x_raet_fiscal_type"] = ftype.get("description") or False
            # Algunos países usan el CUIT/CUIL como SSN del empleado.
            self._raet_set_if_field(vals, "ssnid",
                                    fiscals[0].get("number") or False)

        # Nivel de estudios
        study = detail.get("studyLevel") or {}
        vals["x_raet_study_level"] = study.get("description") or False

        # Discapacidad: flag camelCase 'handicapped' o plano 'sw_disabled'
        if "sw_disabled" in detail:
            handicapped = _raet_to_bool(detail.get("sw_disabled"))
        else:
            handicapped = bool(detail.get("handicapped"))
        vals["x_raet_handicapped"] = handicapped
        htype = detail.get("handicapType") or {}
        vals["x_raet_handicap_type"] = htype.get("description") or False
        self._raet_set_if_field(vals, "disabled", handicapped)

        # Email
        email = _raet_pick(detail, "email")
        self._raet_set_if_field(vals, "work_email", email)
        self._raet_set_if_field(vals, "private_email", email)

        # Teléfonos: lista camelCase o campos planos (movil / telephone)
        phones = detail.get("phones") or []
        if phones:
            self._raet_apply_phones(vals, phones)
        else:
            self._raet_apply_flat_phones(vals, detail)

        # Fechas laborales
        vals["x_raet_hiring_date"] = _raet_parse_date(
            _raet_pick(detail, "hiringDate", "hiring_date"))
        vals["x_raet_entry_date"] = _raet_parse_date(
            _raet_pick(detail, "dateOfCountryEntry", "entry_date"))

        # Jefe directo (se resuelve luego): externalId camelCase o DNI plano
        vals["x_raet_reports_to_external"] = _raet_pick(
            detail, "reportsToExternalId", "immediate_boss") or False

        # --- Fases (baja): sub-recurso o campos planos end_date/low_motive ---
        if phases:
            ph = phases[0]
            vals["x_raet_end_date"] = _raet_parse_date(ph.get("endDate"))
            vals["x_raet_low_motive"] = ph.get("decouplingCause") or False
        else:
            end_date = _raet_parse_date(_raet_pick(detail, "end_date"))
            if end_date:
                vals["x_raet_end_date"] = end_date
            low_motive = _raet_pick(detail, "low_motive")
            if low_motive:
                vals["x_raet_low_motive"] = low_motive

        # --- Estructuras: sub-recurso o campos planos ---
        if structures:
            self._raet_apply_structures(vals, structures, company)
        else:
            self._raet_apply_flat_structures(vals, detail, company)

        # --- Domicilio: sub-recurso o campos planos ---
        if addresses:
            self._raet_apply_address(vals, addresses)
        else:
            self._raet_apply_flat_address(vals, detail)

        vals["x_raet_last_sync"] = fields.Datetime.now()
        return vals

    def _raet_apply_phones(self, vals, phones):
        for phone in phones:
            ptype = (phone.get("type") or "").strip().lower()
            number = " ".join(filter(None, [phone.get("areaCode"),
                                            phone.get("number")])).strip()
            if not number:
                continue
            if ptype in ("movil", "móvil", "celular", "mobile", "cell"):
                self._raet_set_if_field(vals, "mobile_phone", number)
                self._raet_set_if_field(vals, "private_phone", number)
            elif ptype in ("trabajo", "laboral", "work", "oficina"):
                self._raet_set_if_field(vals, "work_phone", number)
            else:  # personal / particular / desconocido
                self._raet_set_if_field(vals, "private_phone", number)
                if "work_phone" not in vals:
                    self._raet_set_if_field(vals, "work_phone", number)

    def _raet_apply_structures(self, vals, structures, company):
        for st in structures or []:
            stype = st.get("type") or {}
            tid = str(stype.get("id") or "")
            desc = st.get("description")
            ext = st.get("externalId")
            if tid == STRUCT_COST_CENTER:
                vals["x_raet_imput_center"] = desc or False
                vals["x_raet_cost_center"] = ext or False
            elif tid == STRUCT_CONTRACT:
                vals["x_raet_contract_type"] = desc or False
            elif tid == STRUCT_WORKING_DAY:
                vals["x_raet_working_day"] = desc or False
            elif tid == STRUCT_POSITION:
                vals["x_raet_position_code"] = ext or desc or False
                if desc:
                    self._raet_set_if_field(vals, "job_title", desc)
                    self._raet_set_if_field(
                        vals, "job_id", self._raet_get_job(desc, company))
            elif tid == STRUCT_DEPARTMENT:
                vals["x_raet_department_code"] = ext or desc or False
                if desc:
                    self._raet_set_if_field(
                        vals, "department_id",
                        self._raet_get_department(desc, company))

    def _raet_apply_address(self, vals, addresses):
        if not addresses:
            return
        addr = addresses[0]
        street = " ".join(filter(None, [addr.get("street"),
                                        addr.get("houseNumber")])).strip()
        self._raet_set_if_field(vals, "private_street", street)
        self._raet_set_if_field(vals, "private_city", addr.get("city"))
        self._raet_set_if_field(vals, "private_zip", addr.get("zipCode"))
        vals["x_raet_address_reference"] = addr.get("betweenStreets") or False
        country_id = self._raet_find_country(addr.get("country"))
        self._raet_set_if_field(vals, "private_country_id", country_id)

    # --------------------------------------------------------------------- #
    # Variantes para el formato plano (snake_case) de RAET
    # --------------------------------------------------------------------- #
    def _raet_apply_flat_phones(self, vals, detail):
        """Teléfonos desde campos planos: 'movil' y 'telephone'."""
        movil = _raet_pick(detail, "movil")
        if movil:
            self._raet_set_if_field(vals, "mobile_phone", str(movil))
            self._raet_set_if_field(vals, "private_phone", str(movil))
        telephone = _raet_pick(detail, "telephone")
        if telephone:
            self._raet_set_if_field(vals, "work_phone", str(telephone))
            if not movil:
                self._raet_set_if_field(vals, "private_phone", str(telephone))

    def _raet_apply_flat_structures(self, vals, detail, company):
        """Estructura organizativa desde campos planos del empleado."""
        imput = _raet_pick(detail, "imput_center")
        if imput:
            vals["x_raet_imput_center"] = imput
        cost_center = _raet_pick(detail, "id_center_cost")
        if cost_center:
            vals["x_raet_cost_center"] = cost_center
        contract = _raet_pick(detail, "contract_type")
        if contract:
            vals["x_raet_contract_type"] = contract
        working_day = _raet_pick(detail, "working_day")
        if working_day:
            vals["x_raet_working_day"] = working_day
        position = _raet_pick(detail, "cod_position")
        if position:
            vals["x_raet_position_code"] = position
            self._raet_set_if_field(vals, "job_title", position)
            self._raet_set_if_field(
                vals, "job_id", self._raet_get_job(position, company))
        department = _raet_pick(detail, "cod_deparment", "cod_department")
        if department:
            vals["x_raet_department_code"] = department
            self._raet_set_if_field(
                vals, "department_id",
                self._raet_get_department(department, company))

    def _raet_apply_flat_address(self, vals, detail):
        """Domicilio desde campos planos: 'address', 'postal_code', etc."""
        street = _raet_pick(detail, "address")
        self._raet_set_if_field(vals, "private_street", street and str(street))
        self._raet_set_if_field(vals, "private_zip",
                                _raet_pick(detail, "postal_code"))
        reference = _raet_pick(detail, "address_reference")
        if reference:
            vals["x_raet_address_reference"] = reference
        country = _raet_pick(detail, "id_code_country", "nationality")
        country_id = self._raet_find_country(country)
        self._raet_set_if_field(vals, "private_country_id", country_id)

    # ===================================================================== #
    # Upsert
    # ===================================================================== #
    @api.model
    def _raet_find_existing(self, vals, company):
        """Busca el empleado existente por id interno, legajo/codbar o DNI."""
        Employee = self.with_context(active_test=False)
        internal = vals.get("x_raet_internal_id")
        external = vals.get("x_raet_external_id")
        identification = vals.get("identification_id")
        emp = self.browse()
        if internal:
            emp = Employee.search([
                ("x_raet_internal_id", "=", internal),
                ("company_id", "=", company.id)], limit=1)
        if not emp and external:
            emp = Employee.search([
                "|", ("x_raet_external_id", "=", external),
                ("barcode", "=", external),
                ("company_id", "=", company.id)], limit=1)
        # Match por DNI (identificacion) cuando no hay id interno/legajo previo.
        if not emp and identification and "identification_id" in self._fields:
            emp = Employee.search([
                ("identification_id", "=", identification),
                ("company_id", "=", company.id)], limit=1)
        return emp

    @api.model
    def _raet_upsert(self, vals, company):
        emp = self._raet_find_existing(vals, company)
        if emp:
            emp.write(vals)
            return emp, False
        emp = self.create(vals)
        return emp, True

    # ===================================================================== #
    # Sincronización por empresa
    # ===================================================================== #
    def _raet_log_line(self, log, raet_id, payload, exc=None, state="error",
                       external_id=None, name=None):
        """Crea (y commitea) una línea de detalle del log para un empleado.

        Se confirma de inmediato para que el error quede registrado y visible
        aunque el proceso se interrumpa después. ``payload`` es la respuesta
        cruda de RAET (dict) que se serializa a JSON para diagnóstico.
        """
        raw = False
        if payload is not None:
            try:
                raw = json.dumps(payload, ensure_ascii=False, indent=2,
                                 default=str)[:50000]
            except (TypeError, ValueError):
                raw = str(payload)[:50000]
        self.env["raet.sync.log.line"].sudo().create({
            "log_id": log.id,
            "raet_id": raet_id and str(raet_id) or False,
            "external_id": external_id and str(external_id) or False,
            "name": name or False,
            "state": state,
            "message": exc is not None and str(exc) or False,
            "payload": raw,
        })
        self.env.cr.commit()

    def _raet_sync_company(self, company, client=None, updated_from=None, log=None):
        """Sincroniza el padrón de una empresa (tenant). Devuelve el log.

        Si se recibe ``log`` (p. ej. un trabajo encolado) se reutiliza y se pasa
        a estado 'running'; si no, se crea uno nuevo. Así el mismo método sirve
        tanto para la ejecución directa como para la cola asíncrona del cron.
        """
        self = self.sudo()
        tenant = company.raet_tenant_id
        if not tenant:
            raise UserError(_(
                "La empresa '%s' no tiene configurado el Tenant RAET.") % company.display_name)
        client = client or self._raet_get_client()
        if log:
            log.write({
                "tenant": tenant,
                "updated_from": updated_from or "",
                "state": "running",
                "date_start": fields.Datetime.now(),
            })
        else:
            log = self.env["raet.sync.log"].create({
                "company_id": company.id,
                "tenant": tenant,
                "updated_from": updated_from or "",
                "state": "running",
            })
        # Deja constancia inmediata de que la corrida empezó: si el worker HTTP
        # muere por timeout, el log seguirá visible en estado 'running' en lugar
        # de perderse en el rollback de la transacción.
        self.env.cr.commit()
        _logger.info(
            "RAET: inicio sync empresa=%s tenant=%s updatedFrom=%s (log id=%s)",
            company.name, tenant, updated_from or "(todo)", log.id)
        created = updated = errors = total = 0
        error_lines = []
        try:
            for summary in client.iter_employee_changes(tenant, updated_from):
                raet_id = summary.get("id")
                if raet_id is None:
                    continue
                total += 1
                detail = None
                try:
                    detail = client.get_employee(tenant, raet_id)
                    if not detail:
                        error_lines.append("rh-%s: sin detalle (respuesta vacía)" % raet_id)
                        self._raet_log_line(
                            log, raet_id, summary,
                            exc="El detalle del empleado vino vacío.",
                            name=(summary or {}).get("name"))
                        errors += 1
                        continue
                    phases = client.get_employee_phases(tenant, raet_id)
                    structures = client.get_employee_structures(tenant, raet_id)
                    addresses = client.get_employee_addresses(tenant, raet_id)
                    vals = self._raet_build_values(
                        detail, phases, structures, addresses, tenant, company)
                    _emp, is_new = self._raet_upsert(vals, company)
                    if is_new:
                        created += 1
                    else:
                        updated += 1
                    # commit incremental para padrones grandes
                    self.env.cr.commit()
                except Exception as exc:  # noqa: BLE001
                    self.env.cr.rollback()
                    errors += 1
                    error_lines.append("rh-%s: %s" % (raet_id, exc))
                    _logger.exception("RAET: error procesando empleado %s (tenant %s)",
                                      raet_id, tenant)
                    # Registrar el error como línea del log, con el JSON recibido
                    # (detalle si se obtuvo, o el resumen) para diagnóstico/mapeo.
                    payload = detail if detail else summary
                    self._raet_log_line(
                        log, raet_id, payload, exc=exc,
                        name=(payload or {}).get("name"))
                # Progreso periódico: persistir contadores parciales y dejar
                # rastro en el log del servidor para padrones grandes.
                if total % 25 == 0:
                    _logger.info(
                        "RAET: progreso tenant=%s -> procesados=%s creados=%s "
                        "actualizados=%s errores=%s",
                        tenant, total, created, updated, errors)
                    log.write({
                        "created_count": created, "updated_count": updated,
                        "error_count": errors, "total_count": total,
                    })
                    self.env.cr.commit()
            # Resolver jefes directos una vez creados todos los empleados.
            self._raet_resolve_managers(company)
            company.sudo().write({"raet_last_sync": fields.Datetime.now()})
        except Exception as exc:  # noqa: BLE001
            # Cualquier fallo no controlado a nivel de la corrida (error de API,
            # red, timeout parcial, paginación, resolución de jefes, etc.) queda
            # registrado y visible en el log en lugar de dejar la corrida colgada
            # en estado 'running'.
            self.env.cr.rollback()
            _logger.exception(
                "RAET: sincronización abortada para empresa=%s tenant=%s",
                company.name, tenant)
            is_api = isinstance(exc, RaetApiError)
            msg = (_("Error de API RAET: %s") if is_api
                   else _("Sincronización interrumpida: %s")) % exc
            if error_lines:
                msg = "%s\n\n%s" % (msg, "\n".join(error_lines))
            log.write({
                "state": "error",
                "date_end": fields.Datetime.now(),
                "created_count": created, "updated_count": updated,
                "error_count": errors, "total_count": total,
                "message": msg,
            })
            self.env.cr.commit()
            # Registrar también el fallo global como línea para que sea visible
            # junto al resto del detalle.
            self._raet_log_line(log, False, None, exc=msg, name=_("Error general"))
            # No se relanza: el error queda registrado y visible en el log para
            # que el usuario pueda diagnosticarlo, y la corrida no aborta el
            # resto de empresas.
            return log

        log.write({
            "state": "error" if errors else "done",
            "date_end": fields.Datetime.now(),
            "created_count": created, "updated_count": updated,
            "error_count": errors, "total_count": total,
            "message": "\n".join(error_lines) or _("Sincronización completada."),
        })
        self.env.cr.commit()
        _logger.info(
            "RAET: fin sync empresa=%s tenant=%s -> procesados=%s creados=%s "
            "actualizados=%s errores=%s", company.name, tenant, total, created,
            updated, errors)
        return log

    def _raet_resolve_managers(self, company):
        """Asigna parent_id buscando el jefe por externalId/codbar o DNI."""
        Employee = self.with_context(active_test=False).sudo()
        pending = Employee.search([
            ("company_id", "=", company.id),
            ("x_raet_reports_to_external", "!=", False)])
        has_identification = "identification_id" in self._fields
        for emp in pending:
            boss_ext = emp.x_raet_reports_to_external
            domain = ["|", ("x_raet_external_id", "=", boss_ext),
                      ("barcode", "=", boss_ext)]
            # En el formato plano, immediate_boss es el DNI del jefe.
            if has_identification:
                domain = ["|"] + domain + [("identification_id", "=", boss_ext)]
            domain += [("company_id", "=", company.id)]
            boss = Employee.search(domain, limit=1)
            if boss and boss.id != emp.id and emp.parent_id.id != boss.id:
                emp.parent_id = boss.id

    # ===================================================================== #
    # Entradas: cron y acción manual
    # ===================================================================== #
    @api.model
    def _raet_sync_all(self, updated_from=None, company_ids=None):
        """Recorre todas las empresas con tenant y sincronización habilitada."""
        Company = self.env["res.company"].sudo()
        domain = [("raet_tenant_id", "!=", False), ("raet_sync_enabled", "=", True)]
        if company_ids:
            domain.append(("id", "in", company_ids))
        companies = Company.search(domain)
        if not companies:
            _logger.info("RAET: no hay empresas con Tenant configurado para sincronizar.")
            return False
        client = self._raet_get_client()
        cfg = self._raet_get_config()
        for company in companies:
            uf = updated_from
            if uf is None:
                # incremental: usar última sync de la empresa o ventana por defecto.
                if company.raet_last_sync:
                    uf = fields.Date.to_string(company.raet_last_sync.date())
                else:
                    uf = fields.Date.to_string(
                        fields.Date.today() - timedelta(days=cfg["default_days"]))
            try:
                self._raet_sync_company(company, client=client, updated_from=uf)
            except Exception:  # noqa: BLE001
                _logger.exception("RAET: fallo sincronizando empresa %s", company.name)
        return True

    @api.model
    def _cron_raet_sync(self):
        """Punto de entrada del cron programado."""
        return self._raet_sync_all(updated_from=None)

    # ===================================================================== #
    # Cola asíncrona (procesada por el worker de cron)
    # ===================================================================== #
    @api.model
    def _raet_enqueue_companies(self, companies, updated_from=None):
        """Crea un trabajo en cola (``raet.sync.log`` en estado 'queued') por
        cada empresa y dispara el cron para procesarlos en segundo plano.

        Se usa desde el asistente manual para que la petición web responda al
        instante y el trabajo pesado corra en un worker de cron, sin el límite
        de tiempo del worker HTTP. Devuelve los logs encolados.
        """
        Log = self.env["raet.sync.log"].sudo()
        logs = Log.browse()
        for company in companies:
            logs |= Log.create({
                "company_id": company.id,
                "tenant": company.raet_tenant_id or "",
                "updated_from": updated_from or "",
                "state": "queued",
            })
        # Persistir la cola de inmediato para que el worker de cron (otra
        # transacción) la vea aunque la petición actual tarde en cerrar.
        self.env.cr.commit()
        cron = self.env.ref(
            "raet_hr_sync.ir_cron_raet_process_queue", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
            _logger.info(
                "RAET: encolados %s trabajo(s) de sincronización; cron disparado.",
                len(logs))
        else:
            _logger.warning(
                "RAET: no se encontró el cron de procesamiento de cola; los "
                "trabajos quedarán 'en cola' hasta la próxima ejecución programada.")
        return logs

    @api.model
    def _raet_process_queue(self, limit=None):
        """Procesa los trabajos de sincronización en estado 'queued'.

        Punto de trabajo del cron: toma cada log encolado y lo sincroniza
        reutilizando el mismo registro. El cron de Odoo evita la ejecución
        concurrente consigo mismo, por lo que no hay riesgo de procesar dos
        veces el mismo trabajo.
        """
        Log = self.env["raet.sync.log"].sudo()
        queued = Log.search([("state", "=", "queued")], order="id", limit=limit)
        if not queued:
            return False
        _logger.info("RAET: procesando %s trabajo(s) en cola.", len(queued))
        try:
            client = self._raet_get_client()
        except Exception as exc:  # noqa: BLE001
            _logger.exception("RAET: no se pudo crear el cliente para la cola")
            queued.write({
                "state": "error",
                "date_end": fields.Datetime.now(),
                "message": _("No se pudo inicializar la conexión con RAET: %s") % exc,
            })
            self.env.cr.commit()
            return False
        for log in queued:
            company = log.company_id
            if not company:
                log.write({
                    "state": "error",
                    "date_end": fields.Datetime.now(),
                    "message": _("El trabajo en cola no tiene empresa asociada."),
                })
                self.env.cr.commit()
                continue
            try:
                self._raet_sync_company(
                    company, client=client,
                    updated_from=log.updated_from or False, log=log)
            except Exception as exc:  # noqa: BLE001
                self.env.cr.rollback()
                _logger.exception(
                    "RAET: fallo procesando trabajo en cola id=%s empresa=%s",
                    log.id, company.name)
                log.write({
                    "state": "error",
                    "date_end": fields.Datetime.now(),
                    "message": _("Error al procesar el trabajo en cola: %s") % exc,
                })
                self.env.cr.commit()
        return True

    @api.model
    def _cron_raet_process_queue(self):
        """Punto de entrada del cron que procesa la cola de sincronización."""
        return self._raet_process_queue()
