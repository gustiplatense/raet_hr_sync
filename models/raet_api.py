# -*- coding: utf-8 -*-
"""
Cliente HTTP para la API de RAET / Visma Latam.

Reproduce el flujo del proyecto VB.NET original:

* Login (OAuth password grant)::

      POST {admin_url}/authentication/login
      Content-Type: application/x-www-form-urlencoded
      username=...&password=...&grant_type=password
      -> { "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }

* Todas las llamadas posteriores envían::

      Authorization: Bearer {access_token}
      X-RAET-Tenant-Id: {tenant}

* Tenants::          GET  {admin_url}/account/tenants?pageSize=500
* Empleados (lista): GET  {api_url}/employees?updatedFrom=YYYY-MM-DD&pageSize=500&page=N
* Detalle:           GET  {api_url}/employees/rh-{id}
* Fases (baja):      GET  {api_url}/employees/rh-{id}/phases
* Estructuras:       GET  {api_url}/employees/rh-{id}/structures
* Domicilios:        GET  {api_url}/employees/rh-{id}/addresses

Esta clase NO depende del ORM de Odoo: recibe la configuración por parámetros y
sólo levanta ``RaetApiError`` en caso de fallo, para que la capa de modelos
decida cómo registrar el error.
"""
import logging
import time

try:
    import requests
except ImportError:  # pragma: no cover - requests viene con Odoo
    requests = None

_logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 500
DEFAULT_TIMEOUT = 60
# Tope de seguridad para evitar bucles infinitos de paginación si la API
# ignora el parámetro 'page' y devuelve siempre páginas llenas.
MAX_PAGES = 1000


class RaetApiError(Exception):
    """Error de comunicación o de negocio con la API de RAET."""


