import json
import httpx
from mediaseed.upload_diagnostics import tracker_diagnostics,diagnostic_message


def test_validation_fields_survive_while_credentials_and_urls_are_removed():
    response=httpx.Response(422,json={'message':'The given data was invalid.', 'errors':{'imdb':['The imdb field is required.'],'api_key':['secret-test-key'],'name':['Invalid Bearer secret-test-key; api_key=secret-test-key https://tracker.invalid/announce/private-passkey']},'request':{'api_key':'secret-test-key'},'trace':'private stack trace'})
    diagnostic=tracker_diagnostics(response,{'api_key':'secret-test-key'})
    assert diagnostic['kind']=='validation' and diagnostic['errors']['imdb']==['The imdb field is required.']
    assert 'imdb: The imdb field is required.' in diagnostic_message(diagnostic)
    text=json.dumps(diagnostic)
    for forbidden in ['secret-test-key','private-passkey','private stack trace','request','trace']:
        assert forbidden not in text
    assert 'api_key' not in diagnostic['errors']


def test_permission_error_is_distinct_and_raw_html_is_not_retained():
    denied=tracker_diagnostics(httpx.Response(403,json={'message':'You do not have permission to upload.'}),{})
    assert denied['kind']=='permission' and 'permission' in diagnostic_message(denied)
    page=tracker_diagnostics(httpx.Response(403,text='<html>private proxy details and cookies</html>'),{})
    assert page['responseFormat']=='html' and not page['message'] and not page['errors']
    assert 'private proxy' not in json.dumps(page)


def test_diagnostics_are_bounded_and_ignore_unexpected_shapes():
    diagnostic=tracker_diagnostics(httpx.Response(422,json={'message':'x'*5000,'errors':{str(i):['x'*5000]*20 for i in range(100)}}),{})
    assert len(diagnostic['message'])==400 and diagnostic['errors']=={}
    diagnostic=tracker_diagnostics(httpx.Response(422,json={'errors':{'field'+str(i):['x'*5000]*20 for i in range(100)}}),{})
    assert len(diagnostic['errors'])==24 and all(len(messages)==3 and len(messages[0])==240 for messages in diagnostic['errors'].values())
    assert len(diagnostic_message(diagnostic))<=2500
    for payload in [None,[],{'message':{'secret':'value'},'errors':{'field':[{},None]}}]:
        assert not tracker_diagnostics(httpx.Response(400,json=payload),{})['errors']


def test_tracker_success_false_data_supports_validation_and_permission_messages():
    response=httpx.Response(422,json={'success':False,'data':{'imdb':['The imdb field is required.'],'trace':'private trace','api_key':'secret-test-key'}})
    diagnostic=tracker_diagnostics(response,{'api_key':'secret-test-key'})
    assert diagnostic['errors']=={'imdb':['The imdb field is required.']}
    assert 'private trace' not in json.dumps(diagnostic) and 'secret-test-key' not in json.dumps(diagnostic)
    response=httpx.Response(403,json={'success':False,'data':'You do not have permission to upload.'})
    assert 'permission' in tracker_diagnostics(response,{})['message']
