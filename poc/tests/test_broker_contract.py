import json
from pathlib import Path


def test_env_template_contains_required_keys() -> None:
    env_file = Path(__file__).resolve().parents[1] / '.env.example'
    content = env_file.read_text(encoding='utf-8')

    required = [
        'AZURE_TENANT_ID=',
        'BROKER_CLIENT_ID=',
        'BROKER_CLIENT_SECRET=',
        'BROKER_EXPECTED_AUDIENCE=',
        'MCP_BROKER_BASE_URL=',
        'DATABRICKS_SERVER_HOSTNAME=',
        'DATABRICKS_HTTP_PATH=',
    ]

    for key in required:
        assert key in content, f'Missing required key in .env.example: {key}'


def test_broker_request_shape_example() -> None:
    payload = {
        'user_assertion': '<token>',
        'operation_profile': 'sql.read.revenue',
        'workspace': 'ri-dbx-workspace',
        'warehouse_id': '<warehouse-id>'
    }
    assert json.loads(json.dumps(payload))['operation_profile'] == 'sql.read.revenue'
