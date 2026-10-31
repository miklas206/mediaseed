import copy,json
import pytest
from deploy.install_seeding_storage import configuration,TARGET


def setup():
    seed={'services':{'app':{'volumes':[{'type':'bind','source':'/mnt/media/Konverterede Film','target':TARGET}]},'qbittorrent':{'volumes':[{'type':'volume','source':'existing-config','target':'/config'}]}}}
    vpn={'services':{'app':{'environment':{'PRIVATE_TOKEN':'preserve','MEDIAVPN_MEDIASEED_STORAGE':json.dumps([{'id':'movies','path':'/downloads/media/movies','source':'/mnt/media/movies'}])}}}}
    return seed,vpn


def test_fresh_and_existing_storage_are_provisioned_idempotently_without_losing_settings():
    seed,vpn=setup();before=copy.deepcopy((seed,vpn));first=configuration(seed,vpn)
    assert (seed,vpn)==before and configuration(*first)==first
    assert first[0]['services']['qbittorrent']['volumes'][0]['source']=='existing-config'
    assert first[0]['services']['qbittorrent']['volumes'][1]['target']==TARGET
    env=first[1]['services']['app']['environment'];assert env['PRIVATE_TOKEN']=='preserve'
    rows=json.loads(env['MEDIAVPN_MEDIASEED_STORAGE']);assert rows[0]['id']=='movies' and rows[1]['path']==TARGET
    assert len(rows)==2 and rows[1]['source']=='/mnt/media/Konverterede Film'


@pytest.mark.parametrize('conflict',['app-readonly','torrent-source','vpn-source'])
def test_conflicting_storage_is_preserved_and_rejected(conflict):
    seed,vpn=setup()
    if conflict=='app-readonly':seed['services']['app']['volumes'][0]['read_only']=True
    elif conflict=='torrent-source':seed['services']['qbittorrent']['volumes'].append({'type':'bind','source':'/foreign','target':TARGET})
    else:vpn['services']['app']['environment']['MEDIAVPN_MEDIASEED_STORAGE']=json.dumps([{'id':'converted-movies','path':TARGET,'source':'/foreign'}])
    before=copy.deepcopy((seed,vpn))
    with pytest.raises(ValueError):configuration(seed,vpn)
    assert (seed,vpn)==before
