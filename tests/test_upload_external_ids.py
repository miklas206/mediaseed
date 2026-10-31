import asyncio,json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from mediaseed.upload_external_ids import upload_external_ids,imdb_number
from mediaseed.upload_engine.metadata import Tmdb


def bridge_with(result):
    return SimpleNamespace(native=SimpleNamespace(helper=SimpleNamespace(metadata=SimpleNamespace(details=Mock(return_value=result)))))


def test_ids_are_normalized_without_inventing_an_imdb_identity():
    for value in ['tt0012345','0012345',12345]:assert imdb_number(value)=='12345'
    for value in [None,True,False,0,'0','tt0','https://imdb.com/title/tt1234567','1.1','garbage']:assert imdb_number(value) is None
    bridge=bridge_with(None)
    assert asyncio.run(upload_external_ids(bridge,{'metadata':{'imdb':'tt1234567'}},12))=={'imdb':'1234567','mal':'0'}
    assert asyncio.run(upload_external_ids(bridge,{'metadata':{'imdb':'tt1234567','mal':54321}},12))['mal']=='54321'
    bridge.native.helper.metadata.details.assert_not_called()


def test_legacy_packet_gets_the_selected_movies_imdb_from_native_metadata():
    bridge=bridge_with({'kind':'movie','tmdb':12,'imdb':'tt22898462'})
    assert asyncio.run(upload_external_ids(bridge,{'metadata':{'tmdb':12}},12))=={'imdb':'22898462','mal':'0'}
    query=bridge.native.helper.metadata.details.call_args.args[0]
    assert query.kind=='movie' and query.tmdb==12


@pytest.mark.parametrize('result',[None,{}, {'kind':'movie','tmdb':99,'imdb':'tt1234567'},{'kind':'tv','tmdb':12,'imdb':'tt1234567'},{'kind':'movie','tmdb':12,'imdb':None}])
def test_unverified_metadata_cannot_supply_an_id(result):
    with pytest.raises(HTTPException) as exc:asyncio.run(upload_external_ids(bridge_with(result),{},12))
    assert exc.value.status_code==409


def test_malformed_mal_and_metadata_failure_are_concrete_local_errors():
    with pytest.raises(HTTPException):asyncio.run(upload_external_ids(bridge_with(None),{'metadata':{'imdb':'tt1234567','mal':'not-an-id'}},12))
    bridge=bridge_with(None);bridge.native.helper.metadata.details.side_effect=RuntimeError('secret must not escape')
    with pytest.raises(HTTPException) as exc:asyncio.run(upload_external_ids(bridge,{},12))
    assert 'secret' not in exc.value.detail and 'ikke sendt' in exc.value.detail


def test_tmdb_details_preserves_external_movie_id_for_new_preparations(tmp_path):
    tmdb=Tmdb(tmp_path);tmdb.credential.write_text(json.dumps({'credential':'synthetic-token'}))
    tmdb.localized=lambda path,credential:({'id':12,'title':'Synthetic movie','overview':'Synthetic plot','release_date':'2025-01-01','imdb_id':'tt22898462'},'en')
    value=tmdb.details('movie',12)
    assert value['imdb']=='tt22898462' and value['tmdb']==12 and value['kind']=='movie'
