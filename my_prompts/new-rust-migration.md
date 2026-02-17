# QuerySource Parsers

Querysource es una librería para tener una colección de queries "con nombre" (named-queries) que soporta parsing, filtrado, etc.

ejemplo sencillo:
imagina que tengo este query:
`SELECT {fields} FROM table.schema {filter}`

si a la API REST le envío un json como:
```json
{
  "fields": ["campo1", "campo2"],
  "filter": {
     "campo3": "valor"
  }
}
```

Lo que ocurrirá es que el parser SQL (en cython) transformará la sentencia en:
`SELECT campo1, campo2 FROM table.schema WHERE campo3 = 'valor'`

Hay un parser base:
`AbstractParser` que contiene métodos como "set_where" donde se computan los valores que tendría una expresión como:
"campo3": "TODAY"
o `set_conditions` para hacer reemplazo dentro del string de condiciones por posición (tipo f-string)

A partir de AbstractParser, se crean los otros parsers, como SQLParser o MongoParser.


## Task
- migrar todos los parsers a Rust (usando pyO3), empezando por AbstractParser, la idea de migrarlo a Rust es la velocidad de manipulación de strings y la capacidad de paralelización.
- en Rust podrían ejecutarse en paralelo el procesamiento de "conditions" y de "filter", "group" y "order" y juntarse al final, actualmente es un proceso secuencial, aunque migrado a Cython.
- El cómputo de condiciones de filtrado (e.g. `filter_conditions` en parsers/sql.pyx) pudiera hacerse en paralelo al igual que el proceso de condiciones como `order_by`, 'group_by`, etc.

## Resultados:

que un query como este:
`SELECT {fields} FROM table.schema {filter}`
pueda convertirse en esto:
`SELECT campo1, campo2 FROM table.schema WHERE campo3 = 'valor'`
pasando esto:
```json
{
  "fields": ["campo1", "campo2"],
  "filter": {
     "campo3": "valor"
  }
}
```

Los nombres de componentes (ejemplo: SQLParser) deberían mantener sus nombres al crearlos en Rust para mantener compatibilidad.
Se quiere un drop-in replacement del SQLParser (y en el futuro, otros parsers) desde Cython a Rust.