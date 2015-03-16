#!/usr/bin/env python

# Turku backups - client agent
# Copyright 2015 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

import uuid
import string
import random
import json
import os
import copy
import subprocess
import platform
import urlparse
import httplib


class RuntimeLock():
    name = None
    file = None

    def __init__(self, name):
        import fcntl
        file = open(name, 'w')
        try:
            fcntl.lockf(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError, e:
            import errno
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise
        file.write('%10s\n' % os.getpid())
        file.flush()
        file.seek(0)
        self.name = name
        self.file = file

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
            os.unlink(self.name)

    def __del__(self):
        self.close()

    def __enter__(self):
        self.file.__enter__()
        return self

    def __exit__(self, exc, value, tb):
        result = self.file.__exit__(exc, value, tb)
        self.close()
        return result


def acquire_lock(name):
    return RuntimeLock(name)


def json_dump_p(obj, f):
    """Calls json.dump with standard (pretty) formatting"""
    return json.dump(obj, f, sort_keys=True, indent=4, separators=(',', ': '))


def json_dumps_p(obj):
    """Calls json.dumps with standard (pretty) formatting"""
    return json.dumps(obj, sort_keys=True, indent=4, separators=(',', ': '))


def dict_merge(s, m):
    """Recursively merge one dict into another."""
    if not isinstance(m, dict):
        return m
    out = copy.deepcopy(s)
    for k, v in m.items():
        if k in out and isinstance(out[k], dict):
            out[k] = dict_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(config_dir):
    config = {}
    config['config_dir'] = config_dir

    config_d = os.path.join(config['config_dir'], 'config.d')
    sources_d = os.path.join(config['config_dir'], 'sources.d')

    # Merge in config.d/*.json to the root level
    config_files = []
    if os.path.isdir(config_d):
        config_files = [os.path.join(config_d, fn) for fn in os.listdir(config_d) if fn.endswith('.json') and os.path.isfile(os.path.join(config_d, fn)) and os.access(os.path.join(config_d, fn), os.R_OK)]
    config_files.sort()
    for file in config_files:
        with open(file) as f:
            j = json.load(f)
        config = dict_merge(config, j)

    if 'var_dir' not in config:
        config['var_dir'] = '/var/lib/turku-agent'

    var_config_d = os.path.join(config['var_dir'], 'config.d')

    # Load /var config.d files
    var_config = {}
    var_config_files = []
    if os.path.isdir(var_config_d):
        var_config_files = [os.path.join(var_config_d, fn) for fn in os.listdir(var_config_d) if fn.endswith('.json') and os.path.isfile(os.path.join(var_config_d, fn)) and os.access(os.path.join(var_config_d, fn), os.R_OK)]
    var_config_files.sort()
    for file in var_config_files:
        with open(file) as f:
            j = json.load(f)
        var_config = dict_merge(var_config, j)
    # /etc gets priority over /var
    var_config = dict_merge(var_config, config)
    config = var_config

    if 'lock_dir' not in config:
        config['lock_dir'] = '/var/lock'

    var_sources_d = os.path.join(config['var_dir'], 'sources.d')

    # Validate the unit name
    if 'unit_name' not in config:
        config['unit_name'] = platform.node()
        # If this isn't in the on-disk config, don't write it; just
        # generate it every time

    # Pull the SSH public key
    if os.path.isfile(os.path.join(config['var_dir'], 'ssh_key.pub')):
        with open(os.path.join(config['var_dir'], 'ssh_key.pub')) as f:
            config['ssh_public_key'] = f.read().rstrip()
        config['ssh_public_key_file'] = os.path.join(config['var_dir'], 'ssh_key.pub')
        config['ssh_private_key_file'] = os.path.join(config['var_dir'], 'ssh_key')
    elif os.path.isfile(os.path.join(config['config_dir'], 'id_rsa.pub')):
        # XXX Legacy
        with open(os.path.join(config['config_dir'], 'id_rsa.pub')) as f:
            config['ssh_public_key'] = f.read().rstrip()
        config['ssh_public_key_file'] = os.path.join(config['config_dir'], 'id_rsa.pub')
        config['ssh_private_key_file'] = os.path.join(config['config_dir'], 'id_rsa')

    sources_config = {}
    # Merge in sources.d/*.json to the sources dict
    sources_files = []
    if os.path.isdir(sources_d):
        sources_files = [os.path.join(sources_d, fn) for fn in os.listdir(sources_d) if fn.endswith('.json') and os.path.isfile(os.path.join(sources_d, fn)) and os.access(os.path.join(sources_d, fn), os.R_OK)]
    sources_files.sort()
    var_sources_files = []
    if os.path.isdir(var_sources_d):
        var_sources_files = [os.path.join(var_sources_d, fn) for fn in os.listdir(var_sources_d) if fn.endswith('.json') and os.path.isfile(os.path.join(var_sources_d, fn)) and os.access(os.path.join(var_sources_d, fn), os.R_OK)]
    var_sources_files.sort()
    sources_files += var_sources_files
    for file in sources_files:
        with open(file) as f:
            j = json.load(f)
        sources_config = dict_merge(sources_config, j)

    # Check for required sources options
    for s in sources_config:
        if 'path' not in sources_config[s]:
            del sources_config[s]

    config['sources'] = sources_config

    return config


def fill_config(config):
    config_d = os.path.join(config['config_dir'], 'config.d')
    sources_d = os.path.join(config['config_dir'], 'sources.d')
    var_config_d = os.path.join(config['var_dir'], 'config.d')
    var_sources_d = os.path.join(config['var_dir'], 'sources.d')

    # Create required directories
    for d in (config_d, sources_d, var_config_d, var_sources_d):
        if not os.path.isdir(d):
            os.makedirs(d)

    # Validate the machine UUID/secret
    write_uuid_data = False
    if 'machine_uuid' not in config:
        config['machine_uuid'] = str(uuid.uuid4())
        write_uuid_data = True
    if 'machine_secret' not in config:
        config['machine_secret'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
        write_uuid_data = True
    # Write out the machine UUID/secret if needed
    if write_uuid_data:
        with open(os.path.join(var_config_d, '10-machine_uuid.json'), 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json_dump_p({'machine_uuid': config['machine_uuid'], 'machine_secret': config['machine_secret']}, f)

    # Restoration configuration
    write_restore_data = False
    if 'restore_path' not in config:
        config['restore_path'] = '/var/backups/turku-agent/restore'
        write_restore_data = True
    if 'restore_module' not in config:
        config['restore_module'] = 'turku-restore'
        write_restore_data = True
    if 'restore_username' not in config:
        config['restore_username'] = str(uuid.uuid4())
        write_restore_data = True
    if 'restore_password' not in config:
        config['restore_password'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
        write_restore_data = True
    if write_restore_data:
        with open(os.path.join(var_config_d, '10-restore.json'), 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            restore_out = {
                'restore_path': config['restore_path'],
                'restore_module': config['restore_module'],
                'restore_username': config['restore_username'],
                'restore_password': config['restore_password'],
            }
            json_dump_p(restore_out, f)
    if not os.path.isdir(config['restore_path']):
        os.makedirs(config['restore_path'])

    # Generate the SSH keypair if it doesn't exist
    if 'ssh_private_key_file' not in config:
        subprocess.check_call(['ssh-keygen', '-t', 'rsa', '-N', '', '-C', 'turku', '-f', os.path.join(config['var_dir'], 'ssh_key')])
        with open(os.path.join(config['var_dir'], 'ssh_key.pub')) as f:
            config['ssh_public_key'] = f.read().rstrip()
        config['ssh_public_key_file'] = os.path.join(config['var_dir'], 'ssh_key.pub')
        config['ssh_private_key_file'] = os.path.join(config['var_dir'], 'ssh_key')

    for s in config['sources']:
        # Check for missing usernames/passwords
        if not ('username' in config['sources'][s] or 'password' in config['sources'][s]):
            sources_secrets_d = os.path.join(config['config_dir'], 'sources_secrets.d')
            if os.path.isfile(os.path.join(sources_secrets_d, s + '.json')):
                # XXX Legacy
                with open(os.path.join(sources_secrets_d, s + '.json')) as f:
                    j = json.load(f)
                config['sources'][s]['username'] = j['username']
                config['sources'][s]['password'] = j['password']
            else:
                if 'username' not in config['sources'][s]:
                    config['sources'][s]['username'] = str(uuid.uuid4())
                if 'password' not in config['sources'][s]:
                    config['sources'][s]['password'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
            with open(os.path.join(var_sources_d, '10-' + s + '.json'), 'w') as f:
                os.fchmod(f.fileno(), 0o600)
                json_dump_p({s: {'username': config['sources'][s]['username'], 'password': config['sources'][s]['password']}}, f)


def api_call(api_url, cmd, post_data, timeout=5):
    url = urlparse.urlparse(api_url)
    if url.scheme == 'https':
        h = httplib.HTTPSConnection(url.netloc, timeout=timeout)
    else:
        h = httplib.HTTPConnection(url.netloc, timeout=timeout)
    out = json.dumps(post_data)
    h.putrequest('POST', '%s/%s' % (url.path, cmd))
    h.putheader('Content-Type', 'application/json')
    h.putheader('Content-Length', len(out))
    h.putheader('Accept', 'application/json')
    h.endheaders()
    h.send(out)

    res = h.getresponse()
    if not res.status == httplib.OK:
        raise Exception('Received error %d (%s) from API server' % (res.status, res.reason))
    if not res.getheader('content-type') == 'application/json':
        raise Exception('Received invalid reply from API server')
    try:
        return json.load(res)
    except ValueError:
        raise Exception('Received invalid reply from API server')
