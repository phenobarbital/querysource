# qsurl — dialecto URL (estilo HTSQL) para Querysource

Borrador de la gramática y del parser en Rust (chumsky 0.13 + PyO3) que convierte
una ruta GET como

```
/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)
```

en el IR JSON que Querysource ya ejecuta. El parser no sabe qué motor hay detrás
del slug: produce *intención* (columnas, condiciones, orden, ventana) y es el
driver quien decide qué empuja al backend y qué post-filtra en el dataframe.

## 1. Principio de diseño: la URL es la piel, el IR es el cuerpo

Querysource ya tiene tres cosas que hacen viable esto sin tocar el ejecutor:

- un slug que resuelve a una consulta guardada con placeholders `{fields}` y `{filter}`;
- un vocabulario de filtros en JSON (`{column, expression, value}`) con operadores
  como `==`, `>=`, `contains`, `startswith`, `regex`, `is_null`;
- una etapa de post-procesado sobre dataframe (`create_filter`, `tFilter`) que
  aplica ese mismo vocabulario en memoria.

El dialecto URL es sólo un *segundo front-end* que compila al mismo IR. La
agnosticidad al motor no la garantiza el parser sino el contrato del IR: ninguna
hoja contiene SQL, CQL, Flux ni SOQL, sólo columna, operador del vocabulario y
literal tipado.

## 2. Gramática (fase 1)

Sobre la URL ya *percent-decoded*. Los espacios se toleran alrededor de cualquier
token, así que `?state = 'CA' & city ~ 'san'` es válido.

```
query      := prefix? slug selection? filter? pipe*
prefix     := '/' | '/queries/' | 'queries/'
slug       := [A-Za-z0-9_-]+
selection  := '{' field (',' field)* ','? '}'
field      := path (':as(' ident ')')?
path       := ident ('.' ident)*                 -- varios segmentos = navegación (fase 2)
filter     := '?' expr
expr       := expr '|' expr                      -- menor precedencia
            | expr '&' expr
            | '!' expr
            | '(' expr ')'
            | operand (cmp operand)?             -- operando solo = "no nulo"
cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>'
            | '~' | '!~' | '^=' | '$=' | '=~'
operand    := list | literal | call | path
list       := '(' literal (',' literal)* ')'     -- pertenencia: state=('CA','NV')
call       := ident '(' (operand (',' operand)*)? ')'
literal    := null | true | false | datetime | date | number | string
date       := DDDD-DD-DD                         -- sin comillas → tipo date
datetime   := date 'T' DD:DD (':' DD)? ('Z' | ('+'|'-') DD:DD)?
number     := '-'? int ('.' digits)?
string     := '\'' ( '\'\'' | [^'] )* '\''  |  '"' ( '\"' | [^"] )* '"'
pipe       := ':sort(' sortkey (',' sortkey)* ')'
            | ':top(' int ')' | ':limit(' int ')'
            | ':skip(' int ')' | ':offset(' int ')'
            | ':distinct'
sortkey    := ('+' | '-')? path                  -- prefijo como JSON:API: -opened
```

Decisiones que conviene defender explícitamente:

- **Gramática cerrada.** No hay forma de expresar un `UPDATE`, un `JOIN` libre
  ni una subconsulta. Un LLM sólo puede generar lecturas. Un operador de
  tubería desconocido no es un error genérico: devuelve la lista de válidos.
- **Literales tipados por el parser.** `2024-01-01` es `date`; `'2024-01-01'`
  es `str`. El driver no adivina sobre strings. `null`, `true`, `false`,
  enteros y flotantes llegan con su tipo JSON nativo.
- **Un solo símbolo por operador de texto**, en vez de funciones: `~` contiene,
  `!~` no contiene, `^=` empieza por, `$=` termina en, `=~` regex. Son cortos
  para la URL y se mapean 1:1 al vocabulario existente.
- **`=` y `==` son sinónimos.** Los modelos alternan entre ambos; rechazar uno
  sólo produce reintentos.
- **Operando desnudo = no nulo; `!col` = nulo.** Azúcar heredada de HTSQL que
  a un agente le resulta natural (`?email&!closed_at`).
