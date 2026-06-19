# QuerySource PBAC — Política de Endurecimiento

Guía para migrar desde la *baseline permisiva* (`baseline.yaml`) hacia un control
de acceso de mínimo privilegio, sin interrumpir la operación en ningún paso.

> Aplica con `QS_PBAC_ENABLED=True`. Todos los archivos `policies/*.yaml` se
> fusionan en un único conjunto al arranque. Cambiar un YAML requiere
> **reiniciar el servicio** (o disparar un `reload()` explícito).

---

## 1. Reglas del motor (lo que debes tener presente)

Por cada request QS evalúa `(resource_type, resource_name, action)`. El motor Rust
(`rs_pep`) decide así:

1. Conserva las políticas que matchean **tenant + resource + action + subject + environment**.
2. **`enforcing: true`** → la primera que matchea (mayor `priority`) decide al instante.
3. Si no hay enforcing → gana el mejor `allow` vs. el mejor `deny`:
   **el allow gana solo si su `priority` es estrictamente mayor; el deny gana en empate.**
4. Si nada matchea → `ABAC_DEFAULT_EFFECT` (env, default **`deny`**).

Capa del handler QS: **sin sesión autenticada → 404 siempre** (fail-closed),
independientemente de las políticas.

### Semántica de matching

| Campo | Vacío / omitido | Comodín | Notas |
|-------|-----------------|---------|-------|
| `resources` | matchea **todos** | `"*"` o `"*:*"` | debe ser `tipo:patrón`; glob (`*` `?`) y regex. Un token sin `:` **no matchea nada**. |
| `actions` | matchea **todas** | `"*"` | exacto o `*`. |
| `subjects` | `groups`+`users`+`roles` vacíos → **todos** | `groups: ["*"]` → todos | `exclude_*` se evalúa primero: pertenecer = no-match inmediato. |

**Tipos de recurso**: `slug`, `datasource`, `driver`, `raw_query`.
**Acciones en uso**: `slug:execute`, `slug:list`, `slug:read`, `datasource:use`,
`datasource:list`, `driver:use`, `driver:list`, `raw_query:execute`.

> `effect` debe ir en **minúsculas** (`allow` / `deny`).

---

## 2. Estrategia de endurecimiento progresivo

La baseline (`authenticated_allow_all`, priority 1, non-enforcing) es un **piso**.
Endurecer = colocar políticas con **priority mayor** que la corrijan, en este orden:

```
priority 100  enforcing  →  admin_full_access      (intocable; admins siempre pasan)
priority  90  enforcing  →  service accounts        (CI/CD, bots)
priority  50             →  deny duros              (raw_query, recursos sensibles)
priority  30             →  allow específicos       (grupo ↔ recurso)
priority   1  non-enforc →  authenticated_allow_all (baseline; se elimina al final)
```

**Tácticas clave (derivadas de las reglas del motor):**

- **Deny gana en empate** → para prohibir algo a *casi* todos pero permitir a un
  grupo, da al `allow` una `priority` mayor que la del `deny` (no igual).
- **`enforcing: true` corta** → úsalo solo para reglas absolutas (admin, o un
  deny de cumplimiento que nadie debe poder sobreescribir). No lo uses en reglas
  que quieras poder matizar después.
- **Migra por recurso**, no de golpe: añade políticas explícitas, observa los
  `PBAC denied` en logs, y solo cuando todo grupo legítimo tenga su `allow`
  explícito, **elimina `baseline.yaml`** (ese borrado es el que activa el
  mínimo privilegio real: lo no permitido pasa a deny por default).

---

## 3. Ejemplos de políticas de endurecimiento

### 3.1 Bloquear raw queries para todos salvo data-engineers

`raw_queries.yaml`:

```yaml
version: "1.0"
defaults:
  effect: deny
policies:
  # Deny duro para cualquiera: tapa la baseline (priority 1).
  - name: deny_raw_queries_default
    effect: deny
    description: "Las raw queries (SQL inline) están prohibidas por defecto."
    resources: ["raw_query:*"]
    actions: ["raw_query:execute"]
    subjects:
      groups: ["*"]
    priority: 40

  # Excepción: data-engineers SÍ pueden. priority > deny → el allow gana.
  - name: allow_raw_queries_data_engineers
    effect: allow
    description: "Data engineers pueden ejecutar raw queries."
    resources: ["raw_query:*"]
    actions: ["raw_query:execute"]
    subjects:
      groups: ["data-engineers"]
    priority: 50
```

