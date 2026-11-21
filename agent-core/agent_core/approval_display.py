"""Small public approval descriptions; never copy an argument object wholesale."""

from typing import Mapping


def clean_display(value):
    if not isinstance(value, Mapping) or set(value) != {'description', 'fields'}:
        raise ValueError('invalid approval display')
    description, fields = value['description'], value['fields']
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 500:
        raise ValueError('invalid approval description')
    if not isinstance(fields, Mapping) or len(fields) > 8:
        raise ValueError('invalid approval fields')
    result = {}
    for key, text in fields.items():
        if (not isinstance(key, str) or not key.isidentifier() or len(key) > 64
                or not isinstance(text, str) or len(text) > 200):
            raise ValueError('approval fields must be bounded strings')
        result[key] = text
    return {'description': description, 'fields': result}


def public_request(value):
    result = {key: value[key] for key in ('request_id', 'tool_name', 'argument_digest')}
    if 'display' in value:
        result['display'] = clean_display(value['display'])
    return result
