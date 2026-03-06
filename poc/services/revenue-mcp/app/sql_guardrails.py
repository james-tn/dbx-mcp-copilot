from __future__ import annotations

import re


DENY_PATTERNS = [
    r'\b(insert|update|delete|merge|truncate|drop|alter|create|grant|revoke)\b',
    r'\b(use\s+catalog|use\s+schema)\b',
    r'\binformation_schema\b',
]


def validate_sql(sql: str, allowed_schema: str) -> None:
    normalized = sql.strip().lower()

    if not normalized.startswith('select') and not normalized.startswith('with'):
        raise ValueError('Only SELECT queries are allowed.')

    if ';' in normalized[:-1]:
        raise ValueError('Multiple SQL statements are not allowed.')

    for pattern in DENY_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise ValueError(f'SQL contains blocked pattern: {pattern}')

    if allowed_schema.lower() not in normalized:
        raise ValueError(f'Query must target allowed schema: {allowed_schema}')
