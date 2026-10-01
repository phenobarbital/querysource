// Copyright (C) 2018-present Jesus Lara
//
// sql_guard.rs — lexical guard for maintenance SQL (FEAT-156).
// Splits a PostgreSQL script into top-level statements and rejects destructive,
// privilege, transaction-control, user-defined-code and escape-function statements
// before anything reaches the database.
// Lexical, not semantic: code inside pre-existing functions is out of reach (spec §7).

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
    Copy,
    TransactionControl,
    Setting,
    ExecutableObject,
    DangerousFunction,
    ForeignAccess,
    Unparsable,
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
            BlockedKind::Copy => "copy",
            BlockedKind::TransactionControl => "transaction_control",
            BlockedKind::Setting => "setting",
            BlockedKind::ExecutableObject => "executable_object",
            BlockedKind::DangerousFunction => "dangerous_function",
            BlockedKind::ForeignAccess => "foreign_access",
            BlockedKind::Unparsable => "unparsable",
        }
    }
}

const TRANSACTION_CONTROL: &[&str] = &[
    "BEGIN", "START", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "ABORT",
];
/// Session settings that change identity, string lexing or the timeouts imposed by the executor.
const BLOCKED_SETTINGS: &[&str] = &[
    "STANDARD_CONFORMING_STRINGS",
    "BACKSLASH_QUOTE",
    "ESCAPE_STRING_WARNING",
    "STATEMENT_TIMEOUT",
    "LOCK_TIMEOUT",
    "IDLE_IN_TRANSACTION_SESSION_TIMEOUT",
    "TRANSACTION_TIMEOUT",
];
const ROLE_WORDS: &[&str] = &["ROLE", "AUTHORIZATION", "SESSION_AUTHORIZATION"];
const ROLE_OBJECTS: &[&str] = &["ROLE", "USER", "GROUP"];
/// Words that may sit between `CREATE` and the object type (`CREATE OR REPLACE CONSTRAINT TRIGGER`,
/// `CREATE TRUSTED PROCEDURAL LANGUAGE`).
const CREATE_MODIFIERS: &[&str] = &["OR", "REPLACE", "CONSTRAINT", "TRUSTED", "PROCEDURAL"];
/// Object types whose creation installs user-defined executable code.
const EXECUTABLE_OBJECTS: &[&str] = &[
    "FUNCTION",
    "PROCEDURE",
    "TRIGGER",
    "EXTENSION",
    "RULE",
    "AGGREGATE",
    "OPERATOR",
    "LANGUAGE",
    "EVENT",
    "TRANSFORM",
    "CAST",
];
/// Object types whose `ALTER` changes or upgrades executable code.
const ALTER_EXECUTABLE_OBJECTS: &[&str] =
    &["FUNCTION", "PROCEDURE", "ROUTINE", "EXTENSION", "EVENT"];
/// Configuration and escape functions rejected when called (identifier followed by `(`).
const DANGEROUS_FUNCTIONS: &[&str] = &[
    "SET_CONFIG",
    "DBLINK",
    "DBLINK_EXEC",
    "DBLINK_CONNECT",
    "DBLINK_OPEN",
    "DBLINK_SEND_QUERY",
    "PG_READ_FILE",
    "PG_READ_BINARY_FILE",
    "PG_LS_DIR",
    "PG_STAT_FILE",
    "LO_IMPORT",
    "LO_EXPORT",
    "LO_FROM_BYTEA",
    "LO_PUT",
    "PG_FILE_WRITE",
    "PG_TERMINATE_BACKEND",
    "PG_CANCEL_BACKEND",
    "PG_RELOAD_CONF",
    "PG_ROTATE_LOGFILE",
];

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

/// What the lexer collects for one top-level statement.
#[derive(Debug, Default)]
struct Lexed {
    /// Unquoted keyword-like tokens, uppercased.
    tokens: Vec<String>,
    /// Keyword tokens plus quoted-identifier contents, in order.
    words: Vec<String>,
    /// Identifiers (quoted or not) immediately followed by `(`, uppercased.
    calls: Vec<String>,
    /// Last significant item when it is an identifier: (uppercased name, from a `U&"…"` identifier).
    last_ident: Option<(String, bool)>,
    /// A `U&"…"` identifier is followed by `UESCAPE`, so its real name cannot be trusted.
    unicode_escape: bool,
}

