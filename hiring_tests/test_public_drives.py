import pytest
from hiring.sources import SourceError, allowed_download, import_source


@pytest.mark.parametrize('url', ['https://cloud.mail.ru/public/abc/def',
    'https://drive.google.com/file/d/abc/view', 'https://drive.google.com/open?id=abc'])
def test_unapproved_drives_never_make_requests(url):
    class Reader:
        def get(self, *args, **kwargs):
            pytest.fail('Disabled provider must not make a request')
        json = get
    with pytest.raises(SourceError, match='отключён'):
        import_source(url, Reader())


@pytest.mark.parametrize('provider,host', [('mail', 'cloclo57.datacloudmail.ru'),
    ('mail', 'cloud.mail.ru'), ('google', 'drive.usercontent.google.com'),
    ('google', 'drive.google.com')])
def test_disabled_provider_cdn_denied(provider, host):
    assert not allowed_download(host, provider)


@pytest.mark.parametrize('host', ['cloclo57.datacloudmail.ru.evil.test','evil.cloud.mail.ru','127.0.0.1','mail.ru'])
def test_mail_unrelated_hosts_denied(host):
    assert not allowed_download(host,'mail')


def test_mail_dispatcher_to_arbitrary_url_denied_before_fetch():
    class Reader:
        def get(self,*args,**kwargs):
            return b'{"dispatcher":{"weblink_get":{"url":"https://evil.test/public/path"}}}'
    with pytest.raises(SourceError,match='отключён'):
        import_source('https://cloud.mail.ru/public/abc/def',Reader())
