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
from utils import load_config, acquire_lock, api_call


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
    for i in ('ssh_private_key_file', 'machine_uuid', 'machine_secret', 'api_url'):
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

    restore_mode = args.restore

    ssh_req = {
        'verbose': True,
    }

    if not restore_mode:
        # Check with the API server
        api_out = {}

        machine_merge_map = (
            ('machine_uuid', 'uuid'),
            ('machine_secret', 'secret'),
        )
        api_out['machine'] = {}
        for a, b in machine_merge_map:
            if a in config:
                api_out['machine'][b] = config[a]

        api_reply = api_call(config['api_url'], 'agent_ping_checkin', api_out)

        if 'scheduled_sources' not in api_reply:
            return
        ssh_req['sources'] = {}
        for source in api_reply['scheduled_sources']:
            if source not in config['sources']:
                continue
            ssh_req['sources'][source] = {
                'username': config['sources'][source]['username'],
                'password': config['sources'][source]['password'],
            }
        if len(ssh_req['sources']) == 0:
            return

    # Write the server host public key
    t = tempfile.NamedTemporaryFile()
    for key in server_config['ssh_ping_host_keys']:
        t.write('%s %s\n' % (server_config['ssh_ping_host'], key))
    t.flush()

    # Use a high port for the remote end
    high_port = random.randint(49152, 65535)
    ssh_req['port'] = high_port

    # Restore mode
    if restore_mode:
        ssh_req['action'] = 'restore'
        print('Entering restore mode.')
        print()
        if 'storage_name' in server_config:
            print('Storage unit: %s' % server_config['storage_name'])
        if 'restore_path' in config:
            print('Local destination path: %s' % config['restore_path'])
            print('Sample restore usage from storage unit:')
            print(
                '    RSYNC_PASSWORD=%s rsync -avzP --numeric-ids ${P?}/ rsync://%s@127.0.0.1:%s/%s/' % (
                    config['restore_password'],
                    config['restore_username'],
                    high_port, config['restore_module']
                )
            )
            print()
    else:
        ssh_req['action'] = 'checkin'

    # Call ssh
    ssh_command = config['ssh_command']
    ssh_command += [
        '-T',
        '-o', 'UserKnownHostsFile=%s' % t.name,
        '-o', 'StrictHostKeyChecking=yes',
        '-i', config['ssh_private_key_file'],
        '-R', '%d:%s:%d' % (high_port, config['rsyncd_local_address'], config['rsyncd_local_port']),
        '-p', str(server_config['ssh_ping_port']),
        '-l', server_config['ssh_ping_user'],
        server_config['ssh_ping_host'],
        'turku-ping-remote',
    ]
    p = subprocess.Popen(ssh_command, stdin=subprocess.PIPE)

    # Write the ssh request
    p.stdin.write(json.dumps(ssh_req) + '\n.\n')

    # Wait for the server to close the SSH connection
    p.wait()

    # Cleanup
    t.close()
    lock.close()
