from dataclasses import FrozenInstanceError
import json

import pytest

from devops_flow.architecture import Component, ProjectArchitecture, component_id, relative_path
from devops_flow.signals import Signal, ScannerRegistry


@pytest.mark.parametrize('path', ['../x', '/x', 'a//b', 'a/./b', 'a/.git/x', 'a/．．/x', 'a\\b', 'a/.env', 'a\n'])
def test_unsafe_relative_paths(path):
    with pytest.raises(ValueError):
        relative_path(path)


def test_architecture_roundtrip_immutable_manual_overlay():
    component = Component(component_id('backend'), 'backend', signals=(
        Signal('language', 'Python', 'high', 'backend/pyproject.toml'),
        Signal('runtime', '3.12', 'manual')))
    architecture = ProjectArchitecture((component,))
    assert ProjectArchitecture.from_dict(json.loads(json.dumps(architecture.to_dict()))) == architecture
    with pytest.raises(FrozenInstanceError):
        component.path = 'other'
    assert ScannerRegistry().scan({}) == ()


@pytest.mark.parametrize('signal', [dict(field='password', value='x', confidence='high'),
    dict(field='port', value=65536, confidence='high'),
    dict(field='environment', value='TOKEN=secret', confidence='high')])
def test_controlled_signal_values(signal):
    with pytest.raises(ValueError):
        Signal(**signal)
