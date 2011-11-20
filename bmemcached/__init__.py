import struct
import socket
import logging

try:
    from cPickle import loads, dumps
except ImportError:
    from Pickle import loads, dumps

__all__ = ['Client']
logger = logging.getLogger('bmemcached')


class Client(object):
    def __init__(self, servers, username=None, password=None):
        self.username = username
        self.password = password
        self.set_servers(servers)

    def set_servers(self, servers):
        self.servers = [Server(server, self.username,
            self.password) for server in servers]

    def get(self, key):
        for server in self.servers:
            value = server.get(key)
            if value:
                return value

    def set(self, key, value, time=100):
        returns = []
        for server in self.servers:
            returns.append(server.set(key, value, time))

        return any(returns)


class Server(object):
    HEADER_STRUCT = '!BBHBBHLLQ'
    HEADER_SIZE = 24

    MAGIC = {
        'request': 0x80,
        'response': 0x81
    }

    # All structures will be appended to HEADER_STRUCT
    COMMANDS = {
        'get': {'command': 0x00, 'struct': '%ds'},
        'set': {'command': 0x01, 'struct': 'LL%ds%ds'},
        'auth_negotiation': {'command': 0x20},
        'auth_request': {'command': 0x21, 'struct': '%ds%ds'}
    }

    STATUS = {
        'success': 0x00,
        'key_not_found': 0x01,
        'unknown_command': 0x81
    }

    FLAGS = {
        'pickle': 1<<0,
        'integer': 1<<1,
        'long': 1<<2,
        'compressed': 1<<3
    }

    def __init__(self, server, username=None, password=None):
        self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server = server.split(':')
        host = server[0]
        if len(server) > 1:
            try:
                port = int(server[1])
            except (ValueError, TypeError):
                port = 11211
        else:
            port = 11211

        self.connection.connect((host, port))
        if username and password:
            self.authenticate(username, password)

    def authenticate(self, username, password):
        logger.info('Authenticating as %s' % username)
        self.connection.send(struct.pack(self.HEADER_STRUCT,
            self.MAGIC['request'],
            self.COMMANDS['auth_negotiation']['command'],
            0, 0, 0, 0, 0, 0, 0))
        header = self.connection.recv(self.HEADER_SIZE)
        (magic, opcode, keylen, extlen, datatype, status, bodylen, opaque,
            cas) = struct.unpack(self.HEADER_STRUCT, header)

        if status == self.STATUS['unknown_command']:
            logger.debug('Server does not requires authentication.')
            return True

        methods = self.connection.recv(bodylen).split(' ')

        if not 'PLAIN' in methods:
            raise AuthenticationNotSupported('This module only supports ' + \
                'PLAIN auth for now.')

        method = 'PLAIN'
        auth = '\x00%s\x00%s' % (username, password)
        self.connection.send(struct.pack(self.HEADER_STRUCT + \
            self.COMMANDS['auth_request']['struct'] % (len(method), len(auth)),
            self.MAGIC['request'], self.COMMANDS['auth_request']['command'],
            len(method), 0, 0, 0, len(method) + len(auth), 0, 0, method, auth))
        header = self.connection.recv(self.HEADER_SIZE)
        (magic, opcode, keylen, extlen, datatype, status, bodylen,
            opaque, cas) = struct.unpack(self.HEADER_STRUCT, header)

        if status != self.STATUS['success']:
            raise MemcachedException('Code: %d Message: %s' % (status,
                self.connection.recv(bodylen)))

        logger.debug('Auth OK. Code: %d Message: %s' % (status,
            self.connection.recv(bodylen)))

        return True

    def serialize(self, value):
        flags = 0
        if isinstance(value, str):
            pass
        elif isinstance(value, int):
            flags |= self.FLAGS['integer']
            value = str(value)
        elif isinstance(value, long):
            flags |= self.FLAGS['long']
            value = str(value)
        else:
            flags |= self.FLAGS['pickle']
            value = dumps(value)

        # TODO: Compression
        return (flags, value)

    def deserialize(self, value, flags):
        if flags == 0:
            return value
        elif flags & self.FLAGS['integer']:
            return int(value)
        elif flags & self.FLAGS['long']:
            return long(value)
        elif flags & self.FLAGS['pickle']:
            return loads(value)

    def get(self, key):
        logger.info('Getting key %s' % key)
        self.connection.send(struct.pack(self.HEADER_STRUCT + \
            self.COMMANDS['get']['struct'] % (len(key)),
            self.MAGIC['request'],
            self.COMMANDS['get']['command'],
            len(key), 0, 0, 0, len(key), 0, 0, key))

        header = self.connection.recv(self.HEADER_SIZE)
        (magic, opcode, keylen, extlen, datatype, status, bodylen,
            opaque, cas) = struct.unpack(self.HEADER_STRUCT, header)

        logger.debug('Len: %d. Data type: %d' % (extlen, datatype))

        if status != self.STATUS['success']:
            if status == self.STATUS['key_not_found']:
                logger.debug('Key not found. Message: %s' \
                    % self.connection.recv(bodylen))
                return None

            raise MemcachedException('Code: %d Message: %s' % (status,
                self.connection.recv(bodylen)))

        flags, value = struct.unpack('!L%ds' % extlen,
            self.connection.recv(bodylen))

        logger.debug('Value "%s"' % value)

        return self.deserialize(value, flags)


    def set(self, key, value, time):
        logger.info('Setting key %s.' % key)
        flags, value = self.serialize(value)
        logger.info('Value bytes %d.' % len(value))

        self.connection.send(struct.pack(self.HEADER_STRUCT + \
            self.COMMANDS['set']['struct'] % (len(key), len(value)),
            self.MAGIC['request'],
            self.COMMANDS['set']['command'],
            len(key),
            8, 0, 0, len(key) + len(value) + 8, 0, 0, flags, time, key, value))

        header = self.connection.recv(self.HEADER_SIZE)
        (magic, opcode, keylen, extlen, datatype, status, bodylen,
            opaque, cas) = struct.unpack(self.HEADER_STRUCT, header)

        logger.debug((magic, opcode, keylen, extlen, datatype, status, bodylen,
            opaque, cas))

        if status != self.STATUS['success']:
            raise MemcachedError('Code: %d Message: %s' % (status,
                self.connection.recv(bodylen)))

        return True


class AuthenticationNotSupported(Exception):
    pass


class MemcachedException(Exception):
    pass