- **Comparación invertida se normaliza**: `100<price` se emite como
  `price > 100`. Los operadores de texto exigen la columna a la izquierda.

## 3. Contrato del IR

```json
{
  "slug": "hisense_stores",
  "fields": ["store_id", "name", {"column": "region.name", "alias": "region"}],
  "filter": {
    "and": [
      {"column": "state_code", "expression": "==", "value": "CA"},
      {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"},
      {"or": [
        {"column": "city", "expression": "contains", "value": "san"},
        {"column": "zip", "expression": "startswith", "value": "9"}
      ]},
      {"column": "closed_at", "expression": "is_null"},
      {"column": {"fn": "lower", "args": ["name"]}, "expression": "==", "value": "acme"}
    ]
  },
  "sort": [{"column": "name", "order": "asc"}],
  "limit": 50,
  "offset": null,
  "distinct": false,
  "requires": ["select", "alias", "filter", "or", "null_check", "text_match", "functions", "navigation", "sort", "limit"]
}
```

Las hojas tienen exactamente la forma que Querysource usa hoy. Lo nuevo es que el
filtro es un árbol (`and` / `or` / `not`) y no una lista implícitamente AND; un
consumidor que sólo entienda la lista plana puede tomar `filter.and` cuando no
haya `or`/`not` dentro, y rechazar (o post-filtrar) en caso contrario.

Mapeo de operadores URL → `expression`:

| URL            | `expression`                 | Notas                                        |
|----------------|------------------------------|----------------------------------------------|
| `=`, `==`      | `==`                         | con lista → pertenencia (`isin`)             |
| `!=`           | `!=`                         | con lista → no pertenencia                   |
| `<` `<=` `>` `>=` | mismo símbolo             |                                              |
| `~` / `!~`     | `contains` / `not_contains`  |                                              |
| `^=` / `$=`    | `startswith` / `endswith`    |                                              |
| `=~`           | `regex`                      |                                              |
| `=null` / `!col` | `is_null`                  |                                              |
| `!=null` / `col` | `not_null`                 |                                              |

## 4. `requires`: cómo se mantiene la agnosticidad con motores desiguales

El parser calcula el conjunto de capacidades que la consulta exige. Cada driver
declara las suyas, y la diferencia se resuelve con **pushdown parcial**: lo que
el backend soporta viaja en la consulta nativa; el resto lo aplica la etapa de
dataframe, que ya implementa todo el vocabulario. El resultado es correcto en
todos los motores; sólo cambia dónde se paga el coste.

| Capacidad     | Postgres / BigQuery | Cassandra                      | InfluxDB (Flux)         | Salesforce (SOQL)       | REST genérico            |
|---------------|---------------------|--------------------------------|-------------------------|-------------------------|--------------------------|
| `filter` `==` | pushdown            | sólo clave de partición/índice | pushdown (tags/fields)  | pushdown                | si la API lo expone      |
| `or`          | pushdown            | post-filtro                    | pushdown                | pushdown                | normalmente post-filtro  |
| `text_match`  | `ILIKE`             | post-filtro (o SASI)           | `strings.containsStr`   | `LIKE`                  | post-filtro              |
| `regex`       | `~*`                | post-filtro                    | `=~ /re/`               | post-filtro             | post-filtro              |
| `in_list`     | `IN`                | `IN` sólo en clave             | `contains(set:)`        | `IN`                    | repetir parámetro / post |
| `sort`        | `ORDER BY`          | sólo clustering key            | `sort()`                | `ORDER BY`              | post-filtro              |
| `limit`/`offset` | pushdown         | `LIMIT` sí, `offset` post      | `limit(n, offset)`      | `LIMIT`/`OFFSET`        | paginación propia / post |
| `functions`   | lista blanca        | post-filtro                    | lista blanca            | limitado                | post-filtro              |
| `navigation`  | JOIN por FK (fase 2)| no                             | no                      | relaciones SOQL         | no                       |

