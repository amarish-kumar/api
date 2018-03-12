import functools
import json
import os
import random
import sys

import jwt
import pytest
import pytest_redis.factories as redis_factories
import tornado.escape
import tornado.gen
import tornado.options as opt

import cloudplayer.api.app


def which(executable):  # pragma: no cover
    path = os.environ['PATH']
    paths = path.split(os.pathsep)
    extlist = ['']
    if os.name == 'os2':
        (base, ext) = os.path.splitext(executable)
        if not ext:
            executable = executable + '.exe'
    elif sys.platform == 'win32':
        pathext = os.environ['PATHEXT'].lower().split(os.pathsep)
        (base, ext) = os.path.splitext(executable)
        if ext.lower() not in pathext:
            extlist = pathext
    for ext in extlist:
        execname = executable + ext
        if os.path.isfile(execname):
            return execname
        else:
            for p in paths:
                f = os.path.join(p, execname)
                if os.path.isfile(f):
                    return f
    else:
        return None


def randstr(
        length=10,
        alphabet=[chr(c) for r in [
            (0x0021, 0x0021),
            (0x0023, 0x0026),
            (0x0028, 0x007E),
            (0x00A1, 0x00AC),
            (0x00AE, 0x00FF),
            (0x0100, 0x017F),
            (0x0180, 0x024F),
            (0x2C60, 0x2C7F),
            (0x16A0, 0x16F0),
            (0x0370, 0x0377),
            (0x037A, 0x037E),
            (0x0384, 0x038A),
            (0x038C, 0x038C)
        ] for c in range(r[0], r[1] + 1)]):  # pragma: no cover
    return ''.join(random.choice(alphabet) for i in range(length))


redis_proc = redis_factories.redis_proc(executable=which('redis-server'))


@pytest.fixture(scope='session', autouse=True)
def app(postgresql_proc, redis_proc):
    opt.define('connect_timeout', default=3, group='httpclient')
    opt.define('request_timeout', default=3, group='httpclient')
    opt.define('max_redirects', default=1, group='httpclient')
    opt.define('youtube', group='app', default={
        'key': 'yt-key', 'secret': 'yt-secret', 'api_key': 'yt-api-key'})
    opt.define('cloudplayer', group='app', default={
        'key': 'cp-key', 'secret': 'cp-secret', 'api_key': 'cp-api-key'})
    opt.define('soundcloud', group='app', default={
        'key': 'sc-key', 'secret': 'sc-secret', 'api_key': 'sc-api-key'})
    opt.define('bugsnag', group='app', default={})
    opt.define('jwt_cookie', default='tok_v1', group='app')
    opt.define('jwt_expiration', default=1, group='app')
    opt.define('jwt_secret', default='secret', group='app')
    opt.define('providers', default=[
        'youtube', 'soundcloud', 'cloudplayer'], group='app')
    opt.define('allowed_origins', default=['*'], group='app')
    opt.define('num_executors', default=1, group='app')
    opt.define('menuflow_state', default='dev', group='app')
    opt.define('redis_host', default=redis_proc.host, group='app')
    opt.define('redis_port', default=redis_proc.port, group='app')
    opt.define('redis_db', default=0, group='app')
    opt.define('redis_password', default=None, group='app')
    opt.define('postgres_host', default=postgresql_proc.host, group='app')
    opt.define('postgres_port', default=postgresql_proc.port, group='app')
    opt.define('postgres_db', default='postgres', group='app')
    opt.define('postgres_user', default='postgres', group='app')
    opt.define('postgres_password', default='', group='app')
    cloudplayer.api.app.configure_httpclient()
    app = cloudplayer.api.app.Application()
    yield app
    app.database.engine.dispose()


@pytest.fixture(scope='function')
def db(app):
    import cloudplayer.api.model.base as model
    app.database.initialize()
    session = app.database.create_session()
    yield session
    session.rollback()
    for table in reversed(model.Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture(scope='function')
def user(db):
    from cloudplayer.api.model.user import User
    user = User()
    db.add(user)
    db.commit()
    return user


@pytest.fixture(scope='function')
def account(db, user):
    from cloudplayer.api.model.account import Account
    from cloudplayer.api.model.favourite import Favourite
    from cloudplayer.api.model.image import Image
    account = Account(
        id=str(user.id),
        provider_id='cloudplayer',
        favourite=Favourite(),
        user_id=user.id,
        image=Image(
            small='https://image.small/{}'.format(randstr()),
            medium='https://image.medium/{}'.format(randstr()),
            large='https://image.large/{}'.format(randstr())),
        title=randstr())
    db.add(account)
    db.commit()
    return account


@pytest.fixture(scope='function')
def current_user(account, user):
    return {
        'user_id': user.id,
        'cloudplayer': account.id,
        'youtube': None,
        'soundcloud': None}


@pytest.fixture(scope='function')
def user_cookie(current_user):
    user_jwt = jwt.encode(
        current_user,
        opt.options['jwt_secret'],
        algorithm='HS256').decode('ascii')
    return '{}={};'.format(opt.options['jwt_cookie'], user_jwt)


@pytest.fixture(scope='function')
def user_fetch(user_cookie, http_client, base_url):

    @tornado.gen.coroutine
    def fetch(req, body=None, **kw):  # pragma: no cover
        if isinstance(req, str):
            if not req.startswith(base_url):
                req = '{}/{}'.format(base_url, req.lstrip('/'))
        else:
            if not req.url.startswith(base_url):
                req.url = '{}/{}'.format(base_url, req.url.lstrip('/'))
        if body is not None and not isinstance(body, str):
            body = json.dumps(body, indent=2)
        headers = kw.get('headers', {})
        cookies = headers.get('Cookie', '')
        cookies = '{};{}'.format(cookies.rstrip(';'), user_cookie)
        headers['Cookie'] = cookies
        kw['headers'] = headers
        response = yield http_client.fetch(req, body=body, **kw)
        decode = functools.partial(tornado.escape.json_decode, response.body)
        object.__setattr__(response, 'json', decode)
        return response
    return fetch
