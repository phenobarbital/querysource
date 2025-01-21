import logging
import re
from ...utils.functions import fdom, ldom


pattern_eval = re.compile(r'^(\w*)\s(.*)\s(\w*)$')


def eval_exp(val):
    parts = val.split(' AND ')
    _exp = {}
    for el in parts:
        print('::: EVAL EXP ::: ', el,  re.findall(pattern_eval, el))
        try:
            rs = list(re.findall(pattern_eval, el)[0])
        except IndexError:
            continue
        name = rs.pop(0)
        _exp[name] = rs
    return _exp


# TODO: use program.json information to fill program hierarchy
def get_hierarchy(program):
    if program == 'walmart' or program == 'retail':
        hierarchy = ['territory_id', 'region_id', 'district_id', 'market_id', 'store_id']
    elif program == 'epson':
        hierarchy = ['region_id', 'cluster_id', 'market_id', 'store_id']
    else:
        hierarchy = ['territory_id', 'region_id', 'district_id', 'market_id', 'store_id']
    return hierarchy

def query_options(options: dict, where: dict = None, program: str = 'default', hierarchy: list = None):
    """
    Query Options.

    Options for Filtering based on a Program Hierarchy.
    """
    if not where:
        _where = {}
    else:
        if isinstance(where, dict):
            _where = where
        else:
            _where = eval_exp(where)
    logging.debug(f"Query Options for: {program} with {options}")
    _ordering = []
    if options:
        if not hierarchy:
            hierarchy = get_hierarchy(program)
        if hierarchy:
            try:
                get_filter = [
                    k.replace('!', '') for k in _where if k.replace('!', '') in hierarchy
                ]
                filter_sorted = sorted(get_filter, key=hierarchy.index)
            except (TypeError, ValueError, KeyError):
                return _where
            ## processing different types of query option
            try:
                get_index = hierarchy.index(filter_sorted.pop())
                selected = hierarchy[get_index + 1:]
            except (KeyError, IndexError):
                selected = []
            if 'null_rolldown' in options:
                try:
                    if selected:
                        for n in selected:
                            _where[n] = 'null'
                    else:
                        if get_filter:
                            last = get_filter.pop(0)
                            if last == hierarchy[-1]:
                                # because is the last index
                                pass
                        else:
                            first = hierarchy.pop(0)
                            _where[first] = 'null'
                except (KeyError, ValueError):
                    pass
            elif 'select_child' in options:
                try:
                    child = selected.pop(0)
                    _where[child] = '!null'
                    for n in selected:
                        _where[n] = 'null'
                except (ValueError, IndexError):
                    if get_filter:
                        pass
                    else:
                        child = hierarchy.pop(0)
                        _where[child] = '!null'
                        for n in hierarchy:
                            _where[n] = 'null'
            elif 'select_children' in options:
                ## add ordering to options:
                try:
                    child = selected.pop(0)
                    _where[child] = '!null'
                    _ordering.append(f'{child} DESC')
                    try:
                        grandchild = selected.pop(0)
                        # _where[grandchild] = '!null'
                        _ordering.append(f'{grandchild} DESC')
                    except (ValueError, IndexError):
                        pass
                    for n in selected:
                        _where[n] = 'null'
                except (ValueError, IndexError):
                    if get_filter:
                        pass
                    else:
                        child = hierarchy.pop(0)
                        _where[child] = '!null'
                        for n in hierarchy:
                            _where[n] = 'null'
            elif 'select_stores' in options:
                try:
                    last = selected.pop()
                    _where[last] = '!null'
                except (ValueError, IndexError):
                    last = hierarchy.pop()
                    _where[last] = '!null'
                except (KeyError, TypeError):
                    pass
    return (_where, _ordering)

def grouping_set(options: dict, where: dict, **kwargs): # pylint: disable=W0613
    """
    Grouping Set.

    Simulating a Grouping Set based on a Program Hierarchy.
    """

def group_by_child(options: dict, where: dict, **kwargs): # pylint: disable=W0613
    """
    Grouping by Child.

    Group by the next one level in the Hierarchy.
    """


def first_day(*args, **kwargs) -> str:  # pylint: disable=W0613
    return fdom()

def last_day(*args, **kwargs) -> str:  # pylint: disable=W0613
    return ldom()
