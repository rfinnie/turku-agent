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

from __future__ import print_function
import os
import json
import random
import subprocess
import tempfile
import time
from utils import load_config, acquire_lock


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--config-dir', '-c', type=str, default='/etc/turku-agent')
    parser.add_argument('--wait', '-w', type=float)
    parser.add_argument('--restore', action='store_true')
    return parser.parse_args()


def main(argv):
    args = parse_args()

    # Sleep a random amount of time if requested
    if args.wait:
        time.sleep(random.uniform(0, args.wait))

    config = load_config(args.config_dir)

    # Basic checks
    for i in ('ssh_private_key_file',):
        if i not in config:
            return
    if not os.path.isfile(config['ssh_private_key_file']):
        return
    if not os.path.isfile(os.path.join(config['var_dir'], 'server_config.json')):
        return
    with open(os.path.join(config['var_dir'], 'server_config.json')) as f:
        server_config = json.load(f)
    for i in ('ssh_ping_host', 'ssh_ping_host_keys', 'ssh_ping_port', 'ssh_ping_user'):
        if i not in server_config:
            return

    lock = acquire_lock(os.path.join(config['lock_dir'], 'turku-agent-ping.lock'))

    # Write the server host public key
    t = tempfile.NamedTemporaryFile()
    for key in server_config['ssh_ping_host_keys']:
        t.write('%s %s\n' % (server_config['ssh_ping_host'], key))
    t.flush()

    # Use a high port for the remote end
    high_port = random.randint(49152, 65535)

    # Restore mode
    restore_mode = False
    if args.restore:
        restore_mode = True
        print('Entering restore mode.')
        print()
        if 'storage_name' in server_config:
            print('Storage unit: %s' % server_config['storage_name'])
        if 'restore_path' in config:
            print('Local destination path: %s' % config['restore_path'])
            print('Sample restore usage from storage unit:')
            print('    RSYNC_PASSWORD=%s rsync -avzP --numeric-ids ${P?}/ rsync://%s@127.0.0.1:%s/%s/' % (config['restore_password'], config['restore_username'], high_port, config['restore_module']))
            print()

    # Call ssh
    p = subprocess.Popen([
        'ssh', '-T',
        '-o', 'UserKnownHostsFile=%s' % t.name,
        '-o', 'StrictHostKeyChecking=yes',
        '-i', config['ssh_private_key_file'],
        '-R', '%s:127.0.0.1:27873' % high_port,
        '-p', str(server_config['ssh_ping_port']),
        '-l', server_config['ssh_ping_user'],
        server_config['ssh_ping_host'],
        'turku-ping-remote',
    ], stdin=subprocess.PIPE)

    out = {
        'action': 'checkin',
        'port': high_port,
        'verbose': True,
    }
    if restore_mode:
        out['action'] = 'restore'
    # Let the server know the high port
    p.stdin.write(json.dumps(out) + '\n.\n')

    # Wait for the server to close the SSH connection
    p.wait()

    # Cleanup
    t.close()
    lock.close()
