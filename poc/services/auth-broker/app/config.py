from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    azure_tenant_id: str
    broker_client_id: str
    broker_client_secret: str
    broker_scope: str = '2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default'
    databricks_server_hostname: str
    databricks_http_path: str

    broker_expected_audience: str
    broker_allowed_tenants: str = ''
    broker_allowed_service_names: str = 'revenue-mcp'
    broker_shared_service_key: str
    broker_allow_passthrough_for_dev: bool = False
    broker_allowed_schema: str = 'ri_poc.revenue'
    broker_max_rows: int = 5000

    @property
    def allowed_tenants(self) -> list[str]:
        if not self.broker_allowed_tenants:
            return []
        return [item.strip() for item in self.broker_allowed_tenants.split(',') if item.strip()]

    @property
    def allowed_service_names(self) -> list[str]:
        return [item.strip() for item in self.broker_allowed_service_names.split(',') if item.strip()]

    @property
    def authority(self) -> str:
        return f'https://login.microsoftonline.com/{self.azure_tenant_id}'

    @property
    def expected_audiences(self) -> list[str]:
        raw_items = [item.strip() for item in self.broker_expected_audience.split(',') if item.strip()]
        if not raw_items:
            return []

        audiences: list[str] = []
        for item in raw_items:
            if item not in audiences:
                audiences.append(item)
            if item.startswith('api://'):
                plain = item[len('api://'):]
                if plain and plain not in audiences:
                    audiences.append(plain)
            else:
                api_uri = f'api://{item}'
                if api_uri not in audiences:
                    audiences.append(api_uri)

        return audiences
