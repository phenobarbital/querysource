use crate::parser::{py_to_value, BaseParser, ConditionValue};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

#[pyclass]
pub struct SQLParser {
    base: BaseParser,
    query_raw: String,
}

#[pymethods]
impl SQLParser {
    #[new]
    #[pyo3(signature = (query_raw, conditions=None, **kwargs))]
    fn new(
        query_raw: String,
        conditions: Option<&Bound<'_, PyDict>>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let mut final_conditions = HashMap::new();

        // Merge conditions if provided positionally
        if let Some(c) = conditions {
            for (k, v) in c {
                final_conditions.insert(k.extract::<String>()?, py_to_value(&v)?);
            }
        }

        // Merge kwargs
        if let Some(dict) = kwargs {
            for (k, v) in dict {
                final_conditions.insert(k.extract::<String>()?, py_to_value(&v)?);
            }
        }

        let mut parser = BaseParser::new(final_conditions);
        // Process attributes immediately upon construction for this PoC
        parser.process_conditions();

        Ok(SQLParser {
            base: parser,
            query_raw,
        })
    }

    fn build_query(&mut self) -> PyResult<String> {
        let mut sql = self.query_raw.clone();

        // SafeDict-like replacement implementation
        // 1. Schema/Table
        if let Some(schema) = &self.base.schema {
            sql = sql.replace("{schema}", schema);
        }
        if let Some(table) = &self.base.table {
            sql = sql.replace("{table}", table);
            sql = sql.replace("{tablename}", table);
        }

        // 2. Fields
        let fields_str = if self.base.fields.is_empty() {
            "*".to_string()
        } else {
            self.base.fields.join(", ")
        };
        sql = sql.replace("{fields}", &fields_str);

        // 3. Filter (Where)
        let where_clause = self.build_where_clause();
        if !where_clause.is_empty() {
            // Basic replacement logic, could be more robust
            if sql.contains("{filter}") {
                sql = sql.replace("{filter}", &format!("WHERE {}", where_clause));
            } else if sql.contains("{where_cond}") {
                sql = sql.replace("{where_cond}", &format!("WHERE {}", where_clause));
            } else {
                // Append if not present? Following logic to append
                if !sql.to_uppercase().contains("WHERE") {
                    sql = format!("{} WHERE {}", sql, where_clause);
                } else {
                    sql = format!("{} AND {}", sql, where_clause);
                }
            }
        } else {
            sql = sql.replace("{filter}", "").replace("{where_cond}", "");
        }

        // 4. Group By
        if !self.base.grouping.is_empty() {
            let group_str = format!("GROUP BY {}", self.base.grouping.join(", "));
            if sql.contains("{grouping}") {
                sql = sql.replace("{grouping}", &group_str);
            } else {
                sql = format!("{} {}", sql, group_str);
            }
        } else {
            sql = sql.replace("{grouping}", "");
        }

        // 5. Order By
        if !self.base.ordering.is_empty() {
            let order_str = format!("ORDER BY {}", self.base.ordering.join(", "));
            // Assuming simplistic replacement or append
            sql = format!("{} {}", sql, order_str);
        }

        // 6. Limit/Offset
        if let Some(limit) = self.base.limit {
            if sql.contains("{limit}") {
                sql = sql.replace("{limit}", &format!("LIMIT {}", limit));
            } else {
                sql = format!("{} LIMIT {}", sql, limit);
            }
        } else {
            sql = sql.replace("{limit}", "");
        }

        if let Some(offset) = self.base.offset {
            if sql.contains("{offset}") {
                sql = sql.replace("{offset}", &format!("OFFSET {}", offset));
            } else {
                sql = format!("{} OFFSET {}", sql, offset);
            }
        } else {
            sql = sql.replace("{offset}", "");
        }

        // Cleanup empty placeholders
        sql = sql.replace("{and_cond}", "");

        Ok(sql)
    }
}

impl SQLParser {
    fn build_where_clause(&self) -> String {
        let mut conditions = Vec::new();
        for (key, val) in &self.base.filter {
            match val {
                ConditionValue::String(s) => conditions.push(format!("{} = '{}'", key, s)),
                ConditionValue::Int(i) => conditions.push(format!("{} = {}", key, i)),
                ConditionValue::Float(f) => conditions.push(format!("{} = {}", key, f)),
                ConditionValue::Bool(b) => conditions.push(format!("{} = {}", key, b)),
                ConditionValue::List(l) => {
                    // Simplified IN clause
                    let values: Vec<String> = l
                        .iter()
                        .map(|v| match v {
                            ConditionValue::String(s) => format!("'{}'", s),
                            ConditionValue::Int(i) => i.to_string(),
                            _ => "NULL".to_string(),
                        })
                        .collect();
                    conditions.push(format!("{} IN ({})", key, values.join(", ")));
                }
                ConditionValue::Dict(d) => {
                    // Handle operators like {">": 10}
                    for (op, v) in d {
                        let val_str = match v {
                            ConditionValue::String(s) => format!("'{}'", s),
                            ConditionValue::Int(i) => i.to_string(),
                            _ => "NULL".to_string(),
                        };
                        conditions.push(format!("{} {} {}", key, op, val_str));
                    }
                }
                _ => {}
            }
        }
        conditions.join(" AND ")
    }
}
