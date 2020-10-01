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

import logging
import os
import random
import time

from .utils import load_config, fill_config, acquire_lock, api_call


class IncompleteConfigError(Exception):
    pass


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config-dir", "-c", type=str, default="/etc/turku-agent")
    parser.add_argument("--wait", "-w", type=float)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def send_config(config):
    required_keys = ["api_url"]
    if "api_auth" not in config:
        required_keys += ["api_auth_name", "api_auth_secret"]
    for k in required_keys:
        if k not in config:
            raise IncompleteConfigError('Required config "%s" not found.' % k)

    api_out = {}
    if ("api_auth_name" in config) and ("api_auth_secret" in config):
        # name/secret style
        api_out["auth"] = {
            "name": config["api_auth_name"],
            "secret": config["api_auth_secret"],
        }
    else:
        # nameless secret style
        api_out["auth"] = config["api_auth"]

    # Merge the following options into the machine section
    machine_merge_map = (
        ("machine_uuid", "uuid"),
        ("machine_secret", "secret"),
        ("environment_name", "environment_name"),
        ("service_name", "service_name"),
        ("unit_name", "unit_name"),
        ("ssh_public_key", "ssh_public_key"),
        ("published", "published"),
    )
    api_out["machine"] = {}
    for a, b in machine_merge_map:
        if a in config:
            api_out["machine"][b] = config[a]

    api_out["machine"]["sources"] = config["sources"]

    api_call(config["api_url"], "update_config", api_out)


def main():
    args = parse_args()

    logging.basicConfig(level=(logging.DEBUG if args.debug else logging.INFO))

    # Sleep a random amount of time if requested
    if args.wait:
        time.sleep(random.uniform(0, args.wait))

    config = load_config(args.config_dir)
    lock = acquire_lock(os.path.join(config["lock_dir"], "turku-update-config.lock"))
    fill_config(config)
    send_config(config)
    lock.close()
