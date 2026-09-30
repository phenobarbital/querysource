// Copyright (C) 2018-present Jesus Lara
//
// sql_guard.rs — lexical guard for maintenance SQL (FEAT-156).
// Splits a PostgreSQL script into top-level statements and rejects destructive,
// privilege and transaction-control statements before anything reaches the database.
// Lexical, not semantic: DDL hidden inside functions is out of reach (spec §7).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Why a statement was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockedKind {
    Drop,
    Truncate,
    AlterDrop,
    DoBlock,
    Privilege,
    Role,
    CopyProgram,
    TransactionControl,
}

impl BlockedKind {
    /// snake_case name used in the `ValueError` message.
    pub fn as_str(self) -> &'static str {
        match self {
            BlockedKind::Drop => "drop",
            BlockedKind::Truncate => "truncate",
            BlockedKind::AlterDrop => "alter_drop",
            BlockedKind::DoBlock => "do_block",
            BlockedKind::Privilege => "privilege",
            BlockedKind::Role => "role",
            BlockedKind::CopyProgram => "copy_program",
            BlockedKind::TransactionControl => "transaction_control",
        }
    }
}

const TRANSACTION_CONTROL: &[&str] = &[
    "BEGIN", "START", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "ABORT",
];
const ROLE_OBJECTS: &[&str] = &["ROLE", "USER", "GROUP"];

/// Lexer states (spec §2).
#[derive(Debug, Clone, PartialEq, Eq)]
enum State {
    Normal,
    SingleQuote,
    EscapeString,
    DoubleQuote,
    Dollar(String),
    LineComment,
    BlockComment(u32),
}

/// One top-level statement found by `lex`: byte range, content flag, keyword tokens.
struct Segment {
    start: usize,
    end: usize,
    has_code: bool,
    tokens: Vec<String>,
}

/// Identifier byte: ASCII alphanumeric, `_`, `$` or any non-ASCII byte.
fn is_ident(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'$' || b >= 0x80
}

/// Flush a pending identifier run into `tokens` when it is a keyword-like token.
fn flush(sql: &str, tok_start: &mut Option<usize>, end: usize, tokens: &mut Vec<String>) {
    if let Some(s) = tok_start.take() {
        let run = &sql[s..end];
        if let Some(&first) = run.as_bytes().first() {
            if first.is_ascii_alphabetic() || first == b'_' {
                tokens.push(run.to_ascii_uppercase());
            }
        }
    }
}

/// Try to read a dollar-quote tag at `i` (`$tag$` / `$$`); returns the delimiter end (inclusive).
fn dollar_tag_end(bytes: &[u8], i: usize) -> Option<usize> {
    let mut j = i + 1;
    if let Some(&c) = bytes.get(j) {
        if c.is_ascii_alphabetic() || c == b'_' {
            while let Some(&c) = bytes.get(j) {
                if c.is_ascii_alphanumeric() || c == b'_' {
                    j += 1;
                } else {
                    break;
                }
            }
        }
    }
    if bytes.get(j) == Some(&b'$') {
        Some(j)
    } else {
        None
    }
}