class RaetClient(object):
    """Cliente fino y reutilizable de la API de RAET / Visma Latam."""

    def __init__(self, login_url, admin_url, api_url, username, password,
                 grant_type="password", page_size=DEFAULT_PAGE_SIZE,
                 timeout=DEFAULT_TIMEOUT):
        if requests is None:
            raise RaetApiError(
                "La librería de Python 'requests' no está instalada en el "
                "servidor de Odoo.")
        self.login_url = (login_url or "").strip()
        self.admin_url = (admin_url or "").rstrip("/")
        self.api_url = (api_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.grant_type = grant_type or "password"
        self.page_size = page_size or DEFAULT_PAGE_SIZE
        self.timeout = timeout or DEFAULT_TIMEOUT
        self._token = None

    # ------------------------------------------------------------------ #
    # Autenticación
    # ------------------------------------------------------------------ #
    def login(self):
        """Obtiene y cachea el access_token Bearer."""
        if not self.login_url:
            raise RaetApiError("No está configurada la URL de login de RAET.")
        data = {
            "username": self.username or "",
            "password": self.password or "",
            "grant_type": self.grant_type,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        _logger.info("RAET: login en %s (usuario=%s)", self.login_url, self.username)
        start = time.time()
        try:
            resp = requests.post(self.login_url, data=data, headers=headers,
                                 timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            _logger.error("RAET: fallo de conexión en login tras %.1fs: %s",
                          time.time() - start, exc)
            raise RaetApiError("No se pudo conectar al login de RAET: %s" % exc)
        elapsed = time.time() - start
        _logger.info("RAET: login respondió HTTP %s en %.1fs",
                     resp.status_code, elapsed)
        if resp.status_code != 200:
            raise RaetApiError(
                "Login RAET falló (HTTP %s): %s" % (resp.status_code, resp.text[:500]))
        try:
            payload = resp.json()
        except ValueError:
            raise RaetApiError("Respuesta de login no es JSON válido: %s" % resp.text[:500])
        token = payload.get("access_token")
        if not token:
            raise RaetApiError("Login RAET sin access_token: %s" % payload)
        self._token = token
        return token

    @property
    def token(self):
        if not self._token:
            self.login()
        return self._token

    def _headers(self, tenant):
        return {
            "Authorization": "Bearer %s" % self.token,
            "X-RAET-Tenant-Id": str(tenant or ""),
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------ #
    # GET genérico con reintento de login si expira el token
    # ------------------------------------------------------------------ #
    def _get(self, url, tenant, params=None, _retry=True):
        _logger.debug("RAET: GET %s params=%s tenant=%s", url, params or {}, tenant)
        start = time.time()
        try:
            resp = requests.get(url, headers=self._headers(tenant),
                                params=params or {}, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            _logger.error("RAET: error de red en GET %s tras %.1fs: %s",
                          url, time.time() - start, exc)
            raise RaetApiError("Error de red en GET %s: %s" % (url, exc))
        elapsed = time.time() - start
        if elapsed > 5:
            _logger.warning("RAET: GET %s tardó %.1fs (HTTP %s)",
                            url, elapsed, resp.status_code)
        else:
            _logger.debug("RAET: GET %s -> HTTP %s en %.2fs",
                          url, resp.status_code, elapsed)
        if resp.status_code == 401 and _retry:
            # token vencido -> reloguear una vez
            _logger.info("RAET: token vencido en %s, re-autenticando", url)
            self._token = None
            return self._get(url, tenant, params=params, _retry=False)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RaetApiError(
                "GET %s falló (HTTP %s): %s" % (url, resp.status_code, resp.text[:500]))
        if not resp.text:
            return None
        try:
            return resp.json()
        except ValueError:
            raise RaetApiError("Respuesta no JSON en %s: %s" % (url, resp.text[:500]))

    # ------------------------------------------------------------------ #
    # Endpoints de negocio
    # ------------------------------------------------------------------ #
    def get_tenants(self, tenant=None):
        """Devuelve la lista de tenants accesibles con la credencial."""
        url = "%s/account/tenants" % self.admin_url
        data = self._get(url, tenant or "", params={"pageSize": self.page_size})
        return data or []

    def iter_employee_changes(self, tenant, updated_from=None):
        """Itera el padrón / novedades del tenant, manejando paginación.

        Cada elemento es el *resumen* del empleado (incluye ``id`` y
        ``externalId``). Para el detalle completo usar :meth:`get_employee`.
        """
        url = "%s/employees" % self.api_url
        page = 1
        emitted = 0
        _logger.info("RAET: listando empleados tenant=%s updatedFrom=%s pageSize=%s",
                     tenant, updated_from or "(todo)", self.page_size)
        while page <= MAX_PAGES:
            params = {"pageSize": self.page_size, "page": page}
            if updated_from:
                params["updatedFrom"] = updated_from
            data = self._get(url, tenant, params=params)
            # La API suele devolver {"values": [...], "totalCount": N}, pero
            # se contempla también una lista directa por robustez.
            if isinstance(data, list):
                values = data
                total = len(data)
            else:
                data = data or {}
                values = data.get("values") or []
                total = data.get("totalCount") or 0
            _logger.info("RAET: página %s -> %s registros (totalCount=%s, acumulado=%s)",
                         page, len(values), total, emitted + len(values))
            if page == 1 and not values:
                _logger.warning(
                    "RAET: la primera página no trajo empleados. Respuesta=%.500s",
                    data)
            for emp in values:
                emitted += 1
                yield emp
            # Condiciones de parada: página no llena, o ya emitimos el total.
            if len(values) < self.page_size:
                break
            if total and emitted >= total:
                break
            page += 1
        else:
            _logger.warning(
                "RAET: se alcanzó el tope de %s páginas para tenant=%s; "
                "se detiene la paginación por seguridad.", MAX_PAGES, tenant)
        _logger.info("RAET: fin del listado tenant=%s, %s empleados emitidos",
                     tenant, emitted)

    def get_employee(self, tenant, raet_id):
        """Detalle completo del empleado (GET /employees/rh-{id})."""
        url = "%s/employees/rh-%s" % (self.api_url, raet_id)
        return self._get(url, tenant)

    def get_employee_phases(self, tenant, raet_id):
        url = "%s/employees/rh-%s/phases" % (self.api_url, raet_id)
        data = self._get(url, tenant) or {}
        return data.get("values") or []

    def get_employee_structures(self, tenant, raet_id):
        url = "%s/employees/rh-%s/structures" % (self.api_url, raet_id)
        data = self._get(url, tenant) or {}
        return data.get("values") or []

    def get_employee_addresses(self, tenant, raet_id):
        url = "%s/employees/rh-%s/addresses" % (self.api_url, raet_id)
        data = self._get(url, tenant) or {}
        return data.get("values") or []
