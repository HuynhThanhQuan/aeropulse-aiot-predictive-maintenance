import json
from types import SimpleNamespace

import numpy as np

from aiot_pm.engine_origin import dataset_split
from aiot_pm.schema import engine_topic, topic_engine_id
from aiot_pm.simulator import EngineSimulator


def test_split_labels_follow_model_metadata_and_do_not_guess(tmp_path, monkeypatch):
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({'train_engine_ids': [95], 'validation_engine_ids': [98]}))
    monkeypatch.setenv('METADATA_PATH', str(metadata))
    assert dataset_split('FD001-095') == 'Train'
    assert dataset_split('FD001-098') == 'Validation'
    assert dataset_split('FD001-TEST-095') == 'Test'
    assert dataset_split('FD001-099') == 'Unknown'
    assert dataset_split('FD001-TEST-999') == 'Unknown'
    metadata.unlink()
    assert dataset_split('FD001-095') == 'Unknown'
    assert dataset_split(None) == 'Unknown'


def test_test_replay_has_distinct_identity_and_uses_selected_rows(tmp_path):
    path = tmp_path / 'test_FD001.txt'
    rows = np.zeros((3, 26))
    rows[:, 0] = [1, 1, 2]
    rows[:, 1] = [1, 2, 1]
    np.savetxt(path, rows)
    test = EngineSimulator(path, 1, .25, True, dataset_split='test')
    train = EngineSimulator(path, 1, .25, True)
    assert test.engine_id == 'FD001-TEST-001'
    assert test.engine_id != train.engine_id
    assert test.rows[:, 1].tolist() == [1, 2]
    assert topic_engine_id(engine_topic(test.engine_id, 'command')) == test.engine_id


def test_dashboard_apis_expose_split_labels(monkeypatch):
    from aiot_pm import dashboard

    monkeypatch.setattr(dashboard, 'dataset_split', lambda key: 'Test' if key else 'Unknown')
    monkeypatch.setattr(dashboard.STATE, 'snapshot', lambda key: {
        'engine_id': 'FD001-TEST-001', 'engines': ['FD001-TEST-001'],
    })
    result = dashboard.snapshot('FD001-TEST-001')
    assert result['dataset_split'] == 'Test'
    assert result['engine_splits']['FD001-TEST-001'] == 'Test'

    class Fleet:
        def engines(self):
            return {'engines': [{'engine_id': 'FD001-TEST-001'}], 'ranking': []}

        def engine(self, key):
            return {'engine_id': key}

    monkeypatch.setattr(dashboard, 'fleet_service', lambda: Fleet())
    assert dashboard.fleet_engines()['engines'][0]['dataset_split'] == 'Test'
    assert dashboard.fleet_engine('FD001-TEST-001')['dataset_split'] == 'Test'


def test_commands_cannot_cross_train_test_identity(tmp_path):
    path = tmp_path / "test_FD001.txt"
    rows = np.zeros((2, 26))
    rows[:, 0] = 1
    np.savetxt(path, rows)
    simulator = EngineSimulator(path, 1, .25, True, dataset_split="test")
    payload = json.dumps({"action": "schedule_maintenance"}).encode()
    simulator.on_message(None, None, SimpleNamespace(
        topic="aiot/engine/FD001-001/command", payload=payload,
    ))
    assert not simulator.maintenance_scheduled
    simulator.on_message(None, None, SimpleNamespace(
        topic="aiot/engine/FD001-TEST-001/command", payload=payload,
    ))
    assert simulator.maintenance_scheduled


def test_retired_engine_excluded_from_console_without_deleting_data(monkeypatch):
    from aiot_pm.config import load_config
    from aiot_pm.dashboard import DashboardState
    from aiot_pm.engine_origin import is_displayed_engine

    monkeypatch.setenv('DASHBOARD_EXCLUDED_ENGINES', ' FD001-TEST-001 ')
    state = DashboardState(load_config())
    state.status['FD001-TEST-001'] = {'state': 'offline'}
    state.status['FD001-TEST-034'] = {'state': 'online'}
    assert state.snapshot()['engines'] == ['FD001-TEST-034']
    assert 'FD001-TEST-001' in state.status
    assert is_displayed_engine('FD001-TEST-034')
    monkeypatch.delenv('DASHBOARD_EXCLUDED_ENGINES')
    assert is_displayed_engine('FD001-TEST-001')
