from string import Template
import timeit

sql = """
SELECT 'MTI Shared Inbox' AS display_name, 'bosebreakfix@mtigs.com' AS corporate_email
UNION ALL
SELECT DISTINCT ON (sd.rep_login) sd.rep_name as display_name, sd.rep_login as corporate_email
FROM bose.stores_details sd
WHERE sd.rep_login IS NOT NULL
AND trim(sd.rep_login) <> ''
AND sd.rep_login !~ '^(\s+)$'
AND sd.rep_login ~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{{2,}}$'
ORDER BY display_name ASC
"""

class NullDefault(dict):
    def __missing__(self, key):
        return ''

# Timing Template and safe_substitute
def template_substitution():
    t = Template(sql)
    return t.safe_substitute(NullDefault())

# Timing simple string usage
def simple_string():
    return sql.format_map(NullDefault())

# Benchmarking
template_time = timeit.timeit(template_substitution, number=10000)
simple_time = timeit.timeit(simple_string, number=10000)

print(f"Template substitution time: {template_time:.6f} seconds")
print(f"Simple string time: {simple_time:.6f} seconds")