impl Lexed {
    /// Record an identifier as the last significant item.
    fn ident(&mut self, name: String, unicode: bool) {
        self.last_ident = Some((name, unicode));
    }

    /// Record a significant non-identifier item (operator, literal, punctuation).
    fn other(&mut self) {
        self.last_ident = None;
    }

    /// An opening parenthesis: the preceding identifier, if any, is a call.
    fn open_paren(&mut self) {
        if let Some((name, _)) = self.last_ident.take() {
            self.calls.push(name);
        }
    }
}

/// One top-level statement found by `lex`: byte range, content flag, collected words.
struct Segment {
    start: usize,
    end: usize,
    has_code: bool,
    lexed: Lexed,
}

/// Identifier byte: ASCII alphanumeric, `_`, `$` or any non-ASCII byte.
fn is_ident(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'$' || b >= 0x80
}

/// Flush a pending identifier run into `lexed` when it is a keyword-like token.
fn flush(sql: &str, tok_start: &mut Option<usize>, end: usize, lexed: &mut Lexed) {
    if let Some(s) = tok_start.take() {
        let run = &sql[s..end];
        match run.as_bytes().first() {
            Some(&first) if first.is_ascii_alphabetic() || first == b'_' => {
                let upper = run.to_ascii_uppercase();
                if upper == "UESCAPE" && matches!(lexed.last_ident, Some((_, true))) {
                    lexed.unicode_escape = true;
                }
                lexed.tokens.push(upper.clone());
                lexed.words.push(upper.clone());
                lexed.ident(upper, false);
            }
            Some(&first) if first.is_ascii_digit() => {
                // `1set_config(`: older servers lex a trailing identifier after a number.
                match run.find(|c: char| c.is_ascii_alphabetic() || c == '_') {
                    Some(pos) => lexed.ident(run[pos..].to_ascii_uppercase(), false),
                    None => lexed.other(),
                }
            }
            _ => lexed.other(),
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

/// True when the `"` at `i` is the start of a `U&"…"` identifier.
fn is_unicode_ident_start(bytes: &[u8], i: usize) -> bool {
    i >= 2
        && bytes[i - 1] == b'&'
        && (bytes[i - 2] == b'U' || bytes[i - 2] == b'u')
        && (i < 3 || !is_ident(bytes[i - 3]))
}

/// Decode the default `\XXXX` / `\+XXXXXX` / `\\` escapes of a `U&"…"` identifier.
/// Returns the raw text unchanged when an escape is malformed (the server rejects it anyway).
fn decode_unicode_ident(raw: &str) -> String {
    let chars: Vec<char> = raw.chars().collect();
    let mut out = String::with_capacity(raw.len());
    let mut i = 0usize;
    while i < chars.len() {
        if chars[i] != '\\' {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        let (start, width) = match chars.get(i + 1) {
            Some('\\') => {
                out.push('\\');
                i += 2;
                continue;
            }
            Some('+') => (i + 2, 6),
            _ => (i + 1, 4),
        };
        let hex: String = chars.iter().skip(start).take(width).collect();
        let decoded = if hex.len() == width && hex.chars().all(|c| c.is_ascii_hexdigit()) {
            u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32)
        } else {
            None
        };
        match decoded {
            Some(c) => {
                out.push(c);
                i = start + width;
            }
            None => return raw.to_string(),
        }
    }
    out
}

fn lex(sql: &str) -> Result<Vec<Segment>, String> {
    let bytes = sql.as_bytes();
    let len = bytes.len();
    let mut segments: Vec<Segment> = Vec::new();
    let mut state = State::Normal;
    let mut seg_start = 0usize;
    let mut has_code = false;
    let mut lexed = Lexed::default();
    let mut tok_start: Option<usize> = None;
    // Start byte of the currently open construct (for error messages).
    let mut open_at = 0usize;
    // The open quoted identifier is a `U&"…"` one.
    let mut quote_unicode = false;
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
                            flush(sql, &mut tok_start, i, &mut lexed);
                            state = State::SingleQuote;
                        }
                        lexed.other();
                        has_code = true;
                        open_at = i;
                        i += 1;
                    }
                    b'"' => {
                        flush(sql, &mut tok_start, i, &mut lexed);
                        lexed.other();
                        quote_unicode = is_unicode_ident_start(bytes, i);
                        has_code = true;
                        open_at = i;
                        state = State::DoubleQuote;
                        i += 1;
                    }
                    b'$' => {
                        flush(sql, &mut tok_start, i, &mut lexed);
                        lexed.other();
                        has_code = true;
                        if let Some(end) = dollar_tag_end(bytes, i) {
                            let delim = sql[i..=end].to_string();
                            open_at = i;
                            i = end + 1;
                            state = State::Dollar(delim);
                        } else {
                            // `$1` parameter or lone `$`: ordinary code byte.
                            i += 1;
                        }
                    }
                    b'-' if bytes.get(i + 1) == Some(&b'-') => {
                        flush(sql, &mut tok_start, i, &mut lexed);
                        state = State::LineComment;
                        i += 2;
                    }
                    b'/' if bytes.get(i + 1) == Some(&b'*') => {
                        flush(sql, &mut tok_start, i, &mut lexed);
                        open_at = i;
                        state = State::BlockComment(1);
                        i += 2;
                    }
                    b';' => {
                        flush(sql, &mut tok_start, i, &mut lexed);
                        segments.push(Segment {
                            start: seg_start,
                            end: i,
                            has_code,
                            lexed: std::mem::take(&mut lexed),
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
                            flush(sql, &mut tok_start, i, &mut lexed);
                            if b == b'(' {
                                lexed.open_paren();
                                has_code = true;
                            } else if !b.is_ascii_whitespace() {
                                lexed.other();
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
                        let raw = sql[open_at + 1..i].replace("\"\"", "\"");
                        let name = if quote_unicode {
                            decode_unicode_ident(&raw)
                        } else {
                            raw
                        }
                        .to_ascii_uppercase();
                        lexed.words.push(name.clone());
                        lexed.ident(name, quote_unicode);
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
                if b == b'\n' || b == b'\r' {
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
    flush(sql, &mut tok_start, len, &mut lexed);
    segments.push(Segment {
        start: seg_start,
        end: len,
        has_code,
        lexed,
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

/// Everything the lexer collected for one statement, merged across segments.
fn statement_lexed(stmt: &str) -> Result<Lexed, String> {
    let mut merged = Lexed::default();
    for seg in lex(stmt)? {
        merged.tokens.extend(seg.lexed.tokens);
        merged.words.extend(seg.lexed.words);
        merged.calls.extend(seg.lexed.calls);
        merged.unicode_escape |= seg.lexed.unicode_escape;
    }
    Ok(merged)
}

/// True when any `CREATE` in a `CREATE` statement (incl. `CREATE SCHEMA` elements) installs code.
fn creates_executable(tokens: &[String]) -> bool {
    tokens.iter().enumerate().any(|(i, t)| {
        t == "CREATE"
            && tokens[i + 1..]
                .iter()
                .map(String::as_str)
                .find(|w| !CREATE_MODIFIERS.contains(w))
                .map(|obj| EXECUTABLE_OBJECTS.contains(&obj))
                .unwrap_or(false)
    })
}

/// Classify by leading keyword; `None` = allowed by the statement-level rules.
fn classify_keywords(tokens: &[String], words: &[String]) -> Option<BlockedKind> {
    let first = tokens.first()?.as_str();
    let second = tokens.get(1).map(String::as_str).unwrap_or("");
    let rest = &tokens[1..];
    match first {
        "DROP" => Some(BlockedKind::Drop),
        "TRUNCATE" => Some(BlockedKind::Truncate),
        "DO" => Some(BlockedKind::DoBlock),
        "GRANT" | "REVOKE" => Some(BlockedKind::Privilege),
        "LOAD" => Some(BlockedKind::ExecutableObject),
        "IMPORT" if second == "FOREIGN" => Some(BlockedKind::ForeignAccess),
        // CREATE|ALTER SERVER, FOREIGN DATA WRAPPER, FOREIGN TABLE, USER MAPPING (before the role rule)
        "CREATE" | "ALTER"
            if second == "SERVER"
                || second == "FOREIGN"
                || (second == "USER" && tokens.get(2).map(String::as_str) == Some("MAPPING")) =>
        {
            Some(BlockedKind::ForeignAccess)
        }
        "CREATE" | "ALTER" if ROLE_OBJECTS.contains(&second) => Some(BlockedKind::Role),
        // ALTER SYSTEM …, ALTER DATABASE … SET|RESET … change settings of future sessions
        "ALTER" if second == "SYSTEM" => Some(BlockedKind::Setting),
        "ALTER" if second == "DATABASE" && rest.iter().any(|t| t == "SET" || t == "RESET") => {
            Some(BlockedKind::Setting)
        }
        "CREATE" if creates_executable(tokens) => Some(BlockedKind::ExecutableObject),
        "ALTER" if ALTER_EXECUTABLE_OBJECTS.contains(&second) => {
            Some(BlockedKind::ExecutableObject)
        }
        // CREATE SCHEMA ... GRANT, ALTER DEFAULT PRIVILEGES GRANT|REVOKE embed privilege statements
        "CREATE" | "ALTER" if rest.iter().any(|t| t == "GRANT" || t == "REVOKE") => {
            Some(BlockedKind::Privilege)
        }
        // SET [SESSION|LOCAL] ROLE …, SET SESSION AUTHORIZATION …, SET session_authorization, RESET ROLE …
        // (quoted identifiers such as SET "role" are in `words`)
        "SET" | "RESET" => {
            if words[1..].iter().any(|w| ROLE_WORDS.contains(&w.as_str())) {
                return Some(BlockedKind::Role);
            }
            if first == "RESET" {
                // RESET <any> / RESET ALL would undo the executor's SET LOCAL timeouts.
                return Some(BlockedKind::Setting);
            }
            let target = words[1..]
                .iter()
                .map(String::as_str)
                .find(|w| *w != "SESSION" && *w != "LOCAL")
                .unwrap_or("");
            if BLOCKED_SETTINGS.contains(&target) {
                Some(BlockedKind::Setting)
            } else if target == "TRANSACTION" || target == "CHARACTERISTICS" {
                Some(BlockedKind::TransactionControl)
            } else {
                None
            }
        }
        "PREPARE" if second == "TRANSACTION" => Some(BlockedKind::TransactionControl),
        "ALTER" if rest.iter().any(|t| t == "DROP") => Some(BlockedKind::AlterDrop),
        "COPY" => Some(BlockedKind::Copy),
        t if TRANSACTION_CONTROL.contains(&t) => Some(BlockedKind::TransactionControl),
        _ => None,
    }
}

/// Classify one statement; `None` = allowed. A statement that cannot be lexed is blocked.
pub fn classify(stmt: &str) -> Option<BlockedKind> {
    let lexed = match statement_lexed(stmt) {
        Ok(v) => v,
        Err(_) => return Some(BlockedKind::Unparsable),
    };
    if lexed.unicode_escape {
        // U&"…" UESCAPE '…': the identifier's real name is not decoded, so fail closed.
        return Some(BlockedKind::Unparsable);
    }
    classify_keywords(&lexed.tokens, &lexed.words).or_else(|| {
        lexed
            .calls
            .iter()
            .any(|c| DANGEROUS_FUNCTIONS.contains(&c.as_str()))
            .then_some(BlockedKind::DangerousFunction)
    })
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
    guard(sql).map_err(PyValueError::new_err)
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
        assert_eq!(guard("SET work_mem = '64MB'").unwrap().len(), 1);
    }

    #[test]
    fn blocks_copy_any_direction() {
        for sql in [
            "COPY t FROM PROGRAM 'x'",
            "COPY t TO STDOUT",
            "COPY t FROM STDIN",
            "copy t (a, b) from '/tmp/x.csv' with (format csv)",
            "COPY (SELECT 1) TO '/tmp/out'",
        ] {
            assert_kind(sql, "copy");
        }
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
    fn line_comment_ends_at_carriage_return() {
        for sql in ["SELECT 1 -- c\r; DROP TABLE t", "SELECT 1 -- c\r\n; DROP TABLE t"] {
            assert!(blocked(sql).contains("drop is not allowed"), "{sql}");
        }
    }

    #[test]
    fn blocks_identity_and_lexer_settings() {
        assert_kind("SET session_authorization = x", "role");
        assert_kind("SET \"role\" TO x", "role");
        assert_kind("SET standard_conforming_strings = off", "setting");
        assert_kind("SET LOCAL statement_timeout = 0", "setting");
        assert_kind("SET TRANSACTION READ ONLY", "transaction_control");
        assert_kind("PREPARE TRANSACTION 'x'", "transaction_control");
        assert_eq!(guard("SET search_path = 'role'").unwrap().len(), 1);
    }

    #[test]
    fn blocks_embedded_privileges() {
        assert_kind("ALTER DEFAULT PRIVILEGES GRANT ALL ON TABLES TO x", "privilege");
        assert_kind("CREATE SCHEMA s GRANT ALL ON t TO u", "privilege");
    }

    #[test]
    fn no_panic_on_odd_input() {
        for sql in ["", ";", "'", "E", "E'", "$", "$a", "$a$", "-", "/", "é;é", "\\", "SELECT E'\\",
            "U&\"", "U&\"\\\"", "U&\"\\+\"(", "u&\"x\" UESCAPE", "1(", "1e(", "_("] {
            let _ = guard(sql);
        }
    }

    #[test]
    fn blocks_create_executable_objects() {
        for sql in [
            "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
            "create or replace function f() returns void language plpgsql as $$ begin execute 'drop table x'; end $$",
            "CREATE PROCEDURE p() LANGUAGE sql AS $$ DELETE FROM t $$",
            "CREATE OR REPLACE PROCEDURE p() LANGUAGE sql AS 'SELECT 1'",
            "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()",
            "CREATE OR REPLACE TRIGGER tr AFTER UPDATE ON t EXECUTE FUNCTION f()",
            "CREATE CONSTRAINT TRIGGER tr AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f()",
            "CREATE EXTENSION dblink",
            "CREATE RULE r AS ON INSERT TO t DO INSTEAD NOTHING",
            "CREATE OR REPLACE RULE r AS ON DELETE TO t DO ALSO NOTHING",
            "CREATE AGGREGATE a (int) (SFUNC = f, STYPE = int)",
            "CREATE OPERATOR === (LEFTARG = int, RIGHTARG = int, FUNCTION = f)",
            "CREATE LANGUAGE plperlu",
            "CREATE OR REPLACE TRUSTED PROCEDURAL LANGUAGE l HANDLER h",
            "CREATE EVENT TRIGGER e ON ddl_command_start EXECUTE FUNCTION f()",
            "CREATE TRANSFORM FOR int LANGUAGE l (FROM SQL WITH FUNCTION f(internal))",
            "CREATE CAST (text AS int) WITH FUNCTION f(text)",
            "CREATE SCHEMA s CREATE TRIGGER tr BEFORE INSERT ON t EXECUTE FUNCTION f()",
            "/* c */ CrEaTe /* c */ FuNcTiOn f() RETURNS int AS 'select 1' LANGUAGE sql",
        ] {
            assert_kind(sql, "executable_object");
        }
    }

    #[test]
    fn blocks_alter_executable_objects() {
        for sql in [
            "ALTER FUNCTION f() SECURITY DEFINER",
            "alter procedure p() owner to x",
            "ALTER ROUTINE f() SET search_path = x",
            "ALTER EXTENSION e UPDATE",
        ] {
            assert_kind(sql, "executable_object");
        }
    }

    #[test]
    fn allows_non_executable_ddl() {
        for sql in [
            "CREATE TABLE t (a int)",
            "CREATE TABLE IF NOT EXISTS s.t (event text, trigger_name text, rule int)",
            "CREATE UNLOGGED TABLE t AS SELECT 1 AS a",
            "CREATE INDEX i ON t (a)",
            "CREATE UNIQUE INDEX CONCURRENTLY i ON t USING btree (a)",
            "CREATE VIEW v AS SELECT 1",
            "CREATE OR REPLACE VIEW v AS SELECT 1",
            "CREATE MATERIALIZED VIEW m AS SELECT 1",
            "CREATE SCHEMA s",
            "CREATE SCHEMA s CREATE TABLE t (a int)",
            "CREATE SEQUENCE seq",
            "ALTER TABLE t ADD COLUMN c int",
            "ALTER TABLE t ENABLE TRIGGER tr",
            "SELECT 'CREATE FUNCTION f()' AS s",
            "SELECT \"function\" FROM t -- CREATE FUNCTION",
            "CALL proc()",
            "CALL s.refresh_profile(1, 'x')",
        ] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }

    #[test]
    fn blocks_reset_entirely() {
        assert_kind("RESET ALL", "setting");
        assert_kind("reset statement_timeout", "setting");
        assert_kind("RESET search_path", "setting");
        assert_kind("RESET \"lock_timeout\"", "setting");
        assert_kind("RESET ROLE", "role");
        assert_kind("RESET SESSION AUTHORIZATION", "role");
        assert_eq!(guard("SET search_path = x").unwrap().len(), 1);
        assert_eq!(guard("SELECT 'RESET ALL'").unwrap().len(), 1);
    }

    #[test]
    fn blocks_dangerous_function_calls() {
        for name in DANGEROUS_FUNCTIONS {
            let lower = name.to_ascii_lowercase();
            assert_kind(&format!("SELECT {lower}('x')"), "dangerous_function");
            assert_kind(&format!("SELECT pg_catalog.{name}('x')"), "dangerous_function");
        }
        for sql in [
            "SELECT set_config('statement_timeout', '0', false)",
            "SELECT Set_Config ('lock_timeout', '0', true)",
            "SELECT pg_catalog . set_config /* c */ ('a', 'b', false)",
            "SELECT set_config -- c\n ('a', 'b', false)",
            "SELECT * FROM dblink('host=x', 'DROP TABLE t') AS r(a int)",
            "SELECT public.dblink_exec('DROP TABLE t')",
            "SELECT \"set_config\"('a', 'b', false)",
            "SELECT \"pg_catalog\".\"set_config\"('a', 'b', false)",
            "SELECT U&\"set\\005fconfig\"('a', 'b', false)",
            "UPDATE t SET a = pg_read_file('/etc/passwd')",
            "INSERT INTO t SELECT lo_import('/etc/passwd')",
            "DELETE FROM t WHERE pg_terminate_backend(pid)",
            "WITH x AS (SELECT pg_reload_conf()) SELECT * FROM x",
            "CREATE TABLE t AS SELECT pg_ls_dir('.')",
            "SET search_path = x; SELECT 1set_config('a', 'b', false)",
        ] {
            let msg = blocked(sql);
            assert!(msg.contains("dangerous_function is not allowed"), "{sql} -> {msg}");
        }
    }

    #[test]
    fn dangerous_names_without_call_are_allowed() {
        for sql in [
            "SELECT set_config FROM t",
            "SELECT t.dblink, t.pg_read_file FROM t WHERE lo_import = 1",
            "SELECT 'set_config(''a'', ''b'', false)'",
            "SELECT $$ pg_read_file('/etc/passwd') $$",
            "SELECT E'dblink_exec(\\'x\\')'",
            "SELECT 1 -- set_config('a', 'b', false)",
            "SELECT /* dblink('x') */ 1",
            "SELECT \"set_config('a')\" FROM t",
            "SELECT \"set_config\" FROM t",
            "SELECT my_set_config('a'), set_configuration('b'), xdblink('c')",
            "SELECT set_config, (a) FROM t",
            "SELECT current_setting('statement_timeout')",
        ] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }

    #[test]
    fn unicode_escape_identifiers_are_decoded() {
        assert_kind("SET U&\"rol\\0065\" TO x", "role");
        assert_kind("SELECT U&\"set\\+00005fconfig\"('a', 'b', false)", "dangerous_function");
        assert_kind("SELECT U&\"set!005fconfig\" UESCAPE '!' ('a', 'b', false)", "unparsable");
        assert_eq!(guard("SELECT U&\"d\\0061ta\" FROM t").unwrap().len(), 1);
        assert_eq!(guard("SELECT U&'caf\\00e9'").unwrap().len(), 1);
    }

    #[test]
    fn decode_unicode_ident_handles_malformed_escapes() {
        assert_eq!(decode_unicode_ident("a\\0062c"), "abc");
        assert_eq!(decode_unicode_ident("a\\\\b"), "a\\b");
        assert_eq!(decode_unicode_ident("a\\zz"), "a\\zz");
        assert_eq!(decode_unicode_ident("a\\+110000"), "a\\+110000");
        assert_eq!(decode_unicode_ident("\\"), "\\");
    }

    #[test]
    fn blocks_server_level_settings() {
        for sql in [
            "ALTER SYSTEM SET statement_timeout = 0",
            "alter system reset all",
            "ALTER DATABASE d SET statement_timeout = 0",
            "ALTER DATABASE d RESET ALL",
            "ALTER DATABASE d IN TABLESPACE x SET work_mem = '1GB'",
        ] {
            assert_kind(sql, "setting");
        }
        assert_kind("ALTER ROLE r SET statement_timeout = 0", "role");
        assert_kind("ALTER USER u RESET ALL", "role");
        assert_eq!(guard("ALTER TABLE t ALTER COLUMN c SET DEFAULT 0").unwrap().len(), 1);
        assert_eq!(guard("ALTER TABLE t SET (fillfactor = 70)").unwrap().len(), 1);
        assert_eq!(guard("SELECT * FROM system WHERE database = 'd'").unwrap().len(), 1);
    }

    #[test]
    fn blocks_load_and_alter_event_trigger() {
        assert_kind("LOAD 'plpgsql'", "executable_object");
        assert_kind("load '$libdir/plugins/x'", "executable_object");
        assert_kind("ALTER EVENT TRIGGER e ENABLE", "executable_object");
        assert_eq!(guard("SELECT load FROM t").unwrap().len(), 1);
        assert_eq!(guard("SELECT 'LOAD x'").unwrap().len(), 1);
    }

    #[test]
    fn blocks_foreign_access() {
        for sql in [
            "CREATE SERVER s FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host 'x')",
            "CREATE SERVER IF NOT EXISTS s FOREIGN DATA WRAPPER postgres_fdw",
            "ALTER SERVER s OPTIONS (SET host 'y')",
            "CREATE FOREIGN DATA WRAPPER w HANDLER h",
            "alter foreign data wrapper w options (add x 'y')",
            "CREATE USER MAPPING FOR CURRENT_USER SERVER s OPTIONS (user 'u', password 'p')",
            "ALTER USER MAPPING FOR u SERVER s OPTIONS (SET password 'p')",
            "CREATE FOREIGN TABLE f (a int) SERVER s",
            "ALTER FOREIGN TABLE f OPTIONS (SET table_name 'x')",
            "IMPORT FOREIGN SCHEMA public FROM SERVER s INTO local",
        ] {
            assert_kind(sql, "foreign_access");
        }
        assert_kind("CREATE USER u", "role");
        for sql in [
            "SELECT * FROM server",
            "SELECT server, foreign_table FROM t",
            "ALTER TABLE t ADD COLUMN c int",
            "ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES s (a)",
            "CREATE TABLE t (a int REFERENCES s (a), server text)",
        ] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }
}
