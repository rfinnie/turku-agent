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

import random
import os
import subprocess
import sys
import time
from utils import json_dump_p, load_config, api_call


CONFIG_D = '/etc/turku-agent/config.d'
SOURCES_D = '/etc/turku-agent/sources.d'
SOURCES_SECRETS_D = '/etc/turku-agent/sources_secrets.d'
SSH_PRIVATE_KEY = '/etc/turku-agent/id_rsa'
SSH_PUBLIC_KEY = '/etc/turku-agent/id_rsa.pub'
RSYNCD_CONF = '/etc/turku-agent/rsyncd.conf'
RSYNCD_SECRETS = '/etc/turku-agent/rsyncd.secrets'
VAR_DIR = '/var/lib/turku-agent'
RESTORE_DIR = '/var/backups/turku-agent/restore'


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--wait', '-w', type=float)
    return parser.parse_args()


def write_conf_files(config):
    # Build rsyncd.conf
    built_rsyncd_conf = 'address = 127.0.0.1\nport = 27873\nlog file = /dev/stdout\nuid = root\ngid = root\nlist = false\n\n'
    rsyncd_secrets = []
    rsyncd_secrets.append((config['restore_username'], config['restore_password']))
    built_rsyncd_conf += '[%s]\n    path = %s\n    auth users = %s\n    secrets file = %s\n    read only = false\n\n' % (config['restore_module'], config['restore_path'], config['restore_username'], RSYNCD_SECRETS)
    for s in config['sources']:
        sd = config['sources'][s]
        rsyncd_secrets.append((sd['username'], sd['password']))
        built_rsyncd_conf += '[%s]\n    path = %s\n    auth users = %s\n    secrets file = %s\n    read only = true\n\n' % (s, sd['path'], sd['username'], RSYNCD_SECRETS)
    with open(RSYNCD_CONF, 'w') as f:
        f.write(built_rsyncd_conf)

    # Build rsyncd.secrets
    built_rsyncd_secrets = ''
    for (username, password) in rsyncd_secrets:
        built_rsyncd_secrets += username + ':' + password + '\n'
    with open(RSYNCD_SECRETS, 'w') as f:
        os.chmod(RSYNCD_SECRETS, 0o600)
        f.write(built_rsyncd_secrets)


def restart_services():
    # Restart rsyncd
    if not subprocess.call(['service', 'turku-agent-rsyncd', 'restart']) == 0:
        subprocess.check_call(['service', 'turku-agent-rsyncd', 'start'])


def send_config(config):
    if not 'api_url' in config:
        return

    api_out = {}

    # Merge the following options into the root
    root_merge_map = (
        ('api_auth', 'auth'),
    )
    for a, b in root_merge_map:
        if a in config:
            api_out[b] = config[a]

    # Merge the following options into the machine section
    machine_merge_map = (
        ('machine_uuid', 'uuid'),
        ('machine_secret', 'secret'),
        ('environment_name', 'environment_name'),
        ('service_name', 'service_name'),
        ('unit_name', 'unit_name'),
        ('ssh_public_key', 'ssh_public_key'),
    )
    api_out['machine'] = {}
    for a, b in machine_merge_map:
        if a in config:
            api_out['machine'][b] = config[a]

    api_out['sources'] = config['sources']

    try:
        api_reply = api_call(config['api_url'], 'update_config', api_out)
    except:
        #pass
        raise

    # Write the response
    with open(os.path.join(VAR_DIR, 'server_config.json'), 'w') as f:
        os.chmod(os.path.join(VAR_DIR, 'server_config.json'), 0o600)
        json_dump_p(api_reply, f)


def main(argv):
    args = parse_args()
    # Sleep a random amount of time if requested
    if args.wait:
        time.sleep(random.uniform(0, args.wait))

    config = load_config()
    write_conf_files(config)
    send_config(config)
    restart_services()