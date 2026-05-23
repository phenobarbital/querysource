---
id: F002
query: Catalog all concrete MultiQuery components
type: grep+read
---

## Operators (inherit AbstractOperator)

| Class | File | Has Docstring |
|-------|------|---------------|
| Join | operators/Join.py | No |
| Concat | operators/Concat.py | Yes (1-line) |
| Melt | operators/Melt.py | No |
| Merge | operators/Merge.py | Yes (detailed) |
| GroupBy | operators/GroupBy.py | Yes (1-line) |
| Info | operators/Info.py | Yes (1-line) |
| Filter | operators/filter/flt.py | No |

## Transforms (inherit AbstractTransform)

| Class | File | Has Docstring |
|-------|------|---------------|
| tPandas | transformations/tPandas.py | Yes (detailed) |
| tOrder | transformations/tOrder.py (via tPandas) | Yes (1-line) |
| Map | transformations/Map.py | Yes (1-line) |
| correlation | transformations/correlation.py | No |
| crosstab | transformations/crosstab.py | No |
| pivot | transformations/pivot.py | No |
| Forecast | transformations/Forecast.py | No |
| GoogleMaps | transformations/google/maps.py | No |

## Sources (Thread subclasses, no common base)

| Class | File | Has Docstring |
|-------|------|---------------|
| ThreadQuery | sources/query.py | Yes |
| ThreadFile | sources/file.py | Yes (copy-paste of ThreadQuery's) |

## Outputs

| Class | File | Has Docstring |
|-------|------|---------------|
| TableOutput | outputs/tables/TableOutput/table.py | No |
| PgOutput | outputs/tables/TableOutput/postgres.py | Yes |
| MysqlOutput | outputs/tables/TableOutput/mysql.py | Yes |
| BigQueryOutput | outputs/tables/TableOutput/bigquery.py | Yes |
| MongoDBOutput | outputs/tables/TableOutput/mongodb.py | Yes |
| DocumentDBOutput | outputs/tables/TableOutput/documentdb.py | Yes |
| RethinkOutput | outputs/tables/TableOutput/rethink.py | Yes |
| SaOutput | outputs/tables/TableOutput/sa.py | No |

## Discovery
Dynamic import by filename convention — no registry.
`get_operator_module(clsname)` and `get_transform_module(clsname)` in `multi/__init__.py`.
