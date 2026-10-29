"""Names-only in-memory diagnostics. Never retain definitions, arguments or history."""
from collections import Counter
import json
import re
import time


def begin_audit(tools, active):
    names = [t['function']['name'] for t in tools]
    return {'captured_at': int(time.time()), 'received_names': names,
            'received_count': len(names), 'tools_active': bool(active),
            'excluded_names': [] if active else list(names), 'stage': 'received',
            'format': None, 'rendered_names': [], 'rendered_count': None,
            'missing_names': [], 'unexpected_names': [], 'names_match': None}


def names_from_definitions(definitions):
    """Parse the actual selected definition block, never substring-search history."""
    if definitions.startswith('Tool table:'):
        header, data = definitions.split('\n', 1)
        data = json.loads(data)
        if 'zero-based index into schemas' in header:
            schemas, rows = data['schemas'], data['tools']
            if any(not isinstance(row, list) or len(row) != 2 or type(row[1]) is not int or not 0 <= row[1] < len(schemas) for row in rows):
                raise ValueError('invalid table index')
            kind = 'shared_schema_table'
        else:
            rows = data
            if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 2 for row in rows):
                raise ValueError('invalid table row')
            kind = 'schema_table'
        names = [row[0] for row in rows]
    elif definitions.startswith('['):
        rows = json.loads(definitions)
        names = [row['function']['name'] for row in rows]
        kind = 'full_json'
    else:
        lines = definitions.splitlines()
        if len(lines) % 2:
            raise ValueError('invalid compact list')
        names = []
        for i in range(0, len(lines), 2):
            match = re.fullmatch(r'- ([^:\r\n]+?)(?:: .*)?', lines[i])
            if not match or not lines[i+1].startswith('  parameters: '):
                raise ValueError('invalid compact tool header')
            json.loads(lines[i+1][len('  parameters: '):])
            names.append(match.group(1))
        kind = 'compact_list'
    if any(not isinstance(name, str) for name in names):
        raise ValueError('invalid tool name')
    return kind, names


def finish_audit(audit, definitions, *, stage='rendered'):
    if audit is None:
        return
    audit['stage'] = stage
    try:
        kind, names = names_from_definitions(definitions) if audit['tools_active'] else ('tools_disabled', [])
        expected = audit['received_names'] if audit['tools_active'] else []
        missing = list((Counter(expected) - Counter(names)).elements())
        unexpected = list((Counter(names) - Counter(expected)).elements())
        audit.update(format=kind, rendered_names=names, rendered_count=len(names),
                     missing_names=missing, unexpected_names=unexpected,
                     names_match=not missing and not unexpected)
    except (ValueError, TypeError, KeyError, IndexError):
        # Do not log exception text: it may contain parts of a schema.
        audit.update(format='unverified', rendered_names=[], rendered_count=None,
                     names_match=None, missing_names=[], unexpected_names=[])
