# SPDX-PackageSummary: Turku backups - client agent
# SPDX-FileCopyrightText: Copyright (C) 2015-2020 Canonical Ltd.
# SPDX-FileCopyrightText: Copyright (C) 2015-2021 Ryan Finnie <ryan@finnie.org>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import random
import time

from .utils import load_config, fill_config, RuntimeLock, api_call


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
    parser.add_argument("--api-auth-name")
    parser.add_argument("--api-auth-secret")
    return parser.parse_args()


def send_config(config, args):
    required_keys = ["api_url"]
    for k in required_keys:
        if k not in config:
            raise IncompleteConfigError('Required config "{}" not found.'.format(k))

    api_out = {}
    # API auth is only needed on initial machine registration
    if args.api_auth_name and args.api_auth_secret:
        # name/secret style, provided on command line
        api_out["auth"] = {
            "name": args.api_auth_name,
            "secret": args.api_auth_secret,
        }
    elif ("api_auth_name" in config) and ("api_auth_secret" in config):
        # name/secret style
        api_out["auth"] = {
            "name": config["api_auth_name"],
            "secret": config["api_auth_secret"],
        }
    elif "api_auth" in config:
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
    with RuntimeLock(lock_dir=config["lock_dir"]):
        fill_config(config)
        send_config(config, args)
