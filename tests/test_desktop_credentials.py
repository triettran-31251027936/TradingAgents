"""A key entered in the app remains private and uses the original dotenv path."""
import json
import stat

import pytest
from dotenv import dotenv_values

from desktop import bridge, credentials


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    monkeypatch.setattr(credentials, 'ENV_FILE', path)
    for name in credentials.ENV_NAME.values():
        monkeypatch.delenv(name, raising=False)
    return path


@pytest.mark.unit
@pytest.mark.parametrize('provider', ['deepseek', 'fred', 'alpha_vantage'])
def test_key_is_private_roundtrips_and_never_returns_to_the_renderer(env_file, provider):
    env_file.write_text('OTHER_VALUE=preserved\n')
    value = "sk-private-test-value'with-quote"
    result = credentials.save(provider, value)
    assert result == {'provider': provider, 'configured': True}
    assert value not in json.dumps(result)
    assert dotenv_values(env_file)[credentials.ENV_NAME[provider]] == value
    assert dotenv_values(env_file)['OTHER_VALUE'] == 'preserved'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert credentials.status(provider)['configured']
    assert not credentials.remove(provider)['configured']
    assert dotenv_values(env_file)['OTHER_VALUE'] == 'preserved'


@pytest.mark.unit
@pytest.mark.parametrize('key', ['', 'key\nINJECT=bad', 'x' * 4097, None])
def test_invalid_key_cannot_inject_configuration(env_file, key):
    with pytest.raises(ValueError):
        credentials.save('deepseek', key)
    assert not env_file.exists()


@pytest.mark.unit
def test_symlink_is_not_used_for_key_storage(env_file, tmp_path):
    target = tmp_path/'unrelated'
    target.write_text('unchanged')
    env_file.symlink_to(target)
    with pytest.raises(ValueError):
        credentials.save('deepseek', 'sk-test-only')
    assert target.read_text() == 'unchanged'


@pytest.mark.unit
@pytest.mark.parametrize('name', ['DEEPSEEK_API_KEY', 'FRED_API_KEY', 'ALPHA_VANTAGE_API_KEY'])
def test_provider_error_cannot_echo_the_loaded_key(monkeypatch, name):
    monkeypatch.setenv(name, 'sk-private-test-secret')
    assert 'sk-private-test-secret' not in bridge.clean_error('Incorrect API key provided: sk-private-test-secret')


@pytest.mark.unit
def test_data_source_keys_are_independent_and_preserve_existing_llm_key(env_file):
    credentials.save('deepseek', 'existing-test-only-deepseek')
    credentials.save('fred', 'test-only-fred')
    credentials.save('alpha_vantage', 'test-only-alpha')
    credentials.remove('fred')
    assert credentials.status('deepseek')['configured']
    assert credentials.status('alpha_vantage')['configured']
    assert not credentials.status('fred')['configured']
    assert dotenv_values(env_file)['DEEPSEEK_API_KEY'] == 'existing-test-only-deepseek'
