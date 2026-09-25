//! Parser chumsky del dialecto URL.
//!
//! Gramática (fase 1), sobre la URL ya *percent-decoded*:
//!
//! ```text
//! query      := prefix? slug selection? filter? pipe*
//! prefix     := '/' | '/queries/' | 'queries/'
//! slug       := [A-Za-z0-9_-]+
//! selection  := '{' field (',' field)* ','? '}'
//! field      := path (':as(' ident ')')?
//! path       := ident ('.' ident)*
//! filter     := '?' expr
//! expr       := expr '|' expr            (menor precedencia)
//!             | expr '&' expr
//!             | '!' expr
//!             | '(' expr ')'
//!             | operand (cmp operand)?   (operando solo = "no nulo")
//! cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>'
//!             | '~' | '!~' | '^=' | '$=' | '=~'
//! operand    := list | literal | call | path
//! list       := '(' literal (',' literal)* ')'
//! call       := ident '(' (operand (',' operand)*)? ')'
//! literal    := null | true | false | datetime | date | number | string
//! date       := DDDD-DD-DD
//! datetime   := date 'T' DD:DD (':' DD)? ('Z' | ('+'|'-') DD:DD)?
//! number     := '-'? int ('.' digits)?
//! string     := '\'' ( '\'\'' | [^'] )* '\''  |  '"' ( '\"' | [^"] )* '"'
//! pipe       := ':sort(' sortkey (',' sortkey)* ')'
//!             | ':top(' int ')' | ':limit(' int ')'
//!             | ':skip(' int ')' | ':offset(' int ')'
//!             | ':distinct'
//! sortkey    := ('+' | '-')? path
//! ```
//!
//! Los espacios (un `%20` decodificado) se toleran alrededor de cualquier
//! token, de modo que `?state = 'CA' & city ~ 'san'` es válido.

use chumsky::pratt::*;
use chumsky::prelude::*;

use crate::ast::*;

type Err<'a> = extra::Err<Rich<'a, char>>;

fn ident<'a>() -> impl Parser<'a, &'a str, String, Err<'a>> + Clone {
    text::ascii::ident().map(|s: &str| s.to_string())
}

fn path<'a>() -> impl Parser<'a, &'a str, Path, Err<'a>> + Clone {
    ident()
        .separated_by(just('.'))
        .at_least(1)
        .collect::<Vec<_>>()
        .labelled("column")
}

