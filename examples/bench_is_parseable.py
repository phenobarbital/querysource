from datetime import datetime
from querysource.utils.handlers import is_parseable, ParseDict, ParseTuple, ParseList


def start_timing():
    return datetime.now()


def generated_at(starts_at):
    return datetime.now() - starts_at


value = '{"Here": "One", "There": "two", "My": "three"}'
tupla = '(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20)'
l = '[1, 2, 3, 3, 4, "hola", "barullo", 3.16, 88.2, 3 ]'
starts_at = start_timing()
for i in range(10000):
    is_parseable(value)
    ParseTuple(tupla)
    ParseDict(value)
    ParseList(l)
total = generated_at(starts_at)

print(f'Parseable Native runs: {total}')


from querysource.utils.parseqs import is_parseable as parseable, ParseDict as parsedict, ParseTuple as parsetuple, ParseList as parselist

starts_at = start_timing()
for i in range(10000):
    parseable(value)
    parsetuple(tupla)
    parsedict(value)
    parselist(l)
total = generated_at(starts_at)

print(f'Parseable Cython runs at: {total}')
