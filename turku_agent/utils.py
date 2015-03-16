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
import sys
import platform
import urlparse
import httplib


CONFIG_D = '/etc/turku-agent/config.d'
SOURCES_D = '/etc/turku-agent/sources.d'
SOURCES_SECRETS_D = '/etc/turku-agent/sources_secrets.d'
SSH_PRIVATE_KEY = '/etc/turku-agent/id_rsa'
SSH_PUBLIC_KEY = '/etc/turku-agent/id_rsa.pub'
RSYNCD_CONF = '/etc/turku-agent/rsyncd.conf'
RSYNCD_SECRETS = '/etc/turku-agent/rsyncd.secrets'
VAR_DIR = '/var/lib/turku-agent'
RESTORE_DIR = '/var/backups/turku-agent/restore'


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


def load_config():
    for d in (CONFIG_D, SOURCES_D, VAR_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    for d in (SOURCES_SECRETS_D,):
        if not os.path.isdir(d):
            os.makedirs(d)
            os.chmod(d, 0o700)
    for f in (SSH_PRIVATE_KEY, SSH_PUBLIC_KEY, RSYNCD_CONF, RSYNCD_SECRETS):
        d = os.path.dirname(f)
        if not os.path.isdir(d):
            os.makedirs(d)

    root_config = {}

    # Merge in config.d/*.json to the root level
    config_files = [os.path.join(CONFIG_D, fn) for fn in os.listdir(CONFIG_D) if fn.endswith('.json') and os.path.isfile(os.path.join(CONFIG_D, fn)) and os.access(os.path.join(CONFIG_D, fn), os.R_OK)]
    config_files.sort()
    for file in config_files:
        with open(file) as f:
            j = json.load(f)
        root_config = dict_merge(root_config, j)

    # Validate the unit name
    if not 'unit_name' in root_config:
        root_config['unit_name'] = platform.node()
        # If this isn't in the on-disk config, don't write it; just
        # generate it every time

    # Validate the machine UUID/secret
    write_uuid_data = False
    if not 'machine_uuid' in root_config:
        root_config['machine_uuid'] = str(uuid.uuid4())
        write_uuid_data = True
    if not 'machine_secret' in root_config:
        root_config['machine_secret'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
        write_uuid_data = True
    # Write out the machine UUID/secret if needed
    if write_uuid_data:
        with open(os.path.join(CONFIG_D, '10-machine_uuid.json'), 'w') as f:
            os.chmod(os.path.join(CONFIG_D, '10-machine_uuid.json'), 0o600)
            json_dump_p({'machine_uuid': root_config['machine_uuid'], 'machine_secret': root_config['machine_secret']}, f)

    # Restoration configuration
    write_restore_data = False
    if not 'restore_path' in root_config:
        root_config['restore_path'] = RESTORE_DIR
        write_restore_data = True
    if not 'restore_module' in root_config:
        root_config['restore_module'] = 'turku-restore'
        write_restore_data = True
    if not 'restore_username' in root_config:
        root_config['restore_username'] = str(uuid.uuid4())
        write_restore_data = True
    if not 'restore_password' in root_config:
        root_config['restore_password'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
        write_restore_data = True
    if write_restore_data:
        with open(os.path.join(CONFIG_D, '10-restore.json'), 'w') as f:
            os.chmod(os.path.join(CONFIG_D, '10-restore.json'), 0o600)
            restore_out = {
                'restore_path': root_config['restore_path'],
                'restore_module': root_config['restore_module'],
                'restore_username': root_config['restore_username'],
                'restore_password': root_config['restore_password'],
            }
            json_dump_p(restore_out, f)
    if not os.path.isdir(root_config['restore_path']):
        os.makedirs(root_config['restore_path'])

    # Generate the SSH keypair if it doesn't exist
    if not os.path.isfile(SSH_PUBLIC_KEY):
        subprocess.check_call(['ssh-keygen', '-t', 'rsa', '-N', '', '-C', 'turku', '-f', SSH_PRIVATE_KEY])

    # Pull the SSH public key
    with open(SSH_PUBLIC_KEY) as f:
        root_config['ssh_public_key'] = f.read().rstrip()

    sources_config = {}
    # Merge in sources.d/*.json to the sources dict
    sources_files = [os.path.join(SOURCES_D, fn) for fn in os.listdir(SOURCES_D) if fn.endswith('.json') and os.path.isfile(os.path.join(SOURCES_D, fn)) and os.access(os.path.join(SOURCES_D, fn), os.R_OK)]
    sources_files.sort()
    for file in sources_files:
        with open(file) as f:
            j = json.load(f)
        sources_config = dict_merge(sources_config, j)

    for s in sources_config:
        # Check for missing usernames/passwords
        if not ('username' in sources_config[s] or 'password' in sources_config[s]):
            # If they're in sources_secrets.d, use them
            if os.path.isfile(os.path.join(SOURCES_SECRETS_D, s + '.json')):
                with open(os.path.join(SOURCES_SECRETS_D, s + '.json')) as f:
                    j = json.load(f)
                sources_config = dict_merge(sources_config, {s: j})
        # Check again and generate sources_secrets.d if still not found
        if not ('username' in sources_config[s] or 'password' in sources_config[s]):
            if not 'username' in sources_config[s]:
                sources_config[s]['username'] = str(uuid.uuid4())
            if not 'password' in sources_config[s]:
                sources_config[s]['password'] = ''.join(random.choice(string.ascii_letters + string.digits) for i in range(30))
            with open(os.path.join(SOURCES_SECRETS_D, s + '.json'), 'w') as f:
                json_dump_p({'username': sources_config[s]['username'], 'password': sources_config[s]['password']}, f)

    # Check for required sources options
    for s in sources_config:
        if not 'path' in sources_config[s]:
            del sources_config[s]

    root_config['sources'] = sources_config

    return root_config


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
