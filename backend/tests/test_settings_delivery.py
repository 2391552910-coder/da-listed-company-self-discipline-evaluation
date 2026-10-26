from urllib.parse import unquote,urlsplit
import pytest
from app.documents.common import DomainError
from app.documents.settings import Settings,database_url_from_env


@pytest.fixture(autouse=True)
def database_env(monkeypatch):
    for name in ("APP_DATABASE_URL","APP_DATABASE_HOST","APP_DATABASE_PORT","APP_DATABASE_NAME","APP_DATABASE_USER","APP_DATABASE_PASSWORD","DALUN_ENV_FILE"):
        monkeypatch.delenv(name,raising=False)


def test_password_reserved_characters_encoded_and_never_in_settings_repr(monkeypatch):
    secret="fixture@pass:/?#%"+"密码"
    monkeypatch.setenv("APP_DATABASE_HOST","postgres")
    monkeypatch.setenv("APP_DATABASE_PASSWORD",secret)
    value=database_url_from_env()
    parsed=urlsplit(value)
    assert parsed.hostname=="postgres" and parsed.port==5432
    assert unquote(parsed.password)==secret
    assert secret not in repr(Settings.from_env()) and value not in repr(Settings.from_env())


def test_explicit_url_precedes_fields(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_HOST","invalid/host")
    monkeypatch.setenv("APP_DATABASE_URL","sqlite:///fixture.sqlite")
    assert database_url_from_env()=="sqlite:///fixture.sqlite"


def test_ipv6_is_bracketed(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_HOST","::1")
    assert urlsplit(database_url_from_env()).hostname=="::1"


@pytest.mark.parametrize("field,value",[("APP_DATABASE_HOST","bad/host"),("APP_DATABASE_HOST","postgres@evil"),("APP_DATABASE_PORT","0"),("APP_DATABASE_PORT","65536"),("APP_DATABASE_PORT","5432.0"),("APP_DATABASE_NAME","bad/name")])
def test_invalid_fields_fail_without_disclosing_credentials(monkeypatch,field,value):
    monkeypatch.setenv("APP_DATABASE_HOST","localhost")
    monkeypatch.setenv("APP_DATABASE_PASSWORD","fixture-secret")
    monkeypatch.setenv(field,value)
    with pytest.raises(DomainError) as error:database_url_from_env()
    assert "fixture-secret" not in str(error.value)