La regla operativa: si `requires - capabilities(driver)` no está vacío, el
driver recibe la consulta *recortada* a lo que sabe hacer y el residuo se aplica
después; si el residuo hace explotar el volumen (un filtro post en Cassandra sin
partición), el planificador debe rechazar con un error explícito en vez de hacer
un full scan silencioso. Ese límite de coste es la única pieza que no existe hoy
y que un cliente-LLM hace obligatoria.

## 5. Errores como feedback para el modelo

Los errores se serializan con `offset`, `message`, `expected` y un `pointer`
visual, y el binding Python los entrega en el `ValueError` tal cual:

```json
{"kind":"parse","offset":18,
 "message":"unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct",
 "pointer":"stores?state='CA':order(name)\n                  ^"}
```

Devolverle este JSON al agente en el tool result cierra el bucle de
autocorrección sin prompt adicional. La misma gramática puede exportarse (Lark /
GBNF) para *constrained decoding* cuando el proveedor lo permita, y entonces el
modelo ni siquiera puede emitir algo inválido.

## 6. Codificación en la URL

`{ } ' " | & ~ ^ $ < > =` sobreviven mal a proxies y a algunos loggers, y `&` y
`=` chocan con la semántica de querystring de cualquier framework HTTP. Por eso
la ruta se acepta de dos formas equivalentes:

1. `GET /api/v2/services/queries/<query>` con la query percent-encoded en el path.
2. `GET /api/v2/services/queries/<slug>?q=<query sin slug>` para clientes que sólo
   saben poner un parámetro. El servidor concatena `slug + q` y parsea igual.

El handler decodifica una sola vez y entrega el string al parser; el parser
nunca ve `%xx`. Longitud práctica: ~8 KB en la mayoría de servidores, más que
suficiente para consultas de agente; listas `IN` largas van por `q=`.

## 7. Integración en Querysource

1. Ruta comodín `/api/v2/services/queries/{path:.*}` delante del handler actual.
2. `qsurl.parse(decoded)` → IR. Si falla, 400 con el JSON del error.
3. El slug carga la consulta guardada; `fields` sustituye `{fields}`, `filter`
   (la parte que el driver soporta) se renderiza en `{filter}` con el
   *builder* nativo del driver, `sort`/`limit`/`offset` idem.
4. El residuo de `requires` va a la cadena de transforms existente
   (`Filter`, orden y ventana en dataframe).
5. La salida (json, xlsx, csv, pdf…) no cambia: sigue siendo la negociación de
   formato de hoy (`Accept` o sufijo).

Un mismo endpoint sirve así a humanos, a front-ends y a agentes. Para el
tool-calling clásico basta exponer el JSON del IR como schema de la tool y
saltarse el parser; ambas superficies convergen en el mismo objeto.

## 8. Fase 2 (fuera de este borrador)

- **Navegación por relaciones** (`store.region.name`, `/stores/123/employees`),
  que es lo que hacía a HTSQL distinto: requiere introspección del esquema o
  metadatos declarados en el slug. El parser ya acepta rutas con puntos y marca
  `navigation` en `requires`; nada más está resuelto.
- **Agregados en contexto** (`{region, count(stores)}`, `:group(...)`): útil,
  pero rompe la equivalencia 1:1 con el `{fields}`/`{filter}` actual; conviene
  decidir primero si Querysource quiere ser motor de agregación o seguir
  delegándolo a la consulta guardada.
- **Campos calculados** (`total:=price*qty`) y funciones con lista blanca por
  driver.

## 9. Uso

```bash
cargo test                          # 11 tests: ejemplo, precedencia, literales, errores
cargo run --example parse -- "/queries/hisense_stores{id,name}?state='CA':top(10)"
maturin develop --features python   # módulo `qsurl` con parse() y requires()
```

```python
import json, qsurl
ir = json.loads(qsurl.parse("/queries/hisense_stores{store_id,name,city}?state_code='CA':top(50)"))
qsurl.requires("stores?a=1|b=2:distinct")   # ['filter', 'or', 'distinct']
```

Estructura: `src/ast.rs` (AST neutral), `src/parser.rs` (gramática chumsky),
`src/ir.rs` (lowering al JSON de Querysource + `requires`), `src/python.rs`
(binding PyO3 tras la feature `python`), `examples/parse.rs` (CLI).
