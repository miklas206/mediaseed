import importlib.util
from pathlib import Path
import pytest


def module():
    spec=importlib.util.spec_from_file_location('pool_install',Path(__file__).parents[1]/'deploy/install_pool_rename.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_live_policy_is_idempotent_and_does_not_remount(tmp_path,monkeypatch):
    m=module();value=[b'false'];writes=[]
    monkeypatch.setattr(m.os.path,'ismount',lambda p:True)
    monkeypatch.setattr(m.os,'getxattr',lambda p,k:value[0])
    def setxattr(p,k,v):writes.append((str(p),k,v));value[0]=v
    monkeypatch.setattr(m.os,'setxattr',setxattr)
    assert m.apply_live(tmp_path)=={'enabled':True,'changed':True,'remounted':False}
    assert m.apply_live(tmp_path)['changed'] is False;assert len(writes)==1
    service=m.service_text(tmp_path,'qa.mount','/usr/local/lib/mediaseed/pool_rename.py')
    assert 'After=qa.mount' in service;assert 'PartOf=qa.mount' in service;assert 'WantedBy=qa.mount' in service
    assert 'DefaultDependencies=no' in service;assert 'Before=pve-container@107.service umount.target' in service
    assert '--live --pool' in service;assert 'mount -' not in service


def test_unmounted_storage_fails_before_setting_policy(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m.os.path,'ismount',lambda p:False)
    monkeypatch.setattr(m.os,'setxattr',lambda *a:pytest.fail('must not write'))
    with pytest.raises(ValueError):m.apply_live(tmp_path)
