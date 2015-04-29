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
    parser.add_argument('--restore-storage', type=str, default=None)
    return parser.parse_args()


def call_ssh(config, storage, ssh_req):
    # Write the server host public key
    t = tempfile.NamedTemporaryFile()
    for key in storage['ssh_ping_host_keys']:
        t.write('%s %s\n' % (storage['ssh_ping_host'], key))
    t.flush()

    # Call ssh
    ssh_command = config['ssh_command']
    ssh_command += [
        '-T',
        '-o', 'BatchMode=yes',
        '-o', 'UserKnownHostsFile=%s' % t.name,
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'CheckHostIP=no',
        '-i', config['ssh_private_key_file'],
        '-R', '%d:%s:%d' % (ssh_req['port'], config['rsyncd_local_address'], config['rsyncd_local_port']),
        '-p', str(storage['ssh_ping_port']),
        '-l', storage['ssh_ping_user'],
        storage['ssh_ping_host'],
        'turku-ping-remote',
    ]
    p = subprocess.Popen(ssh_command, stdin=subprocess.PIPE)

    # Write the ssh request
    p.stdin.write(json.dumps(ssh_req) + '\n.\n')

    # Wait for the server to close the SSH connection
    p.wait()

    # Cleanup
    t.close()


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

    lock = acquire_lock(os.path.join(config['lock_dir'], 'turku-agent-ping.lock'))

    restore_mode = args.restore

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

    if restore_mode:
        print('Entering restore mode.')
        print()
        api_reply = api_call(config['api_url'], 'agent_ping_restore', api_out)

        sources_by_storage = {}
        for source_name in api_reply['machine']['sources']:
            source = api_reply['machine']['sources'][source_name]
            if source_name not in config['sources']:
                continue
            if 'storage' not in source:
                continue
            if source['storage']['name'] not in sources_by_storage:
                sources_by_storage[source['storage']['name']] = {}
            sources_by_storage[source['storage']['name']][source_name] = source

        if len(sources_by_storage) == 0:
            print('Cannot find any appropraite sources.')
            return
        print('This machine\'s sources are on the following storage units:')
        for storage_name in sources_by_storage:
            print('    %s' % storage_name)
            for source_name in sources_by_storage[storage_name]:
                print('        %s' % source_name)
        print()
        if len(sources_by_storage) == 1:
            storage = sources_by_storage.values()[0].values()[0]['storage']
        elif args.restore_storage:
            if args.restore_storage in sources_by_storage:
                storage = sources_by_storage[args.restore_storage]['storage']
            else:
                print('Cannot find appropriate storage "%s"' % args.restore_storage)
                return
        else:
            print('Multiple storages found.  Please use --restore-storage to specify one.')
            return

        ssh_req = {
            'verbose': True,
            'action': 'restore',
            'port': random.randint(49152, 65535),
        }
        print('Storage unit: %s' % storage['name'])
        if 'restore_path' in config:
            print('Local destination path: %s' % config['restore_path'])
            print('Sample restore usage from storage unit:')
            print(
                '    RSYNC_PASSWORD=%s rsync -avzP --numeric-ids ${P?}/ rsync://%s@127.0.0.1:%s/%s/' % (
                    config['restore_password'],
                    config['restore_username'],
                    ssh_req['port'], config['restore_module']
                )
            )
            print()
        call_ssh(config, storage, ssh_req)
    else:
        api_reply = api_call(config['api_url'], 'agent_ping_checkin', api_out)

        if 'scheduled_sources' not in api_reply:
            return
        sources_by_storage = {}
        for source_name in api_reply['machine']['scheduled_sources']:
            source = api_reply['machine']['scheduled_sources'][source_name]
            if source_name not in config['sources']:
                continue
            if 'storage' not in source:
                continue
            if source['storage']['name'] not in sources_by_storage:
                sources_by_storage[source['storage']['name']] = {}
            sources_by_storage[source['storage']['name']][source_name] = source

        for storage_name in sources_by_storage:
            ssh_req = {
                'verbose': True,
                'action': 'checkin',
                'port': random.randint(49152, 65535),
                'sources': {},
            }
            for source in sources_by_storage[storage_name]:
                ssh_req['sources'][source] = {
                    'username': config['sources'][source]['username'],
                    'password': config['sources'][source]['password'],
                }
            call_ssh(config, sources_by_storage[storage_name].values()[0]['storage'], ssh_req)

    # Cleanup
    lock.close()
