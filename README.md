# RAET / Visma Latam HR Sync (Odoo 19)

Módulo de Odoo 19 que conecta con la API REST de **RAET / Visma Latam** para
importar y mantener actualizado el padrón de empleados de varias empresas
(*tenants*) dentro de `hr.employee`.

## Qué hace

- Login OAuth (`grant_type=password`) y consumo de los endpoints de RAET.
- Trae el padrón completo de cada empresa usando su **Tenant RAET**.
- Mapea 1 a 1 la mayor cantidad de campos posibles a los campos estándar de
  Odoo y crea campos `x_raet_*` para todo lo que RAET tiene y Odoo no.
- *Upsert* (alta/actualización) de empleados identificándolos por
  **número de legajo / codbar** (`externalId` de RAET → campo nativo `barcode`),
  guardando además el **id interno** de RAET (`x_raet_internal_id`) para las
  llamadas a la API.
- Sincronización por **cron** (incremental por `updatedFrom`) y por
  **asistente manual**.
- Historial de cada corrida en `raet.sync.log`.

## Instalación

1. Copiar la carpeta `raet_hr_sync/` en el `addons_path` de Odoo 19.
2. Requiere la librería Python `requests` (incluida en Odoo).
3. *Aplicaciones* → *Actualizar lista* → instalar **RAET / Visma Latam HR Sync**.

## Configuración

1. **Credenciales globales**: *Ajustes* → pestaña **RAET HR**. Cargar usuario,
   contraseña y, si difieren de los valores por defecto, las URLs de los
   endpoints. Botón **Probar conexión RAET** para validar el login.
2. **Tenant por empresa**: *Ajustes* → *Usuarios y empresas* → *Empresas* →
   abrir cada empresa y completar **Tenant RAET** (header `X-RAET-Tenant-Id`).
   Marcar *Sincronizar con RAET* para incluirla en el cron.

## Uso

- **Manual**: *Empleados* → *Configuración* → *RAET* → *Sincronizar empleados*.
  Elegir modo (incremental / completo), fecha `updatedFrom` y empresas.
- **Automático**: activar el cron *"RAET: Sincronizar padrón de empleados"*
  (viene desactivado por defecto) en *Ajustes técnicos* → *Acciones planificadas*.
  Corre incremental: usa la última fecha de sync de cada empresa, o la ventana
  de "Días por defecto" si nunca se sincronizó.

## Endpoints de RAET utilizados

| Propósito | Método / URL |
|-----------|--------------|
| Login | `POST {admin_url}/authentication/login` (form-urlencoded) |
| Tenants | `GET {admin_url}/account/tenants?pageSize=500` |
| Lista / novedades | `GET {api_url}/employees?updatedFrom=YYYY-MM-DD&pageSize=500&page=N` |
| Detalle | `GET {api_url}/employees/rh-{id}` |
| Fases (baja) | `GET {api_url}/employees/rh-{id}/phases` |
| Estructuras | `GET {api_url}/employees/rh-{id}/structures` |
| Domicilios | `GET {api_url}/employees/rh-{id}/addresses` |

Headers en cada llamada: `Authorization: Bearer {token}` y
`X-RAET-Tenant-Id: {tenant}`.

## Mapeo de campos (resumen)

| RAET | Odoo |
|------|------|
| `externalId` | `barcode` (legajo/codbar) + `x_raet_external_id` |
| `id` | `x_raet_internal_id` |
| `firstName`+`middleName`+`lastName`+`familyName` | `name` |
| `genre` | `gender` |
| `dateOfBirth` | `birthday` |
| `placeOfBirth` / `countryOfBirth` | `place_of_birth` / `country_of_birth` |
| `nationalities[0].description` | `country_id` (+ `x_raet_nationality_desc`) |
| `maritalStatus.description` | `marital` (+ `x_raet_marital_desc`) |
| `nationalIdentificationNumbers[0].number` | `identification_id` |
| `fiscalNumbers[0].number` | `ssnid` (+ `x_raet_fiscal_number`) |
| `email` | `work_email` / `private_email` |
| `phones[]` | `mobile_phone` / `work_phone` / `private_phone` |
| `hiringDate` / `dateOfCountryEntry` | `x_raet_hiring_date` / `x_raet_entry_date` |
| `reportsToExternalId` | `parent_id` (resuelto por legajo) |
| phases `endDate` / `decouplingCause` | `x_raet_end_date` / `x_raet_low_motive` |
| structures type 5 | `x_raet_imput_center` / `x_raet_cost_center` |
| structures type 18 | `x_raet_contract_type` |
| structures type 21 | `x_raet_working_day` |
| structures type 4 | `job_id` / `job_title` / `x_raet_position_code` |
| structures type 6 | `department_id` / `x_raet_department_code` |
| addresses[0] | `private_street` / `private_city` / `private_zip` / `x_raet_address_reference` |

> Los campos estándar sólo se completan si existen en la instancia (se valida
> contra `self._fields`), por lo que el módulo es tolerante a diferencias de
> edición (Community / Enterprise) y versiones.

## Notas

- El *upsert* hace `commit` por empleado para soportar padrones grandes sin
  perder lo ya procesado ante un error puntual; los errores quedan listados en
  el log de la corrida.
- Los jefes directos se resuelven en una segunda pasada, una vez creados todos
  los empleados de la empresa.
