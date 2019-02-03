import asyncio, os, inspect, logging, functools

from urllib import parse
from aiohttp import web

try:
    from .apis import APIError
except:
    from apis import APIError


# 可以考虑实现__call__的class来实现工厂（嫌麻烦不弄了）
def get(path):
    # print('find route {}'.format(path))
    def decoator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            return fn(*args, **kw)

        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper

    return decoator


def post(path):
    def decoator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            return fn(*args, **kw)

        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper

    return decoator


def get_required_kw_args(fn):
    '''利用内省解析函数，收集标准的仅限关键字参数'''

    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)


def get_named_kw_args(fn):
    '''收集仅限关键字参数'''

    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)


def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True


def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True


def has_request_arg(fn):
    '''找名为request的参数并且要求其为最后一个参数'''

    sig = inspect.signature(fn)
    params = sig.parameters
    param_lst = inspect.Parameter
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        if found and (param.kind != param_lst.VAR_POSITIONAL
                      and param.kind != param_lst.KEYWORD_ONLY
                      and param.kind != param_lst.VAR_KEYWORD):
            raise ValueError('request parameter must be last named parameter in function:%s%s' %
                             (fn.__name__, str(sig)))
    return found


class RequestHandler(object):
    '''用于绑定上层的业务函数与app（业务函数中处理route）'''
    '''向aiohttp接口兼容，返回web.response对象'''

    def __init__(self, app, fn):
        self._app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)  # 判断request参数
        self._has_var_kw_arg = has_var_kw_arg(fn)  # 判断是否有**参数
        self._has_named_kw_args = has_named_kw_args(fn)  # 判断仅限关键字参数
        self._named_kw_args = get_named_kw_args(fn)  # 获取仅限关键字参数的tuple
        self._required_kw_args = get_required_kw_args(fn)  # 获取需要明确传入的仅限关键字参数

    async def __call__(self, request):
        kw = None
        if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
            if request.method == "POST":
                if not request.content_type:
                    return web.HTTPBadRequest("Missinig Content_Type")
                ct = request.content_type.lower()
                if ct.startswith('application/json'):
                    params = await request.json()
                    if not isinstance(params, dict):
                        return web.HTTPBadRequest("JSON body must be object")
                    kw = params
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    params = await request.post()
                    kw = dict(**params)
                else:
                    return web.HTTPBadRequest("Unsupported Content-Type: %s" % request.content_type)
            if request.method == "GET":
                qs = request.query_string
                if qs:
                    kw = dict()
                    for k, v in parse.parse_qs(qs, True).items():
                        kw[k] = v[0]

        if kw is None:
            # match_info主要是保存像@get('/blog/{id}')里面的id，就是路由路径里的参数
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_arg and self._named_kw_args:
                # remove all unamed kw
                copy = dict()
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            # check named arg
            for k, v in request.match_info.items():
                if k in kw:
                    logging.warning("Duplicate arg name in named arg and kw args: %s" % k)
                kw[k] = v
        if self._has_request_arg:
            kw['request'] = request
        # check required kw
        if self._required_kw_args:
            for name in self._required_kw_args:
                if not name in kw:
                    return web.HTTPBadRequest('Mssing argument: %s' % name)
        logging.info("call with args: {}".format(str(kw)))
        try:
            r = await self._func(**kw)
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)


def add_static(app):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    app.router.add_static('/static/', path)
    logging.info("add static {} => {}".format('/static/', path))


def add_route(app, fn):
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    if path is None or method is None:
        raise ValueError("@get or @post not defined in {}.".format(str(fn)))
    if not asyncio.iscoroutine(fn) and not inspect.isgeneratorfunction(fn):
        fn = asyncio.coroutine(fn)
    logging.info('add route {} {} => {}({})' \
                 .format(method, path, fn.__name__, ','.join(inspect.signature(fn).parameters.keys())))
    app.router.add_route(method, path, RequestHandler(app, fn))


def add_routes(app, modele_name):
    n = modele_name.rfind('.')

    if n == (-1):
        mod = __import__(modele_name, globals(), locals())
    else:
        name = modele_name[n + 1:]
        mod = getattr(__import__(modele_name[:n], globals(), locals(), [name]), name)
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        fn = getattr(mod, attr)
        if callable(fn):
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            if method and path:
                add_route(app, fn)
