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
import sys
import tempfile
import time
import fcntl

SSH_PRIVATE_KEY = '/etc/turku-agent/id_rsa'
VAR_DIR = '/var/lib/turku-agent'
LOCK_FILE = '/var/lock/turku-agent.lock'
RESTORE_CONFIG = '/etc/turku-agent/config.d/10-restore.json'


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--wait', '-w', type=float)
    parser.add_argument('--restore', action='store_true')
    return parser.parse_args()


def main(argv):
    # Basic checks
    if not os.path.isfile(SSH_PRIVATE_KEY):
        return
    if not os.path.isfile(os.path.join(VAR_DIR, 'server_config.json')):
        return
    with open(os.path.join(VAR_DIR, 'server_config.json')) as f:
        server_config = json.load(f)
    for i in ('ssh_ping_host', 'ssh_ping_host_keys', 'ssh_ping_port', 'ssh_ping_user'):
        if not i in server_config:
            return

    args = parse_args()
    # Sleep a random amount of time if requested
    if args.wait:
        time.sleep(random.uniform(0, args.wait))

    lock = open(LOCK_FILE, 'w')
    try:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError, e:
        import errno
        if e.errno in (errno.EACCES, errno.EAGAIN):
            return

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
        if os.path.isfile(RESTORE_CONFIG):
            with open(RESTORE_CONFIG) as f:
                restore_config = json.load(f)
            print('Local destination path: %s' % restore_config['restore_path'])
            print('Sample restore usage from storage unit:')
            print('    RSYNC_PASSWORD=%s rsync -avzP --numeric-ids ${P?}/ rsync://%s@127.0.0.1:%s/%s/' % (restore_config['restore_password'], restore_config['restore_username'], high_port, restore_config['restore_module']))
            print()

    # Call ssh
    p = subprocess.Popen([
        'ssh', '-T',
        '-o', 'UserKnownHostsFile=%s' % t.name,
        '-o', 'StrictHostKeyChecking=yes',
        '-i', SSH_PRIVATE_KEY,
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