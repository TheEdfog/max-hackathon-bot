import json
import pytest
from hiring.sources import SourceError, allowed_download, import_source
from test_talent import RESUME, pdf_bytes


def test_mail_public_dispatcher_only_no_javascript_execution():
    class Reader:
        def __init__(self): self.calls=[]
        def get(self,url,provider,limit=None):
            self.calls.append(url)
            if len(self.calls)==1:
                return ('<script>throw new Error("must not execute"); const data={"dispatcher":' +
                        json.dumps({'weblink_get':{'url':'https://cloclo57.cloud.mail.ru/public/example/g/no'}}) + '};</script>').encode()
            assert url=='https://cloclo57.cloud.mail.ru/public/example/g/no/abc/def'
            return pdf_bytes()
    reader=Reader()
    result=import_source('https://cloud.mail.ru/public/abc/def',reader)
    assert result.provider=='mail' and RESUME in result.text and len(reader.calls)==2


@pytest.mark.parametrize('host', ['cloclo57.datacloudmail.ru','cloclo2.cloud.mail.ru'])
def test_mail_public_cdn_allowed(host):
    assert allowed_download(host,'mail')


@pytest.mark.parametrize('host', ['cloclo57.datacloudmail.ru.evil.test','evil.cloud.mail.ru','127.0.0.1','mail.ru'])
def test_mail_unrelated_hosts_denied(host):
    assert not allowed_download(host,'mail')


def test_mail_dispatcher_to_arbitrary_url_denied_before_fetch():
    class Reader:
        def get(self,*args,**kwargs):
            return b'{"dispatcher":{"weblink_get":{"url":"https://evil.test/public/path"}}}'
    with pytest.raises(SourceError,match='Формат'):
        import_source('https://cloud.mail.ru/public/abc/def',Reader())
