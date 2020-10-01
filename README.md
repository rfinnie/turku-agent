# turku-agent

## About Turku
Turku is an agent-based backup system where the server doing the backups has no direct access to the machine being backed up.  Instead, the machine's agent coordinates with an API server and opens a reverse tunnel to the storage server when it is time to do a backup.

Turku is comprised of the following components:

* [turku-api](https://launchpad.net/turku/turku-api), a Django web application which acts as an API server and coordinator.
* [turku-storage](https://launchpad.net/turku/turku-storage), an agent installed on the servers which do the actual backups.
* [turku-agent](https://launchpad.net/turku/turku-agent), an agent installed on the machines to be backed up.

## Installation

turku-agent is a standard Python 3 package.  It requires the following non-stdlib Python packages:

* requests, for HTTPS communication with turku-api.

and the following non-Python requirements:

* rsync, for transferring files from turku-agent to turku-storage.  A turku-specific rsyncd, bound to localhost, will be maintained by turku-agent and running during the lifetime of the turku-agent-ping backup.

Several periodic programs will also need to be run; .cron or systemd .service/.timer examples are available in the source distribution (pick either cron or systemd).

## Configuration

Once installed, create `/etc/turku-agent/config.d/config.json` with the following information:

```json
{
    "api_auth": "MACHINE AUTH STRING",
    "api_url": "https://turku.example.com/v1"
}
```

* **api_auth** - Registration string for a Machine Auth as defined in turku-api.
* **api_url** - URL of the turku-api service.
* **environment_name**, **service_name**, **unit_name** - Optional informational strings describing the machine's role in a service environment.  For example, environment_name "transaction-manager", service_name "sql-database", unit_name "sql-database-regiona/2".
* **published** - Boolean, whether the machine is enabled within turku-api.

You will also need to define one or more sources to back up.  For example, create `/etc/turku-agent/sources.d/etc.json`:

```json
{
    "etc": {
        "path": "/etc"
    }
}
```

No restrictions are placed on the source contents and the dictionary is sent to turku-api verbatim, with "path" being the only required option.  Here are some options which can be used by turku-api:

* **path** - The local path to back up.
* **comment** - Human-readable comment about the source.
* **frequency** - Default "daily", with "weekly", "monthly" or a day such as "tuesday" available.  A time range may be further added, such as "tuesday, 0200-0500".  If turku-api has croniter support, a Jenkins-style hashed cron definition may also be specified, such as "cron H H * * *" for daily.
* **retention** - Default "last 5 days, earliest of month", meaning any backup made within the last 5 days will be saved, along with the earliest backup of the current month. Also supported are variations such as "last 10 snapshots, earliest of 2 weeks".
* **bwlimit** - Transfer limit in KiB/s.
* **snapshot_mode** - "link-dest" or "none".  "link-dest" means using rsync's --link-dest option to build snapshot trees where only files which have changed are stored individually; same files are deduplicated using hard links.
* **large_rotating_files** and **large_modifying_files** - Boolean; this tells turku-storage that it wouldn't be a good idea to use link-dest deduplication on this source.  Currently, this means forcing snapshot_mode to "none".
* **shared_service** - Boolean; at the moment, this does nothing.  It was intended to signify that multiple unit_names within the same environment_name/service_name pair could be treated as one.  For example, if there were multiple mirrored database units within a shared service, it wouldn't matter which unit was backed up since they all contain the same information.

Once configured, run the following to register the Machine unit and sync its backup information with turku-api:

```
sudo turku-update-config
```

## Running

By default, every 5 minutes turku-agent-ping will check in with turku-api to see if there is anything to do.  If there is, turku-api gives turku-agent information about a turku-storage unit.  turku-agent SSHes to turku-storage and sets up a reverse tunnel, allowing turku-storage to contact turku-agent's local rsyncd and perform the backup.

When `turku-agent-ping --restore` is run, it sets up a writable rsync module on the machine to restore to, sets up an idle reverse SSH tunnel to the Storage unit, then gives basic information of what to do on the storage unit. For example:

```
$ sudo turku-agent-ping --restore
Entering restore mode.

This machine's sources are on the following storage units:
    primary
        baremetal

Machine UUID: 9bef0a75-5a9b-43f7-a66a-7e8c6d7ad91d
Machine unit: examplemachine
Storage unit: primary
Local destination path: /var/backups/turku-agent/restore
Sample restore usage from storage unit:
    cd /var/lib/turku-storage/machines/9bef0a75-5a9b-43f7-a66a-7e8c6d7ad91d/
    RSYNC_PASSWORD=RNxHVnnl2zt33ktbkccT rsync -avzP --numeric-ids ${P?}/ \
        rsync://e908212b-e35f-453e-9503-0de047ee9e22@127.0.0.1:64951/turku-restore/

[2020-10-01 06:40:59,439 primary] INFO: Restore mode active on port 64951.  Good luck.
```