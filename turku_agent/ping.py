# SPDX-PackageSummary: Turku backups - client agent
# SPDX-FileCopyrightText: Copyright (C) 2015-2020 Canonical Ltd.
# SPDX-FileCopyrightText: Copyright (C) 2015-2021 Ryan Finnie <ryan@finnie.org>
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import logging
import os
import random
import shlex
import subprocess
import tempfile
import time

from .utils import load_config, RuntimeLock, api_call, safe_write


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config-dir", "-c", type=str, default="/etc/turku-agent")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--wait", "-w", type=float)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--restore-storage", type=str, default=None)
    parser.add_argument(
        "--gonogo-program",
        type=str,
        default=None,
        help="Go/no-go program run each time to determine whether to ping",
    )
    return parser.parse_args()


def call_rsyncd(config, ssh_req):
    """Build configuration files and start rsyncd"""

    # Build rsyncd.conf
    built_rsyncd_conf = (
        "address = {}\n"
        "port = {}\n"
        "log file = /dev/stdout\n"
        "uid = {}\n"
        "gid = {}\n"
        "list = false\n\n"
    ).format(
        config["rsyncd_local_address"],
        ssh_req["port"],
        config["rsyncd_user"],
        config["rsyncd_group"],
    )
    rsyncd_secrets = []
    rsyncd_secrets.append((config["restore_username"], config["restore_password"]))
    built_rsyncd_conf += (
        "[{}]\n"
        "    path = {}\n"
        "    auth users = {}\n"
        "    secrets file = {}\n"
        "    read only = false\n\n"
    ).format(
        config["restore_module"],
        config["restore_path"],
        config["restore_username"],
        os.path.join(config["var_dir"], "rsyncd.secrets"),
    )
    for s in config["sources"]:
        sd = config["sources"][s]
        rsyncd_secrets.append((sd["username"], sd["password"]))
        built_rsyncd_conf += (
            "[{}]\n"
            "    path = {}\n"
            "    auth users = {}\n"
            "    secrets file = {}\n"
            "    read only = true\n\n"
        ).format(
            s,
            sd["path"],
            sd["username"],
            os.path.join(config["var_dir"], "rsyncd.secrets"),
        )
    with safe_write(os.path.join(config["var_dir"], "rsyncd.conf")) as f:
        f.write(built_rsyncd_conf)

    # Build rsyncd.secrets
    built_rsyncd_secrets = ""
    for (username, password) in rsyncd_secrets:
        built_rsyncd_secrets += username + ":" + password + "\n"
    with safe_write(os.path.join(config["var_dir"], "rsyncd.secrets")) as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(built_rsyncd_secrets)

    rsyncd_command = config["rsyncd_command"]
    rsyncd_command.append("--no-detach")
    rsyncd_command.append("--daemon")
    rsyncd_command.append(
        "--config={}".format(os.path.join(config["var_dir"], "rsyncd.conf"))
    )
    logging.debug("Executing: {}".format(rsyncd_command))
    return subprocess.Popen(rsyncd_command, stdin=subprocess.DEVNULL)


def call_ssh(config, storage, ssh_req):
    # Write the server host public key
    t = tempfile.NamedTemporaryFile(mode="w+", encoding="UTF-8")
    for key in storage["ssh_ping_host_keys"]:
        t.write("%s %s\n" % (storage["ssh_ping_host"], key))
    t.flush()

    # Call ssh
    ssh_command = config["ssh_command"]
    ssh_command += [
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "UserKnownHostsFile=%s" % t.name,
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "CheckHostIP=no",
        "-i",
        config["ssh_private_key_file"],
        "-R",
        "{}:{}:{}".format(
            ssh_req["port"], config["rsyncd_local_address"], ssh_req["port"]
        ),
        "-p",
        str(storage["ssh_ping_port"]),
        "-l",
        storage["ssh_ping_user"],
        storage["ssh_ping_host"],
        "turku-ping-remote",
    ]
    logging.debug("SSH command running: {}".format(ssh_command))
    p = subprocess.Popen(ssh_command, stdin=subprocess.PIPE)

    # Write the ssh request
    logging.debug("SSH request: {}".format(ssh_req))
    p.stdin.write((json.dumps(ssh_req) + "\n.\n").encode("UTF-8"))
    p.stdin.flush()

    # Wait for the server to close the SSH connection
    try:
        p.wait()
    except KeyboardInterrupt:
        pass

    # Cleanup
    t.close()