### 3.2 Restringir un grupo a slugs con cierto prefijo

`slugs.yaml`:

```yaml
version: "1.0"
defaults:
  effect: deny
policies:
  # Analysts solo ven/ejecutan slugs "finance_*".
  - name: analysts_finance_slugs
    effect: allow
    description: "Analysts ejecutan y listan únicamente slugs finance_*."
    resources: ["slug:finance_*"]
    actions: ["slug:execute", "slug:list", "slug:read"]
    subjects:
      groups: ["analysts"]
    priority: 30

  # Cualquier slug que NO sea finance_* queda denegado para analysts.
  - name: analysts_deny_other_slugs
    effect: deny
    description: "Analysts no acceden a slugs fuera de finance_*."
    resources: ["slug:*"]
    actions: ["slug:execute", "slug:list", "slug:read"]
    subjects:
      groups: ["analysts"]
    priority: 35   # > 30: este deny gana sobre la baseline y acota el allow anterior
```

> Mientras `baseline.yaml` siga presente, los analysts conservan acceso amplio por
> el piso (priority 1) salvo donde un deny de mayor priority los acote. El recorte
> total ocurre al **eliminar la baseline**.

### 3.3 Datasource/driver sensible: solo lectura para un grupo

`datasources.yaml`:

```yaml
version: "1.0"
defaults:
  effect: deny
policies:
  - name: deny_prod_db_default
    effect: deny
    description: "El datasource de producción está vetado por defecto."
    resources: ["datasource:prod_*"]
    actions: ["datasource:use", "datasource:list"]
    subjects:
      groups: ["*"]
    priority: 40

  - name: allow_prod_db_reporting
    effect: allow
    description: "Solo el grupo reporting usa los datasources prod_*."
    resources: ["datasource:prod_*"]
    actions: ["datasource:use", "datasource:list"]
    subjects:
      groups: ["reporting"]
      exclude_groups: ["contractors"]   # ni siquiera si están en reporting
    priority: 50
```

### 3.4 Cuenta de servicio (CI/CD) — acceso total, inamovible

`superusers.yaml`:

```yaml
version: "1.0"
defaults:
  effect: deny
policies:
  - name: service_account_ci
    effect: allow
    description: "Cuenta de servicio de CI/CD con acceso total."
    resources: ["*:*"]
    actions: ["*"]
    subjects:
      users: ["ci-service@acme.com"]
    priority: 90
    enforcing: true   # corta: ninguna política posterior puede degradarla
```

### 3.5 Restricción por atributo de entorno (horario laboral)

```yaml
  - name: analysts_business_hours_only
    effect: allow
    description: "Analysts ejecutan slugs solo en horario laboral."
    resources: ["slug:*"]
    actions: ["slug:execute"]
    subjects:
      groups: ["analysts"]
    conditions:
      environment:
        is_business_hours: true   # atributos disponibles: hour, dow,
                                  # is_business_hours, is_weekend, day_segment
    priority: 30
```

---

## 4. Procedimiento de corte (de baseline a mínimo privilegio)

1. **Inventaria** grupos y los recursos que cada uno usa hoy.
2. Por cada recurso sensible, añade su `deny` (priority 40) + los `allow`
   específicos (priority 30/50) — la baseline sigue de red de seguridad.
3. Despliega, **reinicia**, y vigila los logs `PBAC denied` (info-level) y los
   patrones de uso reales durante un periodo de observación.
4. Cuando **todo grupo legítimo tenga su `allow` explícito**, elimina
   `baseline.yaml` y reinicia. A partir de aquí, lo no permitido = **deny** por
   default → mínimo privilegio efectivo.
5. (Opcional) revisa que `ABAC_DEFAULT_EFFECT` siga en `deny`.

## 5. Verificación

Validar sintaxis y resumen de políticas cargadas:

```bash
python3 -c "
import yaml, glob
for f in sorted(glob.glob('policies/*.yaml')):
    d = yaml.safe_load(open(f)) or {}
    for p in d.get('policies', []):
        print(f\"{p['priority']:>4} {'ENF' if p.get('enforcing') else '   '} \"
              f\"{p['effect']:<5} {p['name']:<35} {p['resources']}\")
" | sort -rn
```

Lee de mayor a menor priority: así ves el orden real de resolución.
