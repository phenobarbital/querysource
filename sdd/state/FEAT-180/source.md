---
kind: inline
jira_key: null
fetched_at: 2026-10-07T00:00:00Z
summary_oneline: Support like/ilike and partial-matching operators (startswith, endswith, contains, regex) in where_cond/filter dict fields
---

# Source (inline)

Feature name: `filter-with-partial-matching`

> Querysource requires a way to support like, ilike and partial-matching
> (startswith, endswith, contains, postgres regexp) in "where_cond"|"filter"
> options, when a field in "where_cond" is a dictionary, like this:
>
> ```json
> "where_cond": {
>   "full_name": {
>     "startswith": "andre"
>   }
> }
> ```
>
> we need to add a partial-matching expression in "WHERE" parser, the
> partial-matching expressions will be:
>
> - startswith: the "field" will starts with the value
> - endswith: "field" will end with value
> - "contains": word or phrase is contained by "field" value (with a length
>   no less than 3, if length is 1 or 2, raise an error)
> - "regex": field will match the regex