fn lex(sql: &str) -> Result<Vec<Segment>, String> {
    let bytes = sql.as_bytes();
    let len = bytes.len();
    let mut segments: Vec<Segment> = Vec::new();
    let mut state = State::Normal;
    let mut seg_start = 0usize;
    let mut has_code = false;
    let mut tokens: Vec<String> = Vec::new();
    let mut tok_start: Option<usize> = None;
    // Start byte of the currently open construct (for error messages).
    let mut open_at = 0usize;
    let mut i = 0usize;

    while i < len {
        let b = bytes[i];
        match state {
            State::Normal => {
                if tok_start.is_some() && is_ident(b) {
                    i += 1;
                    continue;
                }
                match b {
                    b'\'' => {
                        // E'…' escape-string prefix: discard the adjacent `E` token.
                        let is_e = tok_start
                            .map(|s| &sql[s..i] == "E" || &sql[s..i] == "e")
                            .unwrap_or(false);
                        if is_e {
                            tok_start = None;
                            state = State::EscapeString;
                        } else {
                            flush(sql, &mut tok_start, i, &mut tokens);
                            state = State::SingleQuote;
                        }
                        has_code = true;
                        open_at = i;
                        i += 1;
                    }
                    b'"' => {
                        flush(sql, &mut tok_start, i, &mut tokens);
                        has_code = true;
                        open_at = i;
                        state = State::DoubleQuote;
                        i += 1;
                    }
                    b'$' => {
                        flush(sql, &mut tok_start, i, &mut tokens);
                        if let Some(end) = dollar_tag_end(bytes, i) {
                            let delim = sql[i..=end].to_string();
                            has_code = true;
                            open_at = i;
                            i = end + 1;
                            state = State::Dollar(delim);
                        } else {
                            // `$1` parameter or lone `$`: ordinary code byte.
                            has_code = true;
                            i += 1;
                        }
                    }
                    b'-' if bytes.get(i + 1) == Some(&b'-') => {
                        flush(sql, &mut tok_start, i, &mut tokens);
                        state = State::LineComment;
                        i += 2;
                    }
                    b'/' if bytes.get(i + 1) == Some(&b'*') => {
                        flush(sql, &mut tok_start, i, &mut tokens);
                        open_at = i;
                        state = State::BlockComment(1);
                        i += 2;
                    }
                    b';' => {
                        flush(sql, &mut tok_start, i, &mut tokens);
                        segments.push(Segment {
                            start: seg_start,
                            end: i,
                            has_code,
                            tokens: std::mem::take(&mut tokens),
                        });
                        seg_start = i + 1;
                        has_code = false;
                        i += 1;
                    }
                    _ => {
                        if is_ident(b) {
                            tok_start = Some(i);
                            has_code = true;
                        } else {
                            flush(sql, &mut tok_start, i, &mut tokens);
                            if !b.is_ascii_whitespace() {
                                has_code = true;
                            }
                        }
                        i += 1;
                    }
                }
            }
            State::SingleQuote => {
                if b == b'\'' {
                    if bytes.get(i + 1) == Some(&b'\'') {
                        i += 2;
                    } else {
                        state = State::Normal;
                        i += 1;
                    }
                } else {
                    i += 1;
                }
            }
            State::EscapeString => {
                if b == b'\\' {
                    i += 2;
                } else if b == b'\'' {
                    if bytes.get(i + 1) == Some(&b'\'') {
                        i += 2;
                    } else {
                        state = State::Normal;
                        i += 1;
                    }
                } else {
                    i += 1;
                }
            }
            State::DoubleQuote => {
                if b == b'"' {
                    if bytes.get(i + 1) == Some(&b'"') {
                        i += 2;
                    } else {
                        state = State::Normal;
                        i += 1;
                    }
                } else {
                    i += 1;
                }
            }
            State::Dollar(ref delim) => match sql[i..].find(delim.as_str()) {
                Some(off) => {
                    i += off + delim.len();
                    state = State::Normal;
                }
                None => {
                    return Err(format!(
                        "unterminated dollar-quoted body starting at byte {open_at}"
                    ));
                }
            },
            State::LineComment => {
                if b == b'\n' {
                    state = State::Normal;
                }
                i += 1;
            }
            State::BlockComment(depth) => {
                if b == b'/' && bytes.get(i + 1) == Some(&b'*') {
                    state = State::BlockComment(depth + 1);
                    i += 2;
                } else if b == b'*' && bytes.get(i + 1) == Some(&b'/') {
                    state = if depth <= 1 {
                        State::Normal
                    } else {
                        State::BlockComment(depth - 1)
                    };
                    i += 2;
                } else {
                    i += 1;
                }
            }
        }
    }

    match state {
        State::Normal | State::LineComment => {}
        State::SingleQuote | State::EscapeString => {
            return Err(format!("unterminated string literal starting at byte {open_at}"));
        }
        State::DoubleQuote => {
            return Err(format!("unterminated quoted identifier starting at byte {open_at}"));
        }
        State::Dollar(_) => {
            return Err(format!(
                "unterminated dollar-quoted body starting at byte {open_at}"
            ));
        }
        State::BlockComment(_) => {
            return Err(format!("unterminated block comment starting at byte {open_at}"));
        }
    }
    flush(sql, &mut tok_start, len, &mut tokens);
    segments.push(Segment {
        start: seg_start,
        end: len,
        has_code,
        tokens,
    });
    Ok(segments)
}

/// Split `sql` into top-level statements (trimmed, non-empty, comment-only dropped).
pub fn split_statements(sql: &str) -> Result<Vec<String>, String> {
    Ok(lex(sql)?
        .into_iter()
        .filter(|s| s.has_code)
        .map(|s| sql[s.start..s.end].trim().to_string())
        .collect())
}

/// Upper-cased keyword tokens of one statement, skipping literals, identifiers and comments.
fn keyword_tokens(stmt: &str) -> Vec<String> {
    lex(stmt)
        .map(|segs| segs.into_iter().flat_map(|s| s.tokens).collect())
        .unwrap_or_default()
}