fn slug<'a>() -> impl Parser<'a, &'a str, String, Err<'a>> + Clone {
    any()
        .filter(|c: &char| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .repeated()
        .at_least(1)
        .to_slice()
        .map(|s: &str| s.to_string())
        .labelled("query slug")
}

fn uint<'a>() -> impl Parser<'a, &'a str, u64, Err<'a>> + Clone {
    text::int(10)
        .try_map(|s: &str, span| {
            s.parse::<u64>()
                .map_err(|e| Rich::custom(span, e.to_string()))
        })
        .labelled("integer")
}

fn string<'a>() -> impl Parser<'a, &'a str, String, Err<'a>> + Clone {
    let single = just('\'')
        .ignore_then(
            choice((just("''").to('\''), none_of('\'')))
                .repeated()
                .collect::<String>(),
        )
        .then_ignore(just('\''));
    let double = just('"')
        .ignore_then(
            choice((just("\\\"").to('"'), none_of('"')))
                .repeated()
                .collect::<String>(),
        )
        .then_ignore(just('"'));
    choice((single, double)).labelled("quoted string")
}

fn two_digits<'a>() -> impl Parser<'a, &'a str, (), Err<'a>> + Clone {
    any()
        .filter(|c: &char| c.is_ascii_digit())
        .repeated()
        .exactly(2)
        .ignored()
}

fn temporal<'a>() -> impl Parser<'a, &'a str, Literal, Err<'a>> + Clone {
    let date = any()
        .filter(|c: &char| c.is_ascii_digit())
        .repeated()
        .exactly(4)
        .ignored()
        .then_ignore(just('-'))
        .then_ignore(two_digits())
        .then_ignore(just('-'))
        .then_ignore(two_digits());
    let tz = choice((
        just('Z').ignored(),
        one_of("+-")
            .ignored()
            .then_ignore(two_digits())
            .then_ignore(just(':'))
            .then_ignore(two_digits()),
    ));
    let time = just('T')
        .ignore_then(two_digits())
        .then_ignore(just(':'))
        .then_ignore(two_digits())
        .then_ignore(just(':').ignore_then(two_digits()).or_not())
        .then_ignore(tz.or_not());
    date.then(time.or_not())
        .to_slice()
        .map(|s: &str| {
            if s.contains('T') {
                Literal::DateTime(s.to_string())
            } else {
                Literal::Date(s.to_string())
            }
        })
        .labelled("ISO date")
}

fn number<'a>() -> impl Parser<'a, &'a str, Literal, Err<'a>> + Clone {
    just('-')
        .or_not()
        .then(text::int(10))
        .then(just('.').then(text::digits(10)).or_not())
        .to_slice()
        .try_map(|s: &str, span| {
            let parsed = if s.contains('.') {
                s.parse::<f64>()
                    .map(Literal::Float)
                    .map_err(|e| e.to_string())
            } else {
                s.parse::<i64>()
                    .map(Literal::Int)
                    .map_err(|e| e.to_string())
            };
            parsed.map_err(|e| Rich::custom(span, format!("bad number `{s}`: {e}")))
        })
        .labelled("number")
}

fn literal<'a>() -> impl Parser<'a, &'a str, Literal, Err<'a>> + Clone {
    choice((
        text::ascii::keyword("null").to(Literal::Null),
        text::ascii::keyword("true").to(Literal::Bool(true)),
        text::ascii::keyword("false").to(Literal::Bool(false)),
        temporal(),
        number(),
        string().map(Literal::Str),
    ))
    .labelled("literal")
}

fn cmp_op<'a>() -> impl Parser<'a, &'a str, CmpOp, Err<'a>> + Clone {
    // Los de dos caracteres van primero.
    choice((
        just("==").to(CmpOp::Eq),
        just("!=").to(CmpOp::Ne),
        just("!~").to(CmpOp::NotContains),
        just("<=").to(CmpOp::Le),
        just(">=").to(CmpOp::Ge),
        just("=~").to(CmpOp::Regex),
        just("^=").to(CmpOp::StartsWith),
        just("$=").to(CmpOp::EndsWith),
        just('<').to(CmpOp::Lt),
        just('>').to(CmpOp::Gt),
        just('=').to(CmpOp::Eq),
        just('~').to(CmpOp::Contains),
    ))
    .labelled("comparison operator")
}

fn operand<'a>() -> impl Parser<'a, &'a str, Operand, Err<'a>> + Clone {
    recursive(|operand| {
        let comma = just(',').padded();
        let list = literal()
            .padded()
            .separated_by(comma)
            .at_least(1)
            .collect::<Vec<_>>()
            .delimited_by(just('('), just(')'))
            .map(Operand::List);
        let call = ident()
            .then(
                operand
                    .padded()
                    .separated_by(comma)
                    .collect::<Vec<_>>()
                    .delimited_by(just('('), just(')')),
            )
            .map(|(name, args)| Operand::Call { name, args });
        choice((
            list,
            literal().map(Operand::Literal),
            call,
            path().map(Operand::Path),
        ))
    })
}

fn expr<'a>() -> impl Parser<'a, &'a str, Expr, Err<'a>> + Clone {
    recursive(|expr| {
        let comparison = operand()
            .then(cmp_op().padded().then(operand()).or_not())
            .map(|(lhs, tail)| match tail {
                Some((op, rhs)) => Expr::Compare { lhs, op, rhs },
                None => Expr::Truthy(lhs),
            });
        let atom = choice((
            expr.delimited_by(just('(').padded(), just(')').padded()),
            comparison,
        ))
        .padded();

        atom.pratt((
            prefix(3, just('!').padded(), |_, rhs, _| Expr::Not(Box::new(rhs))),
            infix(left(2), just('&').padded(), |l, _, r, _| Expr::and(l, r)),
            infix(left(1), just('|').padded(), |l, _, r, _| Expr::or(l, r)),
        ))
    })
}

fn selection<'a>() -> impl Parser<'a, &'a str, Vec<Field>, Err<'a>> + Clone {
    let alias = just(":as").ignore_then(ident().padded().delimited_by(just('('), just(')')));
    let field = path()
        .padded()
        .then(alias.or_not())
        .map(|(path, alias)| Field { path, alias })
        .padded();
    field
        .separated_by(just(','))
        .allow_trailing()
        .collect::<Vec<_>>()
        .delimited_by(just('{'), just('}'))
        .labelled("selection")
}

fn pipe_op<'a>() -> impl Parser<'a, &'a str, PipeOp, Err<'a>> + Clone {
    let sort_key = choice((just('-').to(true), just('+').to(false)))
        .or_not()
        .then(path())
        .map(|(d, path)| SortKey {
            path,
            descending: d.unwrap_or(false),
        })
        .padded();
    let int_arg = uint().padded().delimited_by(just('('), just(')'));

    let sort = text::ascii::keyword("sort")
        .ignore_then(
            sort_key
                .separated_by(just(','))
                .at_least(1)
                .collect::<Vec<_>>()
                .delimited_by(just('('), just(')')),
        )
        .map(PipeOp::Sort);
    let top = choice((text::ascii::keyword("top"), text::ascii::keyword("limit")))
        .ignore_then(int_arg.clone())
        .map(PipeOp::Top);
    let skip = choice((text::ascii::keyword("skip"), text::ascii::keyword("offset")))
        .ignore_then(int_arg)
        .map(PipeOp::Skip);
    let distinct = text::ascii::keyword("distinct").to(PipeOp::Distinct);

    // Cualquier otro identificador: error con la lista de operadores válidos.
    let unknown = ident().try_map(|name, span| {
        Err(Rich::custom(
            span,
            format!(
                "unknown pipeline operator `:{name}`; expected one of: {}",
                PipeOp::KNOWN
                    .iter()
                    .map(|k| format!(":{k}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        ))
    });

    just(':')
        .padded()
        .ignore_then(choice((sort, top, skip, distinct, unknown)))
}

pub fn query<'a>() -> impl Parser<'a, &'a str, Query, Err<'a>> {
    let prefix = choice((just("/queries/"), just("queries/"), just("/"))).or_not();
    let filter = just('?').padded().ignore_then(expr());

    prefix
        .ignore_then(slug().padded())
        .then(selection().padded().or_not())
        .then(filter.or_not())
        .then(pipe_op().padded().repeated().collect::<Vec<_>>())
        .then_ignore(end())
        .try_map(|(((slug, fields), filter), pipes), span| {
            let mut q = Query {
                slug,
                fields: fields.unwrap_or_default(),
                filter,
                ..Default::default()
            };
            for p in pipes {
                match p {
                    PipeOp::Sort(keys) => q.sort.extend(keys),
                    PipeOp::Top(n) => {
                        if q.limit.replace(n).is_some() {
                            return Err(Rich::custom(span, ":top/:limit given more than once"));
                        }
                    }
                    PipeOp::Skip(n) => {
                        if q.offset.replace(n).is_some() {
                            return Err(Rich::custom(span, ":skip/:offset given more than once"));
                        }
                    }
                    PipeOp::Distinct => q.distinct = true,
                }
            }
            Ok(q)
        })
}
