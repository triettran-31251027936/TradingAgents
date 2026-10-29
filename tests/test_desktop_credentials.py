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
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    return path


@pytest.mark.unit
def test_key_is_private_roundtrips_and_never_returns_to_the_renderer(env_file):
    env_file.write_text('OTHER_VALUE=preserved\n')
    value = "sk-private-test-value'with-quote"
    result = credentials.save('deepseek', value)
    assert result == {'provider': 'deepseek', 'configured': True}
    assert value not in json.dumps(result)
    assert dotenv_values(env_file)['DEEPSEEK_API_KEY'] == value
    assert dotenv_values(env_file)['OTHER_VALUE'] == 'preserved'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert credentials.status()['configured']
    assert not credentials.remove('deepseek')['configured']
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
def test_provider_error_cannot_echo_the_loaded_deepseek_key(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'sk-private-test-secret')
    assert 'sk-private-test-secret' not in bridge.clean_error('Incorrect API key provided: sk-private-test-secret')