/// Classify one statement; `None` = allowed.
pub fn classify(stmt: &str) -> Option<BlockedKind> {
    let tokens = keyword_tokens(stmt);
    let first = tokens.first()?.as_str();
    let second = tokens.get(1).map(String::as_str).unwrap_or("");
    match first {
        "DROP" => Some(BlockedKind::Drop),
        "TRUNCATE" => Some(BlockedKind::Truncate),
        "DO" => Some(BlockedKind::DoBlock),
        "GRANT" | "REVOKE" => Some(BlockedKind::Privilege),
        "CREATE" | "ALTER" if ROLE_OBJECTS.contains(&second) => Some(BlockedKind::Role),
        // SET [SESSION|LOCAL] ROLE …, SET SESSION AUTHORIZATION …, RESET ROLE / RESET SESSION AUTHORIZATION
        "SET" | "RESET" if tokens[1..].iter().any(|t| t == "ROLE" || t == "AUTHORIZATION") => {
            Some(BlockedKind::Role)
        }
        "ALTER" if tokens[1..].iter().any(|t| t == "DROP") => Some(BlockedKind::AlterDrop),
        "COPY" if tokens.iter().any(|t| t == "PROGRAM") => Some(BlockedKind::CopyProgram),
        t if TRANSACTION_CONTROL.contains(&t) => Some(BlockedKind::TransactionControl),
        _ => None,
    }
}

/// Pure-Rust entry point used by the pyfunction and by `cargo test`.
pub fn guard(sql: &str) -> Result<Vec<String>, String> {
    let statements = split_statements(sql)?;
    for (index, stmt) in statements.iter().enumerate() {
        if let Some(kind) = classify(stmt) {
            let preview: String = stmt.chars().take(80).collect();
            return Err(format!(
                "statement {}: {} is not allowed: {}",
                index + 1,
                kind.as_str(),
                preview
            ));
        }
    }
    Ok(statements)
}

/// Python: `sql_guard(sql: str) -> list[str]`; raises `ValueError`.
#[pyfunction]
#[pyo3(signature = (sql))]
pub fn sql_guard(sql: &str) -> PyResult<Vec<String>> {
    guard(sql).map_err(|e| PyValueError::new_err(e))
}

#[cfg(test)]
mod tests {
    use super::*;

    const REFRESH_CTE: &str = "WITH params AS (SELECT (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date AS start_date), \
deleted AS (DELETE FROM wm_assembly.employee_detail_profile p USING params \
WHERE p.activity_date >= params.start_date AND p.activity_date < CURRENT_DATE RETURNING 1) \
INSERT INTO wm_assembly.employee_detail_profile (employee_id, activity_date) \
SELECT ad.employee_id, ad.activity_date FROM activity_days ad, params WHERE ad.activity_date >= params.start_date";

    fn blocked(sql: &str) -> String {
        guard(sql).expect_err(sql)
    }

    fn assert_kind(sql: &str, kind: &str) {
        let msg = blocked(sql);
        assert!(
            msg.starts_with(&format!("statement 1: {kind} is not allowed")),
            "{sql} -> {msg}"
        );
    }

    #[test]
    fn blocks_drop_any_case() {
        assert!(blocked("DROP TABLE x").starts_with("statement 1: drop is not allowed: DROP TABLE x"));
        assert!(blocked("dRoP schema s cascade").contains(": drop is not allowed"));
    }

    #[test]
    fn keywords_in_literals_and_comments_are_ignored() {
        for sql in [
            "SELECT 'drop table x'",
            "SELECT 1 -- DROP",
            "SELECT /* DROP */ 1",
            "SELECT $$DROP$$",
            "SELECT $fn$ DROP $fn$",
            "SELECT \"drop\" FROM t",
        ] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }

    #[test]
    fn semicolon_in_literal_does_not_split() {
        assert_eq!(
            guard("DELETE FROM t WHERE a = ';'; INSERT INTO t VALUES (1);").unwrap().len(),
            2
        );
    }

    #[test]
    fn blocks_truncate() {
        assert_kind("TRUNCATE TABLE t", "truncate");
        assert_kind("truncate t", "truncate");
    }

    #[test]
    fn blocks_alter_drop_column() {
        assert_kind("ALTER TABLE t DROP COLUMN c", "alter_drop");
    }

    #[test]
    fn blocks_alter_drop_constraint() {
        assert_kind("ALTER TABLE t DROP CONSTRAINT k", "alter_drop");
        assert_kind("ALTER TABLE t ALTER COLUMN c DROP DEFAULT", "alter_drop");
    }

    #[test]
    fn blocks_do_block() {
        assert_kind("DO $$ BEGIN EXECUTE 'DROP TABLE x'; END $$", "do_block");
    }

