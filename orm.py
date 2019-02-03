import asyncio
# import aiohttp
import aiomysql
import logging

logging.basicConfig(level=logging.INFO)

global __pool


def log(sql, args=()):
    logging.info('SQL:%s' % sql)


async def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        host=kw.get('host', '127.0.0.1'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf8'),
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )
    print('connection pool created')


async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    async with __pool.get() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:  # 传入aiomysql.DictCursor，代表返回的结果形式为一个dict
            await cur.execute(sql.replace('?', '%s'), args or ())
            if size:
                rs = await cur.fetchmany(size)
            else:
                rs = await cur.fetchall()
        logging.info('rows returned: %s' % len(rs))
        return rs


async def execute(sql, args, autocommit=True):
    log(sql)
    global __pool
    async with __pool.get() as conn:
        if not autocommit:
            await conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                affected = cur.rowcount
            if autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                conn.rollback()
            raise e
        return affected


def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ','.join(L)


class ModelMetaclass(type):
    def __new__(cls, name, bases, attrs):
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table,%s)' % (name, tableName))
        mappings = {}
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    if primaryKey:
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    fields.append(k)
        if primaryKey == None:
            raise RuntimeError('Primary nor found')
        for k in mappings.keys():
            attrs.pop(k)

        escaped_fields = list(map(lambda f: '`%s`' % f, fields))  # 将列名转化为`列名`
        attrs['_mappings'] = mappings  # 保存映射关系
        attrs['_table'] = tableName  # 保存表名
        attrs['_primary_key'] = primaryKey  # 保存主键
        attrs['_fields'] = fields  # 保存主键以外的列名
        # 构造默认的select,insert,updata,delete语句
        attrs['_select'] = 'select `%s`, %s from `%s`' % (primaryKey, ','.join(escaped_fields), tableName)
        attrs['_insert'] = 'insert into `%s` (%s, `%s`) values (%s)' % (
            tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['_update'] = 'update `%s` set %s where `%s`=?' % (
            tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['_delete'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


class Model(dict, metaclass=ModelMetaclass):
    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'model' object has no attribute '%s' " % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value == None:
            field = self._mappings[key]
            if field.default != None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s:%s' % (key, value))
        return value

    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        # print('call findall')
        sql = [cls._select]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple):
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: {}'.format(str(limit)))
        # logging.INFO(' '.join(sql))
        rs = await select(' '.join(sql), args)
        return [cls(**r) for r in rs]

    @classmethod
    async def findNumber(cls, selectField, where=None, args=None):
        sql = ['select %s _num_ from %s' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = await select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']

    @classmethod
    # @asyncio.coroutine
    async def find(cls, pk):
        rs = await select('%s where `%s`=?' % (cls._select, cls._primary_key), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    # @asyncio.coroutine
    async def save(self):
        args = list(map(self.getValueOrDefault, self._fields))
        args.append(self.getValueOrDefault(self._primary_key))
        rows = await execute(self._insert, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)


class Field(object):
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s,%s,%s>' % (self.__class__.__name__, self.column_type, self.name)


class StringField(Field):
    def __init__(self, name=None, primary_key=None, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)


class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)


class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)


class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)


class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)
