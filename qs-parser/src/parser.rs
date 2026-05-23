use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ConditionValue {
    String(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    List(Vec<ConditionValue>),
    Dict(HashMap<String, ConditionValue>),
    Null,
}

impl From<String> for ConditionValue {
    fn from(s: String) -> Self {
        ConditionValue::String(s)
    }
}

// Helper to convert PyObject to ConditionValue
pub fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<ConditionValue> {
    if obj.is_none() {
        Ok(ConditionValue::Null)
    } else if let Ok(s) = obj.extract::<String>() {
        Ok(ConditionValue::String(s))
    } else if let Ok(b) = obj.extract::<bool>() {
        Ok(ConditionValue::Bool(b))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(ConditionValue::Int(i))
    } else if let Ok(f) = obj.extract::<f64>() {
        Ok(ConditionValue::Float(f))
    } else if let Ok(l) = obj.downcast::<pyo3::types::PyList>() {
        let mut vec = Vec::new();
        for item in l {
            vec.push(py_to_value(&item)?);
        }
        Ok(ConditionValue::List(vec))
    } else if let Ok(d) = obj.downcast::<pyo3::types::PyDict>() {
        let mut map = HashMap::new();
        for (k, v) in d {
            map.insert(k.extract::<String>()?, py_to_value(&v)?);
        }
        Ok(ConditionValue::Dict(map))
    } else {
        // Fallback to string representation for unknown types
        Ok(ConditionValue::String(obj.to_string()))
    }
}

pub trait AbstractParserImpl {
    fn parse(&mut self) -> PyResult<String>;
    fn set_attributes(&mut self);
}

#[derive(Clone, Debug)]
pub struct BaseParser {
    pub conditions: HashMap<String, ConditionValue>,
    pub fields: Vec<String>,
    pub filter: HashMap<String, ConditionValue>,
    pub ordering: Vec<String>,
    pub grouping: Vec<String>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
    pub schema: Option<String>,
    pub table: Option<String>,
    pub query_parsed: Option<String>,
}

impl BaseParser {
    pub fn new(conditions: HashMap<String, ConditionValue>) -> Self {
        BaseParser {
            conditions,
            fields: Vec::new(),
            filter: HashMap::new(),
            ordering: Vec::new(),
            grouping: Vec::new(),
            limit: None,
            offset: None,
            schema: None,
            table: None,
            query_parsed: None,
        }
    }

    pub fn process_conditions(&mut self) {
        // Extract fields
        if let Some(ConditionValue::List(fields)) = self.conditions.get("fields") {
            self.fields = fields
                .iter()
                .filter_map(|v| {
                    if let ConditionValue::String(s) = v {
                        Some(s.clone())
                    } else {
                        None
                    }
                })
                .collect();
        }

        // Extract limit
        if let Some(val) = self
            .conditions
            .get("limit")
            .or_else(|| self.conditions.get("querylimit"))
        {
            if let ConditionValue::Int(i) = val {
                self.limit = Some(*i);
            } else if let ConditionValue::String(s) = val {
                if let Ok(i) = s.parse::<i64>() {
                    self.limit = Some(i);
                }
            }
        }

        // Extract offset
        if let Some(val) = self
            .conditions
            .get("offset")
            .or_else(|| self.conditions.get("_offset"))
        {
            if let ConditionValue::Int(i) = val {
                self.offset = Some(*i);
            } else if let ConditionValue::String(s) = val {
                if let Ok(i) = s.parse::<i64>() {
                    self.offset = Some(i);
                }
            }
        }

        // Extract schema/table
        if let Some(ConditionValue::String(s)) = self.conditions.get("schema") {
            self.schema = Some(s.clone());
        }
        if let Some(ConditionValue::String(t)) = self.conditions.get("tablename") {
            self.table = Some(t.clone());
        }

        // Extract Filter (Where conditions)
        if let Some(ConditionValue::Dict(f)) = self
            .conditions
            .get("filter")
            .or_else(|| self.conditions.get("where_cond"))
        {
            self.filter = f.clone();
        }

        // Parallel processing of grouping and ordering if needed,
        // effectively pulling them out of conditions map.
        // For PoC we just extract them.
        if let Some(ConditionValue::List(o)) = self
            .conditions
            .get("ordering")
            .or_else(|| self.conditions.get("order_by"))
        {
            self.ordering = o
                .iter()
                .filter_map(|v| {
                    if let ConditionValue::String(s) = v {
                        Some(s.clone())
                    } else {
                        None
                    }
                })
                .collect();
        }

        if let Some(ConditionValue::List(g)) = self
            .conditions
            .get("grouping")
            .or_else(|| self.conditions.get("group_by"))
        {
            self.grouping = g
                .iter()
                .filter_map(|v| {
                    if let ConditionValue::String(s) = v {
                        Some(s.clone())
                    } else {
                        None
                    }
                })
                .collect();
        }
    }
}