    #[test]
    fn blocks_grant() {
        assert_kind("GRANT SELECT ON t TO u", "privilege");
    }

    #[test]
    fn blocks_revoke() {
        assert_kind("REVOKE ALL ON t FROM u", "privilege");
    }

    #[test]
    fn blocks_create_role() {
        assert_kind("CREATE ROLE r", "role");
    }

    #[test]
    fn blocks_alter_user() {
        assert_kind("ALTER USER u WITH PASSWORD 'x'", "role");
        assert_kind("ALTER ROLE r DROP x", "role");
    }

    #[test]
    fn blocks_create_group() {
        assert_kind("CREATE GROUP g", "role");
    }

    #[test]
    fn blocks_set_role() {
        assert_kind("SET ROLE admin", "role");
        assert_kind("SET LOCAL ROLE x", "role");
        assert_kind("SET SESSION AUTHORIZATION x", "role");
        assert_kind("RESET ROLE", "role");
        assert_eq!(guard("SET search_path = x").unwrap().len(), 1);
        assert_eq!(guard("SET statement_timeout = 5000").unwrap().len(), 1);
    }

    #[test]
    fn blocks_copy_program() {
        assert_kind("COPY t FROM PROGRAM 'x'", "copy_program");
    }

    #[test]
    fn allows_copy_without_program() {
        assert_eq!(guard("COPY t TO STDOUT").unwrap().len(), 1);
    }

    #[test]
    fn blocks_transaction_control() {
        for sql in [
            "BEGIN",
            "START TRANSACTION",
            "COMMIT",
            "END",
            "ROLLBACK",
            "SAVEPOINT s",
            "RELEASE s",
            "ABORT",
        ] {
            assert_kind(sql, "transaction_control");
        }
    }

    #[test]
    fn allows_dml_and_ddl() {
        for sql in [
            "WITH a AS (SELECT 1) SELECT * FROM a",
            "SELECT 1",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1",
            "DELETE FROM t",
            "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
            "CREATE TABLE t (a int)",
            "CREATE INDEX i ON t (a)",
            "CREATE VIEW v AS SELECT 1",
            "ALTER TABLE t ADD COLUMN c int",
            "CALL proc()",
            "REFRESH MATERIALIZED VIEW m",
            "ANALYZE t",
        ] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }

    #[test]
    fn allows_data_modifying_cte() {
        assert_eq!(guard(REFRESH_CTE).unwrap().len(), 1);
    }

    #[test]
    fn nested_block_comment() {
        assert_eq!(guard("SELECT /* a /* DROP */ still comment */ 1").unwrap().len(), 1);
    }

    #[test]
    fn escape_string_backslash_quote() {
        assert_eq!(guard("SELECT E'it\\'s; DROP'").unwrap().len(), 1);
    }

    #[test]
    fn dollar_param_is_not_a_quote() {
        assert_eq!(guard("SELECT $1; DELETE FROM t WHERE id = $2").unwrap().len(), 2);
    }

    #[test]
    fn drops_empty_and_comment_only() {
        assert_eq!(
            guard("DELETE FROM t; ; -- c\n INSERT INTO t VALUES (1);").unwrap().len(),
            2
        );
    }

    #[test]
    fn unterminated_literal() {
        assert!(blocked("SELECT 'abc").contains("unterminated"));
    }

    #[test]
    fn unterminated_escape_string() {
        assert!(blocked("SELECT E'abc\\'").contains("unterminated"));
    }

    #[test]
    fn unterminated_identifier() {
        assert!(blocked("SELECT \"abc").contains("unterminated"));
    }

    #[test]
    fn unterminated_dollar_body() {
        assert!(blocked("SELECT $$abc").contains("unterminated"));
    }

    #[test]
    fn unterminated_block_comment() {
        assert!(blocked("SELECT /* abc").contains("unterminated"));
    }

    #[test]
    fn first_blocked_statement_is_reported() {
        assert!(blocked("SELECT 1; DROP TABLE x; TRUNCATE y").starts_with("statement 2: drop"));
    }

    #[test]
    fn preview_is_80_chars_and_utf8_safe() {
        let sql = format!("DROP TABLE {}", "ñ".repeat(100));
        let msg = blocked(&sql);
        let after = msg.splitn(3, ": ").nth(2).unwrap_or("");
        assert_eq!(after.chars().count(), 80);
    }

    #[test]
    fn no_panic_on_odd_input() {
        for sql in ["", ";", "'", "E", "E'", "$", "$a", "$a$", "-", "/", "é;é", "\\", "SELECT E'\\"] {
            let _ = guard(sql);
        }
    }
}
