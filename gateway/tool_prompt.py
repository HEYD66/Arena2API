"""Shared tool prompt compaction, adapted from Arena2API's original formatter.
Only description annotations are shortened. No tools/schema constraints are cut.
"""
import copy
import json

# Apply recursion only where JSON Schema contains schemas, never inside literal
# enum/const/default/examples data or arbitrary extension dictionaries.
_SCHEMA_MAPS = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas', 'dependencies'}
_SCHEMA_CHILDREN = {'items', 'additionalItems', 'additionalProperties', 'unevaluatedProperties',
                    'unevaluatedItems', 'contains', 'propertyNames', 'not', 'if', 'then', 'else', 'contentSchema'}
_SCHEMA_ARRAYS = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}


def scrub_schema(node, desc_cap=None):
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    out = {}
    for key, value in node.items():
        if key == 'description' and isinstance(value, str):
            if desc_cap is None:
                out[key] = value
            elif desc_cap > 0:
                out[key] = value if len(value) <= desc_cap else value[:desc_cap] + '…'
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: scrub_schema(schema, desc_cap) for name, schema in value.items()}
        elif key in _SCHEMA_CHILDREN:
            out[key] = ([scrub_schema(item, desc_cap) for item in value] if isinstance(value, list)
                        else scrub_schema(value, desc_cap))
        elif key in _SCHEMA_ARRAYS and isinstance(value, list):
            out[key] = [scrub_schema(item, desc_cap) for item in value]
        else:
            out[key] = copy.deepcopy(value)
    return out


def format_tools(tools, desc_cap=300, param_desc_cap=None):
    """Original compact name/description + JSON-parameters layout, without loss of constraints."""
    lines = []
    for tool in tools:
        fn = tool.get('function', tool) if isinstance(tool, dict) else {}
        name = fn.get('name', '?')
        desc = (fn.get('description') or '').replace('\n', ' ').strip()
        if desc_cap <= 0:
            desc = ''
        elif len(desc) > desc_cap:
            desc = desc[:desc_cap] + '…'
        params = scrub_schema(fn.get('parameters') or {}, param_desc_cap)
        head = f'- {name}: {desc}'.rstrip(': ')
        lines.append(f'{head}\n  parameters: {json.dumps(params, ensure_ascii=False, separators=(",", ":"))}')
    return '\n'.join(lines)


def tool_variants(tools, first_description_cap=300):
    """Original progressive caps, then reversible table layouts; never slices a list."""
    for tool_cap, parameter_cap in ((first_description_cap, None), (120, 300), (60, 100), (40, 40), (16, 0), (0, 0)):
        yield format_tools(tools, tool_cap, parameter_cap)
    # These layouts carry the SAME description-free schemas; prefer the smaller
    # layout rather than stopping at a larger one merely because it barely fits.
    best = format_tools(tools, 0, 0)
    for candidate in (format_tool_table(tools), format_tool_table(tools, deduplicate=True)):
        if len(candidate) < len(best) and len(candidate.encode('utf-16-le', errors='surrogatepass')) < len(best.encode('utf-16-le', errors='surrogatepass')):
            best = candidate
    if best != format_tools(tools, 0, 0):
        yield best


def format_tool_table(tools, *, deduplicate=False):
    """Table encoding of the existing zero-description stage, not schema rewriting.
    Whole identical schemas may be shared via an external table index. JSON Schema
    $ref/$id fields and literal data stay untouched; no schema reference injection.
    """
    rows = []
    schemas = []
    indices = {}
    for tool in tools:
        fn = tool.get('function', tool) if isinstance(tool, dict) else {}
        name = fn.get('name', '?')
        schema = scrub_schema(fn.get('parameters') or {}, 0)
        if deduplicate:
            key = json.dumps(schema, ensure_ascii=False, separators=(',', ':'))
            if key not in indices:
                indices[key] = len(schemas)
                schemas.append(schema)
            rows.append([name, indices[key]])
        else:
            rows.append([name, schema])
    if deduplicate:
        header = 'Tool table: tools rows are [function_name, zero-based index into schemas]. Each indexed schema is the complete parameters schema.\n'
        data = {'schemas': schemas, 'tools': rows}
    else:
        header = 'Tool table: each row is [function_name, complete parameters schema].\n'
        data = rows
    return header + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