def main():
    args = parse_args()

    logging.basicConfig(level=(logging.DEBUG if args.debug else logging.INFO))

    # Sleep a random amount of time if requested
    if args.wait:
        wait_time = random.uniform(0, args.wait)
        logging.debug("Waiting {} seconds".format(wait_time))
        time.sleep(wait_time)

    config = load_config(args.config_dir)

    # Basic checks
    for i in ("ssh_private_key_file", "machine_uuid", "machine_secret", "api_url"):
        if i not in config:
            logging.debug("Missing required configs, exiting silently")
            return
    if not os.path.isfile(config["ssh_private_key_file"]):
        logging.debug("Missing required configs, exiting silently")
        return

    # If a go/no-go program is defined, run it and only go if it exits 0.
    # Example: prevent backups during high-load for sensitive systems:
    #   ['check_load', '-c', '1,5,15']
    gonogo_program = (
        args.gonogo_program if args.gonogo_program else config["gonogo_program"]
    )
    if isinstance(gonogo_program, (list, tuple)):
        # List, program name first, optional arguments after
        gonogo_program_and_args = list(gonogo_program)
    elif isinstance(gonogo_program, str):
        # String, shlex split it
        gonogo_program_and_args = shlex.split(gonogo_program)
    else:
        # None
        gonogo_program_and_args = []
    if gonogo_program_and_args:
        try:
            logging.debug("Executing go/no-go: {}".format(gonogo_program_and_args))
            subprocess.check_call(gonogo_program_and_args)
        except (subprocess.CalledProcessError, OSError):
            logging.debug("Go/no-go exited non-zero, exiting silently")
            return

    lock = RuntimeLock(lock_dir=config["lock_dir"])

    restore_mode = args.restore

    # Check with the API server
    api_out = {}

    machine_merge_map = (("machine_uuid", "uuid"), ("machine_secret", "secret"))
    api_out["machine"] = {}
    for a, b in machine_merge_map:
        if a in config:
            api_out["machine"][b] = config[a]

    if restore_mode:
        print("Entering restore mode.")
        print()
        api_reply = api_call(config["api_url"], "agent_ping_restore", api_out)

        sources_by_storage = {}
        for source_name in api_reply["machine"]["sources"]:
            source = api_reply["machine"]["sources"][source_name]
            if source_name not in config["sources"]:
                continue
            if "storage" not in source:
                continue
            if source["storage"]["name"] not in sources_by_storage:
                sources_by_storage[source["storage"]["name"]] = {}
            sources_by_storage[source["storage"]["name"]][source_name] = source

        if len(sources_by_storage) == 0:
            print("Cannot find any appropraite sources.")
            return
        print("This machine's sources are on the following storage units:")
        for storage_name in sources_by_storage:
            print("    %s" % storage_name)
            for source_name in sources_by_storage[storage_name]:
                print("        %s" % source_name)
        print()
        if len(sources_by_storage) == 1:
            storage = list(list(sources_by_storage.values())[0].values())[0]["storage"]
        elif args.restore_storage:
            if args.restore_storage in sources_by_storage:
                storage = sources_by_storage[args.restore_storage]["storage"]
            else:
                print('Cannot find appropriate storage "%s"' % args.restore_storage)
                return
        else:
            print(
                "Multiple storages found.  Please use --restore-storage to specify one."
            )
            return

        ssh_req = {
            "verbose": True,
            "action": "restore",
            "port": random.randint(49152, 65535),
        }
        print("Machine UUID: {}".format(config["machine_uuid"]))
        if config.get("environment_name"):
            print("Machine environment: {}".format(config["environment_name"]))
        if config.get("service_name"):
            print("Machine service: {}".format(config["service_name"]))
        if config.get("unit_name"):
            print("Machine unit: {}".format(config["unit_name"]))
        print("Storage unit: %s" % storage["name"])
        if "restore_path" in config:
            print("Local destination path: %s" % config["restore_path"])
            print("Sample restore usage from storage unit:")
            print(
                "    cd /var/lib/turku-storage/machines/{}/".format(
                    config["machine_uuid"]
                )
            )
            print(
                "    RSYNC_PASSWORD=%s rsync -avzP --numeric-ids ${P?}/ rsync://%s@127.0.0.1:%s/%s/"
                % (
                    config["restore_password"],
                    config["restore_username"],
                    ssh_req["port"],
                    config["restore_module"],
                )
            )
            print()
        rsyncd_process = call_rsyncd(config, ssh_req)
        time.sleep(3)
        call_ssh(config, storage, ssh_req)
        rsyncd_process.terminate()
        rsyncd_process.wait()
    else:
        api_reply = api_call(config["api_url"], "agent_ping_checkin", api_out)

        sources_by_storage = {}
        for source_name in api_reply["machine"]["scheduled_sources"]:
            source = api_reply["machine"]["scheduled_sources"][source_name]
            if source_name not in config["sources"]:
                continue
            if "storage" not in source:
                continue
            if source["storage"]["name"] not in sources_by_storage:
                sources_by_storage[source["storage"]["name"]] = {}
            sources_by_storage[source["storage"]["name"]][source_name] = source

        for storage_name in sources_by_storage:
            ssh_req = {
                "verbose": True,
                "action": "checkin",
                "port": random.randint(49152, 65535),
                "sources": {},
            }
            for source in sources_by_storage[storage_name]:
                ssh_req["sources"][source] = {
                    "username": config["sources"][source]["username"],
                    "password": config["sources"][source]["password"],
                }
            rsyncd_process = call_rsyncd(config, ssh_req)
            time.sleep(3)
            call_ssh(
                config,
                list(sources_by_storage[storage_name].values())[0]["storage"],
                ssh_req,
            )
            rsyncd_process.terminate()
            rsyncd_process.wait()

    # Cleanup
    lock.close()
