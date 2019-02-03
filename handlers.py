import re, time, json, logging, hashlib, base64, asyncio

from aiohttp import web

try:
    from .coreweb import get, post
except:
    from coreweb import get, post

try:
    from .model import User, Comment, Blog, next_id
except:
    from model import User, Comment, Blog, next_id

try:
    from .apis import APIError, ApiValueError, APIPermissionError
except:
    from apis import APIError, ApiValueError, APIPermissionError
try:
    from . import config
except:
    import config

_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-z]{40}$')
COOKIE_NAME = 'mywebcookie'
_COOKIE_KEY = config.configs.session.secret

def user2cookie(user: User, max_age):
    expires = str(int(time.time() + max_age))
    s = '{}-{}-{}-{}'.format(user.id, user.passwd, expires, _COOKIE_KEY)
    L = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(L)

async def cookie2user(cookie_str: str):
    if not cookie_str:
        return None
    try:
        L = cookie_str.split('-')
        if len(L) != 3:
            return None
        uid, expires, sha1 = L
        if int(expires) < time.time():
            return None
        user = await User.find(uid)
        if user is None:
            return None
        s = '{}-{}-{}-{}'.format(uid, user.passwd, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(s.encode('utf-8')).hexdigest():
            logging.info('invaild sha1')
            return None
        user.passwd = '********'
        return user
    except Exception as e:
        logging.exception(e)
        return None




@get('/')
async def index(request):
    summary = 'Lorem ipsum dolor sit amet, consectetur adipisicing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.'
    blogs = [
        Blog(id='1', name='Test Blog', summary=summary, created_at=time.time() - 120),
        Blog(id='2', name='Something New', summary=summary, created_at=time.time() - 3600),
        Blog(id='4', name='Learn Swift', summary=summary, created_at=time.time() - 7200)
    ]
    return {
        '_template': 'blogs.html',
        'blogs': blogs
    }


@get('/register')
def register():
    return {
        '_template': 'register.html'
    }

@get('/signin')
async def signin():
    return {
        '_template': 'signin.html'
    }

@get('/signout')
async def singout(request):
    referer = request.headers.get("Referer")
    r = web.HTTPFound(referer or '/')
    r.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)
    logging.info('user signed out')
    return r



@post('/api/users')
async def api_register_user(*, email, name, passwd):
    if not name or not name.strip():
        raise ApiValueError('name')
    if not email or not _RE_EMAIL.match(email):
        raise ApiValueError("email")
    if not passwd or not _RE_SHA1.match(passwd):
        raise ApiValueError('password')
    users = await User.findAll('email=?', email)
    if len(users) > 0:
        raise APIError("register:failed", "email", "Email is already in use.")
    uid = next_id()
    sha1_passwd = '{}:{}'.format(uid, passwd)
    user = User(id=uid, name=name.strip(), email=email, passwd=hashlib.sha1(sha1_passwd.encode('utf-8')).hexdigest(),
                image='null')
    await user.save()
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '********'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r


@post('/api/authenticate')
async def authenticate(*, email, passwd):
    if not email:
        raise ApiValueError('email', 'Invalid email')
    if not passwd:
        raise ApiValueError('passwd', 'Invalid password')
    users = await User.findAll('email=?', email)
    if len(users) == 0:
        raise ApiValueError('email', 'Emial not exist.')
    user = users[0]
    sha1 = hashlib.sha1()
    sha1.update(user.id.encoude('utf-8'))
    sha1.update(b':')
    sha1.update(passwd.encode('utf-8'))
    if user.passwd != sha1.hexdigest():
        return ApiValueError('passwd', 'Invalid password')
    # setcookie
    r = web.Response()
    r.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.passwd = '********'
    r.content_type = 'application/json'
    r.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return r



